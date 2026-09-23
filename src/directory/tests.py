"""Enough tests to catch the things that would quietly break.

Not exhaustive coverage. These cover the three failure modes that would be
expensive to discover in production: a route that 500s, unsanitized HTML
reaching a template that renders with `|safe`, and a draft program being
visible on the public site.
"""

import os
import shutil
import tempfile
from io import BytesIO

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from PIL import Image

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
            locations="101 Woodland Blvd, DeLand 32720",
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
            reverse("directory:register"),
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

    def test_search_matches_name_and_location(self):
        for query in ["Sample", "DeLand"]:
            with self.subTest(query=query):
                response = self.client.get(reverse("directory:program_list"), {"q": query})
                self.assertContains(response, "Sample Co-op")
        response = self.client.get(reverse("directory:program_list"), {"q": "nothing-matches"})
        self.assertNotContains(response, "Sample Co-op")

    def test_every_address_shows_on_the_program_page(self):
        self.program.locations = "101 Woodland Blvd, DeLand 32720\n7 Rich Ave, DeLand 32724"
        self.program.save(update_fields=["locations"])
        response = self.client.get(reverse("directory:program", kwargs={"slug": self.program.slug}))
        self.assertContains(response, "101 Woodland Blvd, DeLand 32720")
        self.assertContains(response, "7 Rich Ave, DeLand 32724")

    def test_the_facebook_link_is_a_mark_with_a_name_a_screen_reader_can_read(self):
        """An icon-only link is invisible to a screen reader without a label."""
        self.program.facebook = "https://facebook.com/groups/sample"
        self.program.save(update_fields=["facebook"])
        response = self.client.get(reverse("directory:program", kwargs={"slug": self.program.slug}))
        self.assertContains(response, 'href="https://facebook.com/groups/sample"')
        self.assertContains(response, 'aria-label="Facebook page for Sample Co-op"')
        self.assertContains(response, "<svg")

    def test_step_up_direct_pay_reads_as_a_sentence(self):
        for direct_pay, pep, fes_ua, expected in [
            (False, False, False, ""),
            (False, True, True, ""),
            (True, False, False, "Step Up direct pay"),
            (True, True, False, "Step Up direct pay (PEP)"),
            (True, False, True, "Step Up direct pay (FES-UA)"),
            (True, True, True, "Step Up direct pay (PEP, FES-UA)"),
        ]:
            with self.subTest(direct_pay=direct_pay, pep=pep, fes_ua=fes_ua):
                program = Program(
                    step_up_direct_pay=direct_pay, step_up_pep=pep, step_up_fes_ua=fes_ua
                )
                self.assertEqual(program.step_up_display, expected)

    def test_step_up_status_shows_on_the_listing_card(self):
        """The listings are where a family decides what to click.

        A program that takes scholarship money directly is the one they can
        afford, so that has to be visible before they open the page.
        """
        self.program.step_up_direct_pay = True
        self.program.step_up_pep = True
        self.program.save(update_fields=["step_up_direct_pay", "step_up_pep"])
        for url in [reverse("directory:home"), reverse("directory:program_list")]:
            with self.subTest(url=url):
                self.assertContains(self.client.get(url), "Step Up direct pay (PEP)")

    def test_step_up_status_shows_on_the_program_page(self):
        self.program.step_up_direct_pay = True
        self.program.step_up_fes_ua = True
        self.program.save(update_fields=["step_up_direct_pay", "step_up_fes_ua"])
        response = self.client.get(reverse("directory:program", kwargs={"slug": self.program.slug}))
        self.assertContains(response, "Step Up direct pay (FES-UA)")

    def test_contacts_never_reach_the_public_page(self):
        """There is no flag that publishes a contact, and there should not be.

        This is the test that has to fail if someone later adds `contacts` back
        into the view context or the template, because the people on this list
        gave us their details for our use, not for the directory.
        """
        ContactPerson.objects.create(
            program=self.program, name="Dana Reed", email="dana@example.org", phone="386-555-0101"
        )
        response = self.client.get(reverse("directory:program", kwargs={"slug": self.program.slug}))
        self.assertNotContains(response, "Dana Reed")
        self.assertNotContains(response, "dana@example.org")
        self.assertNotContains(response, "386-555-0101")


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


def make_test_image(name="logo.png"):
    """A one-pixel PNG, small enough to keep the suite fast."""
    buffer = BytesIO()
    Image.new("RGB", (1, 1), "white").save(buffer, format="PNG")
    return SimpleUploadedFile(name, buffer.getvalue(), content_type="image/png")


