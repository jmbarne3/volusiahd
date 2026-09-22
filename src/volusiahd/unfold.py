"""django-unfold admin configuration.

The admin is the product here: one non-technical editor uses it weekly. The
sidebar is deliberately short — Programs, Categories, Pages, Submissions — and
everything Django registers by default that she will never touch is hidden.
"""

from django.urls import reverse_lazy
from django.utils.translation import gettext_lazy as _

UNFOLD = {
    "SITE_TITLE": _("Homeschool Directory"),
    "SITE_HEADER": _("Volusia County Homeschool Directory"),
    "SITE_SUBHEADER": _("Program records"),
    "SITE_URL": "/",
    "SHOW_HISTORY": True,
    "SHOW_VIEW_ON_SITE": True,
    "SHOW_LANGUAGES": False,
    "ENVIRONMENT": "volusiahd.unfold.environment_callback",
    "COLORS": {
        "primary": {
            "50": "240 249 255",
            "100": "224 242 254",
            "200": "186 230 253",
            "300": "125 211 252",
            "400": "56 189 248",
            "500": "14 165 233",
            "600": "2 132 199",
            "700": "3 105 161",
            "800": "7 89 133",
            "900": "12 74 110",
            "950": "8 47 73",
        },
    },
    "SIDEBAR": {
        "show_search": True,
        "show_all_applications": False,
        "navigation": [
            {
                "title": _("Directory"),
                "items": [
                    {
                        "title": _("Programs"),
                        "icon": "school",
                        "link": reverse_lazy("admin:directory_program_changelist"),
                    },
                    {
                        "title": _("Submissions"),
                        "icon": "inbox",
                        "link": reverse_lazy("admin:directory_submission_changelist"),
                        "badge": "directory.admin.pending_submission_count",
                    },
                    {
                        "title": _("Categories"),
                        "icon": "category",
                        "link": reverse_lazy("admin:directory_category_changelist"),
                    },
                    {
                        "title": _("Pages"),
                        "icon": "article",
                        "link": reverse_lazy("admin:directory_page_changelist"),
                    },
                ],
            },
            {
                # Who may sign in. Superusers only: an editor has no business
                # here, and the admin itself refuses them if they arrive.
                "title": _("Access"),
                "separator": True,
                "items": [
                    {
                        "title": _("People"),
                        "icon": "manage_accounts",
                        "link": reverse_lazy("admin:auth_user_changelist"),
                        "permission": "accounts.navigation.is_superuser",
                    },
                ],
            },
        ],
    },
}


def environment_callback(request):
    """Show a banner in the admin when this is not production."""
    from django.conf import settings

    if settings.DEBUG:
        return [_("Development"), "warning"]
    return None


__all__ = ["UNFOLD", "environment_callback"]
