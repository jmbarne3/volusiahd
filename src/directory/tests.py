"""Enough tests to catch the things that would quietly break.

Not exhaustive coverage. These cover the three failure modes that would be
expensive to discover in production: a route that 500s, unsanitized HTML
reaching a template that renders with `|safe`, and a draft program being
visible on the public site.
"""

import json
import os
import shutil
import tempfile
from contextlib import contextmanager
from io import BytesIO, StringIO
from types import SimpleNamespace
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from django.conf import settings
from django.contrib.auth.models import User
from django.contrib.staticfiles import finders
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.db.models import ProtectedError
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from PIL import Image

from . import geocoding
from .addresses import DEBOUNCE_MS, MIN_QUERY_CHARS, LocationsField
from .models import (
    Category,
    ContactPerson,
    Page,
    Program,
    ProgramLocation,
    Submission,
    SubmissionLocation,
    Tag,
)
from .templatetags.directory_extras import CATEGORY_COLOUR_COUNT, colour_code


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
            is_featured=True,
            category=cls.category,
        )
        cls.program.locations.create(query="101 Woodland Blvd, DeLand 32720", city="DeLand")
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
        self.program.locations.create(query="7 Rich Ave, DeLand 32724", sort_order=1)
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


class TagTests(TestCase):
    """Tags are found, not browsed. Every route to one is worth a test."""

    @classmethod
    def setUpTestData(cls):
        cls.tag = Tag.objects.create(name="Lego", slug="lego", description="Brick-based clubs.")
        cls.other = Tag.objects.create(name="Dual enrollment", slug="dual-enrollment")
        cls.program = Program.objects.create(
            name="Brick Builders",
            slug="brick-builders",
            short_description="A Lego club in Deltona.",
            status=Program.Status.PUBLISHED,
        )
        cls.program.tags.add(cls.tag)
        cls.draft = Program.objects.create(
            name="Secret Bricks",
            slug="secret-bricks",
            short_description="Not ready.",
            status=Program.Status.DRAFT,
        )
        cls.draft.tags.add(cls.tag)

    def test_a_tag_page_lists_its_published_programs_only(self):
        response = self.client.get(reverse("directory:tag", kwargs={"slug": "lego"}))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Brick Builders")
        self.assertContains(response, "Brick-based clubs.")
        self.assertNotContains(response, "Secret Bricks")

    def test_an_unknown_tag_is_a_404(self):
        response = self.client.get(reverse("directory:tag", kwargs={"slug": "nope"}))
        self.assertEqual(response.status_code, 404)

    def test_a_tag_with_no_programs_renders_rather_than_erroring(self):
        response = self.client.get(reverse("directory:tag", kwargs={"slug": "dual-enrollment"}))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Nothing here yet.")

    def test_searching_a_tag_name_finds_the_program(self):
        """The search box is the main way anyone reaches a tag, because tags
        are never listed on a filter bar."""
        response = self.client.get(reverse("directory:program_list"), {"q": "Lego"})
        self.assertContains(response, "Brick Builders")

    def test_a_program_matching_on_several_tags_appears_once(self):
        self.program.tags.add(self.other)
        self.program.tags.create(name="Legoland trips", slug="legoland-trips")
        response = self.client.get(reverse("directory:program_list"), {"q": "lego"})
        self.assertContains(response, "Brick Builders", count=1)

    def test_tags_show_on_the_program_page_and_link_to_their_own_page(self):
        response = self.client.get(reverse("directory:program", kwargs={"slug": self.program.slug}))
        self.assertContains(response, "Lego")
        self.assertContains(response, 'href="/tags/lego/"')

    def test_tag_pages_are_in_the_sitemap_once_they_have_a_published_program(self):
        sitemap = self.client.get("/sitemap.xml").content.decode()
        self.assertIn("/tags/lego/", sitemap)
        self.assertNotIn("/tags/dual-enrollment/", sitemap)


class CategoryDeletionTests(TestCase):
    def test_a_category_in_use_cannot_be_deleted(self):
        """PROTECT, not SET_NULL.

        Deleting a heading that programs are filed under should stop and make
        someone re-file them. Silently unfiling a dozen listings is the failure
        mode this prevents, and it is one nobody would notice.
        """
        category = Category.objects.create(name="Co-ops", slug="co-ops")
        Program.objects.create(
            name="Sample", slug="sample", short_description="A co-op.", category=category
        )
        with self.assertRaises(ProtectedError):
            category.delete()

    def test_a_category_on_an_old_submission_can_be_deleted(self):
        """SET_NULL there, because a processed record should never block tidying
        up the taxonomy."""
        category = Category.objects.create(name="Retired", slug="retired")
        submission = Submission.objects.create(program_name="Old thing", category=category)
        category.delete()
        submission.refresh_from_db()
        self.assertIsNone(submission.category)


class VendoredAssetTests(SimpleTestCase):
    """The registration page borrows Select2 from django.contrib.admin.

    Nothing in Django promises those paths to code outside the admin, so a
    Django upgrade that moved or dropped them would break the tag field with no
    other warning — the page would render, the script would 404, and the field
    would quietly fall back to a native multiselect.
    """

    def test_select2_and_jquery_resolve(self):
        for path in [
            "admin/css/vendor/select2/select2.min.css",
            "admin/js/vendor/select2/select2.full.min.js",
            "admin/js/vendor/jquery/jquery.min.js",
            "css/tag-select.css",
            # Ours rather than borrowed, but the admin loads these through
            # `Media` and a typo there fails the same silent way.
            "css/address-picker.css",
            "js/address-picker.js",
        ]:
            with self.subTest(path=path):
                self.assertIsNotNone(finders.find(path), f"{path} is no longer on disk")