def make_oversized_image(name="huge.png"):
    """A real PNG over 2 MB. Random noise, because it will not compress."""
    side = 1100
    image = Image.frombytes("RGB", (side, side), os.urandom(side * side * 3))
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return SimpleUploadedFile(name, buffer.getvalue(), content_type="image/png")


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix="hsd-test-media-"))
class RegistrationTests(TestCase):
    """The registration form's job is to arrive complete enough to publish."""

    @classmethod
    def setUpTestData(cls):
        cls.category = Category.objects.create(name="Co-ops", slug="co-ops")

    def _payload(self, **overrides):
        return {
            "program_name": "Coastal Co-op",
            "short_description": "A Thursday co-op for K–8 families in Ormond Beach.",
            "description": "We meet weekly.\n\nClasses run September through May.",
            "categories": [str(self.category.pk)],
            "website": "https://coastal.example.org",
            "facebook": "https://facebook.com/groups/coastal",
            "email": "hello@coastal.example.org",
            "phone": "386-555-0100",
            "locations": "12 Ocean Ave, Ormond Beach 32176\n4 Granada Blvd, Ormond Beach 32176",
            "serves_grades": "K–8",
            "age_min": "5",
            "age_max": "14",
            "cost_notes": "$45 per semester.",
            "step_up_direct_pay": "on",
            "step_up_pep": "on",
            "meeting_schedule": "Thursdays 9am–noon.",
            "submitter_name": "Dana Reed",
            "submitter_email": "dana@coastal.example.org",
            "submitter_role": "Director",
            "is_authorized": "on",
            "website_url": "",
            **overrides,
        }

    def test_a_registration_is_stored_as_a_registration(self):
        response = self.client.post(reverse("directory:register"), self._payload())
        self.assertRedirects(response, reverse("directory:register_thanks"))

        submission = Submission.objects.get()
        self.assertEqual(submission.kind, Submission.Kind.REGISTRATION)
        self.assertEqual(submission.status, Submission.Status.NEW)
        self.assertEqual(submission.serves_grades, "K–8")
        self.assertTrue(submission.is_authorized)

    def test_a_program_can_meet_in_more_than_one_place(self):
        """The reason this field is free text rather than street/city/ZIP.

        Every line the registrant typed has to survive the round trip, because
        a co-op that meets in two churches has two addresses and no amount of
        structured address fields will hold the second one.
        """
        self.client.post(reverse("directory:register"), self._payload())
        submission = Submission.objects.get()
        self.assertEqual(
            submission.locations.splitlines(),
            ["12 Ocean Ave, Ormond Beach 32176", "4 Granada Blvd, Ormond Beach 32176"],
        )

    def test_a_meeting_place_is_required(self):
        response = self.client.post(reverse("directory:register"), self._payload(locations=""))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Submission.objects.count(), 0)

    def test_grades_served_is_required(self):
        response = self.client.post(reverse("directory:register"), self._payload(serves_grades=""))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Submission.objects.count(), 0)

    def test_authorization_is_required(self):
        response = self.client.post(reverse("directory:register"), self._payload(is_authorized=""))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Submission.objects.count(), 0)

    def test_at_least_one_way_to_make_contact_is_required(self):
        response = self.client.post(
            reverse("directory:register"),
            self._payload(website="", facebook="", email="", phone=""),
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "at least one way for families to reach you")
        self.assertEqual(Submission.objects.count(), 0)

    def test_a_facebook_page_is_enough_to_be_reachable(self):
        """The reason Facebook is its own field.

        A co-op whose entire presence is a Facebook group has no website, no
        published email and no phone. Before this field existed they failed the
        contact check and never made it into the directory at all.
        """
        response = self.client.post(
            reverse("directory:register"),
            self._payload(website="", email="", phone=""),
        )
        self.assertRedirects(response, reverse("directory:register_thanks"))
        self.assertEqual(Submission.objects.get().facebook, "https://facebook.com/groups/coastal")

    def test_a_scholarship_tick_implies_direct_pay(self):
        """Ticking PEP without the box above it is an answer, not a mistake."""
        self.client.post(
            reverse("directory:register"),
            self._payload(step_up_direct_pay="", step_up_pep="", step_up_fes_ua="on"),
        )
        submission = Submission.objects.get()
        self.assertTrue(submission.step_up_direct_pay)
        self.assertTrue(submission.step_up_fes_ua)
        self.assertFalse(submission.step_up_pep)

    def test_scholarships_are_dropped_when_they_are_not_a_direct_pay_provider(self):
        """Direct pay for a scholarship is meaningless without direct pay."""
        self.client.post(
            reverse("directory:register"),
            self._payload(step_up_direct_pay="", step_up_pep=""),
        )
        submission = Submission.objects.get()
        self.assertFalse(submission.step_up_direct_pay)
        self.assertFalse(submission.step_up_pep)

    def test_age_range_must_make_sense(self):
        response = self.client.post(
            reverse("directory:register"), self._payload(age_min="14", age_max="5")
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Submission.objects.count(), 0)

    def test_honeypot_rejects_bots(self):
        response = self.client.post(
            reverse("directory:register"), self._payload(website_url="http://spam.example")
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Submission.objects.count(), 0)

    def test_a_logo_can_be_uploaded(self):
        response = self.client.post(
            reverse("directory:register"), self._payload(logo=make_test_image())
        )
        self.assertRedirects(response, reverse("directory:register_thanks"))
        self.assertTrue(Submission.objects.get().logo)

    def test_an_oversized_logo_is_refused(self):
        """A genuine image over the ceiling, not a corrupt file — otherwise
        Pillow rejects it first and the size limit never gets exercised."""
        response = self.client.post(
            reverse("directory:register"), self._payload(logo=make_oversized_image())
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "larger than 2 MB")
        self.assertEqual(Submission.objects.count(), 0)


