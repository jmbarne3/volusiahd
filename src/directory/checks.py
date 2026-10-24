"""System checks: configuration mistakes worth hearing about at deploy time."""

from django.conf import settings
from django.core.checks import Warning, register


@register()
def google_analytics_id(app_configs, **kwargs):
    """A GOOGLE_ANALYTICS_ID that was set but is not shaped like one.

    Settings drop it rather than crash the site over a typo, which means the
    only sign of the mistake would be an analytics dashboard that never fills
    up. This puts it in `manage.py check`, which runs on every deploy.
    """
    raw = getattr(settings, "GOOGLE_ANALYTICS_ID_RAW", "")
    if raw and not settings.GOOGLE_ANALYTICS_ID:
        return [
            Warning(
                f"GOOGLE_ANALYTICS_ID {raw!r} is not a GA4 measurement ID, so no "
                "analytics tag is being added to pages.",
                hint="It looks like G-XXXXXXXXXX. Find it in Google Analytics under "
                "Admin → Data streams.",
                id="directory.W001",
            )
        ]
    return []