class CategoryColourTests(SimpleTestCase):
    """The colour code lives in Python; the colours live in the stylesheet.

    Nothing connects the two but a class name, so a category added past the
    end of the ring would render a dot with no hue at all and nobody would
    notice until it shipped.
    """

    def test_every_code_has_a_colour_in_the_stylesheet(self):
        css = (settings.BASE_DIR / "src" / "static" / "css" / "site.css").read_text()
        for code in range(1, CATEGORY_COLOUR_COUNT + 1):
            with self.subTest(code=code):
                self.assertRegex(css, rf"\.c{code}\b")

    def test_codes_are_stable_and_wrap_at_the_end_of_the_ring(self):
        class FakeCategory:
            def __init__(self, pk):
                self.pk = pk

        self.assertEqual(colour_code(FakeCategory(1)), 1)
        self.assertEqual(colour_code(FakeCategory(CATEGORY_COLOUR_COUNT)), CATEGORY_COLOUR_COUNT)
        self.assertEqual(colour_code(FakeCategory(CATEGORY_COLOUR_COUNT + 1)), 1)
        # An unsaved category has no pk. It lands on the last code rather than
        # the first, which is arbitrary but in range — what matters is that it
        # resolves to a real colour instead of blowing up mid-render.
        self.assertIn(colour_code(FakeCategory(None)), range(1, CATEGORY_COLOUR_COUNT + 1))


