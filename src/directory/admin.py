"""Admin configuration.

Phase 4 of the plan treats the admin as a real deliverable rather than a
cleanup pass. The test of this file is that someone who has never seen the
codebase can add a program without asking a question.
"""

from pathlib import Path

from django import forms
from django.contrib import admin, messages
from django.contrib.auth.models import Group
from django.core.files.base import ContentFile
from django.db.models import Count
from django.urls import reverse_lazy
from django.utils import timezone
from django.utils.html import format_html, format_html_join
from django.utils.safestring import mark_safe
from django.utils.text import slugify
from unfold.admin import ModelAdmin, TabularInline
from unfold.decorators import action, display

from .addresses import DEBOUNCE_MS, MIN_QUERY_CHARS, parse_place
from .models import (
    Category,
    ContactPerson,
    Page,
    Program,
    ProgramLocation,
    Submission,
    Tag,
)

# She will never touch Groups. Site-wide permissions are not a thing we use.
admin.site.unregister(Group)

admin.site.site_title = "Homeschool Directory"
admin.site.site_header = "Volusia County Homeschool Directory"
admin.site.index_title = "Records"


def _addresses(count):
    """Count of addresses, pluralised: 1 address, 3 addresses."""
    return f"{count} address{'es' if count != 1 else ''}"


def pending_submission_count(request):
    """Sidebar badge: how many submissions are waiting on her."""
    count = Submission.objects.filter(status=Submission.Status.NEW).count()
    return count or None


