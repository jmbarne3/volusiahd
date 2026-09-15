"""Enough tests to catch the things that would quietly break.

Not exhaustive coverage. These cover the three failure modes that would be
expensive to discover in production: a route that 500s, unsanitized HTML
reaching a template that renders with `|safe`, and a draft program being
visible on the public site.
"""

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from .models import Category, ContactPerson, Page, Program, Submission


class PublicSiteTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.category = Category.objects.create(name="Co-ops", slug="co-ops", sort_order=10)
        cls.program = Program.objects.create(
            name="Sample Co-op",
            slug="sample-co-op",
            short_description="A weekly co-op in DeLand.",
            description="<p>Hello</p>",
            status=Program.Status.PUBLISHED,
            city="DeLand",
            is_featured=True,
        )
        cls.program.categories.add(cls.category)
        cls.draft = Program.objects.create(
            name="Not Ready",
            slug="not-ready",
            short_description="Still being checked.",
            status=Program.Status.DRAFT,
        )
        cls.page = Page.objects.create(
            title="About", slug="about", body="<p>About us.</p>", is_published=True
        )

    def test_public_routes_render(self):
        for url in [
            reverse("directory:home"),
            reverse("directory:program_list"),
            reverse("directory:program", kwargs={"slug": self.program.slug}),
            reverse("directory:category", kwargs={"slug": self.category.slug}),
            reverse("directory:page", kwargs={"slug": self.page.slug}),
            reverse("directory:submit"),
            reverse("directory:robots"),
            "/sitemap.xml",
        ]:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)

    def test_draft_programs_are_not_public(self):
        self.assertEqual(
            self.client.get(
                reverse("directory:program", kwargs={"slug": self.draft.slug})
            ).status_code,
            404,
        )
        response = self.client.get(reverse("directory:program_list"))
        self.assertNotContains(response, "Not Ready")

    def test_search_matches_name_and_city(self):
        for query in ["Sample", "DeLand"]:
            with self.subTest(query=query):
                response = self.client.get(reverse("directory:program_list"), {"q": query})
                self.assertContains(response, "Sample Co-op")
        response = self.client.get(reverse("directory:program_list"), {"q": "nothing-matches"})
        self.assertNotContains(response, "Sample Co-op")

    def test_private_contacts_stay_private(self):
        ContactPerson.objects.create(
            program=self.program, name="Public Pat", email="pat@example.org", is_public=True
        )
        ContactPerson.objects.create(
            program=self.program, name="Private Pat", email="private@example.org", is_public=False
        )
        response = self.client.get(reverse("directory:program", kwargs={"slug": self.program.slug}))
        self.assertContains(response, "Public Pat")
        self.assertNotContains(response, "Private Pat")


class SanitizationTests(TestCase):
    def test_script_tags_are_stripped_on_save(self):
        """Templates render this field with `|safe`, so the stripping has to
        happen before storage — including on imports that bypass forms."""
        program = Program.objects.create(
            name="XSS",
            slug="xss",
            short_description="x",
            description='<p>ok<script>alert(1)</script><span style="color:red">styled</span></p>',
            status=Program.Status.PUBLISHED,
        )
        program.refresh_from_db()
        self.assertNotIn("<script>", program.description)
        self.assertNotIn("style=", program.description)
        self.assertIn("ok", program.description)

    def test_page_body_is_sanitized_too(self):
        page = Page.objects.create(
            title="T", slug="t", body="<p>hi<script>x</script></p>", is_published=True
        )
        page.refresh_from_db()
        self.assertNotIn("<script>", page.body)


class SubmissionTests(TestCase):
    def _payload(self, **overrides):
        return {
            "program_name": "New Group",
            "website": "https://example.com",
            "email": "group@example.com",
            "phone": "",
            "city": "Ormond Beach",
            "description": "A new group that meets on Thursdays.",
            "submitter_name": "Jane",
            "submitter_email": "jane@example.com",
            "website_url": "",
            **overrides,
        }

    def test_a_real_submission_is_stored(self):
        response = self.client.post(reverse("directory:submit"), self._payload())
        self.assertRedirects(response, reverse("directory:submit_thanks"))
        self.assertEqual(Submission.objects.count(), 1)
        self.assertEqual(Submission.objects.get().status, Submission.Status.NEW)

    def test_honeypot_rejects_bots(self):
        response = self.client.post(
            reverse("directory:submit"), self._payload(website_url="http://spam.example")
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Submission.objects.count(), 0)


class AdminTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_superuser("editor", "e@example.org", "pw-for-tests-only")
        cls.category = Category.objects.create(name="Co-ops", slug="co-ops")

    def setUp(self):
        self.client.force_login(self.user)

    def test_admin_screens_render(self):
        for url in [
            "/admin/",
            "/admin/directory/program/",
            "/admin/directory/program/add/",
            "/admin/directory/category/",
            "/admin/directory/page/",
            "/admin/directory/submission/",
        ]:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)

    def test_groups_are_hidden(self):
        self.assertEqual(self.client.get("/admin/auth/group/").status_code, 404)

    def test_approving_a_submission_creates_a_draft_program(self):
        submission = Submission.objects.create(
            program_name="Suggested Co-op", description="They meet on Fridays."
        )
        submission.categories.add(self.category)

        response = self.client.post(
            "/admin/directory/submission/",
            {
                "action": "approve_into_programs",
                "_selected_action": [str(submission.pk)],
            },
            follow=True,
        )
        self.assertEqual(response.status_code, 200)

        submission.refresh_from_db()
        self.assertEqual(submission.status, Submission.Status.APPROVED)
        self.assertIsNotNone(submission.created_program)

        program = submission.created_program
        self.assertEqual(program.name, "Suggested Co-op")
        self.assertEqual(program.slug, "suggested-co-op")
        # Approved, but not yet public: she still checks it before publishing.
        self.assertEqual(program.status, Program.Status.DRAFT)
        self.assertIn(self.category, program.categories.all())

    def test_approving_twice_does_not_duplicate(self):
        submission = Submission.objects.create(program_name="Once Only")
        for _ in range(2):
            self.client.post(
                "/admin/directory/submission/",
                {"action": "approve_into_programs", "_selected_action": [str(submission.pk)]},
                follow=True,
            )
        self.assertEqual(Program.objects.filter(name="Once Only").count(), 1)

    def test_mark_verified_today_action(self):
        program = Program.objects.create(name="P", slug="p", short_description="s")
        self.client.post(
            "/admin/directory/program/",
            {"action": "mark_verified_today", "_selected_action": [str(program.pk)]},
            follow=True,
        )
        program.refresh_from_db()
        self.assertIsNotNone(program.last_verified_on)