class FormControlStyleTests(SimpleTestCase):
    """Every control on a form has to be dressed by the same rule.

    The failure this guards against does not look like a failure. A control no
    selector happens to mention renders in the browser's own style, sitting next
    to fields in ours, and every test still passes — which is exactly how the
    category dropdown and the address box came to look like visitors from
    another site. The fix was to write the rule as "every input, select and
    textarea except these", so the next control added is covered before anybody
    thinks about it.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.css = (settings.BASE_DIR / "src" / "static" / "css" / "site.css").read_text()

    def test_the_control_recipe_is_written_by_exception_not_by_list(self):
        self.assertIn(":is(input, select, textarea)", self.css)
        # The things that are genuinely not boxes stay out of it.
        for excluded in ["[type=checkbox]", "[type=radio]", ".select2-search__field"]:
            with self.subTest(excluded=excluded):
                self.assertIn(excluded, self.css)

    def test_a_dropdown_draws_its_own_arrow_in_a_theme_colour(self):
        """The native arrow is the one part of a select a stylesheet cannot reach."""
        self.assertIn("select:not([multiple])", self.css)
        # To the end of the rule, not a fixed number of characters: a comment
        # added inside it should not be able to fail this.
        start = self.css.index("select:not([multiple])")
        rule = self.css[start : self.css.index("}", start)]
        self.assertIn("var(--muted)", rule)
        self.assertIn("background-repeat: no-repeat", rule)
        # The native arrow has to go, or ours is the second one on the control.
        self.assertIn("appearance: none", rule)

    def test_the_pickers_tokens_are_in_scope_wherever_it_is_used(self):
        """The bug this locks down was invisible in one place and fatal in another.

        The admin builds the suggestion list in a table cell, outside the
        `.address-picker` element the tokens used to be declared on. An undefined
        custom property does not fall back — the declaration becomes invalid and
        the property reverts to its initial value — so `background` became
        transparent and the list sat unreadably over the row beneath it, while
        the public form, where the list is inside the element, looked perfect.
        """
        css = (settings.BASE_DIR / "src" / "static" / "css" / "address-picker.css").read_text()
        root = css[css.index(":root {") : css.index("}", css.index(":root {"))]
        for token in ["--ap-ink", "--ap-muted", "--ap-pane", "--ap-line", "--ap-radius"]:
            with self.subTest(token=token):
                self.assertIn(token, root)
        # And the panel that covers a row while it is open has to be opaque.
        panel = css[css.index(".address-picker__results {") :]
        self.assertIn("background: var(--ap-pane)", panel[: panel.index("}")])

        # A class name agreed between a stylesheet and a script, which is the
        # kind of pair that rots silently. Both sides are asserted here.
        script = (settings.BASE_DIR / "src" / "static" / "js" / "address-picker.js").read_text()
        self.assertIn("address-picker-open", css)
        self.assertIn("address-picker-open", script)

    def test_going_opaque_does_not_erase_that_arrow(self):
        """`background: solid` would take the arrow with it; `background-color` does not."""
        for block in ["prefers-reduced-transparency", "@supports not"]:
            with self.subTest(block=block):
                start = self.css.index(block)
                self.assertIn("background-color: var(--pane-solid)", self.css[start : start + 700])


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
            "category": str(self.category.pk),
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
            [place.query for place in submission.locations.all()],
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

    def _tag(self, name, slug, *categories):
        """A tag, offered under the headings given. Under none if given none."""
        tag = Tag.objects.create(name=name, slug=slug)
        for category in categories:
            category.tags.add(tag)
        return tag

    def test_a_registrant_can_only_pick_tags_that_already_exist(self):
        """The whole point of a curated vocabulary.

        A submitted value that is not a tag primary key has to be refused, or
        the form becomes free entry by another route.
        """
        tag = self._tag("Lego", "lego", self.category)
        response = self.client.post(
            reverse("directory:register"), self._payload(tags=[str(tag.pk), "9999"])
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Submission.objects.count(), 0)

    def test_chosen_tags_are_stored_on_the_submission(self):
        tag = self._tag("Lego", "lego", self.category)
        self._tag("Unpicked", "unpicked", self.category)
        self.client.post(reverse("directory:register"), self._payload(tags=[str(tag.pk)]))
        self.assertEqual(list(Submission.objects.get().tags.all()), [tag])

    def test_the_tag_field_is_a_picker_and_not_a_text_box(self):
        self._tag("Dual enrollment", "dual-enrollment", self.category)
        response = self.client.get(reverse("directory:register"))
        self.assertContains(response, "data-tag-select")
        self.assertContains(response, "Dual enrollment")
        # A <select> the browser validates against, not an input they can type into.
        self.assertContains(response, 'name="tags"')
        self.assertNotContains(response, '<input type="text" name="tags"')

    def test_the_form_only_offers_tags_that_belong_to_a_heading(self):
        """A tag under no heading is one we apply ourselves.

        Offering it here would be offering a choice that can only ever fail
        validation, so it is left out of the field entirely.
        """
        self._tag("Dual enrollment", "dual-enrollment", self.category)
        self._tag("Internal only", "internal-only")
        response = self.client.get(reverse("directory:register"))
        self.assertContains(response, "Dual enrollment")
        self.assertNotContains(response, "Internal only")

    def test_each_tag_option_says_which_headings_it_belongs_to(self):
        """What the page's JavaScript narrows the list with.

        If this attribute stops being rendered the field silently stops
        narrowing, and the only sign would be a rejected submission.
        """
        clubs = Category.objects.create(name="Clubs", slug="clubs")
        self._tag("Lego", "lego", self.category, clubs)
        response = self.client.get(reverse("directory:register"))
        headings = " ".join(str(pk) for pk in sorted([self.category.pk, clubs.pk]))
        self.assertContains(response, f'data-categories="{headings}"')

    def test_a_tag_that_does_not_go_with_the_chosen_heading_is_refused(self):
        """The check the JavaScript makes unreachable and a direct post does not."""
        clubs = Category.objects.create(name="Clubs", slug="clubs")
        tag = self._tag("Lego", "lego", clubs)
        response = self.client.post(
            reverse("directory:register"),
            self._payload(category=str(self.category.pk), tags=[str(tag.pk)]),
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Submission.objects.count(), 0)
        self.assertContains(response, "We do not use Lego under Co-ops")

    def test_a_tag_can_belong_to_more_than_one_heading(self):
        clubs = Category.objects.create(name="Clubs", slug="clubs")
        tag = self._tag("Lego", "lego", self.category, clubs)
        for category in [self.category, clubs]:
            with self.subTest(category=category.name):
                self.client.post(
                    reverse("directory:register"),
                    self._payload(category=str(category.pk), tags=[str(tag.pk)]),
                )
        self.assertEqual(Submission.objects.count(), 2)
        for submission in Submission.objects.all():
            self.assertEqual(list(submission.tags.all()), [tag])

    def test_tags_without_a_heading_to_scope_them_are_refused(self):
        """Category is optional; picking tags without one is not.

        Tags are scoped by heading, so tags with no heading chosen cannot be
        checked against anything — the error goes on the category field, which
        is where the fix is.
        """
        tag = self._tag("Lego", "lego", self.category)
        response = self.client.post(
            reverse("directory:register"), self._payload(category="", tags=[str(tag.pk)])
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Submission.objects.count(), 0)
        self.assertContains(response, "Please choose a heading")

    def test_a_registration_with_no_category_and_no_tags_is_still_accepted(self):
        response = self.client.post(reverse("directory:register"), self._payload(category=""))
        self.assertRedirects(response, reverse("directory:register_thanks"))
        self.assertIsNone(Submission.objects.get().category)

    def test_only_one_category_survives_a_form_that_posts_two(self):
        """The field is a dropdown, but the wire is not.

        Nothing stops a crafted POST carrying two `category` values, and the
        point of this change is that a program ends up under exactly one
        heading however the request arrived.
        """
        other = Category.objects.create(name="Sports", slug="sports", sort_order=20)
        self.client.post(
            reverse("directory:register"),
            self._payload(category=[str(self.category.pk), str(other.pk)]),
        )
        submission = Submission.objects.get()
        self.assertEqual(submission.category, other)

    def test_the_category_field_is_a_dropdown(self):
        response = self.client.get(reverse("directory:register"))
        self.assertContains(response, '<select name="category"')
        self.assertNotContains(response, 'name="categories"')

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
        fields.setdefault("category", self.category)
        places = fields.pop("locations", "")
        submission = Submission.objects.create(**fields)
        submission.tags.add(Tag.objects.get_or_create(name="Lego", slug="lego")[0])
        for order, line in enumerate(line for line in places.splitlines() if line.strip()):
            # The first one resolved, the second one did not: approval has to
            # carry both kinds across without looking anything up.
            resolved = order == 0
            submission.locations.create(
                query=line.strip(),
                label=line.strip() if resolved else "",
                latitude=29.2858 if resolved else None,
                longitude=-81.0559 if resolved else None,
                city="Ormond Beach" if resolved else "",
                status=(
                    SubmissionLocation.Status.RESOLVED
                    if resolved
                    else SubmissionLocation.Status.PENDING
                ),
                sort_order=order,
            )
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
        self.assertEqual(program.category, self.category)
        # The tags the registrant picked, not a fresh empty set.
        self.assertEqual(
            list(program.tags.values_list("name", flat=True)),
            list(submission.tags.values_list("name", flat=True)),
        )
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

    def test_a_point_the_submitter_chose_survives_approval_untouched(self):
        """Approval copies, it does not re-decide.

        The submitter picked which match was right. Looking the address up again
        here would overwrite their answer with a guess, and would also mean a
        network call per address every time somebody clears the queue.
        """
        submission = self._registration()
        with patch("directory.models.geocoding.resolve") as resolve:
            self._run("approve_and_publish", submission)
        resolve.assert_not_called()

        submission.refresh_from_db()
        picked, typed = submission.created_program.locations.all()
        self.assertAlmostEqual(picked.latitude, 29.2858)
        self.assertEqual(picked.city, "Ormond Beach")
        self.assertEqual(picked.status, ProgramLocation.Status.RESOLVED)
        # And the one they typed themselves arrives waiting to be looked up.
        self.assertFalse(typed.has_point)
        self.assertEqual(typed.status, ProgramLocation.Status.PENDING)

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
            f"/admin/directory/category/{self.category.pk}/change/",
            "/admin/directory/tag/",
            "/admin/directory/tag/add/",
            "/admin/directory/page/",
            "/admin/directory/submission/",
            f"/admin/directory/submission/{submission.pk}/change/",
        ]:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)

    def test_a_headings_tags_are_chosen_on_the_category_screen(self):
        """Where the editing happens, and where it deliberately does not.

        The pairing is one list per heading, curated in one sitting, so the
        category screen owns it and the tag screen only reports it.
        """
        tag = Tag.objects.create(name="Lego", slug="lego")
        self.category.tags.add(tag)

        category_screen = self.client.get(f"/admin/directory/category/{self.category.pk}/change/")
        self.assertContains(category_screen, 'name="tags"')
        self.assertContains(category_screen, "Lego")

        tag_screen = self.client.get(f"/admin/directory/tag/{tag.pk}/change/")
        self.assertEqual(tag_screen.status_code, 200)
        self.assertNotContains(tag_screen, 'name="categories"')

    def test_the_tag_list_says_which_headings_offer_each_tag(self):
        tag = Tag.objects.create(name="Lego", slug="lego")
        self.category.tags.add(tag)
        response = self.client.get("/admin/directory/tag/")
        self.assertContains(response, "Offered under")
        self.assertContains(response, "Co-ops")

    def _program_payload(self, program, **overrides):
        """Everything the program change form insists on, plus empty formsets."""
        payload = {
            "name": program.name,
            "slug": program.slug,
            "short_description": program.short_description,
            "status": program.status,
            "locations-TOTAL_FORMS": "0",
            "locations-INITIAL_FORMS": "0",
            "locations-MIN_NUM_FORMS": "0",
            "locations-MAX_NUM_FORMS": "1000",
            "contacts-TOTAL_FORMS": "0",
            "contacts-INITIAL_FORMS": "0",
            "contacts-MIN_NUM_FORMS": "0",
            "contacts-MAX_NUM_FORMS": "1000",
        }
        payload.update(overrides)
        return payload

    def test_the_program_screen_has_a_row_for_typing_a_meeting_place(self):
        program = Program.objects.create(name="P", slug="p", short_description="s")
        response = self.client.get(f"/admin/directory/program/{program.pk}/change/")
        self.assertContains(response, "Where this program meets")
        self.assertContains(response, "locations-0-query")

    def test_saving_a_new_address_looks_it_up_there_and_then(self):
        """The editor's loop: type an address, save, see where it landed.

        This is the only place in the project that calls a geocoder during a
        request, and it is an admin request with somebody sitting in front of it.
        """
        program = Program.objects.create(name="P", slug="p", short_description="s")
        found = geocoding.Place(
            label="101 W Woodland Blvd, DeLand, 32720",
            latitude=29.0289,
            longitude=-81.3031,
            city="DeLand",
        )
        payload = self._program_payload(
            program,
            **{
                "locations-TOTAL_FORMS": "1",
                "locations-0-query": "101 Woodland Blvd DeLand",
                "locations-0-sort_order": "0",
            },
        )
        with patch("directory.models.geocoding.resolve", return_value=found) as resolve:
            response = self.client.post(
                f"/admin/directory/program/{program.pk}/change/", payload, follow=True
            )
        self.assertEqual(response.status_code, 200)
        resolve.assert_called_once_with("101 Woodland Blvd DeLand")

        location = program.locations.get()
        self.assertEqual(location.status, ProgramLocation.Status.RESOLVED)
        self.assertEqual(location.label, "101 W Woodland Blvd, DeLand, 32720")
        self.assertContains(response, "Put 1 address on the map")

    def test_an_address_that_cannot_be_matched_says_so_and_is_not_lost(self):
        program = Program.objects.create(name="P", slug="p", short_description="s")
        payload = self._program_payload(
            program,
            **{
                "locations-TOTAL_FORMS": "1",
                "locations-0-query": "members' homes, term by term",
                "locations-0-sort_order": "0",
            },
        )
        with patch("directory.models.geocoding.resolve", return_value=None):
            response = self.client.post(
                f"/admin/directory/program/{program.pk}/change/", payload, follow=True
            )
        self.assertContains(response, "Could not match 1 address")
        location = program.locations.get()
        self.assertEqual(location.status, ProgramLocation.Status.FAILED)
        self.assertEqual(location.display_name, "members' homes, term by term")

    def test_a_geocoder_outage_during_a_save_loses_neither_the_address_nor_the_editor(self):
        program = Program.objects.create(name="P", slug="p", short_description="s")
        payload = self._program_payload(
            program,
            **{
                "locations-TOTAL_FORMS": "1",
                "locations-0-query": "12 Ocean Ave",
                "locations-0-sort_order": "0",
            },
        )
        with patch(
            "directory.models.geocoding.resolve",
            side_effect=geocoding.GeocoderUnavailable("down"),
        ):
            response = self.client.post(
                f"/admin/directory/program/{program.pk}/change/", payload, follow=True
            )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "could not be reached")
        self.assertEqual(program.locations.get().status, ProgramLocation.Status.PENDING)

    def test_the_look_up_addresses_action_retries_what_failed_before(self):
        program = Program.objects.create(name="P", slug="p", short_description="s")
        program.locations.create(query="12 Ocean Ave", status=ProgramLocation.Status.FAILED)
        found = geocoding.Place(label="12 Ocean Avenue", latitude=29.28, longitude=-81.05)
        with patch("directory.models.geocoding.resolve", return_value=found):
            self.client.post(
                "/admin/directory/program/",
                {"action": "look_up_addresses", "_selected_action": [str(program.pk)]},
                follow=True,
            )
        self.assertEqual(program.locations.get().status, ProgramLocation.Status.RESOLVED)

    def test_picking_a_suggestion_in_the_admin_fills_in_the_whole_row(self):
        """The admin picker, and the reason the hidden field exists.

        The inline has no column for city or postcode. Without the payload they
        would be silently lost every time an editor used the picker — which is
        the worst kind of loss, because everything on screen would look right.
        """
        program = Program.objects.create(name="P", slug="p", short_description="s")
        picked = json.dumps(
            {
                "query": "12 ocean ave",
                "label": "12 Ocean Avenue, Ormond Beach, 32176",
                "latitude": 29.2858,
                "longitude": -81.0559,
                "city": "Ormond Beach",
                "postcode": "32176",
                "osm_type": "W",
                "osm_id": 123456,
            }
        )
        payload = self._program_payload(
            program,
            **{
                "locations-TOTAL_FORMS": "1",
                "locations-0-query": "12 ocean ave",
                "locations-0-label": "12 Ocean Avenue, Ormond Beach, 32176",
                "locations-0-latitude": "29.2858",
                "locations-0-longitude": "-81.0559",
                "locations-0-sort_order": "0",
                "locations-0-picked": picked,
            },
        )
        with patch("directory.models.geocoding.resolve") as resolve:
            response = self.client.post(
                f"/admin/directory/program/{program.pk}/change/", payload, follow=True
            )
        self.assertEqual(response.status_code, 200)
        # Already resolved, so nothing was asked of the geocoder.
        resolve.assert_not_called()

        place = program.locations.get()
        self.assertEqual(place.city, "Ormond Beach")
        self.assertEqual(place.postcode, "32176")
        self.assertEqual(place.osm_id, 123456)
        self.assertEqual(place.status, ProgramLocation.Status.RESOLVED)
        # The editor's own wording is kept, not overwritten by the label.
        self.assertEqual(place.query, "12 ocean ave")

    def test_an_address_box_in_the_admin_carries_what_the_script_needs(self):
        program = Program.objects.create(name="P", slug="p", short_description="s")
        response = self.client.get(f"/admin/directory/program/{program.pk}/change/")
        self.assertContains(response, "data-address-row")
        self.assertContains(response, f'data-debounce="{DEBOUNCE_MS}"')
        self.assertContains(response, "address-picker.js")

    def test_the_program_list_shows_how_many_addresses_are_on_the_map(self):
        program = Program.objects.create(name="P", slug="p", short_description="s")
        program.locations.create(query="12 Ocean Ave", latitude=29.28, longitude=-81.05)
        program.locations.create(query="members' homes", sort_order=1)
        response = self.client.get("/admin/directory/program/")
        self.assertContains(response, "1 of 2")

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


def answering(payload):
    """Stand in for `urlopen`, handing back `payload` as a JSON body."""

    @contextmanager
    def opener(request, timeout=None):
        yield SimpleNamespace(read=lambda: json.dumps(payload).encode())

    return opener


A_CHURCH = {
    "features": [
        {
            # GeoJSON order: longitude first. The test would pass with these
            # swapped only if the code swapped them too, which is the point.
            "geometry": {"type": "Point", "coordinates": [-81.0559, 29.2858]},
            "properties": {
                "name": "Ormond Beach Presbyterian Church",
                "housenumber": "12",
                "street": "Ocean Avenue",
                "city": "Ormond Beach",
                "state": "Florida",
                "postcode": "32176",
                "osm_type": "W",
                "osm_id": 123456,
            },
        }
    ]
}


@override_settings(GEOCODER_URL="https://photon.example/api")
class GeocodingClientTests(SimpleTestCase):
    """The client with the network replaced by a canned answer.

    Every one of these is a shape the real service returns. The label assembly
    in particular has no single template to test against — Photon fills in the
    keys it happens to know — so the cases below are the ones that differ.
    """

    def test_a_match_becomes_a_place_with_the_coordinates_the_right_way_round(self):
        with patch("directory.geocoding.urlopen", answering(A_CHURCH)):
            place = geocoding.resolve("12 Ocean Ave Ormond Beach")
        self.assertAlmostEqual(place.latitude, 29.2858)
        self.assertAlmostEqual(place.longitude, -81.0559)
        self.assertEqual(place.city, "Ormond Beach")
        self.assertEqual(place.postcode, "32176")
        self.assertEqual(place.osm_id, 123456)
        self.assertEqual(
            place.label,
            "Ormond Beach Presbyterian Church, 12 Ocean Avenue, Ormond Beach, 32176",
        )

    def test_florida_is_left_out_of_a_label_and_another_state_is_not(self):
        """The state is noise until it is the most important word in the label."""
        payload = {
            "features": [
                {
                    "geometry": {"coordinates": [-81.3, 29.0]},
                    "properties": {"name": "DeLand", "state": "Florida"},
                }
            ]
        }
        with patch("directory.geocoding.urlopen", answering(payload)):
            self.assertEqual(geocoding.resolve("DeLand").label, "DeLand")

        payload["features"][0]["properties"] = {"name": "Kingsland", "state": "Georgia"}
        with patch("directory.geocoding.urlopen", answering(payload)):
            self.assertEqual(geocoding.resolve("Kingsland").label, "Kingsland, Georgia")

    def test_a_place_whose_name_is_its_city_is_not_said_twice(self):
        payload = {
            "features": [
                {
                    "geometry": {"coordinates": [-81.3, 29.0]},
                    "properties": {"name": "DeLand", "city": "DeLand", "postcode": "32720"},
                }
            ]
        }
        with patch("directory.geocoding.urlopen", answering(payload)):
            self.assertEqual(geocoding.resolve("DeLand").label, "DeLand, 32720")

    def test_nothing_found_is_an_answer_and_not_an_error(self):
        with patch("directory.geocoding.urlopen", answering({"features": []})):
            self.assertIsNone(geocoding.resolve("Narnia, Volusia County"))

    def test_a_feature_without_coordinates_is_skipped(self):
        payload = {"features": [{"properties": {"name": "Nowhere"}}]}
        with patch("directory.geocoding.urlopen", answering(payload)):
            self.assertEqual(geocoding.search("nowhere"), [])

    def test_a_service_that_is_down_is_not_a_miss(self):
        """The distinction the whole retry story rests on.

        An unreachable geocoder has to raise, because recording "no match" would
        mark every address in the directory as unmatchable during an outage and
        nothing would ever look at them again.
        """
        for failure in [URLError("down"), TimeoutError(), HTTPError(None, 503, "x", None, None)]:
            with self.subTest(failure=type(failure).__name__):
                with patch("directory.geocoding.urlopen", side_effect=failure):
                    with self.assertRaises(geocoding.GeocoderUnavailable):
                        geocoding.resolve("12 Ocean Ave")

    def test_a_response_that_is_not_what_we_expect_is_not_a_miss(self):
        with patch("directory.geocoding.urlopen", answering({"nope": True})):
            with self.assertRaises(geocoding.GeocoderUnavailable):
                geocoding.resolve("12 Ocean Ave")

    def test_a_blank_query_is_never_a_request(self):
        with patch("directory.geocoding.urlopen") as urlopen:
            self.assertEqual(geocoding.search("   "), [])
        urlopen.assert_not_called()

    @override_settings(GEOCODER_URL="")
    def test_with_no_geocoder_configured_nothing_calls_out(self):
        """What keeps this test suite off somebody else's server.

        Settings blank the geocoder whenever `manage.py test` is running, so a
        test that resolves an address has to say so explicitly. If that ever
        stops being true, this fails first.
        """
        with patch("directory.geocoding.urlopen") as urlopen:
            with self.assertRaises(geocoding.GeocoderUnavailable):
                geocoding.resolve("12 Ocean Ave")
        urlopen.assert_not_called()


class MeetingPlaceTests(TestCase):
    """One row: what it shows, and what saving it decides."""

    @classmethod
    def setUpTestData(cls):
        cls.program = Program.objects.create(
            name="Coastal Co-op", slug="coastal", short_description="s"
        )

    def _place(self, **overrides):
        return geocoding.Place(
            **{
                "label": "12 Ocean Avenue, Ormond Beach, 32176",
                "latitude": 29.2858,
                "longitude": -81.0559,
                "city": "Ormond Beach",
                "postcode": "32176",
                "osm_type": "W",
                "osm_id": 123456,
                **overrides,
            }
        )

    def test_an_address_nothing_has_resolved_still_has_something_to_show(self):
        location = self.program.locations.create(query="the church on Ocean Ave")
        self.assertEqual(location.display_name, "the church on Ocean Ave")
        self.assertEqual(self.program.location_list, ["the church on Ocean Ave"])

    def test_a_resolved_address_shows_the_resolved_wording(self):
        location = self.program.locations.create(query="12 ocean ave")
        location.apply(self._place())
        location.save()
        self.assertEqual(location.status, ProgramLocation.Status.RESOLVED)
        self.assertEqual(location.display_name, "12 Ocean Avenue, Ormond Beach, 32176")
        self.assertTrue(location.has_point)
        self.assertIsNotNone(location.resolved_at)

    def test_a_miss_is_recorded_rather_than_left_looking_untried(self):
        """ "Nobody looked" and "we looked and found nothing" need different handling."""
        location = self.program.locations.create(query="members' homes")
        location.apply(None)
        location.save()
        self.assertEqual(location.status, ProgramLocation.Status.FAILED)
        self.assertFalse(location.has_point)
        self.assertEqual(ProgramLocation.objects.pending().count(), 0)

    def test_coordinates_typed_in_by_hand_count_as_resolved(self):
        """Otherwise the next lookup would overwrite the editor's correction."""
        location = self.program.locations.create(query="Sanborn Center")
        location.latitude, location.longitude = 29.0289, -81.3031
        location.save()
        location.refresh_from_db()
        self.assertEqual(location.status, ProgramLocation.Status.RESOLVED)

    def test_correcting_the_address_throws_away_where_the_old_one_was(self):
        """A corrected address is a different address.

        Keeping the old coordinates against new text is how a program ends up
        published at a place it does not meet.
        """
        location = self.program.locations.create(query="12 ocean ave")
        location.apply(self._place())
        location.save()

        location = ProgramLocation.objects.get(pk=location.pk)
        location.query = "4 Granada Blvd, Ormond Beach"
        location.save()

        location.refresh_from_db()
        self.assertEqual(location.status, ProgramLocation.Status.PENDING)
        self.assertIsNone(location.latitude)
        self.assertEqual(location.label, "")
        self.assertEqual(location.display_name, "4 Granada Blvd, Ormond Beach")

    def test_correcting_the_address_and_the_coordinates_together_keeps_the_coordinates(self):
        """An editor with a map has already answered the question."""
        location = self.program.locations.create(query="12 ocean ave")
        location.apply(self._place())
        location.save()

        location = ProgramLocation.objects.get(pk=location.pk)
        location.query = "4 Granada Blvd, Ormond Beach"
        location.latitude, location.longitude = 29.2861, -81.0430
        location.save()

        location.refresh_from_db()
        self.assertEqual(location.status, ProgramLocation.Status.RESOLVED)
        self.assertAlmostEqual(location.latitude, 29.2861)

    def test_only_addresses_with_a_point_are_mappable(self):
        """The subset a distance search can answer for — smaller than the directory."""
        placed = self.program.locations.create(query="12 Ocean Ave", latitude=29.2, longitude=-81.0)
        self.program.locations.create(query="members' homes", status=ProgramLocation.Status.FAILED)
        self.assertEqual(list(ProgramLocation.objects.mappable()), [placed])