class ProgramLocationForm(forms.ModelForm):
    """The inline row, with the address picker on its address box.

    Typing in `query` offers suggestions and picking one fills the row. The
    hidden `picked` field carries the whole match — including the city and
    postcode, which have no column here and would otherwise be lost for no better
    reason than that the table is already wide enough.

    Nothing here is required for the row to work. An editor who types an address
    and saves gets the same server-side lookup as before, which is also what
    happens when the script does not run.
    """

    picked = forms.CharField(required=False, widget=forms.HiddenInput)

    class Meta:
        model = ProgramLocation
        fields = ["query", "label", "latitude", "longitude", "sort_order"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["query"].widget.attrs.update(
            {
                "data-address-row": "",
                "data-endpoint": str(reverse_lazy("directory:address_search")),
                "data-min-chars": MIN_QUERY_CHARS,
                "data-debounce": DEBOUNCE_MS,
                "autocomplete": "off",
            }
        )

    def _post_clean(self):
        # After super(), because `construct_instance` has just written the
        # visible columns onto the instance and a pick has to win over them —
        # it is the newer answer, and the one the editor actually chose.
        super()._post_clean()
        raw = self.cleaned_data.get("picked")
        if not raw or not self.cleaned_data.get("query"):
            return
        try:
            place = parse_place(raw)
        except forms.ValidationError as error:
            self.add_error(None, error)
            return
        for field, value in place.items():
            setattr(self.instance, field, value)
        # The editor's own wording for the place, not the geocoder's.
        self.instance.query = self.cleaned_data["query"]


class ProgramLocationInline(TabularInline):
    """Where a program meets, one row per place.

    `query` is the only column an editor normally types into: pick a suggestion
    and the rest fills itself in, or save and the server looks it up. The
    resolved columns stay editable anyway, because a geocoder that has put a
    program on the wrong side of town is a thing an editor with a map can fix in
    ten seconds and cannot fix at all through a read-only field.
    """

    model = ProgramLocation
    form = ProgramLocationForm
    extra = 1
    fields = ["query", "label", "latitude", "longitude", "sort_order", "picked"]
    verbose_name = "meeting place"
    verbose_name_plural = "Where this program meets"

    class Media:
        css = {"all": ["css/address-picker.css"]}
        js = ["js/address-picker.js"]


class ContactPersonInline(TabularInline):
    model = ContactPerson
    extra = 0
    fields = ["name", "role", "email", "phone"]
    verbose_name = "contact"
    verbose_name_plural = "Who to call about this program (never published)"


@admin.register(Program)
class ProgramAdmin(ModelAdmin):
    inlines = [ProgramLocationInline, ContactPersonInline]
    prepopulated_fields = {"slug": ["name"]}
    list_display = [
        "name",
        "category",
        "located_display",
        "status",
        "verified_display",
        "is_featured",
    ]
    list_display_links = ["name"]
    list_editable = ["status", "is_featured"]
    list_filter = ["status", "category", "is_featured", "step_up_direct_pay"]
    list_per_page = 50
    search_fields = [
        "name",
        "short_description",
        "locations__query",
        "locations__label",
        "locations__city",
        "tags__name",
        "contacts__name",
    ]
    # One category is a dropdown. Tags are not: there will be hundreds, so they
    # get a search box rather than a wall of checkboxes.
    autocomplete_fields = ["tags"]
    date_hierarchy = "created_at"
    readonly_fields = ["created_at", "updated_at"]
    actions = ["look_up_addresses", "mark_verified_today", "publish_selected"]

    fieldsets = [
        (
            None,
            {
                "fields": ["name", "slug", "status", "is_featured", "category", "tags"],
            },
        ),
        (
            "Description",
            {
                "fields": ["short_description", "description", "logo"],
            },
        ),
        (
            "Contact",
            {
                "fields": ["website", "facebook", "email", "phone"],
            },
        ),
        (
            "Who it serves",
            {
                "fields": ["serves_grades", ("age_min", "age_max")],
            },
        ),
        (
            "Practical details",
            {
                "fields": ["cost_notes", "meeting_schedule"],
            },
        ),
        (
            "Step Up For Students",
            {
                "fields": ["step_up_direct_pay", ("step_up_pep", "step_up_fes_ua")],
                "description": "Whether families can pay this program directly from "
                "their scholarship account through the EMA marketplace.",
            },
        ),
        (
            "Record keeping",
            {
                "fields": ["last_verified_on", "created_at", "updated_at"],
            },
        ),
    ]

    def get_queryset(self, request):
        return (
            super().get_queryset(request).select_related("category").prefetch_related("locations")
        )

    def get_search_results(self, request, queryset, search_term):
        # Searching a joined m2m duplicates rows. Without this, one program
        # carrying three matching tags shows up three times in the changelist.
        queryset, _ = super().get_search_results(request, queryset, search_term)
        return queryset.distinct(), False

    def save_related(self, request, form, formsets, change):
        """Look up any address that still needs it, as part of the save.

        This is the only place a geocoder is called inside a request, and it is
        an admin request on purpose: somebody is sitting there, the wait is about
        a second per new address, and the result is on screen before they
        navigate away. No public page ever calls out to anybody.
        """
        super().save_related(request, form, formsets, change)
        self._look_up(request, form.instance.locations.pending())

    def _look_up(self, request, locations, limit=25):
        """Resolve `locations` and tell the editor what happened.

        Bounded, because an editor who selects two hundred programs should not
        be left watching a spinner while we work politely through a free
        service. `manage.py geocode` is the path for that, and it says so.
        """
        pending = list(locations[:limit])
        if not pending:
            return
        resolved, missed, gave_up = ProgramLocation.objects.filter(
            pk__in=[location.pk for location in pending]
        ).look_up()

        if resolved:
            self.message_user(request, f"Put {_addresses(resolved)} on the map.", messages.SUCCESS)
        if missed:
            self.message_user(
                request,
                f"Could not match {_addresses(missed)}. Reword it, or fill in the "
                "latitude and longitude by hand.",
                messages.WARNING,
            )
        if gave_up:
            self.message_user(
                request,
                "The address lookup service could not be reached, so some addresses are "
                "still waiting. Select the program and run “Look up addresses” later.",
                messages.WARNING,
            )

    @action(description="Look up addresses")
    def look_up_addresses(self, request, queryset):
        self._look_up(
            request,
            ProgramLocation.objects.filter(program__in=queryset).exclude(
                status=ProgramLocation.Status.RESOLVED
            ),
        )

    @display(description="On the map")
    def located_display(self, obj):
        locations = list(obj.locations.all())
        if not locations:
            return "—"
        placed = sum(1 for location in locations if location.has_point)
        colour = "#166534" if placed == len(locations) else "#b45309"
        return format_html('<span style="color:{}">{} of {}</span>', colour, placed, len(locations))

    @display(description="Last verified", ordering="last_verified_on")
    def verified_display(self, obj):
        if not obj.last_verified_on:
            return format_html('<span style="color:#b45309">never</span>')
        days = (timezone.localdate() - obj.last_verified_on).days
        color = "#b45309" if days > 365 else "#166534"
        return format_html(
            '<span style="color:{}">{}</span>', color, obj.last_verified_on.strftime("%b %d, %Y")
        )

    @action(description="Mark as verified today")
    def mark_verified_today(self, request, queryset):
        updated = queryset.update(last_verified_on=timezone.localdate())
        self.message_user(
            request,
            f"Marked {updated} program{'s' if updated != 1 else ''} as verified today.",
            messages.SUCCESS,
        )

    @action(description="Publish")
    def publish_selected(self, request, queryset):
        updated = queryset.update(status=Program.Status.PUBLISHED)
        self.message_user(
            request,
            f"Published {updated} program{'s' if updated != 1 else ''}.",
            messages.SUCCESS,
        )


@admin.register(Category)
class CategoryAdmin(ModelAdmin):
    """Also where the tag vocabulary gets divided up.

    Picking a heading's tags is done here and not on the tag screen, because it
    is a list you curate in one sitting — read down the vocabulary once and
    decide what belongs under Co-ops — rather than a hundred separate decisions
    made one tag at a time.
    """

    prepopulated_fields = {"slug": ["name"]}
    list_display = ["name", "program_count", "tag_count", "sort_order"]
    list_editable = ["sort_order"]
    search_fields = ["name"]
    # Two panes and a search box, rather than autocomplete: choosing a heading's
    # tags means reading the whole list and deciding, so the whole list should be
    # on screen.
    filter_horizontal = ["tags"]
    fields = ["name", "slug", "description", "icon", "sort_order", "tags"]

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .annotate(_program_count=Count("programs", distinct=True))
            .annotate(_tag_count=Count("tags", distinct=True))
        )

    @display(description="Programs", ordering="_program_count")
    def program_count(self, obj):
        return obj._program_count

    @display(description="Tags offered", ordering="_tag_count")
    def tag_count(self, obj):
        return obj._tag_count


