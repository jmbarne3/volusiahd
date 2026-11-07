"""Make the page-sized copy of every logo that does not have one yet.

    uv run python manage.py resize_logos
    uv run python manage.py resize_logos --all

A logo saved after the copy existed gets one automatically, in
`Program.save()`. This is for the logos uploaded before that, and for redoing
every copy if `images.DISPLAY_WIDTH` ever changes. It is safe to run again: a
program that already has a copy is skipped unless `--all` says otherwise.
"""

from django.core.management.base import BaseCommand

from directory.models import Program


class Command(BaseCommand):
    help = "Make the page-sized copy of each program logo that does not have one."

    def add_arguments(self, parser):
        parser.add_argument(
            "--all",
            action="store_true",
            help="Remake every copy, not only the missing ones.",
        )

    def handle(self, *args, **options):
        programs = Program.objects.exclude(logo="")
        if not options["all"]:
            programs = programs.filter(logo_display="")

        made, failed = 0, []
        for program in programs.iterator():
            if program.refresh_logo_display():
                program.save(update_fields=["logo_display"])
                made += 1
            else:
                failed.append(program.name)

        self.stdout.write(self.style.SUCCESS(f"Resized {made} logo{'' if made == 1 else 's'}."))
        if failed:
            self.stdout.write(
                self.style.WARNING(
                    "Could not read these, so their pages keep the original: " + ", ".join(failed)
                )
            )