class LookupTests(TestCase):
    """Working through a queue of addresses, including when the service fails."""

    @classmethod
    def setUpTestData(cls):
        cls.program = Program.objects.create(name="C", slug="c", short_description="s")

    def _rows(self, *queries):
        for order, query in enumerate(queries):
            self.program.locations.create(query=query, sort_order=order)
        return ProgramLocation.objects.pending()

    def test_a_run_reports_what_it_found_and_what_it_did_not(self):
        rows = self._rows("12 Ocean Ave", "members' homes")
        found = geocoding.Place(label="12 Ocean Avenue", latitude=29.28, longitude=-81.05)
        with patch("directory.models.geocoding.resolve", side_effect=[found, None]):
            self.assertEqual(rows.look_up(), (1, 1, False))
        self.assertEqual(
            sorted(ProgramLocation.objects.values_list("status", flat=True)),
            ["failed", "resolved"],
        )

    def test_a_service_that_stops_answering_ends_the_run_and_leaves_the_rest_pending(self):
        """Pending is what the next attempt looks for, so stopping loses nothing."""
        rows = self._rows("first", "second", "third")
        found = geocoding.Place(label="First Street", latitude=29.28, longitude=-81.05)
        with patch(
            "directory.models.geocoding.resolve",
            side_effect=[found, geocoding.GeocoderUnavailable("down")],
        ):
            self.assertEqual(rows.look_up(), (1, 0, True))
        self.assertEqual(ProgramLocation.objects.pending().count(), 2)

    def test_the_command_works_through_what_is_waiting(self):
        self._rows("12 Ocean Ave")
        found = geocoding.Place(label="12 Ocean Avenue", latitude=29.28, longitude=-81.05)
        output = StringIO()
        with patch("directory.models.geocoding.resolve", return_value=found):
            call_command("geocode", stdout=output)
        self.assertIn("Resolved 1", output.getvalue())
        self.assertEqual(ProgramLocation.objects.pending().count(), 0)

    def test_the_command_says_so_when_there_is_nothing_to_do(self):
        output = StringIO()
        with patch("directory.models.geocoding.resolve") as resolve:
            call_command("geocode", stdout=output)
        resolve.assert_not_called()
        self.assertIn("Nothing to look up", output.getvalue())

    def test_a_search_finds_a_program_by_what_its_address_resolved_to(self):
        """Somebody typing a landmark; somebody else searching the town it is in."""
        program = Program.objects.create(
            name="Brick Builders",
            slug="brick-builders",
            short_description="A Lego club.",
            status=Program.Status.PUBLISHED,
        )
        program.locations.create(
            query="the church on Granada",
            label="4 Granada Blvd, Ormond Beach, 32176",
            city="Ormond Beach",
            latitude=29.2861,
            longitude=-81.0430,
            status=ProgramLocation.Status.RESOLVED,
        )
        for query in ["Granada", "Ormond", "32176"]:
            with self.subTest(query=query):
                response = self.client.get(reverse("directory:program_list"), {"q": query})
                self.assertContains(response, "Brick Builders")

    def test_a_program_matching_on_two_addresses_appears_once(self):
        program = Program.objects.create(
            name="Two Places",
            slug="two-places",
            short_description="Meets twice.",
            status=Program.Status.PUBLISHED,
        )
        program.locations.create(query="4 Granada Blvd, Ormond Beach")
        program.locations.create(query="12 Granada Blvd, Ormond Beach", sort_order=1)
        response = self.client.get(reverse("directory:program_list"), {"q": "Granada"})
        self.assertContains(response, "Two Places", count=1)


