from django.contrib.sitemaps.views import sitemap
from django.urls import path
from django.views.generic import TemplateView

from . import views
from .sitemaps import SITEMAPS

app_name = "directory"

urlpatterns = [
    path("", views.home, name="home"),
    path("programs/", views.program_list, name="program_list"),
    path("programs/<slug:slug>/", views.program_detail, name="program"),
    path("categories/<slug:slug>/", views.category_detail, name="category"),
    path("suggest/", views.submit_program, name="submit"),
    path("suggest/thanks/", views.submit_thanks, name="submit_thanks"),
    path(
        "robots.txt",
        TemplateView.as_view(template_name="robots.txt", content_type="text/plain"),
        name="robots",
    ),
    path(
        "sitemap.xml",
        sitemap,
        {"sitemaps": SITEMAPS},
        name="django.contrib.sitemaps.views.sitemap",
    ),
    # Flat pages last: this pattern would otherwise swallow the routes above.
    path("<slug:slug>/", views.page_detail, name="page"),
]
