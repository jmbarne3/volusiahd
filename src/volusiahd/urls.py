from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    # Ahead of the admin: `admin.site.urls` would otherwise swallow these and
    # answer 404 from inside the admin.
    path("admin/google/", include("accounts.urls")),
    path("admin/", admin.site.urls),
    path("", include("directory.urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