class AddressSearchEndpointTests(TestCase):
    """The one thing the picker talks to."""

    def setUp(self):
        # LocMemCache outlives a test, and this view caches on purpose.
        cache.clear()

    def _get(self, query):
        return self.client.get(reverse("directory:address_search"), {"q": query})

    def test_a_query_too_short_to_mean_anything_never_reaches_the_geocoder(self):
        with patch("directory.views.geocoding.search") as search:
            response = self._get("de")
        search.assert_not_called()
        self.assertEqual(response.json(), {"results": []})

    def test_it_returns_what_the_picker_needs_to_store_a_place(self):
        place = geocoding.Place(
            label="12 Ocean Avenue, Ormond Beach, 32176",
            latitude=29.2858,
            longitude=-81.0559,
            city="Ormond Beach",
            postcode="32176",
            osm_type="W",
            osm_id=123456,
        )
        with patch("directory.views.geocoding.search", return_value=[place]):
            body = self._get("12 Ocean Ave").json()
        self.assertEqual(
            body["results"],
            [
                {
                    "label": "12 Ocean Avenue, Ormond Beach, 32176",
                    "latitude": 29.2858,
                    "longitude": -81.0559,
                    "city": "Ormond Beach",
                    "postcode": "32176",
                    "osm_type": "W",
                    "osm_id": 123456,
                }
            ],
        )

    def test_the_same_question_is_only_asked_once(self):
        """What makes a public typeahead affordable for everyone involved.

        The second person to type "Ormond Beach" should cost the geocoder
        nothing, and neither should the same person typing it twice.
        """
        place = geocoding.Place(label="Ormond Beach", latitude=29.28, longitude=-81.05)
        with patch("directory.views.geocoding.search", return_value=[place]) as search:
            self._get("Ormond Beach")
            self._get("ormond beach")  # same question, different shift key
            second = self._get("Ormond Beach")
        search.assert_called_once()
        self.assertTrue(second.json()["cached"])

    def test_a_geocoder_that_is_down_is_an_empty_answer_and_not_an_error(self):
        """The picker has a fallback, but only if it gets a reply at all."""
        with patch(
            "directory.views.geocoding.search",
            side_effect=geocoding.GeocoderUnavailable("down"),
        ):
            response = self._get("12 Ocean Ave")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"results": [], "unavailable": True})

    def test_it_answers_nothing_but_get(self):
        self.assertEqual(
            self.client.post(reverse("directory:address_search"), {"q": "x"}).status_code, 405
        )


