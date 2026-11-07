"""Admin configuration.

Phase 4 of the plan treats the admin as a real deliverable rather than a
cleanup pass. The test of this file is that someone who has never seen the
codebase can add a program without asking a question.
"""

from django import forms
from django.contrib import admin, messages
from django.contrib.auth.models import Group
from django.db.models import Count
from django.shortcuts import redirect
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.utils.html import format_html, format_html_join
from django.utils.safestring import mark_safe
from django.utils.text import slugify
from unfold.admin import ModelAdmin, TabularInline
from unfold.decorators import action, display

from . import edits
from .addresses import DEBOUNCE_MS, MIN_QUERY_CHARS, parse_place
from .models import (
    Category,
    ContactPerson,
    Page,
    Program,
    ProgramLocation,
    SearchSynonym,
    Submission,
    SubmissionLocation,
    Tag,
    TagGroup,
    TagSuggestion,
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


def pending_tag_suggestion_count(request):
    """Sidebar badge: how many tag suggestions are waiting on her (#25)."""
    count = TagSuggestion.objects.filter(status=TagSuggestion.Status.PENDING).count()
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


class SubmissionLocationForm(ProgramLocationForm):
    class Meta(ProgramLocationForm.Meta):
        model = SubmissionLocation


class SubmissionLocationInline(ProgramLocationInline):
    """The places a submission names, editable before it becomes a program.

    The same rows, and the same address picker, as the program screen. Rows
    without coordinates are the ones somebody typed in their own words; they
    are looked up after approval, as they always were.
    """

    model = SubmissionLocation
    form = SubmissionLocationForm
    extra = 0
    verbose_name_plural = "Where they meet"


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
    list_filter = ["status", "category", "is_featured", "step_up_direct_pay", "faith_basis"]
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
                "fields": [
                    "short_description",
                    "description",
                    "logo",
                ],
            },
        ),
        (
            "Who runs it",
            {
                "fields": ["class_names", "instructor_info", "faith_basis"],
                "description": "A host name is only for a listing that is a class or a "
                "team under a larger group. Faith affiliation may be left blank, which "
                "publishes nothing either way.",
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
                "fields": ["grade_levels"],
            },
        ),
        (
            "When it meets",
            {
                "fields": [
                    "meeting_days",
                    ("meeting_time_start", "meeting_time_end"),
                    ("season_start", "season_end"),
                    "meeting_schedule",
                ],
                "description": "The days and dates are what a family filters by. "
                "Anything the boxes cannot say goes in the last box.",
            },
        ),
        (
            "Enrolling",
            {
                "fields": [("enrollment_opens", "enrollment_closes"), "enrollment_notes"],
            },
        ),
        (
            "Cost",
            {
                "fields": ["cost_basis", "cost_notes"],
            },
        ),
        (
            "Reach",
            {
                "fields": ["delivery", "travels_to_student_home", "service_area"],
                "description": "For programs that come to the family, or meet wherever "
                "suits, rather than at a fixed address.",
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


class TagAnswerInline(TabularInline):
    """The answers to a question, written on the question's own screen.

    A question and its answers are one editorial act — "ability level means
    beginner, intermediate, advanced" — and splitting them across two screens
    would mean writing five tags and then remembering what they were for.
    """

    model = Tag
    extra = 3
    fields = ["name", "slug", "description"]
    prepopulated_fields = {"slug": ["name"]}
    verbose_name = "answer"
    verbose_name_plural = "The answers to this question"


@admin.register(TagGroup)
class TagGroupAdmin(ModelAdmin):
    """The questions whose answers are tags.

    What this screen does not decide is who gets asked. A heading asks a question
    when it offers one of the answers, which is set under Categories — so an
    answer written here appears on no form until somebody offers it to a heading.
    That is the one thing about this screen worth remembering, and the column
    below says it out loud.
    """

    inlines = [TagAnswerInline]
    prepopulated_fields = {"slug": ["name"]}
    list_display = ["name", "answer_count", "offered_count", "allows_several", "sort_order"]
    list_editable = ["sort_order"]
    search_fields = ["name"]
    fields = ["name", "slug", "prompt", "allows_several", "sort_order"]

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .annotate(_answer_count=Count("tags", distinct=True))
            .annotate(_offered_count=Count("tags__categories", distinct=True))
        )

    @display(description="Answers", ordering="_answer_count")
    def answer_count(self, obj):
        return obj._answer_count

    @display(description="Asked of")
    def offered_count(self, obj):
        if not obj._offered_count:
            return format_html('<span style="color:#b45309">no headings yet</span>')
        return f"{obj._offered_count} heading{'s' if obj._offered_count != 1 else ''}"


@admin.register(Tag)
class TagAdmin(ModelAdmin):
    """Deliberately plain. The work here is vocabulary discipline, not features:
    one tag per idea, and no near-duplicates.

    "Offered under" is shown but not edited here — see `CategoryAdmin`. A tag
    with nothing in that column still works; it is simply one we apply
    ourselves rather than one a registrant can pick.

    A tag with a question beside it is an answer to that question rather than a
    subject: it renders as a radio button or a checkbox on the registration form
    instead of joining the searchable list. `TagGroupAdmin` is the better screen
    for writing those.
    """

    prepopulated_fields = {"slug": ["name"]}
    list_display = ["name", "group", "offered_under", "program_count", "slug"]
    list_per_page = 100
    search_fields = ["name"]
    list_filter = ["categories", "group"]
    fields = ["name", "slug", "description", "group"]

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .select_related("group")
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


@admin.register(SearchSynonym)
class SearchSynonymAdmin(ModelAdmin):
    """Words search treats as the same: one row per group, "theater, theatre"."""

    list_display = ["words"]
    search_fields = ["words"]
    fields = ["words"]


class TagSuggestionInline(TabularInline):
    """The tags a registration suggested, on the screen where it's approved.

    Shown so they can be decided in the same sitting, but approving the
    registration never waits on them (#25). The decisions themselves are made
    on the Tag suggestions screen, which this links to.
    """

    model = TagSuggestion
    extra = 0
    can_delete = False
    # The name is already the row's first column.
    show_title = False
    fields = ["name", "status", "tag", "decide_link"]
    readonly_fields = fields
    verbose_name_plural = "Tags they suggested"

    def has_add_permission(self, request, obj=None):
        return False

    @display(description="")
    def decide_link(self, obj):
        if obj.status != TagSuggestion.Status.PENDING:
            return "—"
        url = reverse_lazy("admin:directory_tagsuggestion_change", args=[obj.pk])
        return format_html('<a href="{}" class="text-primary-600 font-medium">Decide →</a>', url)


@admin.register(TagSuggestion)
class TagSuggestionAdmin(ModelAdmin):
    """Tags providers asked for, waiting on a decision (#25).

    Three outcomes. **Approve** makes it a new general tag: no heading, so it's
    never suggested on the form, only found by typing. **Use an existing tag**
    (pick one below and save) files it under a tag we already have. **Reject**
    leaves the program as it is. Either of the first two adds the tag to the
    registration, and to its program if that's already published.
    """

    list_display = ["name", "program_display", "status", "tag", "created_at"]
    list_filter = ["status"]
    search_fields = ["name", "submission__program_name"]
    actions = ["approve_selected", "reject_selected"]
    actions_detail = ["approve_one", "reject_one"]
    autocomplete_fields = ["tag"]
    fields = ["name", "submission_link", "status", "existing_match", "tag", "decided_at"]
    readonly_fields = ["name", "submission_link", "status", "existing_match", "decided_at"]

    def has_add_permission(self, request):
        return False

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("submission", "tag")

    @display(description="Program", ordering="submission__program_name")
    def program_display(self, obj):
        return obj.submission.program_name

    @display(description="From the registration")
    def submission_link(self, obj):
        url = reverse_lazy("admin:directory_submission_change", args=[obj.submission_id])
        return format_html('<a href="{}">{}</a>', url, obj.submission.program_name)

    @display(description="A tag with this name")
    def existing_match(self, obj):
        if obj.status != TagSuggestion.Status.PENDING:
            return "—"
        tag = obj.existing_tag()
        if not tag:
            return "None — approving makes a new tag."
        return f"“{tag.name}” already exists. Approving uses it rather than making a duplicate."

    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)
        field = form.base_fields.get("tag")
        if field:
            field.label = "Use an existing tag instead"
            field.help_text = (
                "Pick a tag and save to file this suggestion under it. Leave blank and "
                "use the Approve or Reject actions on the list instead."
            )
        return form

    def get_readonly_fields(self, request, obj=None):
        # Once decided, the outcome is a record, not something to edit.
        if obj and obj.status != TagSuggestion.Status.PENDING:
            return [*self.readonly_fields, "tag"]
        return self.readonly_fields

    def save_model(self, request, obj, form, change):
        if "tag" in form.changed_data and obj.tag and obj.status == TagSuggestion.Status.PENDING:
            obj.use(obj.tag)
            self.message_user(request, f"“{obj.name}” filed under “{obj.tag.name}”.")
            return
        super().save_model(request, obj, form, change)

    @action(description="Approve — make a new tag")
    def approve_selected(self, request, queryset):
        done = self._decide(queryset, TagSuggestion.approve)
        self.message_user(request, f"Approved {done}.", messages.SUCCESS)

    @action(description="Reject")
    def reject_selected(self, request, queryset):
        done = self._decide(queryset, TagSuggestion.reject)
        self.message_user(request, f"Rejected {done}.", messages.SUCCESS)

    @action(description="Approve — make a new tag", url_path="approve")
    def approve_one(self, request, object_id):
        return self._decide_one(request, object_id, TagSuggestion.approve, "Approved")

    @action(description="Reject", url_path="reject")
    def reject_one(self, request, object_id):
        return self._decide_one(request, object_id, TagSuggestion.reject, "Rejected")

    def _decide_one(self, request, object_id, decide, done):
        suggestion = TagSuggestion.objects.select_related("submission__created_program").get(
            pk=object_id
        )
        if suggestion.status != TagSuggestion.Status.PENDING:
            self.message_user(request, "That suggestion was already decided.", messages.WARNING)
        else:
            decide(suggestion)
            self.message_user(request, f"{done} “{suggestion.name}”.", messages.SUCCESS)
        return redirect(reverse("admin:directory_tagsuggestion_change", args=[object_id]))

    @staticmethod
    def _decide(queryset, decide):
        pending = queryset.filter(status=TagSuggestion.Status.PENDING).select_related(
            "submission__created_program"
        )
        count = 0
        for suggestion in pending:
            decide(suggestion)
            count += 1
        return count


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
    inlines = [SubmissionLocationInline, TagSuggestionInline]
    actions = ["approve_and_publish", "create_draft_program", "apply_edits", "mark_rejected"]
    # The same as buttons on a registration's own screen (#41). Which of them
    # show depends on the kind; see `get_actions_detail`.
    actions_detail = [
        "approve_and_publish_one",
        "create_draft_program_one",
        "apply_edit_one",
        "mark_rejected_one",
    ]
    # Her chance to correct the registrant's tagging before it is published.
    autocomplete_fields = ["tags"]

    # What the program says is hers to correct before it goes live: a typo in a
    # registration, or the half of a suggested edit she agrees with (#49).
    # Applying an edit copies only what differs from the program as it stood,
    # so setting a field back to that value drops that part of the edit.
    #
    # What stays fixed is the record of who sent it and what has been done
    # with it. No copy of the original wording is kept; she is the only
    # reviewer, and the change is hers to make.
    readonly_fields = [
        "kind",
        "logo_preview",
        "submitter_name",
        "submitter_email",
        "submitter_role",
        "is_authorized",
        "created_at",
        "created_program",
        "edits_program",
        "changes_display",
        # Set by the Approve and Reject buttons. Picking "Approved" by hand
        # would mark it approved without ever building the program.
        "status",
    ]

    fieldsets = [
        (
            "The program",
            {
                "fields": [
                    "kind",
                    "program_name",
                    "class_names",
                    "short_description",
                    "description",
                    "category",
                    "tags",
                    "logo_preview",
                    "logo",
                ],
                "description": "Correct anything here before you approve or apply it. "
                "The buttons at the top don't save this page, so save your "
                "corrections first.",
            },
        ),
        (
            "How to reach them",
            {"fields": ["website", "facebook", "email", "phone"]},
        ),
        (
            "Who teaches, and for whom",
            {
                "fields": [
                    "instructor_info",
                    "faith_basis",
                    "grade_levels",
                ]
            },
        ),
        (
            "When",
            {
                "fields": [
                    "meeting_days",
                    ("meeting_time_start", "meeting_time_end"),
                    ("season_start", "season_end"),
                    "meeting_schedule",
                    ("enrollment_opens", "enrollment_closes"),
                    "enrollment_notes",
                ]
            },
        ),
        (
            "Cost and reach",
            {
                "fields": [
                    "cost_basis",
                    "cost_notes",
                    "step_up_direct_pay",
                    "step_up_pep",
                    "step_up_fes_ua",
                    "delivery",
                    "travels_to_student_home",
                    "service_area",
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

    def get_fieldsets(self, request, obj=None):
        fieldsets = super().get_fieldsets(request, obj)
        if obj is None or not obj.is_edit:
            return fieldsets
        # An edit is read as a list of changes. The whole listing is still
        # below it, as the person sent it, for anything the table leaves out.
        return [
            (
                "Suggested changes",
                {
                    "fields": ["edits_program", "changes_display"],
                    "description": "Only what this person changed is listed, and only "
                    "that is copied when you apply it. To leave part of it out, set "
                    "that field below back to what the program says now, and save.",
                },
            ),
            *fieldsets,
        ]

    @display(description="What would change")
    def changes_display(self, obj):
        if not obj.is_edit:
            return "—"
        if obj.edits_program is None:
            return "The program this edit was for has been deleted."
        rows = edits.changes(obj)
        if not rows:
            return "Nothing — they sent the listing back unchanged."

        notes = []
        if obj.status == Submission.Status.NEW and edits.is_stale(obj):
            notes.append(
                mark_safe(
                    "<strong>The program has been edited since this was sent.</strong> "
                    "Rows marked ⚠ were changed there too, and applying would overwrite that."
                )
            )
        if any(row.key == "description" for row in rows):
            notes.append(
                "Applying replaces the description, and its formatting (bold, lists, "
                "links) with it."
            )
        notes_html = format_html_join(
            "", '<p style="margin:0 0 .75rem">{}</p>', ((note,) for note in notes)
        )

        cell = "padding:.4rem .75rem .4rem 0;vertical-align:top;text-align:left"
        head = f"{cell};font-weight:600;white-space:nowrap"
        body = format_html_join(
            "",
            f'<tr><th scope="row" style="{head}">{{}}{{}}</th>'
            f'<td style="{cell};white-space:pre-line">{{}}</td>'
            f'<td style="{cell};white-space:pre-line">{{}}</td></tr>',
            (
                (
                    row.label,
                    " ⚠" if row.changed_since else "",
                    *edits.highlight(row.current, row.suggested),
                )
                for row in rows
            ),
        )
        return format_html(
            '{}<table style="border-collapse:collapse;width:100%">'
            '<thead><tr><th style="{}">Field</th><th style="{};width:42%">Now</th>'
            '<th style="{};width:42%">Suggested</th></tr></thead><tbody>{}</tbody></table>',
            notes_html,
            head,
            head,
            head,
            body,
        )

    @display(description="Kind", ordering="kind", label=True)
    def kind_display(self, obj):
        return obj.get_kind_display().split(" — ")[0]

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

    @action(description="Apply edit — update the program")
    def apply_edits(self, request, queryset):
        applied, skipped = 0, 0
        for submission in queryset:
            # A rejected edit can still be applied, as a rejected registration
            # can still be approved.
            if (
                not submission.is_edit
                or submission.status == Submission.Status.APPROVED
                or submission.edits_program is None
            ):
                skipped += 1
                continue
            edits.apply(submission)
            applied += 1
        if applied:
            word = "edit" if applied == 1 else "edits"
            self.message_user(request, f"Applied {applied} {word}.", messages.SUCCESS)
        if skipped:
            self.message_user(
                request,
                f"Skipped {skipped}: only edits not yet applied, to a program that still "
                "exists, can be applied.",
                messages.WARNING,
            )

    @action(description="Apply edit — update the program", url_path="apply-edit")
    def apply_edit_one(self, request, object_id):
        self.apply_edits(request, self.get_queryset(request).filter(pk=object_id))
        return redirect(reverse("admin:directory_submission_change", args=[object_id]))

    @action(description="Approve — publish the program", url_path="approve")
    def approve_and_publish_one(self, request, object_id):
        return self._decide_one(request, object_id, self.approve_and_publish)

    @action(description="Approve as a draft", url_path="approve-draft")
    def create_draft_program_one(self, request, object_id):
        return self._decide_one(request, object_id, self.create_draft_program)

    @action(description="Reject", url_path="reject")
    def mark_rejected_one(self, request, object_id):
        return self._decide_one(request, object_id, self.mark_rejected)

    def get_actions_detail(self, request, object_id):
        # Buttons until a program exists. A rejected registration can still be
        # approved, as it can from the list.
        submission = self.get_object(request, object_id)
        if submission is None or submission.created_program_id:
            return []
        actions = super().get_actions_detail(request, object_id)
        if submission.is_edit:
            # An edit changes a program that exists; approving one would build
            # a second copy of it. Apply and Reject until it has been applied.
            if submission.status == Submission.Status.APPROVED:
                return []
            wanted = ("_apply_edit_one", "_mark_rejected_one")
        else:
            wanted = ("_approve_and_publish_one", "_create_draft_program_one", "_mark_rejected_one")
        return [action for action in actions if action.action_name.endswith(wanted)]

    def _decide_one(self, request, object_id, decide):
        """Run a list action on this one registration, then come back to it."""
        queryset = self.get_queryset(request).filter(pk=object_id)
        if queryset.filter(created_program__isnull=False).exists():
            self.message_user(request, "That registration already has a program.", messages.WARNING)
        else:
            decide(request, queryset)
        return redirect(reverse("admin:directory_submission_change", args=[object_id]))

    def _approve(self, request, queryset, status):
        created, skipped, incomplete = 0, 0, []

        edits_skipped = 0
        for submission in queryset:
            if submission.created_program_id:
                skipped += 1
                continue
            if submission.is_edit:
                edits_skipped += 1
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
        if edits_skipped:
            self.message_user(
                request,
                f"Skipped {edits_skipped} suggested "
                f"{'edit' if edits_skipped == 1 else 'edits'}. Use “Apply edit” for those — "
                "approving would make a second copy of the program.",
                messages.WARNING,
            )


def build_program_from(submission, status):
    """Copy a submission into a real Program, field for field.

    This is the function that has to stay honest: anything a registrant fills
    in and this does not carry across becomes something she retypes by hand.
    """
    program = Program(
        # Everything the two models hold identically, copied in one go rather
        # than named twice. A field added to `SharedProgramFields` arrives here
        # without anybody remembering to add it, which is the whole reason that
        # base class exists — a question a registrant answered and we then failed
        # to carry across is a question she has to ask them again.
        **submission.shared_values(),
        name=submission.program_name,
        slug=_unique_slug(submission.program_name),
        short_description=(
            submission.short_description or submission.description[:240] or submission.program_name
        ),
        category=submission.category,
        description=submission.description_as_html(),
        status=status,
        # The provider described it today, so today is when it was last verified.
        last_verified_on=timezone.localdate(),
    )

    if submission.logo:
        submission.copy_logo_to(program)

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
