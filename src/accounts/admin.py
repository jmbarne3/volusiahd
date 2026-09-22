"""The admin screens for deciding who may use the admin.

Only superusers see any of this. The workflow it supports is one screen long:
add a person by their Google address, and they can sign in; deactivate them,
and they cannot. Everything Django's stock user admin offers that does not
serve that — per-user permissions, group membership, raw password entry —
is left out, because this site has one editor and one superuser and those
screens are only somewhere to make a mistake.
"""

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.utils.html import format_html
from unfold.admin import ModelAdmin, TabularInline
from unfold.forms import AdminPasswordChangeForm, UserChangeForm

from .forms import ApprovedUserCreationForm
from .models import GoogleAccountLink

User = get_user_model()

admin.site.unregister(User)


class GoogleAccountLinkInline(TabularInline):
    """Shown read-only. Deleting a row unbinds the Google account so the next
    sign-in re-matches by email; it does not revoke anything. To revoke
    access, clear the Active checkbox above."""

    model = GoogleAccountLink
    extra = 0
    max_num = 1
    can_delete = True
    fields = ["email", "subject", "linked_on", "last_used_on"]
    readonly_fields = fields
    verbose_name = "linked Google account"
    verbose_name_plural = "linked Google account"

    def has_add_permission(self, request, obj):
        # Links are only ever created by a successful sign-in.
        return False


@admin.register(User)
class UserAdmin(BaseUserAdmin, ModelAdmin):
    add_form = ApprovedUserCreationForm
    form = UserChangeForm
    change_password_form = AdminPasswordChangeForm
    inlines = [GoogleAccountLinkInline]

    list_display = ["email", "full_name", "role", "google_status", "is_active", "last_login"]
    list_filter = ["is_active", "is_superuser"]
    search_fields = ["email", "username", "first_name", "last_name"]
    ordering = ["email"]

    add_fieldsets = [
        (
            None,
            {
                "description": (
                    "Creating the account is what grants access. Until it "
                    "exists, that address cannot sign in with Google."
                ),
                "fields": ["email", "first_name", "last_name", "is_superuser"],
            },
        ),
    ]

    fieldsets = [
        (None, {"fields": ["username", "password"]}),
        ("Person", {"fields": ["first_name", "last_name", "email"]}),
        (
            "Access",
            {
                "description": (
                    "Clear Active to revoke access immediately, including any "
                    "session already signed in."
                ),
                "fields": ["is_active", "is_staff", "is_superuser"],
            },
        ),
        ("History", {"fields": ["last_login", "date_joined"]}),
    ]
    readonly_fields = ["last_login", "date_joined"]

    @admin.display(description="Name", ordering="first_name")
    def full_name(self, obj):
        return obj.get_full_name() or "—"

    @admin.display(description="Role")
    def role(self, obj):
        return "Superuser" if obj.is_superuser else "Editor"

    @admin.display(description="Google", boolean=False)
    def google_status(self, obj):
        link = getattr(obj, "google_link", None)
        if link is None:
            return format_html('<span class="text-base-400">Not yet signed in</span>')
        return format_html('<span class="text-green-600">Linked</span>')

    def get_inlines(self, request, obj=None):
        # There is nothing to link to before the account exists.
        return self.inlines if obj is not None else []

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("google_link")

    # Managing who has access is a superuser job, not an editor one. Without
    # these, a staff editor granted the stock `auth.change_user` permission
    # could promote themselves.
    def has_module_permission(self, request):
        return request.user.is_active and request.user.is_superuser

    def has_view_permission(self, request, obj=None):
        return self.has_module_permission(request)

    def has_add_permission(self, request):
        return self.has_module_permission(request)

    def has_change_permission(self, request, obj=None):
        return self.has_module_permission(request)

    def has_delete_permission(self, request, obj=None):
        return self.has_module_permission(request)