class AddressFieldTests(SimpleTestCase):
    """Turning what a browser submits into rows, without trusting it."""

    def _clean(self, *values, **kwargs):
        return LocationsField(required=False, **kwargs).clean(list(values))

    def _picked(self, **overrides):
        return json.dumps(
            {
                "query": "12 ocean ave",
                "label": "12 Ocean Avenue, Ormond Beach, 32176",
                "latitude": 29.2858,
                "longitude": -81.0559,
                "city": "Ormond Beach",
                "postcode": "32176",
                "osm_type": "W",
                "osm_id": 123456,
                **overrides,
            }
        )

    def test_a_picked_place_arrives_whole(self):
        (place,) = self._clean(self._picked())
        self.assertEqual(place["query"], "12 ocean ave")
        self.assertEqual(place["label"], "12 Ocean Avenue, Ormond Beach, 32176")
        self.assertAlmostEqual(place["latitude"], 29.2858)
        self.assertEqual(place["city"], "Ormond Beach")
        self.assertEqual(place["status"], "resolved")
        self.assertIsNotNone(place["resolved_at"])

    def test_something_typed_is_a_place_waiting_to_be_looked_up(self):
        """The + button, and the reason it exists: "members' homes" is an answer."""
        (place,) = self._clean("at members' homes, term by term")
        self.assertEqual(place["query"], "at members' homes, term by term")
        self.assertIsNone(place["latitude"])
        self.assertEqual(place["status"], "pending")
        self.assertIsNone(place["resolved_at"])

    def test_one_address_per_line_still_works_without_javascript(self):
        places = self._clean("12 Ocean Ave\n\n4 Granada Blvd\n")
        self.assertEqual([place["query"] for place in places], ["12 Ocean Ave", "4 Granada Blvd"])

    def test_half_a_point_is_no_point(self):
        """One coordinate without the other would put a program on the meridian."""
        (place,) = self._clean(self._picked(longitude=None))
        self.assertIsNone(place["latitude"])
        self.assertIsNone(place["longitude"])
        self.assertEqual(place["status"], "pending")

    def test_a_coordinate_that_is_not_one_is_dropped_rather_than_stored(self):
        for bad in ["north", 91, -181, float("nan"), None, {}]:
            with self.subTest(value=bad):
                (place,) = self._clean(self._picked(latitude=bad))
                self.assertIsNone(place["latitude"])

    def test_a_payload_that_cannot_be_read_is_refused(self):
        """Only a value that opens with a brace is read as a payload at all.

        Anything else is text somebody typed, however odd it looks, so "[1, 2]"
        becomes a place called "[1, 2]" rather than an error. A broken payload,
        though, is a tampered form or a bug, and gets refused.
        """
        for bad in ["{not json", "{}", '{"query": "   "}', '{"query": null}']:
            with self.subTest(payload=bad):
                with self.assertRaises(ValidationError):
                    self._clean(bad)
        (place,) = self._clean("[1, 2]")
        self.assertEqual(place["query"], "[1, 2]")

    def test_the_same_place_twice_is_stored_once(self):
        self.assertEqual(len(self._clean(self._picked(), self._picked())), 1)
        self.assertEqual(len(self._clean("DeLand", "deland")), 1)

    def test_a_place_too_long_for_its_column_is_trimmed_rather_than_refused(self):
        (place,) = self._clean(self._picked(label="x" * 400, city="y" * 200))
        self.assertEqual(len(place["label"]), 255)
        self.assertEqual(len(place["city"]), 80)

    def test_more_places_than_the_form_takes_is_refused(self):
        values = [json.dumps({"query": f"place {n}"}) for n in range(12)]
        with self.assertRaises(ValidationError):
            LocationsField(required=False).clean(values)

    def test_required_asks_for_something_a_person_can_give(self):
        with self.assertRaisesMessage(ValidationError, "even if it is only a city"):
            LocationsField(required=True).clean([])


