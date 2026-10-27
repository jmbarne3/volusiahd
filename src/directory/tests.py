"""Enough tests to catch the things that would quietly break.

Not exhaustive coverage. These cover the three failure modes that would be
expensive to discover in production: a route that 500s, unsanitized HTML
reaching a template that renders with `|safe`, and a draft program being
visible on the public site.
"""

import importlib
import json
import math
import os
import shutil
import tempfile
from contextlib import contextmanager
from io import BytesIO, StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from django import forms as forms_module
from django.apps import apps as django_apps
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
from django.utils.html import escapejs
from PIL import Image

from . import geocoding
from . import questions as q
from .addresses import DEBOUNCE_MS, MIN_QUERY_CHARS, LocationsField
from .models import (
    GRADE_LEVELS,
    GRADE_SEARCH_TERMS,
    WEEKDAY_NAMES,
    Category,
    ContactPerson,
    Page,
    Program,
    ProgramLocation,
    SharedProgramFields,
    Submission,
    SubmissionLocation,
    Tag,
    TagGroup,
    join_weekdays,
    split_weekdays,
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

    def test_search_words_match_in_any_order_and_any_field(self):
        """Families search with words, not titles. "co-op deland" matches the
        name with one word and the address with the other."""
        for query in ["co-op sample", "co-op deland", "  Sample,  DeLand "]:
            with self.subTest(query=query):
                response = self.client.get(reverse("directory:program_list"), {"q": query})
                self.assertContains(response, "Sample Co-op")

    def test_every_search_word_has_to_match(self):
        response = self.client.get(reverse("directory:program_list"), {"q": "sample tampa"})
        self.assertNotContains(response, "Sample Co-op")

    def test_filler_words_do_not_sink_a_search(self):
        response = self.client.get(
            reverse("directory:program_list"), {"q": "the co-op classes in DeLand"}
        )
        self.assertContains(response, "Sample Co-op")

    def test_a_search_of_only_filler_still_searches(self):
        response = self.client.get(reverse("directory:program_list"), {"q": "the"})
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

    def test_two_words_can_match_two_different_tags(self):
        self.program.tags.add(self.other)
        response = self.client.get(reverse("directory:program_list"), {"q": "lego dual"})
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
            "grade_levels": ["elementary", "middle"],
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
        self.assertEqual(submission.grade_levels, "elementary,middle")
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
        response = self.client.post(reverse("directory:register"), self._payload(grade_levels=[]))
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
        # Slugs, not primary keys: the same script reads `data-asked-by`, which
        # comes from `questions.py` and can only speak in slugs.
        headings = " ".join(sorted([self.category.slug, clubs.slug]))
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

    def test_direct_pay_without_a_scholarship_is_sent_back(self):
        response = self.client.post(
            reverse("directory:register"),
            self._payload(step_up_direct_pay="on", step_up_pep="", step_up_fes_ua=""),
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Submission.objects.count(), 0)
        self.assertContains(response, "Tick PEP, FES-UA or both")

    def test_a_grade_level_that_is_not_one_is_refused(self):
        response = self.client.post(
            reverse("directory:register"), self._payload(grade_levels=["college"])
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
            "grade_levels": "elementary,middle",
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
        self.assertEqual(program.grade_levels, "elementary,middle")
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
            "grade_levels": ["elementary", "middle"],
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
            reverse("directory:register"), self._payload([picked], grade_levels=[])
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


class QuestionMapTests(TestCase):
    """The map from a field to the headings that ask about it.

    A `TestCase` rather than a `SimpleTestCase` because the last of these builds
    the real form, and the form reads the tag questions out of the database.

    Everything here is a typo that would otherwise go unnoticed. A field name
    misspelled in `ASKED_BY` scopes nothing and shows that question to everybody;
    a heading slug misspelled scopes it to nobody and hides the question from
    everybody. Both look like a design decision from the outside.
    """

    def test_every_heading_named_in_the_map_is_a_real_heading(self):
        for field, headings in q.ASKED_BY.items():
            with self.subTest(field=field):
                self.assertEqual(headings - q.EVERY, set())

    def test_every_required_field_is_a_field_somebody_is_asked(self):
        self.assertEqual(q.REQUIRED - set(q.ASKED_BY), set())

    def test_every_field_in_the_map_is_a_field_on_the_registration_form(self):
        """The check that catches a renamed model field.

        `questions.py` scopes by field name, and a name that no longer matches a
        field is silently ignored — the question comes back unscoped and every
        heading is asked it again.
        """
        from .forms import ProgramRegistrationForm

        self.assertEqual(set(q.ASKED_BY) - set(ProgramRegistrationForm().fields), set())

    def test_an_unknown_heading_is_asked_the_questions_everybody_is_asked(self):
        """What a heading she adds in the admin tomorrow gets.

        Not nothing: a heading with no entry here has to produce a usable form on
        the day it is created, and be tailored later or never.
        """
        self.assertEqual(q.fields_for("something-she-invented"), q.CORE)
        self.assertIn("program_name", q.fields_for(None))

    def test_a_heading_is_only_required_to_answer_what_it_is_asked(self):
        self.assertIn("grade_levels", q.required_for(q.CO_OPS))
        # A parent support group serves the adults, so it is never asked.
        self.assertNotIn("grade_levels", q.fields_for(q.PARENT_SUPPORT))
        self.assertNotIn("grade_levels", q.required_for(q.PARENT_SUPPORT))
        self.assertIn("program_name", q.required_for(q.PARENT_SUPPORT))


class SharedFieldTests(TestCase):
    """The two models mirror each other, and the mirror is checked rather than trusted."""

    def test_both_models_hold_every_shared_field(self):
        names = SharedProgramFields.shared_field_names()
        self.assertGreater(len(names), 20)
        for name in names:
            with self.subTest(field=name):
                Program._meta.get_field(name)
                Submission._meta.get_field(name)

    def test_approval_carries_every_shared_field_across(self):
        """The test that makes the shared base worth having.

        A field a registrant fills in and approval drops is a field she retypes
        by hand, and nothing would tell her it happened. Rather than listing what
        to check, this fills every shared field with something distinguishable
        from its default and asserts the published program matches.
        """
        from .admin import build_program_from

        submission = Submission.objects.create(
            program_name="Everything Co-op",
            short_description="One line.",
            host_name="Mainland Fellowship",
            class_names="Algebra I, Latin",
            instructor_info="Two teachers, both certified.",
            faith_basis=Submission.Faith.FAITH_BASED,
            website="https://example.org",
            facebook="https://facebook.com/groups/x",
            email="hi@example.org",
            phone="386-555-0000",
            grade_levels="elementary,middle",
            meeting_days="tue,thu",
            meeting_time_start="09:00",
            meeting_time_end="12:00",
            season_start="2026-09-01",
            season_end="2027-05-20",
            meeting_schedule="Alternate Fridays too.",
            enrollment_opens="2026-06-01",
            enrollment_closes="2026-08-15",
            enrollment_notes="Rolling after that.",
            cost_basis=Submission.Cost.PAID,
            cost_notes="$45 a semester.",
            step_up_direct_pay=True,
            step_up_pep=True,
            step_up_fes_ua=True,
            delivery=Submission.Delivery.HYBRID,
            travels_to_student_home=True,
            service_area="West Volusia.",
        )
        submission.refresh_from_db()

        program = build_program_from(submission, Program.Status.PUBLISHED)
        program.refresh_from_db()

        for name in SharedProgramFields.shared_field_names():
            with self.subTest(field=name):
                expected = getattr(submission, name)
                self.assertEqual(getattr(program, name), expected)
                # And the value was actually worth copying, so a base class that
                # copied nothing at all could not pass this.
                self.assertNotIn(expected, ["", None])


class WeekdayFieldTests(TestCase):
    """Days of the week, stored as text and filtered by substring."""

    def test_no_day_code_is_a_substring_of_another(self):
        """The whole reason `meeting_days__contains` is safe.

        If a code ever became a prefix of another one, every filter in
        `meeting_on` would start returning the wrong programs and nothing else
        here would fail.
        """
        codes = list(WEEKDAY_NAMES)
        for code in codes:
            for other in codes:
                if code != other:
                    self.assertNotIn(code, other)

    def test_days_are_stored_in_week_order_however_they_were_ticked(self):
        self.assertEqual(join_weekdays(["thu", "mon", "sat"]), "mon,thu,sat")
        self.assertEqual(join_weekdays("sun,tue"), "tue,sun")

    def test_anything_that_is_not_a_day_is_dropped_rather_than_stored(self):
        """A garbled value should mean "we do not know", not a 500 on a page."""
        self.assertEqual(split_weekdays("mon,notaday,wed"), ["mon", "wed"])
        self.assertEqual(split_weekdays(None), [])
        self.assertEqual(split_weekdays("<script>"), [])

    def test_a_list_assigned_straight_onto_a_program_is_normalised_on_save(self):
        program = Program.objects.create(
            name="Shell", slug="shell", short_description="x", meeting_days=["fri", "mon"]
        )
        program.refresh_from_db()
        self.assertEqual(program.meeting_days, "mon,fri")

    def test_filtering_by_a_day_finds_only_the_programs_meeting_on_it(self):
        thursday = Program.objects.create(
            name="Thursday", slug="thu", short_description="x", meeting_days="thu"
        )
        Program.objects.create(
            name="Tuesday", slug="tue", short_description="x", meeting_days="tue"
        )
        self.assertEqual(list(Program.objects.meeting_on("thu")), [thursday])
        # Nothing to narrow by is not the same as nothing matching.
        self.assertEqual(Program.objects.meeting_on("").count(), 2)
        self.assertEqual(Program.objects.meeting_on("funday").count(), 2)
        self.assertEqual(list(Program.objects.meeting_on(["thu", "funday"])), [thursday])

    def test_the_days_read_as_a_sentence(self):
        program = Program(meeting_days="tue,thu")
        self.assertEqual(program.meeting_days_display, "Tuesdays and Thursdays")
        self.assertEqual(
            Program(meeting_days="mon,wed,fri").meeting_days_display,
            "Mondays, Wednesdays and Fridays",
        )


class DisplayTests(SimpleTestCase):
    """What a program page actually prints, for the answers assembled from parts."""

    def test_a_time_reads_as_somebody_would_say_it(self):
        import datetime

        program = Program(
            meeting_time_start=datetime.time(9, 0), meeting_time_end=datetime.time(12, 0)
        )
        self.assertEqual(program.meeting_time_display, "9 a.m. to noon")
        self.assertEqual(
            Program(meeting_time_start=datetime.time(13, 30)).meeting_time_display,
            "from 1:30 p.m.",
        )

    def test_a_season_reads_as_months_when_both_ends_are_known(self):
        import datetime

        program = Program(
            season_start=datetime.date(2026, 9, 2), season_end=datetime.date(2027, 5, 20)
        )
        self.assertEqual(program.season_display, "September through May")
        self.assertEqual(
            Program(season_start=datetime.date(2026, 9, 2)).season_display, "from September 2"
        )

    def test_cost_says_the_headline_and_the_detail_without_saying_either_twice(self):
        self.assertEqual(
            Program(cost_basis="paid", cost_notes="$45 a semester").cost_display,
            "There is a cost — $45 a semester",
        )
        self.assertEqual(Program(cost_basis="free").cost_display, "Free")
        # The note already says it; the headline would be noise.
        self.assertEqual(
            Program(cost_basis="free", cost_notes="Free, but bring a snack").cost_display,
            "Free, but bring a snack",
        )

    def test_when_nothing_about_the_schedule_is_known_nothing_is_printed(self):
        self.assertEqual(Program().when_display, "")
        self.assertEqual(Program().enrollment_display, "")
        self.assertEqual(Program().cost_display, "")


class ScopedFormTests(TestCase):
    """One form, thirteen shapes. The heading decides which questions count.

    The rule being guarded is that the server decides what it asked about. The
    script narrows the form for the person filling it in, but a direct post — or a
    browser with no script — has to land in the same place, and the way it lands
    matters: an answer to a question this heading is not asked is dropped quietly,
    because somebody who changed their mind about the heading has not done
    anything wrong.
    """

    @classmethod
    def setUpTestData(cls):
        cls.co_ops = Category.objects.create(name="Co-ops", slug=q.CO_OPS)
        cls.tutoring = Category.objects.create(name="Tutoring", slug=q.TUTORING)
        cls.parents = Category.objects.create(name="Parent support", slug=q.PARENT_SUPPORT)

    def _payload(self, **overrides):
        return {
            "program_name": "Coastal Co-op",
            "short_description": "A Thursday co-op in Ormond Beach.",
            "description": "We meet weekly.",
            "category": str(self.co_ops.pk),
            "email": "hello@coastal.example.org",
            "locations": "12 Ocean Ave, Ormond Beach 32176",
            "grade_levels": ["elementary", "middle"],
            "submitter_name": "Dana Reed",
            "submitter_email": "dana@coastal.example.org",
            "is_authorized": "on",
            "website_url": "",
            **overrides,
        }

    def _submit(self, **overrides):
        response = self.client.post(reverse("directory:register"), self._payload(**overrides))
        self.assertRedirects(response, reverse("directory:register_thanks"))
        return Submission.objects.get()

    def test_an_answer_this_heading_is_asked_for_is_kept(self):
        submission = self._submit(faith_basis="faith", meeting_days=["tue", "thu"])
        self.assertEqual(submission.faith_basis, "faith")
        self.assertEqual(submission.meeting_days, "tue,thu")

    def test_an_answer_to_a_question_this_heading_is_not_asked_is_dropped(self):
        """Dropped, not refused.

        Tutoring is never asked about faith affiliation or enrollment windows. A
        post carrying them is somebody who filled in the co-op version of this
        form and then changed the heading, and rejecting it would be the form
        blaming them for our own narrowing.
        """
        submission = self._submit(
            category=str(self.tutoring.pk),
            faith_basis="faith",
            enrollment_notes="Rolling",
            host_name="Somebody Else",
        )
        self.assertEqual(submission.faith_basis, "")
        self.assertEqual(submission.enrollment_notes, "")
        self.assertEqual(submission.host_name, "")
        # And the questions tutoring *is* asked still work.
        self.assertEqual(submission.grade_levels, "elementary,middle")

    def test_a_boolean_nobody_was_asked_about_comes_back_false(self):
        submission = self._submit(
            category=str(self.parents.pk), grade_levels=[], step_up_direct_pay="on"
        )
        self.assertFalse(submission.step_up_direct_pay)

    def test_nothing_is_dropped_when_no_heading_was_chosen(self):
        """With no heading we have no basis for deciding a question was not asked.

        Narrowing on the strength of an unanswered question would throw away work
        somebody had already done.
        """
        submission = self._submit(category="", faith_basis="secular", host_name="A Host")
        self.assertEqual(submission.faith_basis, "secular")
        self.assertEqual(submission.host_name, "A Host")

    def test_grades_are_required_of_a_heading_that_is_asked_for_them(self):
        response = self.client.post(reverse("directory:register"), self._payload(grade_levels=[]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Submission.objects.count(), 0)

    def test_grades_are_not_required_of_a_heading_that_is_never_asked(self):
        submission = self._submit(category=str(self.parents.pk), grade_levels=[])
        self.assertEqual(submission.grade_levels, "")

    def test_grades_are_still_required_when_no_heading_was_chosen(self):
        response = self.client.post(
            reverse("directory:register"), self._payload(category="", grade_levels=[])
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Submission.objects.count(), 0)

    def test_a_scoped_question_says_which_headings_ask_it(self):
        """What the script narrows the form with.

        If this attribute stops being rendered the form silently stops narrowing
        and everybody is asked all forty questions, which is the failure that
        looks like a design decision.
        """
        response = self.client.get(reverse("directory:register"))
        self.assertContains(response, 'data-asked-by="')
        page = response.content.decode()
        # Faith affiliation is asked of co-ops and not of tutoring.
        start = page.index('name="faith_basis"')
        attribute = page[start : page.index(">", start)]
        self.assertIn(q.CO_OPS, attribute)
        self.assertNotIn(q.TUTORING, attribute)

    def test_a_question_everybody_is_asked_carries_no_scope(self):
        """An unscoped field is one the script never touches, and that is the point.

        Marking a universal question with all thirteen slugs would be thirteen
        slugs of markup saying nothing, on every one of them.
        """
        page = self.client.get(reverse("directory:register")).content.decode()
        start = page.index('name="short_description"')
        self.assertNotIn("data-asked-by", page[start : page.index(">", start)])

    def test_the_heading_dropdown_carries_each_slug(self):
        """The one thing the script needs that the value does not give it."""
        page = self.client.get(reverse("directory:register")).content.decode()
        self.assertIn(f'data-slug="{q.CO_OPS}"', page)


class TagQuestionTests(TestCase):
    """The questions whose answers are tags."""

    @classmethod
    def setUpTestData(cls):
        cls.sports = Category.objects.create(name="Sports", slug=q.SPORTS)
        cls.tutoring = Category.objects.create(name="Tutoring", slug=q.TUTORING)
        cls.ability = TagGroup.objects.create(
            name="Ability level", slug="ability-level", allows_several=True
        )
        cls.beginner = Tag.objects.create(name="Beginner", slug="beginner", group=cls.ability)
        cls.varsity = Tag.objects.create(name="Varsity", slug="varsity", group=cls.ability)
        cls.sports.tags.add(cls.beginner, cls.varsity)
        cls.subject = Tag.objects.create(name="Soccer", slug="soccer")
        cls.sports.tags.add(cls.subject)

    def _payload(self, **overrides):
        return {
            "program_name": "Coastal United",
            "short_description": "Youth soccer in Ormond Beach.",
            "description": "We play on Saturdays.",
            "category": str(self.sports.pk),
            "email": "hello@coastal.example.org",
            "locations": "12 Ocean Ave, Ormond Beach 32176",
            "grade_levels": ["elementary", "middle", "high"],
            "submitter_name": "Dana Reed",
            "submitter_email": "dana@coastal.example.org",
            "is_authorized": "on",
            "website_url": "",
            **overrides,
        }

    def test_an_answer_is_stored_as_a_tag_like_any_other(self):
        """No column, no second place to look.

        The payoff of answering a type-specific question with a tag: the program
        page, the tag page and every filter treat "Varsity" exactly as they treat
        "Soccer", and the type-specific question needed no code of its own.
        """
        response = self.client.post(
            reverse("directory:register"),
            self._payload(question_ability_level=[str(self.varsity.pk)]),
        )
        self.assertRedirects(response, reverse("directory:register_thanks"))
        submission = Submission.objects.get()
        self.assertEqual([tag.name for tag in submission.tags.all()], ["Varsity"])

    def test_an_answer_the_chosen_heading_is_not_offered_is_dropped(self):
        submission_count = Submission.objects.count()
        self.client.post(
            reverse("directory:register"),
            self._payload(
                category=str(self.tutoring.pk),
                question_ability_level=[str(self.varsity.pk)],
            ),
        )
        self.assertEqual(Submission.objects.count(), submission_count + 1)
        self.assertEqual(list(Submission.objects.get().tags.all()), [])

    def test_the_answers_are_offered_alongside_the_subject_tags_not_inside_them(self):
        """Two controls, because they are two different kinds of question.

        A fixed set of answers belongs in radio buttons; an open vocabulary of
        hundreds belongs in a search box. Leaving the answers in the subject list
        as well would offer the same tag twice on one page.
        """
        page = self.client.get(reverse("directory:register")).content.decode()
        opens = page.index('name="tags"')
        tag_select = page[opens : page.index("</select>", opens)]
        self.assertIn("Soccer", tag_select)
        self.assertNotIn("Varsity", tag_select)
        self.assertIn("data-answer-field", page)

    def test_a_question_nobody_is_offered_an_answer_to_is_not_rendered(self):
        """A question with no answers on offer anywhere is not a question."""
        unused = TagGroup.objects.create(name="Unused", slug="unused")
        Tag.objects.create(name="Nobody offers this", slug="nobody", group=unused)
        page = self.client.get(reverse("directory:register")).content.decode()
        self.assertNotIn("question_unused", page)

    def test_a_program_page_lists_an_answer_against_its_question(self):
        """An answer on its own does not say what it is an answer to."""
        program = Program.objects.create(
            name="Coastal United",
            slug="coastal-united",
            short_description="Youth soccer.",
            status=Program.Status.PUBLISHED,
            category=self.sports,
        )
        program.tags.set([self.varsity, self.subject])
        response = self.client.get(program.get_absolute_url())
        self.assertContains(response, "Ability level")
        # The subject tag is still a chip, because it reads on its own.
        self.assertContains(response, "tag-chip")


class ListingFilterTests(TestCase):
    """Narrowing a listing. Every filter is a link somebody can send to a friend."""

    @classmethod
    def setUpTestData(cls):
        cls.category = Category.objects.create(name="Sports", slug=q.SPORTS)
        cls.varsity = Tag.objects.create(name="Varsity", slug="varsity")
        cls.saturday_tag = Tag.objects.create(name="Soccer", slug="soccer")

        cls.thursday = cls._program(
            "Thursday Club", "thursday", meeting_days="thu", grade_levels="elementary,middle"
        )
        cls.saturday = cls._program(
            "Saturday League",
            "saturday",
            meeting_days="sat",
            faith_basis=Program.Faith.FAITH_BASED,
            delivery=Program.Delivery.HYBRID,
            step_up_direct_pay=True,
        )
        cls.saturday.tags.set([cls.varsity, cls.saturday_tag])
        cls.anytime = cls._program("No Idea", "no-idea")

    @classmethod
    def _program(cls, name, slug, **fields):
        return Program.objects.create(
            name=name,
            slug=slug,
            short_description=f"{name} in Volusia County.",
            status=Program.Status.PUBLISHED,
            category=cls.category,
            **fields,
        )

    def _names(self, **params):
        response = self.client.get(reverse("directory:program_list"), params)
        self.assertEqual(response.status_code, 200)
        return sorted(program.name for program in response.context["programs"])

    def test_filtering_by_day(self):
        self.assertEqual(self._names(day="thu"), ["Thursday Club"])

    def test_several_days_mean_any_of_them(self):
        """Tuesday or Thursday, not Tuesday and Thursday: families tick the days
        they are free."""
        self.assertEqual(self._names(day=["thu", "sat"]), ["Saturday League", "Thursday Club"])
        self.assertEqual(self._names(day=["thu", "mon"]), ["Thursday Club"])

    def test_a_program_that_never_gave_its_days_only_shows_with_no_day_ticked(self):
        every_day = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
        self.assertNotIn("No Idea", self._names(day=every_day))
        self.assertIn("No Idea", self._names())

    def test_unknown_days_are_ignored_rather_than_emptying_the_list(self):
        self.assertEqual(self._names(day=["thu", "funday"]), ["Thursday Club"])
        self.assertEqual(len(self._names(day=["funday"])), 3)

    def test_filtering_by_faith_affiliation(self):
        self.assertEqual(self._names(faith="faith"), ["Saturday League"])

    def test_filtering_by_online_availability(self):
        self.assertEqual(self._names(online="1"), ["Saturday League"])

    def test_filtering_by_direct_pay(self):
        self.assertEqual(self._names(directpay="1"), ["Saturday League"])

    def test_a_grade_filter_keeps_the_programs_that_never_said(self):
        """An unknown has to widen the answer, not narrow it.

        Dropping every program that left its grade levels blank would hide them
        behind the filter most likely to be used.
        """
        self.assertEqual(
            self._names(grade="middle"), ["No Idea", "Saturday League", "Thursday Club"]
        )
        # Outside the levels we know about, that program drops out and the ones
        # that never said still stand.
        self.assertEqual(self._names(grade="prek"), ["No Idea", "Saturday League"])

    def test_several_grade_levels_mean_any_of_them(self):
        self.assertIn("Thursday Club", self._names(grade=["prek", "elementary"]))
        self.assertNotIn("Thursday Club", self._names(grade=["prek", "high"]))

    def test_unknown_grade_levels_and_old_age_links_are_ignored(self):
        self.assertEqual(len(self._names(grade="college")), 3)
        self.assertEqual(len(self._names(age="12")), 3)

    def test_each_tag_narrows_the_list_rather_than_widening_it(self):
        """Two tags means both, not either.

        `tags__slug__in` would have made "varsity and Saturdays" return every
        varsity programme in the county, which is the opposite of what adding a
        filter is for.
        """
        self.assertEqual(self._names(tag=["varsity"]), ["Saturday League"])
        self.assertEqual(self._names(tag=["varsity", "soccer"]), ["Saturday League"])
        self.assertEqual(self._names(tag=["varsity", "nonexistent"]), [])

    def test_nothing_in_the_query_string_narrows_nothing(self):
        self.assertEqual(len(self._names()), 3)
        self.assertEqual(len(self._names(day="", faith="", grade="")), 3)

    def test_the_filters_are_shown_back_so_somebody_can_see_what_is_applied(self):
        response = self.client.get(reverse("directory:program_list"), {"day": "thu", "online": "1"})
        self.assertEqual(response.context["filters"]["days"], ["thu"])
        self.assertTrue(response.context["filters"]["online"])
        self.assertTrue(response.context["filters_active"])
        self.assertContains(response, "Clear")

    def test_the_same_filters_work_on_a_category_page(self):
        response = self.client.get(self.category.get_absolute_url(), {"day": "sat"})
        self.assertEqual(
            [program.name for program in response.context["programs"]], ["Saturday League"]
        )

    def test_the_same_filters_work_on_a_tag_page(self):
        response = self.client.get(self.varsity.get_absolute_url(), {"day": "thu"})
        self.assertEqual(list(response.context["programs"]), [])


class ScopedFormAssetTests(SimpleTestCase):
    """The contract between the form, the stylesheet and the script.

    Three files have to agree on a handful of attribute names, and none of them
    can see the other two. Every failure this catches is silent: the script stops
    finding what it narrows, or the stylesheet stops honouring what the script
    hides, and the page looks exactly like a page where the feature was never
    built.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        static = settings.BASE_DIR / "src" / "static"
        cls.css = (static / "css" / "site.css").read_text()
        cls.js = (static / "js" / "register-form.js").read_text()
        cls.template = (
            settings.BASE_DIR / "src" / "templates" / "directory" / "register.html"
        ).read_text()

    def test_the_script_and_the_page_agree_on_what_the_script_looks_for(self):
        for hook in ["data-scoped-form", "data-asked-by", "data-categories", "data-slug"]:
            with self.subTest(hook=hook):
                self.assertIn(hook, self.js)
        self.assertIn("data-scoped-form", self.template)
        self.assertIn("register-form.js", self.template)

    def test_the_answer_rows_are_marked_for_the_script(self):
        """`is_answer=True` is what puts `data-answer-field` in the markup."""
        self.assertIn("data-answer-field", self.js)
        self.assertIn("is_answer=True", self.template)
        field_tag = (
            settings.BASE_DIR / "src" / "templates" / "directory" / "_field.html"
        ).read_text()
        self.assertIn("data-answer-field", field_tag)

    def test_the_stylesheet_honours_the_hidden_attribute_the_script_sets(self):
        """A `display` in a stylesheet beats the user agent's rule for `[hidden]`.

        Every selector that sets a display on one of these has to say `display:
        none` for the hidden case as well, or the script hides a question and the
        question stays on screen.
        """
        for selector in [".field[hidden]", "fieldset[hidden]"]:
            with self.subTest(selector=selector):
                self.assertIn(selector, self.css)
        # The answer rows set `display: flex` on their labels, so those need it too.
        self.assertIn("label[hidden]", self.css)

    def test_a_group_of_checkboxes_is_styled_even_though_django_gives_it_no_class(self):
        self.assertIn(":has(input:is([type=checkbox], [type=radio]))", self.css)


class ListingFilterStyleTests(TestCase):
    """The filter row is a form control block, and has to be dressed like one.

    It was not, and the way it failed is worth writing down. The row was put
    inside `.search`, which is a pill with a pane behind it, hides its own label
    on purpose, and strips the border off any input within it — all correct for
    one row and all wrong underneath it. The result was native operating-system
    dropdowns, an invisible number box and three labels somewhere off the left of
    the screen, none of which any test noticed.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.css = (settings.BASE_DIR / "src" / "static" / "css" / "site.css").read_text()

    def test_the_search_pill_holds_only_the_box_and_its_button(self):
        """Anything else in there gets styling written for one row of one control."""
        Category.objects.create(name="Co-ops", slug=q.CO_OPS)
        page = self.client.get(reverse("directory:program_list")).content.decode()
        start = page.index('<div class="search">')
        pill = page[start : page.index("</div>", start)]
        self.assertIn('type="search"', pill)
        for stray in ["<select", 'type="number"', 'type="checkbox"']:
            with self.subTest(stray=stray):
                self.assertNotIn(stray, pill)

    def test_the_filter_controls_are_covered_by_the_shared_recipe(self):
        """The recipe is written by exception so a new control is dressed by default.

        That only works for the wrappers it names, and it named `.field` and
        `.stack`. A control in any other wrapper renders in the browser's own
        style beside fields in ours, and every test still passes.
        """
        start = self.css.index(".field :is(input, select, textarea):not(")
        selector = self.css[start : self.css.index("{", start)]
        self.assertIn(".refine :is(input, select, textarea)", selector)

    def test_the_filter_dropdowns_draw_the_same_caret_as_every_other_dropdown(self):
        start = self.css.index(".field select:not([multiple])")
        selector = self.css[start : self.css.index("{", start)]
        self.assertIn(".refine select:not([multiple])", selector)

    def test_a_filter_dropdown_keeps_room_for_the_caret_it_draws(self):
        """The row shortens these controls, and a `padding` shorthand would put
        the longest option name straight underneath the arrow."""
        start = self.css.index(".refine select:not([multiple]) {")
        rule = self.css[start : self.css.index("}", start)]
        self.assertIn("2.5rem", rule)


class SeedTaxonomyTests(TestCase):
    """The command that makes an empty database usable."""

    def test_it_creates_every_heading_the_question_map_expects(self):
        """The two halves of the same contract, in two files.

        `questions.py` scopes questions to slugs; this command is what makes those
        slugs exist. A slug in one and not the other means a question asked of
        nobody, or a heading asked only the core questions and nobody noticing.
        """
        call_command("seed_taxonomy", stdout=StringIO())
        self.assertEqual(set(Category.objects.values_list("slug", flat=True)), set(q.EVERY))

    def test_it_creates_the_questions_with_their_answers_attached(self):
        call_command("seed_taxonomy", stdout=StringIO())
        ability = TagGroup.objects.get(slug="ability-level")
        answers = set(ability.tags.values_list("name", flat=True))
        self.assertIn("Beginner", answers)
        self.assertIn("Varsity", answers)
        # Sports is offered the varsity answers; an art studio is not.
        sports = Category.objects.get(slug=q.SPORTS)
        arts = Category.objects.get(slug=q.ARTS_AND_CRAFTS)
        self.assertIn("Varsity", set(sports.tags.values_list("name", flat=True)))
        self.assertNotIn("Varsity", set(arts.tags.values_list("name", flat=True)))
        self.assertIn("Beginner", set(arts.tags.values_list("name", flat=True)))

    def test_running_it_twice_changes_nothing(self):
        call_command("seed_taxonomy", stdout=StringIO())
        counts = (Category.objects.count(), Tag.objects.count(), TagGroup.objects.count())
        call_command("seed_taxonomy", stdout=StringIO())
        self.assertEqual(
            (Category.objects.count(), Tag.objects.count(), TagGroup.objects.count()), counts
        )

    def test_it_leaves_a_renamed_heading_alone(self):
        """Matched on slug, and never edited.

        Re-running this after she has reworded a heading must not put our wording
        back — that would make the command something you cannot safely run.
        """
        Category.objects.create(name="Co-operatives, as we call them", slug=q.CO_OPS)
        call_command("seed_taxonomy", stdout=StringIO())
        self.assertEqual(Category.objects.get(slug=q.CO_OPS).name, "Co-operatives, as we call them")


def taxonomy_snapshot():
    """The whole taxonomy as comparable data, with primary keys left out.

    Primary keys are deliberately absent: what has to match between the fixture
    and the command that generates it is the vocabulary, not the order rows
    happened to be written in.
    """
    return {
        "categories": sorted(
            (c.slug, c.name, c.sort_order, c.icon, tuple(sorted(t.name for t in c.tags.all())))
            for c in Category.objects.all()
        ),
        "tags": sorted(
            (t.name, t.slug, t.group.slug if t.group else None) for t in Tag.objects.all()
        ),
        "questions": sorted(
            (g.slug, g.name, g.allows_several, g.sort_order) for g in TagGroup.objects.all()
        ),
    }


class DeployCommandTests(TestCase):
    """What runs against the database every time new code reaches it.

    `deploy/serve.sh` calls this on the way up, so everything here runs against
    production on every deployment and every restart. The tests that matter are
    therefore about what it does to data that is already there.
    """

    def test_it_brings_an_empty_database_up_with_a_usable_taxonomy(self):
        """The point of loading the fixture at all.

        Without it a fresh database serves a site with nothing on the filter bar
        and a registration form that offers no headings and asks no questions.
        """
        Category.objects.all().delete()
        call_command("deploy", stdout=StringIO())

        self.assertEqual(set(Category.objects.values_list("slug", flat=True)), set(q.EVERY))
        self.assertEqual(TagGroup.objects.count(), 4)
        self.assertEqual(Tag.objects.count(), 27)
        # Every question has answers offered to at least one heading, or it is a
        # question the registration form will never show.
        for group in TagGroup.objects.all():
            with self.subTest(question=group.slug):
                self.assertTrue(
                    any(tag.categories.exists() for tag in group.tags.all()),
                    f"{group.slug} has no answer offered under any heading",
                )

    def test_running_it_again_changes_nothing(self):
        """It runs on every restart, not just every deployment."""
        call_command("deploy", stdout=StringIO())
        before = taxonomy_snapshot()
        call_command("deploy", stdout=StringIO())
        self.assertEqual(taxonomy_snapshot(), before)

    def test_it_leaves_real_records_alone(self):
        """The check that makes this safe to run against the live site.

        `loaddata` inserts and updates the rows named in the fixture and deletes
        nothing, but "deletes nothing" is a claim worth holding a test against,
        because the command runs unattended against a database with other
        people's work in it.
        """
        call_command("deploy", stdout=StringIO())
        heading = Category.objects.get(slug=q.CO_OPS)
        program = Program.objects.create(
            name="Somebody's Co-op",
            slug="somebodys-co-op",
            short_description="Real work, entered by hand.",
            status=Program.Status.PUBLISHED,
            category=heading,
        )
        submission = Submission.objects.create(program_name="A registration")
        user = User.objects.create_user("editor", "editor@example.org", "x")
        # A tag she added herself, which the fixture has never heard of.
        mine = Tag.objects.create(name="Wheelchair accessible", slug="wheelchair-accessible")
        heading.tags.add(mine)

        call_command("deploy", stdout=StringIO())

        self.assertTrue(Program.objects.filter(pk=program.pk).exists())
        self.assertTrue(Submission.objects.filter(pk=submission.pk).exists())
        self.assertTrue(User.objects.filter(pk=user.pk).exists())
        self.assertTrue(Tag.objects.filter(pk=mine.pk).exists())

    def test_it_restores_a_heading_somebody_renamed(self):
        """The trade this design makes, written down as a test rather than a surprise.

        The fixture is the source of truth for the rows in it. An edit made in
        the admin to a heading's name, ordering or tag list is undone by the next
        deployment, so edits meant to last belong in the fixture.
        """
        call_command("deploy", stdout=StringIO())
        Category.objects.filter(slug=q.CO_OPS).update(name="Renamed in the admin")
        call_command("deploy", stdout=StringIO())
        self.assertEqual(Category.objects.get(slug=q.CO_OPS).name, "Co-ops")

    def test_skipping_fixtures_leaves_the_taxonomy_alone(self):
        call_command("deploy", "--skip-fixtures", stdout=StringIO())
        self.assertEqual(Category.objects.count(), 0)


class TaxonomyFixtureTests(TestCase):
    """The fixture is generated from `seed_taxonomy`, and this is what says so.

    Two files describing one vocabulary is the drift this whole codebase keeps
    trying to avoid. `seed_taxonomy` is where it is written and commented;
    `taxonomy.json` is what the deployment loads. If somebody edits one and not
    the other, the site starts deploying a taxonomy nobody wrote.
    """

    def test_the_fixture_and_the_seed_command_describe_the_same_taxonomy(self):
        call_command("seed_taxonomy", stdout=StringIO())
        seeded = taxonomy_snapshot()

        Category.objects.all().delete()
        Tag.objects.all().delete()
        TagGroup.objects.all().delete()
        call_command("loaddata", "taxonomy", verbosity=0)

        self.assertEqual(
            taxonomy_snapshot(),
            seeded,
            "The fixture and seed_taxonomy disagree. Regenerate the fixture — the "
            "command's docstring in directory/management/commands/deploy.py has the "
            "three lines that do it.",
        )

    def test_the_fixture_holds_exactly_the_headings_the_question_map_expects(self):
        call_command("loaddata", "taxonomy", verbosity=0)
        self.assertEqual(set(Category.objects.values_list("slug", flat=True)), set(q.EVERY))

    def test_every_heading_has_a_distinct_colour(self):
        """Chip colours come from the primary key, so the fixture decides them.

        Thirteen headings and thirteen colours only line up while the keys run
        1 to 13 without gaps, which is why the fixture is generated against an
        empty database rather than dumped from a working one.
        """
        call_command("loaddata", "taxonomy", verbosity=0)
        codes = [colour_code(c) for c in Category.objects.all()]
        self.assertEqual(len(set(codes)), len(codes))


class FixtureCollisionTests(TestCase):
    """Loading the baseline over a database that disagrees with it.

    Written after an outage. The fixture used to carry primary keys, so loading
    it over a database whose `co-ops` row sat on a different key raised
    `UNIQUE constraint failed: directory_category.slug` — the fixture writing a
    slug onto one key while another key already held it. Because `serve.sh` runs
    under `set -e` and `manage.py deploy` runs on the way up, the container
    exited, gunicorn never started, and the site went down.

    The rehearsal that missed it loaded the fixture into a *cleaned* database and
    proved only that the happy path worked. These load it over the mess.
    """

    def test_it_loads_over_a_matching_slug_on_a_different_primary_key(self):
        """The exact shape that took the site down."""
        # Occupy the low keys with rows whose slugs the fixture also uses, so a
        # key-based load would collide on every one of them.
        for n, slug in enumerate(["parent-support-groups", "co-ops", "field-trips"], start=1):
            Category.objects.create(pk=n, name=f"Old {slug}", slug=slug)

        call_command("deploy", stdout=StringIO())

        self.assertEqual(Category.objects.filter(slug=q.CO_OPS).count(), 1)
        self.assertEqual(Category.objects.get(slug=q.CO_OPS).name, "Co-ops")
        self.assertTrue(set(q.EVERY) <= set(Category.objects.values_list("slug", flat=True)))

    def test_it_loads_over_a_taxonomy_from_an_older_scheme(self):
        """Headings we no longer use are left alone, not collided with.

        Production carried twenty-four headings from the taxonomy before this
        one, eight of whose slugs the new set reuses. The deploy has to bring the
        thirteen up to date and leave the strangers where they are, rather than
        refusing to start.
        """
        older = ["book-clubs", "stem-programs", "dance", "co-ops", "microschools"]
        for n, slug in enumerate(older, start=1):
            Category.objects.create(pk=n, name=f"Old {slug}", slug=slug)

        call_command("deploy", stdout=StringIO())

        # The two the new scheme also names were updated in place.
        self.assertEqual(Category.objects.get(slug=q.CO_OPS).name, "Co-ops")
        self.assertEqual(Category.objects.get(slug=q.MICROSCHOOLS).sort_order, 30)
        # The three it does not name were left for somebody to tidy up by hand.
        for slug in ["book-clubs", "stem-programs", "dance"]:
            with self.subTest(slug=slug):
                self.assertTrue(Category.objects.filter(slug=slug).exists())

    def test_a_tag_keeps_its_question_when_the_tag_already_existed(self):
        """Tags and questions are matched by slug too, not only categories."""
        Tag.objects.create(pk=99, name="Something else entirely", slug="beginner")

        call_command("deploy", stdout=StringIO())

        tag = Tag.objects.get(slug="beginner")
        self.assertEqual(tag.name, "Beginner")
        self.assertEqual(tag.group.slug, "ability-level")

    def test_the_fixture_names_rows_by_slug_rather_than_by_key(self):
        """The property the three tests above depend on, asserted directly.

        Regenerating the fixture without `--natural-primary --natural-foreign`
        would put the keys back and re-arm the outage, and every test above would
        still pass on an empty database.
        """
        rows = json.loads(
            (settings.BASE_DIR / "src" / "directory" / "fixtures" / "taxonomy.json").read_text()
        )
        self.assertTrue(rows)
        for row in rows:
            with self.subTest(model=row["model"], slug=row["fields"].get("slug")):
                self.assertIsNone(row.get("pk"), "fixture rows must not carry primary keys")


class LocationFilterTests(TestCase):
    """Narrowing a listing to programs near a place.

    Points used throughout: DeLand, Deltona about nine miles south, Daytona
    Beach about twenty miles east, Jacksonville about ninety miles north.
    """

    DELAND = (29.03, -81.30)

    @classmethod
    def setUpTestData(cls):
        cls.deltona = cls._program("Deltona Club", "deltona", (28.90, -81.26))
        cls.daytona = cls._program("Daytona Club", "daytona", (29.21, -81.02))
        cls.far = cls._program("Jacksonville Club", "jacksonville", (30.33, -81.66))
        cls.online = cls._program("Online Club", "online", None)
        # Two places, one far and one near: the near one is what counts.
        cls.both = cls._program("Two Campus Club", "two-campus", (30.33, -81.66))
        cls.both.locations.create(query="DeLand", latitude=29.03, longitude=-81.30, sort_order=1)

    @classmethod
    def _program(cls, name, slug, point):
        program = Program.objects.create(
            name=name, slug=slug, short_description=f"{name}.", status=Program.Status.PUBLISHED
        )
        if point:
            program.locations.create(query=name, latitude=point[0], longitude=point[1])
        else:
            program.locations.create(query="At members' homes")
        return program

    def setUp(self):
        cache.clear()

    def _list(self, **params):
        return self.client.get(reverse("directory:program_list"), params)

    def _near_deland(self, **params):
        lat, lng = self.DELAND
        return self._list(near="DeLand", lat=lat, lng=lng, **params)

    def test_distance_is_measured_in_the_database_to_within_a_tenth_of_a_mile(self):
        lat, lng = self.DELAND
        found = Program.objects.near(lat, lng, 50).get(pk=self.daytona.pk)
        # The same haversine, worked in Python: what the SQL has to agree with.
        p1, p2 = math.radians(lat), math.radians(29.21)
        a = (
            math.sin((p2 - p1) / 2) ** 2
            + math.cos(p1) * math.cos(p2) * math.sin(math.radians(-81.02 - lng) / 2) ** 2
        )
        self.assertAlmostEqual(found.distance, 2 * 3958.8 * math.asin(math.sqrt(a)), delta=0.1)

    def test_only_programs_in_range_are_listed_nearest_first(self):
        with patch("directory.views.geocoding.search") as search:
            page = self._near_deland(within=25).content.decode()
        search.assert_not_called()
        nearby = page[: page.index("No fixed location")]
        self.assertNotIn("Jacksonville Club", nearby)
        self.assertLess(nearby.index("Two Campus Club"), nearby.index("Deltona Club"))
        self.assertLess(nearby.index("Deltona Club"), nearby.index("Daytona Club"))

    def test_a_narrower_distance_drops_the_further_program(self):
        page = self._near_deland(within=10).content.decode()
        self.assertIn("Deltona Club", page)
        self.assertNotIn("Daytona Club", page)

    def test_a_program_counts_from_its_nearest_place_and_appears_once(self):
        response = self._near_deland(within=5)
        self.assertContains(response, 'href="/programs/two-campus/"', count=1)
        self.assertContains(response, "0.0 mi")

    def test_programs_with_no_point_are_listed_separately_not_dropped(self):
        page = self._near_deland().content.decode()
        self.assertIn("No fixed location", page)
        self.assertGreater(page.index("Online Club"), page.index("No fixed location"))

    def test_without_a_place_the_listing_is_unchanged(self):
        page = self._list().content.decode()
        self.assertNotIn("No fixed location", page)
        self.assertNotIn(" mi</span>", page)
        for name in ["Deltona Club", "Jacksonville Club", "Online Club"]:
            self.assertIn(name, page)

    def test_coordinates_are_rounded_before_they_reach_the_page(self):
        response = self._list(near="Your location", lat="29.031234", lng="-81.303456")
        self.assertContains(response, 'data-lat="29.03"')
        self.assertContains(response, 'data-lng="-81.3"')
        self.assertNotContains(response, "29.0312")
        self.assertContains(response, "within 25 miles of you")

    def test_a_typed_place_is_looked_up_on_the_server_when_nothing_was_picked(self):
        found = geocoding.Place(label="DeLand, 32720", latitude=29.03, longitude=-81.30)
        with patch("directory.views.geocoding.search", return_value=[found]) as search:
            response = self._list(near="DeLand", within=10)
            self._list(near="deland", within=10)
        search.assert_called_once()  # the second, differently cased, came from the cache
        self.assertContains(response, "Deltona Club")
        self.assertNotContains(response, "Daytona Club")

    def test_a_geocoder_that_is_down_leaves_the_list_unfiltered_and_says_so(self):
        with patch("directory.views.geocoding.search", side_effect=geocoding.GeocoderUnavailable):
            response = self._list(near="DeLand")
        self.assertContains(response, "could not look up")
        self.assertContains(response, "Jacksonville Club")
        self.assertNotContains(response, "No fixed location")

    def test_a_place_nobody_can_find_says_so(self):
        with patch("directory.views.geocoding.search", return_value=[]):
            response = self._list(near="Atlantis")
        self.assertContains(response, "could not find")
        self.assertContains(response, "Jacksonville Club")

    def test_nonsense_coordinates_and_distances_are_ignored_rather_than_errors(self):
        with patch("directory.views.geocoding.search", return_value=[]):
            for params in [
                {"near": "x", "lat": "abc", "lng": "-81.3"},
                {"near": "x", "lat": "95", "lng": "-81.3"},
                {"near": "DeLand", "lat": "29.03", "lng": "-81.3", "within": "9999"},
                {"near": "DeLand", "lat": "29.03", "lng": "-81.3", "within": "far"},
            ]:
                with self.subTest(params=params):
                    self.assertEqual(self._list(**params).status_code, 200)
        self.assertContains(
            self._near_deland(within="9999"), "within 25 miles"
        )  # an unknown distance falls back to the default

    def test_it_composes_with_the_keyword_search(self):
        page = self._near_deland(q="club deltona").content.decode()
        self.assertIn("Deltona Club", page)
        self.assertNotIn("Daytona Club", page)

    def test_the_script_and_the_page_agree_on_their_hooks(self):
        js = (settings.BASE_DIR / "src" / "static" / "js" / "address-picker.js").read_text()
        page = self._list().content.decode()
        for hook in [
            "data-near-picker",
            "data-near-input",
            "data-near-locate",
            "data-near-status",
            "data-address-results",
        ]:
            with self.subTest(hook=hook):
                self.assertIn(hook, js)
                self.assertIn(hook, page)
        # Read through `dataset`, which spells the attribute in camel case.
        self.assertIn("data-your-location", page)
        self.assertIn("dataset.yourLocation", js)
        self.assertIn("address-picker.js", page)


class ListingSidebarTests(TestCase):
    """The sidebar and the bar above the results."""

    @classmethod
    def setUpTestData(cls):
        cls.category = Category.objects.create(name="Sports", slug=q.SPORTS)
        cls.tag = Tag.objects.create(name="Varsity", slug="varsity")
        Program.objects.create(
            name="Saturday League",
            slug="saturday",
            short_description="Soccer.",
            status=Program.Status.PUBLISHED,
            category=cls.category,
            meeting_days="sat",
        )

    def _chips(self, **params):
        response = self.client.get(reverse("directory:program_list"), params)
        return {chip["label"]: chip["remove_url"] for chip in response.context["active_chips"]}

    def test_each_applied_filter_gets_a_chip_that_removes_only_itself(self):
        chips = self._chips(day="sat", online="1", tag=["varsity"])
        self.assertEqual(set(chips), {"Meets Saturday", "Virtual options available", "Varsity"})
        self.assertNotIn("day=", chips["Meets Saturday"])
        self.assertIn("online=1", chips["Meets Saturday"])
        self.assertIn("tag=varsity", chips["Meets Saturday"])
        self.assertNotIn("tag=", chips["Varsity"])

    def test_each_day_gets_its_own_chip_that_removes_only_that_day(self):
        chips = self._chips(day=["sat", "tue"])
        # Week order, not the order they arrived in.
        self.assertEqual(list(chips), ["Meets Tuesday", "Meets Saturday"])
        self.assertIn("day=tue", chips["Meets Saturday"])
        self.assertNotIn("day=sat", chips["Meets Saturday"])

    def test_ticked_days_are_ticked_when_the_page_comes_back(self):
        response = self.client.get(reverse("directory:program_list"), {"day": ["sat", "tue"]})
        page = response.content.decode()
        for code, ticked in [("sat", True), ("tue", True), ("mon", False)]:
            with self.subTest(code=code):
                tag = page[page.index(f'name="day" value="{code}"') :].split(">", 1)[0]
                self.assertEqual("checked" in tag, ticked)

    def test_a_filter_that_was_ignored_gets_no_chip(self):
        self.assertEqual(self._chips(grade="college", day="someday", faith="maybe"), {})

    def test_switching_category_keeps_the_other_filters(self):
        response = self.client.get(
            reverse("directory:program_list"), {"day": "sat", "category": q.SPORTS}
        )
        self.assertEqual(response.context["keep_query"], "day=sat")
        self.assertContains(response, f'href="{self.category.get_absolute_url()}?day=sat"')

    def test_links_on_the_page_never_repeat_exact_coordinates(self):
        response = self.client.get(
            reverse("directory:program_list"),
            {"near": "Your location", "lat": "29.031234", "lng": "-81.303456", "day": "sat"},
        )
        self.assertNotContains(response, "29.0312")
        self.assertIn("lat=29.03", response.context["keep_query"])

    def test_the_count_and_the_empty_message_say_what_happened(self):
        response = self.client.get(reverse("directory:program_list"), {"day": "sat"})
        self.assertContains(response, "<strong>1</strong> program")
        response = self.client.get(reverse("directory:program_list"), {"day": "mon"})
        self.assertContains(response, "Nothing matches. Try removing a filter.")

    def test_without_a_script_there_is_still_a_button_to_apply_the_filters(self):
        response = self.client.get(reverse("directory:program_list"))
        self.assertContains(response, "Apply filters")
        self.assertContains(response, "listing-filters.js")


class CategoryFoldTests(TestCase):
    """Five categories show; the rest fold, unless the current one is folded."""

    @classmethod
    def setUpTestData(cls):
        cls.categories = [
            Category.objects.create(name=f"Heading {n:02}", slug=f"heading-{n}", sort_order=n)
            for n in range(8)
        ]

    def test_only_five_show_before_the_fold(self):
        page = self.client.get(reverse("directory:program_list")).content.decode()
        for n in range(8):
            with self.subTest(n=n):
                item = page[: page.index(f"Heading {n:02}")].rsplit("<li", 1)[1]
                self.assertEqual("fold-extra" in item, n >= 5)
        self.assertIn('data-more="Show 3 more"', page)
        self.assertNotIn("data-fold-open", page)

    def test_the_toggle_sits_under_the_whole_list(self):
        page = self.client.get(reverse("directory:program_list")).content.decode()
        self.assertGreater(page.index("data-fold-toggle"), page.index("Heading 07"))

    def test_the_fold_starts_open_when_the_current_category_is_inside_it(self):
        page = self.client.get(self.categories[6].get_absolute_url()).content.decode()
        self.assertIn("data-fold-open", page)

    def test_the_script_and_the_page_agree_on_the_fold(self):
        js = (settings.BASE_DIR / "src" / "static" / "js" / "listing-filters.js").read_text()
        css = (settings.BASE_DIR / "src" / "static" / "css" / "site.css").read_text()
        for hook in ["[data-fold]", "data-fold-toggle", "data-fold-open", "is-folded"]:
            with self.subTest(hook=hook):
                self.assertIn(hook, js)
        self.assertIn("[data-fold].is-folded .fold-extra", css)

    def test_there_is_no_affiliation_filter_but_old_links_still_work(self):
        response = self.client.get(reverse("directory:program_list"), {"faith": "faith"})
        self.assertNotContains(response, 'name="faith"')
        self.assertEqual(response.status_code, 200)


class GradeLevelTests(TestCase):
    """Grade levels: stored on the program, filtered on, and shown back."""

    @classmethod
    def setUpTestData(cls):
        cls.program = Program.objects.create(
            name="Coastal Co-op",
            slug="coastal",
            short_description="A co-op.",
            status=Program.Status.PUBLISHED,
            grade_levels=["middle", "elementary"],  # stored in level order regardless
        )

    def test_levels_are_stored_in_order_and_read_as_a_family_would(self):
        self.program.refresh_from_db()
        self.assertEqual(self.program.grade_levels, "elementary,middle")
        self.assertEqual(self.program.grade_levels_display, "Elementary (K–5), Middle school (6–8)")

    def test_the_program_page_shows_the_levels(self):
        response = self.client.get(reverse("directory:program", kwargs={"slug": "coastal"}))
        self.assertContains(response, "Elementary (K–5), Middle school (6–8)")

    def test_each_ticked_level_gets_a_chip_and_a_ticked_box(self):
        response = self.client.get(reverse("directory:program_list"), {"grade": ["high", "prek"]})
        labels = [chip["label"] for chip in response.context["active_chips"]]
        self.assertEqual(labels, ["Pre-K", "High school (9–12)"])
        page = response.content.decode()
        tag = page[page.index('name="grade" value="high"') :].split(">", 1)[0]
        self.assertIn("checked", tag)

    def test_no_code_is_a_substring_of_another(self):
        """What keeps `grade_levels__contains` an exact test. See `CodeListField`."""
        codes = [code for code, _ in GRADE_LEVELS]
        for code in codes:
            for other in codes:
                if code != other:
                    self.assertNotIn(code, other)


class GradeLevelConversionTests(SimpleTestCase):
    """The migration that turned free-text grades and ages into levels.

    It runs once, on production data nobody has read line by line, so the rules
    are worth pinning down — especially the edges where an age belongs to two
    grades.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.m = importlib.import_module("directory.migrations.0015_grade_levels")

    def _text(self, text):
        return [level for level in self.m.LEVELS if level in self.m.levels_from_text(text)]

    def _ages(self, youngest, oldest):
        return [
            level for level in self.m.LEVELS if level in self.m.levels_from_ages(youngest, oldest)
        ]

    def test_the_usual_ways_of_writing_grades(self):
        for text, expected in [
            ("K-5", ["elementary"]),
            ("K–8", ["elementary", "middle"]),
            ("grades 3-8", ["elementary", "middle"]),
            ("grades 6-12", ["middle", "high"]),
            ("high school only", ["high"]),
            ("Pre-K through 2nd", ["prek", "elementary"]),
            ("3rd–8th grade", ["elementary", "middle"]),
            ("grade 7", ["middle"]),
            ("middle and high school", ["middle", "high"]),
            ("VPK", ["prek"]),
            ("all ages", ["prek", "elementary", "middle", "high"]),
        ]:
            with self.subTest(text=text):
                self.assertEqual(self._text(text), expected)

    def test_text_that_is_not_grades_names_nothing(self):
        for text in ["varies by class", "teens", "", None]:
            with self.subTest(text=text):
                self.assertEqual(self._text(text), [])

    def test_ages_at_a_boundary_do_not_spill_into_the_next_level(self):
        """An elementary program listed as ages 5–11 is not a middle school one."""
        self.assertEqual(self._ages(5, 11), ["elementary"])
        self.assertEqual(self._ages(5, 14), ["elementary", "middle"])
        self.assertEqual(self._ages(11, 18), ["middle", "high"])
        self.assertEqual(self._ages(14, 18), ["high"])
        self.assertEqual(self._ages(3, 5), ["prek"])
        self.assertEqual(self._ages(None, None), [])


class GradeTermSearchTests(TestCase):
    """Searching with a grade word finds programs by the levels they ticked."""

    @classmethod
    def setUpTestData(cls):
        def program(name, levels, description="We meet weekly."):
            return Program.objects.create(
                name=name,
                slug=name.lower().replace(" ", "-"),
                short_description=description,
                status=Program.Status.PUBLISHED,
                grade_levels=levels,
            )

        program("Little Sprouts", "prek")
        program("Brick Builders", "elementary")
        program("Junior Debate", "middle")
        program("Varsity Rowing", "high")
        program("Unsure Club", "")
        program("Says It Club", "", description="Open to elementary students.")
        program("Soccer Stars", "elementary", description="Soccer for younger players.")

    def _found(self, query):
        response = self.client.get(reverse("directory:program_list"), {"q": query})
        return sorted(program.name for program in response.context["programs"])

    def test_a_grade_word_finds_programs_that_never_wrote_it(self):
        self.assertIn("Brick Builders", self._found("elementary"))
        self.assertEqual(self._found("preschool"), ["Little Sprouts"])
        self.assertEqual(self._found("pre-k"), ["Little Sprouts"])
        self.assertEqual(self._found("pre k"), ["Little Sprouts"])

    def test_the_text_still_counts(self):
        """A program with no levels that says "elementary" is still found by it."""
        self.assertIn("Says It Club", self._found("elementary"))

    def test_a_program_with_no_levels_is_not_found_by_a_grade_word(self):
        for term in ["elementary", "teens", "middle school", "preschool"]:
            with self.subTest(term=term):
                self.assertNotIn("Unsure Club", self._found(term))

    def test_two_word_terms_are_read_as_one(self):
        self.assertEqual(self._found("high school"), ["Varsity Rowing"])
        self.assertEqual(self._found("middle school"), ["Junior Debate"])

    def test_high_on_its_own_is_an_ordinary_word(self):
        """ "High" alone is in "highlights" and "high-energy", so it means nothing
        about grades. Varsity Rowing never wrote it."""
        self.assertNotIn("Varsity Rowing", self._found("high"))

    def test_tweens_and_teens_straddle_two_levels(self):
        self.assertEqual(self._found("tweens"), ["Brick Builders", "Junior Debate", "Soccer Stars"])
        self.assertEqual(self._found("teens"), ["Junior Debate", "Varsity Rowing"])

    def test_the_other_words_still_have_to_match(self):
        self.assertEqual(self._found("elementary soccer"), ["Soccer Stars"])
        self.assertEqual(self._found("the elementary classes"), self._found("elementary"))

    def test_every_term_points_at_real_levels(self):
        codes = {code for code, _ in GRADE_LEVELS}
        for term, levels in GRADE_SEARCH_TERMS.items():
            with self.subTest(term=term):
                self.assertTrue(levels)
                self.assertLessEqual(set(levels), codes)
                self.assertLessEqual(len(term.split()), 2)


class VirtualOptionsFilterTests(TestCase):
    """The "Virtual options available" filter, offered only where it means something."""

    OFFERED = [
        q.CORE_ACADEMICS,
        q.TESTING,
        q.SPECIAL_NEEDS,
        q.TUTORING,
        q.PARENT_SUPPORT,
        q.ENRICHMENT,
    ]

    @classmethod
    def setUpTestData(cls):
        cls.categories = {
            slug: Category.objects.create(name=slug.title(), slug=slug) for slug in q.EVERY
        }
        cls.tag = Tag.objects.create(name="Lego", slug="lego")
        for slug, delivery in [
            (q.TUTORING, Program.Delivery.ONLINE),
            (q.TUTORING, Program.Delivery.IN_PERSON),
            (q.CO_OPS, Program.Delivery.HYBRID),
        ]:
            Program.objects.create(
                name=f"{slug} {delivery}",
                slug=f"{slug}-{delivery}",
                short_description="A program.",
                status=Program.Status.PUBLISHED,
                category=cls.categories[slug],
                delivery=delivery,
            )

    def _page(self, url, **params):
        return self.client.get(url, params)

    def test_the_six_categories_and_the_all_programs_page_offer_it(self):
        for slug in self.OFFERED:
            with self.subTest(slug=slug):
                response = self._page(self.categories[slug].get_absolute_url())
                self.assertContains(response, "Virtual options available")
        self.assertContains(
            self._page(reverse("directory:program_list")), "Virtual options available"
        )

    def test_the_other_categories_and_tag_pages_do_not(self):
        for slug in sorted(q.EVERY - set(self.OFFERED)):
            with self.subTest(slug=slug):
                response = self._page(self.categories[slug].get_absolute_url())
                self.assertNotContains(response, 'name="online"')
        self.assertNotContains(self._page(self.tag.get_absolute_url()), 'name="online"')

    def test_the_question_is_asked_exactly_where_the_filter_is_offered(self):
        """A filter on a heading that is never asked the question would always
        come back empty."""
        self.assertEqual(q.headings_asking("delivery"), frozenset(self.OFFERED))

    def test_it_still_filters_where_it_is_offered(self):
        response = self._page(self.categories[q.TUTORING].get_absolute_url(), online="1")
        names = [program.name for program in response.context["programs"]]
        self.assertEqual(names, [f"{q.TUTORING} online"])

    def test_an_old_link_cannot_filter_by_a_control_the_page_does_not_show(self):
        response = self._page(self.categories[q.CO_OPS].get_absolute_url(), online="1")
        self.assertFalse(response.context["filters"]["online"])
        self.assertEqual(response.context["active_chips"], [])
        self.assertEqual(len(response.context["programs"]), 1)

    def test_the_wording_says_virtual_everywhere(self):
        self.assertEqual(Program.Delivery.ONLINE.label, "Virtual")
        self.assertEqual(Program.Delivery.HYBRID.label, "In person or virtual")


class InstructorWordingTests(TestCase):
    def test_the_program_page_heading_covers_leaders_as_well_as_teachers(self):
        Program.objects.create(
            name="Tide Pool Club",
            slug="tide-pool",
            short_description="A club.",
            status=Program.Status.PUBLISHED,
            instructor_info="Run by a marine biologist.",
        )
        response = self.client.get(reverse("directory:program", kwargs={"slug": "tide-pool"}))
        self.assertContains(response, "<h2>Teacher qualifications / leader bio</h2>", html=True)
        self.assertNotContains(response, "Who teaches")

    def test_the_form_label_matches_the_page(self):
        from .forms import ProgramRegistrationForm

        label = ProgramRegistrationForm().fields["instructor_info"].label
        self.assertEqual(label, "Teacher qualifications / leader bio")


class StepUpScholarshipTests(TestCase):
    """A direct pay provider has to name PEP, FES-UA or both (#6)."""

    def _program(self, **step_up):
        return Program(
            name="Coastal Co-op",
            slug="coastal",
            short_description="A co-op.",
            **step_up,
        )

    def test_direct_pay_alone_is_refused(self):
        with self.assertRaises(ValidationError) as caught:
            self._program(step_up_direct_pay=True).full_clean()
        self.assertIn("step_up_direct_pay", caught.exception.message_dict)

    def test_naming_either_scholarship_or_both_is_enough(self):
        for step_up in [
            {"step_up_pep": True},
            {"step_up_fes_ua": True},
            {"step_up_pep": True, "step_up_fes_ua": True},
        ]:
            with self.subTest(step_up=step_up):
                self._program(step_up_direct_pay=True, **step_up).full_clean()

    def test_not_being_a_provider_needs_no_scholarship(self):
        self._program().full_clean()


class ParentInvolvementTests(TestCase):
    """The parent involvement question: several answers, one pair refused (#10)."""

    @classmethod
    def setUpTestData(cls):
        call_command("loaddata", "taxonomy", verbosity=0)
        cls.co_ops = Category.objects.get(slug=q.CO_OPS)
        cls.sports = Category.objects.get(slug=q.SPORTS)
        cls.group = TagGroup.objects.get(slug="parent-involvement")

    def _answers(self, category):
        return sorted(category.tags.filter(group=self.group).values_list("slug", flat=True))

    def _payload(self, category, *answers):
        tags = {tag.slug: tag.pk for tag in Tag.objects.filter(slug__in=answers)}
        return {
            "program_name": "Coastal Club",
            "short_description": "A club.",
            "description": "We meet weekly.",
            "category": str(category.pk),
            "email": "hello@coastal.example.org",
            "locations": "12 Ocean Ave, Ormond Beach 32176",
            "grade_levels": ["elementary"],
            "question_parent_involvement": [str(tags[slug]) for slug in answers],
            "submitter_name": "Dana Reed",
            "submitter_email": "dana@coastal.example.org",
            "is_authorized": "on",
            "website_url": "",
        }

    def test_the_question_takes_several_answers(self):
        self.assertTrue(self.group.allows_several)

    def test_classes_and_co_ops_are_offered_their_own_answers(self):
        self.assertEqual(
            self._answers(self.sports),
            [
                "drop-off",
                "parents-help-or-teach",
                "parents-remain-on-site",
                "parents-welcome-to-stay",
            ],
        )
        self.assertEqual(
            self._answers(self.co_ops),
            ["parents-help-or-teach", "parents-remain-on-site"],
        )

    def test_none_of_the_old_answers_survive(self):
        old = ["drop-off-only", "families-stay-all-day", "parents-must-attend"]
        self.assertFalse(Tag.objects.filter(slug__in=old).exists())

    def test_answers_that_go_together_are_stored_together(self):
        response = self.client.post(
            reverse("directory:register"),
            self._payload(self.sports, "drop-off", "parents-welcome-to-stay"),
        )
        self.assertRedirects(response, reverse("directory:register_thanks"))
        stored = set(Submission.objects.get().tags.values_list("slug", flat=True))
        self.assertEqual(stored, {"drop-off", "parents-welcome-to-stay"})

    def test_drop_off_and_remaining_on_site_together_are_sent_back(self):
        response = self.client.post(
            reverse("directory:register"),
            self._payload(self.sports, "drop-off", "parents-remain-on-site"),
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Submission.objects.count(), 0)
        self.assertContains(response, "a program can’t be both")
        self.assertIn("question_parent_involvement", response.context["form"].errors)


class RetireParentInvolvementMigrationTests(TestCase):
    """Migration 0018 removes exactly the seven answers the new five replace."""

    def test_it_removes_the_old_answers_and_nothing_else(self):
        migration = importlib.import_module(
            "directory.migrations.0018_retire_parent_involvement_tags"
        )
        for slug in [*migration.RETIRED, "drop-off", "lego"]:
            Tag.objects.create(name=slug, slug=slug)
        migration.retire(django_apps, None)
        self.assertEqual(sorted(Tag.objects.values_list("slug", flat=True)), ["drop-off", "lego"])


class FoldHighlightsMigrationTests(SimpleTestCase):
    """Migration 0019 keeps what people wrote in "What makes it special" (#18).

    The column is gone from the current models, so this runs the migration's
    function against stand-ins rather than the real historical models.
    """

    class _Row:
        def __init__(self, description, highlights):
            self.description, self.highlights, self.saved = description, highlights, None

        def save(self, update_fields):
            self.saved = update_fields

    def _run(self, programs, submissions):
        migration = importlib.import_module(
            "directory.migrations.0019_fold_highlights_into_description"
        )

        def model(rows):
            manager = SimpleNamespace(
                exclude=lambda highlights: [r for r in rows if r.highlights != highlights]
            )
            return SimpleNamespace(objects=manager)

        models_by_name = {"Program": model(programs), "Submission": model(submissions)}
        apps = SimpleNamespace(get_model=lambda app, name: models_by_name[name])
        migration.fold_highlights(apps, None)

    def test_program_highlights_become_escaped_paragraphs(self):
        program = self._Row("<p>We meet weekly.</p>", "Small <b>groups</b>.\n\nSpring show.")
        self._run([program], [])
        self.assertEqual(
            program.description,
            "<p>We meet weekly.</p><p>Small &lt;b&gt;groups&lt;/b&gt;.</p><p>Spring show.</p>",
        )

    def test_submission_highlights_follow_a_blank_line(self):
        submission = self._Row("We meet weekly.\n", "Small groups.")
        empty = self._Row("", "Only highlights.")
        self._run([], [submission, empty])
        self.assertEqual(submission.description, "We meet weekly.\n\nSmall groups.")
        self.assertEqual(empty.description, "Only highlights.")

    def test_rows_without_highlights_are_left_alone(self):
        program = self._Row("<p>Unchanged.</p>", "")
        self._run([program], [])
        self.assertIsNone(program.saved)


class DescriptionWordingTests(SimpleTestCase):
    """The PO's wording for the full description, and no separate highlights (#17, #18)."""

    def test_the_description_hint_uses_the_product_owners_wording(self):
        from .forms import ProgramRegistrationForm

        self.assertIn(
            "What you do and the benefits of your program — e.g. what a typical session "
            "looks like, and what makes it special. Leave a blank line between paragraphs.",
            str(ProgramRegistrationForm.base_fields["description"].help_text),
        )

    def test_highlights_is_not_on_the_form(self):
        from .forms import ProgramRegistrationForm

        self.assertNotIn("highlights", ProgramRegistrationForm.base_fields)


class RetirePartOfTheDayMigrationTests(TestCase):
    """Migration 0020 removes "Part of the day" and nothing else (#19)."""

    def test_it_removes_only_part_of_the_day(self):
        migration = importlib.import_module("directory.migrations.0020_retire_part_of_the_day")
        for slug in ["part-of-the-day", "parents-remain-on-site"]:
            Tag.objects.get_or_create(slug=slug, defaults={"name": slug})
        migration.retire(django_apps, None)
        self.assertFalse(Tag.objects.filter(slug="part-of-the-day").exists())
        self.assertTrue(Tag.objects.filter(slug="parents-remain-on-site").exists())


class RegistrationIntroWordingTests(TestCase):
    """The basics has no hint about the heading, and the Category hint has no
    "also" (#22, #23)."""

    def test_the_basics_has_no_hint_about_the_heading(self):
        response = self.client.get(reverse("directory:register"))
        self.assertNotContains(response, "Start with the heading")

    def test_the_category_hint_drops_also(self):
        from .forms import ProgramRegistrationForm

        help_text = str(ProgramRegistrationForm.base_fields["category"].help_text)
        self.assertTrue(help_text.startswith("The one heading that fits best. It decides "))


class RegistrationExampleTextTests(TestCase):
    """Every free-text field on the registration form shows an example (#8)."""

    def test_every_text_field_gives_an_example(self):
        from .forms import ProgramRegistrationForm

        form = ProgramRegistrationForm()
        text_widgets = (
            forms_module.TextInput,
            forms_module.Textarea,
            forms_module.URLInput,
            forms_module.EmailInput,
        )
        # Dates and times are pickers, not typing. The contact email says what it
        # is for instead; an example address would add nothing.
        exempt = (forms_module.DateInput, forms_module.TimeInput)
        for name, field in form.fields.items():
            if name == "submitter_email" or isinstance(field.widget, exempt):
                continue
            if isinstance(field.widget, text_widgets) and not field.widget.is_hidden:
                with self.subTest(field=name):
                    self.assertIn("e.g.", str(field.help_text))

    def test_the_product_owners_examples_are_used(self):
        from .forms import ProgramRegistrationForm

        fields = ProgramRegistrationForm().fields
        self.assertIn("Beginners Spanish", fields["program_name"].help_text)
        self.assertIn("Learning with Patty", fields["host_name"].help_text)

    def test_no_hint_talks_about_the_provider_in_the_third_person(self):
        """Model help text is written for us in the admin ("Tick if they come to
        the family"); on the public form it has to speak to the provider."""
        from .forms import ProgramRegistrationForm

        for name in ["delivery", "travels_to_student_home", "submitter_role"]:
            with self.subTest(field=name):
                hint = str(ProgramRegistrationForm().fields[name].help_text)
                self.assertNotIn("they", hint.split())
                self.assertFalse(hint.startswith("Their"))


class GoogleAnalyticsTests(TestCase):
    TAG = "googletagmanager.com/gtag/js"

    def test_no_id_means_no_tag(self):
        response = self.client.get(reverse("directory:home"))
        self.assertNotContains(response, self.TAG)

    @override_settings(GOOGLE_ANALYTICS_ID="G-TEST12345")
    def test_an_id_adds_the_tag_to_public_pages(self):
        response = self.client.get(reverse("directory:home"))
        self.assertContains(response, self.TAG)
        # Escaped for a JavaScript string, which JavaScript reads back as written.
        self.assertContains(response, f'var id = "{escapejs("G-TEST12345")}"')
        self.assertContains(response, 'gtag("config", id')

    @override_settings(GOOGLE_ANALYTICS_ID="G-TEST12345")
    def test_the_script_waits_for_the_page_to_load(self):
        """Not a <script src> in the head, which the browser would fetch while
        the page is still loading (#15), but added once it has finished."""
        page = self.client.get(reverse("directory:home")).content.decode()
        self.assertNotIn(f'<script async src="https://www.{self.TAG}', page)
        self.assertIn('addEventListener("load", whenIdle', page)
        self.assertIn("requestIdleCallback", page)

    @override_settings(GOOGLE_ANALYTICS_ID="G-TEST12345")
    def test_the_reported_address_leaves_out_the_coordinates(self):
        response = self.client.get(reverse("directory:program_list"))
        self.assertContains(response, 'searchParams.delete("lat")')
        self.assertContains(response, 'searchParams.delete("lng")')
        self.assertContains(response, "page_location")

    @override_settings(GOOGLE_ANALYTICS_ID="G-TEST12345")
    def test_the_admin_never_loads_it(self):
        response = self.client.get(reverse("admin:login"))
        self.assertNotContains(response, self.TAG)

    def test_a_misshapen_id_is_a_warning_not_a_tag(self):
        from .checks import google_analytics_id

        with override_settings(GOOGLE_ANALYTICS_ID_RAW="UA-12345-1", GOOGLE_ANALYTICS_ID=""):
            warnings = google_analytics_id(None)
        self.assertEqual([w.id for w in warnings], ["directory.W001"])
        with override_settings(
            GOOGLE_ANALYTICS_ID_RAW="G-TEST12345", GOOGLE_ANALYTICS_ID="G-TEST12345"
        ):
            self.assertEqual(google_analytics_id(None), [])


class MinifiedStaticFilesTests(TestCase):
    """Production's static storage minifies our CSS before hashing it (#16)."""

    def test_our_css_is_minified_and_compressed_and_other_css_is_left_alone(self):
        source = Path(tempfile.mkdtemp())
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, source)
        self.addCleanup(shutil.rmtree, root)
        (source / "css").mkdir()
        # Enough rules that compressing is worth it: WhiteNoise skips writing a
        # .gz or .br that would not be smaller than the file.
        rules = "".join(
            f"/* Why card {n} looks the way it does. */\n.card-{n} {{\n  color: red;\n}}\n"
            for n in range(60)
        )
        (source / "css" / "ours.css").write_text(rules)
        # Our folder only: collecting every installed app's files as well takes
        # seconds and tests nothing more.
        with override_settings(
            STATICFILES_DIRS=[source],
            STATICFILES_FINDERS=["django.contrib.staticfiles.finders.FileSystemFinder"],
            STATIC_ROOT=root,
            STORAGES={
                **settings.STORAGES,
                "staticfiles": {
                    "BACKEND": "volusiahd.storage.MinifiedCompressedManifestStaticFilesStorage"
                },
            },
        ):
            call_command("collectstatic", interactive=False, verbosity=0)

        manifest = json.loads((root / "staticfiles.json").read_text())["paths"]
        served = root / manifest["css/ours.css"]
        self.assertEqual(served.read_text(), "".join(f".card-{n}{{color:red}}" for n in range(60)))
        self.assertTrue(served.with_name(served.name + ".gz").exists())
        self.assertTrue(served.with_name(served.name + ".br").exists())

    def test_only_files_from_our_own_folders_count_as_ours(self):
        """The admin, Unfold and the prose editor ship their own CSS, which is
        not ours to rewrite."""
        from django.core.files.storage import FileSystemStorage

        from volusiahd.storage import MinifiedCompressedManifestStaticFilesStorage as storage

        admin = Path(django_apps.get_app_config("admin").path) / "static"
        self.assertTrue(storage._is_ours(FileSystemStorage(settings.STATICFILES_DIRS[0])))
        self.assertFalse(storage._is_ours(FileSystemStorage(admin)))