@admin.register(Tag)
class TagAdmin(ModelAdmin):
    """Deliberately plain. The work here is vocabulary discipline, not features:
    one tag per idea, and no near-duplicates.

    "Offered under" is shown but not edited here — see `CategoryAdmin`. A tag
    with nothing in that column still works; it is simply one we apply
    ourselves rather than one a registrant can pick.
    """

    prepopulated_fields = {"slug": ["name"]}
    list_display = ["name", "offered_under", "program_count", "slug"]
    list_per_page = 100
    search_fields = ["name"]
    list_filter = ["categories"]
    fields = ["name", "slug", "description"]

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .prefetch_related("categories")
            .annotate(_program_count=Count("programs", distinct=True))
        )

    @display(description="Offered under")
    def offered_under(self, obj):
        return ", ".join(category.name for category in obj.categories.all()) or "—"

    @display(description="Programs", ordering="_program_count")
    def program_count(self, obj):
        return obj._program_count


@admin.register(Page)
class PageAdmin(ModelAdmin):
    prepopulated_fields = {"slug": ["title"]}
    list_display = ["title", "is_published", "show_in_nav", "sort_order", "updated_at"]
    list_editable = ["is_published", "show_in_nav", "sort_order"]
    fields = ["title", "slug", "body", "is_published", "show_in_nav", "sort_order"]