class AddressPickerFormTests(TestCase):
    """The picker end to end on the public form."""

    @classmethod
    def setUpTestData(cls):
        cls.category = Category.objects.create(name="Co-ops", slug="co-ops")

    def _payload(self, locations, **overrides):
        return {
            "program_name": "Coastal Co-op",
            "short_description": "A Thursday co-op.",
            "description": "We meet weekly.",
            "category": str(self.category.pk),
            "website": "https://coastal.example.org",
            "locations": locations,
            "serves_grades": "K–8",
            "submitter_name": "Dana Reed",
            "submitter_email": "dana@coastal.example.org",
            "is_authorized": "on",
            "website_url": "",
            **overrides,
        }

    def test_the_places_somebody_picked_arrive_with_their_coordinates(self):
        """The point of the whole exercise.

        The submitter chose which match was right, so nothing has to guess later
        — and nothing calls a geocoder during their submission either.
        """
        picked = [
            json.dumps(
                {
                    "query": "12 ocean ave",
                    "label": "12 Ocean Avenue, Ormond Beach, 32176",
                    "latitude": 29.2858,
                    "longitude": -81.0559,
                    "city": "Ormond Beach",
                }
            ),
            json.dumps({"query": "at members' homes"}),
        ]
        with patch("directory.views.geocoding.search") as search:
            response = self.client.post(reverse("directory:register"), self._payload(picked))
        self.assertRedirects(response, reverse("directory:register_thanks"))
        search.assert_not_called()

        first, second = Submission.objects.get().locations.all()
        self.assertEqual(first.label, "12 Ocean Avenue, Ormond Beach, 32176")
        self.assertAlmostEqual(first.latitude, 29.2858)
        self.assertEqual(first.city, "Ormond Beach")
        self.assertEqual(first.status, SubmissionLocation.Status.RESOLVED)
        # Typed rather than picked, and none the worse for it.
        self.assertEqual(second.query, "at members' homes")
        self.assertFalse(second.has_point)
        self.assertEqual(second.status, SubmissionLocation.Status.PENDING)
        self.assertEqual([place.sort_order for place in [first, second]], [0, 1])

    def test_an_address_typed_but_never_added_is_still_an_address(self):
        """The trap this closes.

        Somebody types where they meet, presses Send without pressing +, and a
        form that dropped it would answer "please add at least one place" to a
        person who had just done exactly that.
        """
        response = self.client.post(
            reverse("directory:register"),
            self._payload([], locations_typed="Deltona Regional Library"),
        )
        self.assertRedirects(response, reverse("directory:register_thanks"))
        place = Submission.objects.get().locations.get()
        self.assertEqual(place.query, "Deltona Regional Library")
        self.assertEqual(place.status, SubmissionLocation.Status.PENDING)

    def test_what_was_picked_comes_before_what_was_left_in_the_box(self):
        picked = json.dumps({"query": "12 ocean ave", "label": "12 Ocean Avenue"})
        self.client.post(
            reverse("directory:register"),
            self._payload([picked], locations_typed="4 Granada Blvd"),
        )
        self.assertEqual(
            [place.query for place in Submission.objects.get().locations.all()],
            ["12 ocean ave", "4 Granada Blvd"],
        )

    def test_a_registration_with_nowhere_to_meet_is_refused(self):
        response = self.client.post(
            reverse("directory:register"), self._payload([], locations_typed="")
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "even if it is only a city")
        self.assertEqual(Submission.objects.count(), 0)

    def test_a_form_that_comes_back_with_an_error_keeps_the_places_already_chosen(self):
        """Otherwise a wrong phone number costs somebody all five addresses.

        This is the difference between a form people finish and a form people
        abandon, and it is the first thing a client-side-only picker loses.
        """
        picked = json.dumps(
            {
                "query": "12 ocean ave",
                "label": "12 Ocean Avenue",
                "latitude": 29.28,
                "longitude": -81.05,
            }
        )
        response = self.client.post(
            reverse("directory:register"), self._payload([picked], serves_grades="")
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "12 Ocean Avenue")
        self.assertContains(response, "data-address-place")

    def test_the_form_tells_the_script_how_long_to_wait_and_how_little_to_send(self):
        """The debounce is a promise to the geocoder, so it is asserted, not assumed."""
        response = self.client.get(reverse("directory:register"))
        self.assertContains(response, f'data-debounce="{DEBOUNCE_MS}"')
        self.assertContains(response, f'data-min-chars="{MIN_QUERY_CHARS}"')
        self.assertContains(response, reverse("directory:address_search"))
        self.assertGreaterEqual(DEBOUNCE_MS, 300)

    def test_there_is_still_a_field_to_type_into_without_javascript(self):
        response = self.client.get(reverse("directory:register"))
        self.assertContains(response, "<noscript>")
        self.assertContains(response, 'name="locations"')

    def test_the_referral_form_takes_a_place_the_same_way(self):
        response = self.client.post(
            reverse("directory:refer"),
            {
                "program_name": "Surf Lessons",
                "website": "https://surf.example.org",
                "locations": [
                    json.dumps(
                        {"query": "New Smyrna Beach", "latitude": 29.02, "longitude": -80.92}
                    )
                ],
                "description": "They take homeschoolers on Wednesdays.",
                "submitter_name": "Jane",
                "submitter_email": "jane@example.com",
                "website_url": "",
            },
        )
        self.assertRedirects(response, reverse("directory:refer_thanks"))
        place = Submission.objects.get().locations.get()
        self.assertEqual(place.query, "New Smyrna Beach")
        self.assertTrue(place.has_point)


def tearDownModule():
    """Remove the temporary media trees the upload tests wrote into."""
    for cls in [RegistrationTests, ApprovalTests]:
        root = cls._overridden_settings.get("MEDIA_ROOT")
        if root:
            shutil.rmtree(root, ignore_errors=True)
