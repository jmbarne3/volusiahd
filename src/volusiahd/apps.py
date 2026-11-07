from django.contrib.admin.apps import AdminConfig as DjangoAdminConfig


class AdminConfig(DjangoAdminConfig):
    """django.contrib.admin, with `sites.DirectoryAdminSite` as its default site."""

    default_site = "volusiahd.sites.DirectoryAdminSite"
