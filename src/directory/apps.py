from django.apps import AppConfig


class DirectoryConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "directory"
    verbose_name = "Directory"

    def ready(self):
        from . import checks  # noqa: F401 — registers the system checks
