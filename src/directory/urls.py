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
    path("tags/<slug:slug>/", views.tag_detail, name="tag"),
    path("register/", views.register_program, name="register"),
    path("register/thanks/", views.register_thanks, name="register_thanks"),
    path("suggest/", views.refer_program, name="refer"),
    path("suggest/thanks/", views.refer_thanks, name="refer_thanks"),
    # Asked by the address picker on both public forms and in the admin.
    path("where/", views.address_search, name="address_search"),
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
