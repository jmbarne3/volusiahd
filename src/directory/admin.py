"""Admin configuration.

Phase 4 of the plan treats the admin as a real deliverable rather than a
cleanup pass. The test of this file is that someone who has never seen the
codebase can add a program without asking a question.
"""

from pathlib import Path

from django.contrib import admin, messages
from django.contrib.auth.models import Group
from django.core.files.base import ContentFile
from django.db.models import Count
from django.utils import timezone
from django.utils.html import format_html
from django.utils.text import slugify
from unfold.admin import ModelAdmin, TabularInline
from unfold.decorators import action, display

from .models import Category, ContactPerson, Page, Program, Submission

# She will never touch Groups. Site-wide permissions are not a thing we use.
admin.site.unregister(Group)

admin.site.site_title = "Homeschool Directory"
admin.site.site_header = "Volusia County Homeschool Directory"
admin.site.index_title = "Records"


def pending_submission_count(request):
    """Sidebar badge: how many submissions are waiting on her."""
    count = Submission.objects.filter(status=Submission.Status.NEW).count()
    return count or None


class ContactPersonInline(TabularInline):
    model = ContactPerson
    extra = 0
    fields = ["name", "role", "email", "phone"]
    verbose_name = "contact"
    verbose_name_plural = "Who to call about this program (never published)"


@admin.register(Program)
class ProgramAdmin(ModelAdmin):
    inlines = [ContactPersonInline]
    prepopulated_fields = {"slug": ["name"]}
    list_display = ["name", "category_list", "status", "verified_display", "is_featured"]
    list_display_links = ["name"]
    list_editable = ["status", "is_featured"]
    list_filter = ["status", "categories", "is_featured", "step_up_direct_pay"]
    list_per_page = 50
    search_fields = ["name", "short_description", "locations", "contacts__name"]
    filter_horizontal = ["categories"]
    date_hierarchy = "created_at"
    readonly_fields = ["created_at", "updated_at"]
    actions = ["mark_verified_today", "publish_selected"]

    fieldsets = [
        (
            None,
            {
                "fields": ["name", "slug", "status", "is_featured", "categories"],
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
            "Location",
            {
                "fields": ["locations"],
                "classes": ["collapse"],
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
        return super().get_queryset(request).prefetch_related("categories")

    @display(description="Categories")
    def category_list(self, obj):
        return ", ".join(c.name for c in obj.categories.all()) or "—"

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
    prepopulated_fields = {"slug": ["name"]}
    list_display = ["name", "program_count", "sort_order"]
    list_editable = ["sort_order"]
    search_fields = ["name"]
    fields = ["name", "slug", "description", "icon", "sort_order"]

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(_program_count=Count("programs"))

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
    list_filter = ["kind", "status", "categories"]
    search_fields = ["program_name", "submitter_name", "submitter_email", "locations"]
    date_hierarchy = "created_at"
    actions = ["approve_and_publish", "create_draft_program", "mark_rejected"]

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
        "locations",
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
                    "categories",
                    "logo_preview",
                ]
            },
        ),
        (
            "How to reach them",
            {"fields": ["website", "facebook", "email", "phone", "locations"]},
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

    @display(description="Where")
    def where_display(self, obj):
        """One line in a list column, however many addresses they gave us."""
        lines = [line.strip() for line in (obj.locations or "").splitlines() if line.strip()]
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
        description=submission.description_as_html(),
        website=submission.website,
        facebook=submission.facebook,
        email=submission.email,
        phone=submission.phone,
        locations=submission.locations,
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
    program.categories.set(submission.categories.all())
    return program


def _unique_slug(name):
    base = slugify(name)[:150] or "program"
    slug = base
    n = 2
    while Program.objects.filter(slug=slug).exists():
        slug = f"{base}-{n}"
        n += 1
    return slug
