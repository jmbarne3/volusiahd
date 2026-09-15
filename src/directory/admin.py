"""Admin configuration.

Phase 4 of the plan treats the admin as a real deliverable rather than a
cleanup pass. The test of this file is that someone who has never seen the
codebase can add a program without asking a question.
"""

from django.contrib import admin, messages
from django.contrib.auth.models import Group
from django.db.models import Count
from django.utils import timezone
from django.utils.html import format_html
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
    fields = ["name", "role", "email", "phone", "is_public"]
    verbose_name = "contact"
    verbose_name_plural = "Contacts for this program"


@admin.register(Program)
class ProgramAdmin(ModelAdmin):
    inlines = [ContactPersonInline]
    prepopulated_fields = {"slug": ["name"]}
    list_display = ["name", "category_list", "status", "verified_display", "is_featured"]
    list_display_links = ["name"]
    list_editable = ["status", "is_featured"]
    list_filter = ["status", "categories", "is_featured"]
    list_per_page = 50
    search_fields = ["name", "short_description", "city", "contacts__name"]
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
                "fields": ["website", "email", "phone"],
            },
        ),
        (
            "Location",
            {
                "fields": ["street", "city", "zip_code"],
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
    list_display = ["program_name", "city", "status", "submitter_name", "created_at"]
    list_filter = ["status", "categories"]
    search_fields = ["program_name", "submitter_name", "submitter_email"]
    readonly_fields = [
        "program_name",
        "website",
        "email",
        "phone",
        "city",
        "description",
        "submitter_name",
        "submitter_email",
        "created_at",
        "created_program",
    ]
    actions = ["approve_into_programs"]

    fieldsets = [
        (
            "What was submitted",
            {
                "fields": [
                    "program_name",
                    "website",
                    "email",
                    "phone",
                    "city",
                    "categories",
                    "description",
                ]
            },
        ),
        ("Who submitted it", {"fields": ["submitter_name", "submitter_email", "created_at"]}),
        ("Review", {"fields": ["status", "review_notes", "created_program"]}),
    ]

    def has_add_permission(self, request):
        # Submissions arrive from the public form, never typed in here.
        return False

    @action(description="Approve — create a draft program from this")
    def approve_into_programs(self, request, queryset):
        created = 0
        skipped = 0
        for submission in queryset:
            if submission.created_program_id:
                skipped += 1
                continue
            program = Program.objects.create(
                name=submission.program_name,
                slug=_unique_slug(submission.program_name),
                short_description=submission.description[:240],
                website=submission.website,
                email=submission.email,
                phone=submission.phone,
                city=submission.city,
                status=Program.Status.DRAFT,
            )
            program.categories.set(submission.categories.all())
            submission.created_program = program
            submission.status = Submission.Status.APPROVED
            submission.save(update_fields=["created_program", "status"])
            created += 1

        if created:
            self.message_user(
                request,
                f"Created {created} draft program{'s' if created != 1 else ''}. "
                "Open each one, check the details, then set it to Published.",
                messages.SUCCESS,
            )
        if skipped:
            self.message_user(
                request,
                f"Skipped {skipped} submission{'s' if skipped != 1 else ''} already approved.",
                messages.WARNING,
            )


def _unique_slug(name):
    from django.utils.text import slugify

    base = slugify(name)[:150] or "program"
    slug = base
    n = 2
    while Program.objects.filter(slug=slug).exists():
        slug = f"{base}-{n}"
        n += 1
    return slug
