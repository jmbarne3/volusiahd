"""Put the thirteen headings, and the questions they ask, into an empty database.

    uv run python manage.py seed_taxonomy

Safe to run again. It creates what is missing, matched on slug, and never edits
or deletes what is already there — so a heading she has renamed stays renamed and
a tag she has taken off a heading stays off it. Re-running after a rename
therefore does nothing rather than quietly putting the old name back.

The content here is the site owner's own field list, turned into rows. Two things
about it are worth knowing before adding more.

The tag questions are structural: `questions.py` asks a heading about ability
level or parent involvement, and that question only appears if these rows exist.
Those are seeded in full.

The subject vocabularies are not structural, and most of them are deliberately
absent. Only the two the field list actually enumerates are here — the eleven
enrichment subjects and the three performing arts ones. Inventing "Algebra,
Geometry, Chemistry" for core academics would be us deciding a vocabulary that
belongs to whoever runs the directory, and a tag nobody chose is worse than a
tag that is missing, because a missing one gets asked for.
"""

from django.core.management.base import BaseCommand
from django.db import transaction

from directory import questions as q
from directory.models import Category, Tag, TagGroup

# slug, name, sort order, Material Symbols icon, the line under the heading.
# The order is editorial: what most families are looking for, first.
HEADINGS = [
    (q.CO_OPS, "Co-ops", 10, "groups", "Parents take part unless a listing says otherwise."),
    (q.CORE_ACADEMICS, "Core academic classes", 20, "school", "Math, science, writing, history."),
    (
        q.MICROSCHOOLS,
        "Microschools and hybrid programs",
        30,
        "cottage",
        "Drop-off programs unless a listing says otherwise.",
    ),
    (
        q.ENRICHMENT,
        "Enrichment, electives and clubs",
        40,
        "extension",
        "Everything from robotics to book club.",
    ),
    (
        q.SPORTS,
        "Sports and recreation",
        50,
        "sports_soccer",
        "Teams, leagues, and skills classes.",
    ),
    (q.PERFORMING_ARTS, "Performing arts", 60, "theater_comedy", "Dance, theater, and music."),
    (q.ARTS_AND_CRAFTS, "Arts and crafts", 70, "palette", "Studio classes and making."),
    (q.TUTORING, "Tutoring", 80, "menu_book", "One to one and small group help."),
    (
        q.TESTING,
        "Testing and evaluation",
        90,
        "assignment",
        "Annual evaluations and standardized testing.",
    ),
    (
        q.SPECIAL_NEEDS,
        "Special needs support",
        100,
        "accessibility_new",
        "Therapies, services, and support.",
    ),
    (q.FIELD_TRIPS, "Field trips", 110, "map", "Places to go, and groups that organise going."),
    (
        q.PLAY_GROUPS,
        "Play and social groups",
        120,
        "diversity_3",
        "Park days, meet-ups, and teen hangouts.",
    ),
    (
        q.PARENT_SUPPORT,
        "Parent support groups",
        130,
        "volunteer_activism",
        "For the adults doing the teaching.",
    ),
]

# Each question: slug, label, whether several answers are allowed, sort order,
# the sentence under it, then its answers as (tag name, the headings offered it).
#
# Which headings are offered which answers is the whole reason this is a tag
# question and not a column. Sports asks about ability and means junior varsity;
# an art studio asks about ability and means advanced. Same question, same filter,
# different answers on offer.
QUESTIONS = [
    {
        "slug": "ability-level",
        "name": "Ability level",
        "allows_several": True,
        "sort_order": 10,
        "prompt": "Tick every level you take. Most programs take more than one.",
        "answers": [
            ("Beginner", [q.SPORTS, q.ARTS_AND_CRAFTS, q.PERFORMING_ARTS, q.ENRICHMENT]),
            ("Intermediate", [q.SPORTS, q.ARTS_AND_CRAFTS, q.PERFORMING_ARTS, q.ENRICHMENT]),
            ("Advanced", [q.SPORTS, q.ARTS_AND_CRAFTS, q.PERFORMING_ARTS, q.ENRICHMENT]),
            ("Junior varsity", [q.SPORTS]),
            ("Varsity", [q.SPORTS]),
        ],
    },
    {
        "slug": "program-style",
        "name": "Competition or recreational",
        "allows_several": True,
        "sort_order": 20,
        "prompt": "Both is a perfectly good answer.",
        "answers": [
            ("Competition team", [q.SPORTS]),
            ("Skill-based or recreational", [q.SPORTS]),
        ],
    },
    {
        # One answer only. These are alternatives, and a program that ticked
        # "drop-off only" and "parents must attend" would have told a family
        # nothing at all.
        "slug": "parent-involvement",
        "name": "Parent involvement",
        "allows_several": False,
        "sort_order": 30,
        "prompt": "What is expected of the adult who brings them.",
        "answers": [
            (
                "Parents must attend",
                [q.CORE_ACADEMICS, q.SPORTS, q.ARTS_AND_CRAFTS, q.PERFORMING_ARTS, q.ENRICHMENT],
            ),
            (
                "Parents may attend",
                [q.CORE_ACADEMICS, q.SPORTS, q.ARTS_AND_CRAFTS, q.PERFORMING_ARTS, q.ENRICHMENT],
            ),
            (
                "Parents must help",
                [q.CORE_ACADEMICS, q.SPORTS, q.ARTS_AND_CRAFTS, q.PERFORMING_ARTS, q.ENRICHMENT],
            ),
            (
                "Drop-off allowed",
                [q.CORE_ACADEMICS, q.SPORTS, q.ARTS_AND_CRAFTS, q.PERFORMING_ARTS, q.ENRICHMENT],
            ),
            (
                "Drop-off only",
                [q.CORE_ACADEMICS, q.SPORTS, q.ARTS_AND_CRAFTS, q.PERFORMING_ARTS, q.ENRICHMENT],
            ),
            # A co-op's version of the question. Parents are involved by
            # definition, so what is left to ask is for how much of the day.
            ("Families stay all day", [q.CO_OPS]),
            ("Families stay part of the day", [q.CO_OPS]),
        ],
    },
    {
        "slug": "subjects-offered",
        "name": "Subjects offered",
        "allows_several": True,
        "sort_order": 40,
        "prompt": "",
        "answers": [
            ("Core subjects", [q.MICROSCHOOLS]),
            ("Electives", [q.MICROSCHOOLS]),
        ],
    },
]

