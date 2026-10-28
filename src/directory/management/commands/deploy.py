"""Everything that has to happen to a database when new code reaches it.

    uv run python manage.py deploy

`deploy/serve.sh` runs this on the way up, so it happens on every deployment and
on every restart. It is the one command to change when a new step joins the list,
rather than a shell script that drifts from what anybody remembers it doing.

Two steps today.

**Migrate**, which is what used to be here on its own.

**Load the baseline taxonomy** from `directory/fixtures/taxonomy.json` — the
thirteen headings, the four tag questions and their answers, and each heading's
suggested tags: 112 tags in all. The point is that an empty database comes up
usable rather than coming up as a site with no headings on the filter bar and a
registration form that offers nothing. It is safe to repeat: `loaddata` inserts
rows that are missing and updates rows it already matches, and never deletes
anything, so no program, submission or user is touched by this.

One consequence worth knowing, because it is the whole trade this design makes.
**The fixture is the source of truth for the rows in it.** Rename a heading in
the admin and the next deployment renames it back; change which tags sit under a
heading and the next deployment restores the baseline pairing. Tags added in the
admin survive, because nothing in the fixture mentions them. So edits meant to
last belong in the fixture, which is written by:

    DATABASE_PATH=/tmp/fresh.sqlite3 python manage.py migrate
    DATABASE_PATH=/tmp/fresh.sqlite3 python manage.py seed_taxonomy
    DATABASE_PATH=/tmp/fresh.sqlite3 python manage.py dumpdata \\
        directory.TagGroup directory.Tag directory.Category \\
        --natural-foreign --natural-primary \\
        --indent 2 -o src/directory/fixtures/taxonomy.json

**Both natural-key flags are required.** Without them the fixture is keyed by
row id, and loading it over production would overwrite whichever rows happen to
hold those ids rather than the rows with those slugs. A test fails if they are
left off.

`seed_taxonomy` is where the vocabulary is actually written and commented; the
fixture is generated from it. A test loads the fixture and seeds a database from
scratch and fails if the two disagree, so neither can quietly drift from the
other.
"""

from django.core.management import call_command
from django.core.management.base import BaseCommand

# Loaded in order, every deployment. A fixture listed here has to be safe to
# load repeatedly and safe to load over live data.
FIXTURES = ["taxonomy"]


class Command(BaseCommand):
    help = "Prepare the database for the code being deployed: migrate, then load fixtures."

    def add_arguments(self, parser):
        parser.add_argument(
            "--skip-fixtures",
            action="store_true",
            help="Migrate only. For the rare deployment where a fixture is the thing "
            "that is broken and the site needs to come up without it.",
        )

    def handle(self, *args, **options):
        # Every inner command writes to this command's own streams, so `-v 0`
        # quietens the whole thing and a caller capturing output captures all of
        # it rather than half.
        streams = {"stdout": self.stdout, "stderr": self.stderr}

        self.stdout.write("Migrating…")
        call_command("migrate", "--noinput", verbosity=options["verbosity"], **streams)

        if options["skip_fixtures"]:
            self.stdout.write(self.style.WARNING("Skipped fixtures, as asked."))
            return

        for fixture in FIXTURES:
            self.stdout.write(f"Loading {fixture}…")
            call_command("loaddata", fixture, verbosity=options["verbosity"], **streams)

        self.stdout.write(self.style.SUCCESS("Ready."))