class ReferralTests(TestCase):
    def _payload(self, **overrides):
        return {
            "program_name": "Surf Lessons",
            "website": "https://surf.example.org",
            "email": "",
            "phone": "",
            "locations": "New Smyrna Beach",
            "description": "I think they take homeschoolers on Wednesdays.",
            "submitter_name": "Jane",
            "submitter_email": "jane@example.com",
            "website_url": "",
            **overrides,
        }

    def test_a_referral_is_stored_as_a_referral(self):
        response = self.client.post(reverse("directory:refer"), self._payload())
        self.assertRedirects(response, reverse("directory:refer_thanks"))
        submission = Submission.objects.get()
        self.assertEqual(submission.kind, Submission.Kind.REFERRAL)

    def test_honeypot_rejects_bots(self):
        self.client.post(reverse("directory:refer"), self._payload(website_url="http://spam"))
        self.assertEqual(Submission.objects.count(), 0)


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix="hsd-test-media-"))
class ApprovalTests(TestCase):
    """The promise of the registration form is that approving it is the only
    action left. These assert that nothing a registrant typed gets dropped."""

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_superuser("editor", "e@example.org", "pw-for-tests-only")
        cls.category = Category.objects.create(name="Co-ops", slug="co-ops")

    def setUp(self):
        self.client.force_login(self.user)

    def _registration(self, **overrides):
        fields = {
            "kind": Submission.Kind.REGISTRATION,
            "program_name": "Coastal Co-op",
            "short_description": "A Thursday co-op for K–8 families.",
            "description": "We meet weekly.\n\nClasses run September through May.",
            "website": "https://coastal.example.org",
            "facebook": "https://facebook.com/groups/coastal",
            "email": "hello@coastal.example.org",
            "phone": "386-555-0100",
            "locations": "12 Ocean Ave, Ormond Beach 32176\n4 Granada Blvd, Ormond Beach 32176",
            "serves_grades": "K–8",
            "age_min": 5,
            "age_max": 14,
            "cost_notes": "$45 per semester.",
            "meeting_schedule": "Thursdays 9am–noon.",
            "step_up_direct_pay": True,
            "step_up_pep": True,
            "submitter_name": "Dana Reed",
            "submitter_email": "dana@coastal.example.org",
            "is_authorized": True,
            **overrides,
        }
        submission = Submission.objects.create(**fields)
        submission.categories.add(self.category)
        return submission

    def _run(self, action, submission):
        return self.client.post(
            "/admin/directory/submission/",
            {"action": action, "_selected_action": [str(submission.pk)]},
            follow=True,
        )

    def test_approving_a_registration_publishes_a_complete_program(self):
        submission = self._registration()
        self.assertEqual(self._run("approve_and_publish", submission).status_code, 200)

        submission.refresh_from_db()
        self.assertEqual(submission.status, Submission.Status.APPROVED)
        program = submission.created_program
        self.assertIsNotNone(program)

        # Published immediately: this is the whole point of the longer form.
        self.assertEqual(program.status, Program.Status.PUBLISHED)

        # Every field the registrant filled in came across.
        self.assertEqual(program.name, "Coastal Co-op")
        self.assertEqual(program.short_description, "A Thursday co-op for K–8 families.")
        self.assertEqual(program.website, "https://coastal.example.org")
        self.assertEqual(program.facebook, "https://facebook.com/groups/coastal")
        self.assertEqual(program.email, "hello@coastal.example.org")
        self.assertEqual(program.phone, "386-555-0100")
        self.assertEqual(
            program.location_list,
            ["12 Ocean Ave, Ormond Beach 32176", "4 Granada Blvd, Ormond Beach 32176"],
        )
        self.assertEqual(program.serves_grades, "K–8")
        self.assertEqual(program.age_min, 5)
        self.assertEqual(program.age_max, 14)
        self.assertEqual(program.cost_notes, "$45 per semester.")
        self.assertEqual(program.meeting_schedule, "Thursdays 9am–noon.")
        self.assertTrue(program.step_up_direct_pay)
        self.assertTrue(program.step_up_pep)
        self.assertFalse(program.step_up_fes_ua)
        self.assertIn(self.category, program.categories.all())
        self.assertIsNotNone(program.last_verified_on)

        # Plain text became paragraphs.
        self.assertIn("<p>We meet weekly.</p>", program.description)
        self.assertIn("September through May", program.description)

    def test_an_approved_registration_is_immediately_visible_to_the_public(self):
        submission = self._registration()
        self._run("approve_and_publish", submission)
        submission.refresh_from_db()

        self.client.logout()
        response = self.client.get(submission.created_program.get_absolute_url())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Coastal Co-op")

    def test_description_html_is_escaped_not_injected(self):
        submission = self._registration(description="<script>alert(1)</script>\n\nSecond.")
        self._run("approve_and_publish", submission)
        submission.refresh_from_db()
        self.assertNotIn("<script>", submission.created_program.description)

    def test_approve_as_draft_does_not_publish(self):
        submission = self._registration()
        self._run("create_draft_program", submission)
        submission.refresh_from_db()
        self.assertEqual(submission.created_program.status, Program.Status.DRAFT)

    def test_a_thin_referral_is_held_back_from_publishing(self):
        """A referral has no one-line description, and a listing shows nothing
        without one. Approving it makes a draft and says why."""
        submission = Submission.objects.create(
            kind=Submission.Kind.REFERRAL,
            program_name="Surf Lessons",
            description="They might take homeschoolers.",
        )
        response = self._run("approve_and_publish", submission)
        submission.refresh_from_db()
        self.assertEqual(submission.created_program.status, Program.Status.DRAFT)
        self.assertContains(response, "no one-line description")

    def test_approving_twice_does_not_duplicate(self):
        submission = self._registration(program_name="Once Only")
        for _ in range(2):
            self._run("approve_and_publish", submission)
        self.assertEqual(Program.objects.filter(name="Once Only").count(), 1)

    def test_slugs_do_not_collide(self):
        first = self._registration()
        second = self._registration()
        self._run("approve_and_publish", first)
        self._run("approve_and_publish", second)
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertNotEqual(first.created_program.slug, second.created_program.slug)

    def test_the_logo_is_copied_not_shared(self):
        """The program's file has to outlive any tidying up of submissions,
        so approval copies the bytes rather than pointing at the same path."""
        submission = self._registration(logo=make_test_image())
        self._run("approve_and_publish", submission)
        submission.refresh_from_db()

        program = submission.created_program
        self.assertTrue(program.logo)
        self.assertNotEqual(program.logo.name, submission.logo.name)
        with program.logo.open("rb") as handle:
            self.assertTrue(handle.read().startswith(b"\x89PNG"))

    def test_rejecting_creates_nothing(self):
        submission = self._registration()
        self._run("mark_rejected", submission)
        submission.refresh_from_db()
        self.assertEqual(submission.status, Submission.Status.REJECTED)
        self.assertIsNone(submission.created_program)
        self.assertEqual(Program.objects.count(), 0)


class AdminTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_superuser("editor", "e@example.org", "pw-for-tests-only")
        cls.category = Category.objects.create(name="Co-ops", slug="co-ops")

    def setUp(self):
        self.client.force_login(self.user)

    def test_admin_screens_render(self):
        submission = Submission.objects.create(program_name="A Program")
        for url in [
            "/admin/",
            "/admin/directory/program/",
            "/admin/directory/program/add/",
            "/admin/directory/category/",
            "/admin/directory/page/",
            "/admin/directory/submission/",
            f"/admin/directory/submission/{submission.pk}/change/",
        ]:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)

    def test_groups_are_hidden(self):
        self.assertEqual(self.client.get("/admin/auth/group/").status_code, 404)

    def test_submissions_cannot_be_typed_in_by_hand(self):
        self.assertEqual(self.client.get("/admin/directory/submission/add/").status_code, 403)

    def test_mark_verified_today_action(self):
        program = Program.objects.create(name="P", slug="p", short_description="s")
        self.client.post(
            "/admin/directory/program/",
            {"action": "mark_verified_today", "_selected_action": [str(program.pk)]},
            follow=True,
        )
        program.refresh_from_db()
        self.assertIsNotNone(program.last_verified_on)


def tearDownModule():
    """Remove the temporary media trees the upload tests wrote into."""
    for cls in [RegistrationTests, ApprovalTests]:
        root = cls._overridden_settings.get("MEDIA_ROOT")
        if root:
            shutil.rmtree(root, ignore_errors=True)
