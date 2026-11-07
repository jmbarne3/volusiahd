"""The admin site, with the editor's guide added to it (#39).

This is Django's documented way to add pages to the admin: subclass the site,
add views in `get_urls()`, and wrap each in `admin_view()` so it gets the
admin's own sign-in and permission checks. `apps.AdminConfig` makes this the
default site, so `admin.site` and every `@admin.register` elsewhere use it
unchanged.

It subclasses Unfold's site rather than Django's, because Unfold's is what
draws the sidebar and theme the guide pages sit inside.
"""

from django.urls import path
from django.views.generic import RedirectView
from unfold.sites import UnfoldAdminSite


class DirectoryAdminSite(UnfoldAdminSite):
    def get_urls(self):
        from directory.help import views as help_views

        # Ahead of the admin's own routes, which end in a catch-all.
        #
        # The front page is help/overview/ rather than help/ itself because
        # Unfold marks a sidebar link active when its path appears anywhere in
        # the current one. At help/, the Overview link would light up on every
        # topic as well.
        return [
            path("help/", RedirectView.as_view(pattern_name="admin:help")),
            path("help/overview/", self.admin_view(help_views.index), name="help"),
            path("help/<slug:slug>/", self.admin_view(help_views.topic), name="help_topic"),
            *super().get_urls(),
        ]
