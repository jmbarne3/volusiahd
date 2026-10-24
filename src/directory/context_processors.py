from django.conf import settings

from .models import Page


def site(request):
    return {
        "site_name": settings.SITE_NAME,
        "site_base_url": settings.SITE_BASE_URL,
        "google_analytics_id": settings.GOOGLE_ANALYTICS_ID,
        "nav_pages": Page.objects.filter(is_published=True, show_in_nav=True),
    }
