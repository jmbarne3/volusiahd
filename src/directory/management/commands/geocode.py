"""Look up the addresses nothing has looked up yet.

The polite path for bulk work, and the safety net under the admin: an editor who
adds a meeting place and never notices the lookup failed leaves a row pending,
and this is what finds it. Safe to re-run, and it does nothing at all when every
address already has a point.

    uv run python manage.py geocode
    uv run python manage.py geocode --retry-failed --limit 50

Run it on a schedule if you like. There is deliberately a second of quiet
between requests, so a few hundred addresses takes a few minutes and nobody at
komoot notices us.
"""

from django.core.management.base import BaseCommand

from directory import geocoding
from directory.models import ProgramLocation


class Command(BaseCommand):
    help = "Resolve program addresses to coordinates."

    def add_arguments(self, parser):
        parser.add_argument(
            "--retry-failed",
            action="store_true",
            help="Also try addresses that were looked up before and did not match. "
            "Worth doing after an editor rewords them, and pointless otherwise.",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=0,
            help="Stop after this many. 0, the default, means all of them.",
        )

    def handle(self, *args, **options):
        locations = ProgramLocation.objects.pending()
        if options["retry_failed"]:
            locations = ProgramLocation.objects.exclude(status=ProgramLocation.Status.RESOLVED)
        if limit := options["limit"]:
            locations = locations[:limit]

        waiting = len(locations)
        if not waiting:
            self.stdout.write("Nothing to look up.")
            return

        self.stdout.write(f"Looking up {waiting} address{'es' if waiting != 1 else ''}…")
        resolved, missed, gave_up = locations.look_up(pause=geocoding.BATCH_PAUSE_SECONDS)

        self.stdout.write(self.style.SUCCESS(f"Resolved {resolved}."))
        if missed:
            self.stdout.write(
                self.style.WARNING(
                    f"No match for {missed}. Reword them, or set coordinates by hand."
                )
            )
        if gave_up:
            self.stdout.write(
                self.style.WARNING(
                    "Stopped early: the geocoder stopped answering. The rest are still "
                    "pending, so running this again picks up where it left off."
                )
            )