# Ordinary subject tags, only where the field list spells them out.
SUBJECT_TAGS = [
    (q.ENRICHMENT, [
        "Baking and cooking",
        "STEM",
        "Life skills",
        "Nature and outdoor skills",
        "Business and professional development",
        "Book club",
        "Gaming",
        "Foreign language",
        "Bible study",
        "Youth group",
        "Scouting",
    ]),
    (q.PERFORMING_ARTS, ["Dance", "Theater", "Music lessons"]),
]


class Command(BaseCommand):
    help = "Create the headings, tag questions and starter tags this directory expects."

    def handle(self, *args, **options):
        with transaction.atomic():
            headings = self._headings()
            self._questions(headings)
            self._subjects(headings)
        self.stdout.write(
            self.style.SUCCESS(
                "Done. The subject vocabularies for core academics, sports, arts, "
                "tutoring, testing and special needs are deliberately empty — those "
                "are yours to write, under Categories."
            )
        )

    def _headings(self):
        headings, made = {}, 0
        for slug, name, sort_order, icon, description in HEADINGS:
            heading, created = Category.objects.get_or_create(
                slug=slug,
                defaults={
                    "name": name,
                    "sort_order": sort_order,
                    "icon": icon,
                    "description": description,
                },
            )
            headings[slug] = heading
            made += created
        self._say("heading", made, len(HEADINGS))
        return headings

    def _questions(self, headings):
        made = linked = 0
        for spec in QUESTIONS:
            group, created = TagGroup.objects.get_or_create(
                slug=spec["slug"],
                defaults={
                    "name": spec["name"],
                    "allows_several": spec["allows_several"],
                    "sort_order": spec["sort_order"],
                    "prompt": spec["prompt"],
                },
            )
            made += created
            for name, offered_to in spec["answers"]:
                tag = self._tag(name, group=group)
                linked += self._offer(tag, offered_to, headings)
        self._say("tag question", made, len(QUESTIONS))
        if linked:
            self.stdout.write(f"Offered {linked} answer{'s' if linked != 1 else ''} to a heading.")

    def _subjects(self, headings):
        made = linked = total = 0
        for slug, names in SUBJECT_TAGS:
            for name in names:
                total += 1
                before = Tag.objects.filter(name=name).exists()
                tag = self._tag(name)
                made += not before
                linked += self._offer(tag, [slug], headings)
        self._say("subject tag", made, total)
        if linked:
            self.stdout.write(f"Offered {linked} subject tag{'s' if linked != 1 else ''}.")

    def _tag(self, name, group=None):
        """A tag by name, grouped if it is an answer to a question.

        Matched on name rather than slug because the name is the thing that has
        to stay unique to a single idea — two tags called the same thing with
        different slugs is exactly the duplication the vocabulary exists to stop.
        """
        tag, created = Tag.objects.get_or_create(
            name=name,
            defaults={"slug": self._slug(name), "group": group},
        )
        # An existing ungrouped tag becomes an answer if that is what it now is.
        # Never the other way round: taking a tag out of a question would change
        # what she meant by it.
        if group and created is False and tag.group_id is None:
            tag.group = group
            tag.save(update_fields=["group"])
        return tag

    def _offer(self, tag, slugs, headings):
        offered = 0
        for slug in slugs:
            heading = headings.get(slug)
            if heading and not heading.tags.filter(pk=tag.pk).exists():
                heading.tags.add(tag)
                offered += 1
        return offered

    def _slug(self, name):
        from django.utils.text import slugify

        base = slugify(name)[:60] or "tag"
        slug, n = base, 2
        while Tag.objects.filter(slug=slug).exists():
            slug = f"{base[:57]}-{n}"
            n += 1
        return slug

    def _say(self, thing, made, total):
        kept = total - made
        plural = "s" if made != 1 else ""
        line = f"Created {made} {thing}{plural}."
        if kept:
            line += f" Left {kept} alone — already there."
        self.stdout.write(line)