@admin.register(Submission)
class SubmissionAdmin(ModelAdmin):
    """One queue, two kinds.

    A registration arrives complete, and "Approve" publishes it — that is the
    whole point of the longer form. A referral is a lead about someone else's
    program, so it gets a draft instead and someone reaches out.
    """

    list_display = [
        "program_name",
        "kind_display",
        "where_display",
        "status",
        "submitter_name",
        "created_at",
    ]
    list_filter = ["kind", "status", "category"]
    search_fields = [
        "program_name",
        "submitter_name",
        "submitter_email",
        "locations__query",
        "locations__label",
        "locations__city",
    ]
    date_hierarchy = "created_at"
    actions = ["approve_and_publish", "create_draft_program", "mark_rejected"]
    # Her chance to correct the registrant's tagging before it is published.
    autocomplete_fields = ["tags"]

    # Everything the public typed is a record of what they said, not something
    # we edit. Corrections happen on the Program after approval.
    readonly_fields = [
        "kind",
        "program_name",
        "short_description",
        "description",
        "website",
        "facebook",
        "email",
        "phone",
        "places_display",
        "serves_grades",
        "age_min",
        "age_max",
        "cost_notes",
        "meeting_schedule",
        "step_up_direct_pay",
        "step_up_pep",
        "step_up_fes_ua",
        "logo_preview",
        "submitter_name",
        "submitter_email",
        "submitter_role",
        "is_authorized",
        "created_at",
        "created_program",
    ]

    fieldsets = [
        (
            "The program",
            {
                "fields": [
                    "kind",
                    "program_name",
                    "short_description",
                    "description",
                    "category",
                    "tags",
                    "logo_preview",
                ]
            },
        ),
        (
            "How to reach them",
            {"fields": ["website", "facebook", "email", "phone", "places_display"]},
        ),
        (
            "Who it serves and when",
            {
                "fields": [
                    "serves_grades",
                    ("age_min", "age_max"),
                    "meeting_schedule",
                    "cost_notes",
                    "step_up_direct_pay",
                    "step_up_pep",
                    "step_up_fes_ua",
                ]
            },
        ),
        (
            "Who sent it",
            {
                "fields": [
                    "submitter_name",
                    "submitter_email",
                    "submitter_role",
                    "is_authorized",
                    "created_at",
                ]
            },
        ),
        ("Review", {"fields": ["status", "review_notes", "created_program"]}),
    ]

    def has_add_permission(self, request):
        # Submissions arrive from the public forms, never typed in here.
        return False

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("created_program")

    @display(description="Kind", ordering="kind", label=True)
    def kind_display(self, obj):
        return obj.get_kind_display().split(" — ")[0]

    @display(description="Where they meet")
    def places_display(self, obj):
        """Every place they named, and whether it is actually on the map.

        Worth saying out loud on this screen: a place the submitter picked off
        the suggestion list arrives with coordinates, and one they typed in their
        own words does not. The second kind is not a problem, but it is the kind
        that will need a look after approval.
        """
        places = list(obj.locations.all())
        if not places:
            return "—"
        return format_html_join(
            mark_safe("<br>"),
            "{}{}",
            (
                (place.display_name, "" if place.has_point else " — not on the map")
                for place in places
            ),
        )

    @display(description="Where")
    def where_display(self, obj):
        """One line in a list column, however many addresses they gave us."""
        lines = obj.location_lines
        if not lines:
            return "—"
        first = lines[0] if len(lines[0]) <= 40 else lines[0][:39] + "…"
        return f"{first} (+{len(lines) - 1} more)" if len(lines) > 1 else first

    @display(description="Logo")
    def logo_preview(self, obj):
        if not obj.logo:
            return "—"
        return format_html('<img src="{}" style="max-height:120px">', obj.logo.url)

    @action(description="Approve — publish the program")
    def approve_and_publish(self, request, queryset):
        self._approve(request, queryset, Program.Status.PUBLISHED)

    @action(description="Approve as a draft — do not publish yet")
    def create_draft_program(self, request, queryset):
        self._approve(request, queryset, Program.Status.DRAFT)

    @action(description="Reject")
    def mark_rejected(self, request, queryset):
        updated = queryset.update(status=Submission.Status.REJECTED)
        self.message_user(request, f"Rejected {updated}.", messages.SUCCESS)

    def _approve(self, request, queryset, status):
        created, skipped, incomplete = 0, 0, []

        for submission in queryset:
            if submission.created_program_id:
                skipped += 1
                continue

            # Nothing to show in a listing means nothing worth publishing.
            target_status = status
            if status == Program.Status.PUBLISHED and not submission.is_complete_enough_to_publish:
                target_status = Program.Status.DRAFT
                incomplete.append(submission.program_name)

            program = build_program_from(submission, target_status)
            submission.created_program = program
            submission.status = Submission.Status.APPROVED
            submission.save(update_fields=["created_program", "status"])
            created += 1

        if created:
            word = "program" if created == 1 else "programs"
            where = "published" if status == Program.Status.PUBLISHED else "created as drafts"
            self.message_user(request, f"{created} {word} {where}.", messages.SUCCESS)
        if incomplete:
            self.message_user(
                request,
                "Left as drafts because they have no one-line description, which is "
                f"what a listing shows: {', '.join(incomplete)}.",
                messages.WARNING,
            )
        if skipped:
            word = "submission" if skipped == 1 else "submissions"
            self.message_user(
                request, f"Skipped {skipped} already-approved {word}.", messages.WARNING
            )


def build_program_from(submission, status):
    """Copy a submission into a real Program, field for field.

    This is the function that has to stay honest: anything a registrant fills
    in and this does not carry across becomes something she retypes by hand.
    """
    program = Program(
        name=submission.program_name,
        slug=_unique_slug(submission.program_name),
        short_description=(
            submission.short_description or submission.description[:240] or submission.program_name
        ),
        category=submission.category,
        description=submission.description_as_html(),
        website=submission.website,
        facebook=submission.facebook,
        email=submission.email,
        phone=submission.phone,
        serves_grades=submission.serves_grades,
        age_min=submission.age_min,
        age_max=submission.age_max,
        cost_notes=submission.cost_notes,
        meeting_schedule=submission.meeting_schedule,
        step_up_direct_pay=submission.step_up_direct_pay,
        step_up_pep=submission.step_up_pep,
        step_up_fes_ua=submission.step_up_fes_ua,
        status=status,
        # The provider described it today, so today is when it was last verified.
        last_verified_on=timezone.localdate(),
    )

    if submission.logo:
        # Copy the bytes rather than sharing a path, so the submission record
        # and the published program have independent file lifetimes.
        submission.logo.open("rb")
        try:
            program.logo.save(
                Path(submission.logo.name).name,
                ContentFile(submission.logo.read()),
                save=False,
            )
        finally:
            submission.logo.close()

    program.save()
    program.tags.set(submission.tags.all())

    # The places come across exactly as they arrived, coordinates and all.
    # Anything the submitter picked off the suggestion list is already resolved
    # and needs no lookup; anything they typed in their own words arrives
    # pending, which is what "Look up addresses" and `manage.py geocode` are for.
    ProgramLocation.objects.bulk_create(
        [
            ProgramLocation(program=program, **place.copied_values())
            for place in submission.locations.all()
        ]
    )
    return program


def _unique_slug(name):
    base = slugify(name)[:150] or "program"
    slug = base
    n = 2
    while Program.objects.filter(slug=slug).exists():
        slug = f"{base}-{n}"
        n += 1
    return slug
