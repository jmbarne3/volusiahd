"""Fill a local database with something to look at: four or five programs per heading.

    uv run python manage.py demo_data
    uv run python manage.py demo_data --clear

Fifty-seven invented programs across the thirteen headings, published and ready to
click through. The point is to see the shape of the thing — what a co-op's page
looks like against a testing service's, whether the filter bar reads, whether a
heading that asks eight questions looks thin next to one that asks twenty.

Two things about how it fills them in.

Which fields each program gets is read from `questions.py` rather than typed out
here, so this is also a check on that map: a co-op with a "how far they travel"
line, or a tutoring service with an enrollment window, would be a scoping mistake
you could see on the page. Nothing here sets a field its heading is not asked.

The addresses carry real coordinates for real places in Volusia County, so the
records look the way records look after a geocoder has been near them. Nothing is
looked up and no request leaves the machine.

Local only. It refuses to run with DEBUG off, because fifty-seven invented
programs in the live directory would be somebody's afternoon to undo.
"""

import datetime
import random

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils.text import slugify

from directory import questions as q
from directory.models import Category, Program, ProgramLocation

# Real places, with the coordinates a lookup would have found. Each is
# (label, latitude, longitude, city, postcode).
VENUES = {
    "deland": [
        ("DeLand Regional Library, 130 E Howry Ave", 29.0269, -81.3012, "DeLand", "32724"),
        ("First Presbyterian Church, 724 N Woodland Blvd", 29.0364, -81.3035, "DeLand", "32720"),
        ("Earl Brown Park, 750 S Alabama Ave", 29.0141, -81.2999, "DeLand", "32724"),
    ],
    "deltona": [
        ("Deltona Regional Library, 2150 Eustace Ave", 28.9047, -81.2201, "Deltona", "32725"),
        ("Dewey O. Boster Sports Complex, 1200 S Kepler Rd", 28.9283, -81.2722, "Deltona", "32725"),
    ],
    "daytona": [
        ("City Island Library, 105 E Magnolia Ave", 29.2117, -81.0084, "Daytona Beach", "32114"),
        ("Bethune Point Park, 925 Bellevue Ave", 29.1922, -81.0166, "Daytona Beach", "32114"),
    ],
    "ormond": [
        ("Ormond Beach Library, 30 S Beach St", 29.2853, -81.0551, "Ormond Beach", "32174"),
        ("Central Park, 151 W Granada Blvd", 29.2830, -81.0631, "Ormond Beach", "32174"),
    ],
    "newsmyrna": [
        ("NSB Library, 1001 S Dixie Fwy", 29.0125, -80.9283, "New Smyrna Beach", "32168"),
        ("Riverside Park, 105 Ocean Ave", 29.0264, -80.9203, "New Smyrna Beach", "32169"),
    ],
    "portorange": [
        ("Port Orange Library, 1005 City Center Cir", 29.1088, -81.0139, "Port Orange", "32129"),
        ("Spruce Creek Park, 6250 Ridgewood Ave", 29.0703, -80.9636, "Port Orange", "32127"),
    ],
    "orangecity": [
        ("Orange City Library, 148 Albertus Way", 28.9463, -81.2989, "Orange City", "32763"),
    ],
    "debary": [
        ("DeBary Hall, 210 Sunrise Blvd", 28.8797, -81.3116, "DeBary", "32713"),
    ],
    "edgewater": [
        ("Edgewater Library, 103 W Indian River Blvd", 28.9887, -80.9047, "Edgewater", "32132"),
    ],
}

# Per heading: a description opener, how the cost usually reads, and the ordinary
# meeting pattern. Everything else is varied per program below.
SHAPES = {
    q.CO_OPS: ("thu", (9, 0), (14, 0), "paid", "$60 per family per semester, plus materials."),
    q.CORE_ACADEMICS: ("tue", (10, 0), (12, 30), "paid", "$240 per class per semester."),
    q.MICROSCHOOLS: ("mon,tue,wed,thu", (8, 30), (15, 0), "paid", "$4,800 a year, monthly."),
    q.ENRICHMENT: ("wed", (13, 0), (15, 0), "paid", "$95 for the eight-week session."),
    q.SPORTS: ("sat", (9, 0), (11, 0), "paid", "$150 per season, uniform included."),
    q.PERFORMING_ARTS: ("mon", (16, 0), (18, 0), "paid", "$85 a month."),
    q.ARTS_AND_CRAFTS: ("fri", (10, 0), (12, 0), "paid", "$30 a class, or $150 for six."),
    q.TUTORING: ("", None, None, "paid", "$45 an hour, or $40 in a group of three."),
    q.TESTING: ("", None, None, "paid", "$120 for the evaluation, $75 for a sibling."),
    q.SPECIAL_NEEDS: ("", None, None, "varies", "Varies by service. Most insurance accepted."),
    q.FIELD_TRIPS: ("", None, None, "varies", "Group rate of $8 a head for ten or more."),
    q.PLAY_GROUPS: ("fri", (10, 0), (12, 0), "free", "Free. Bring a snack to share."),
    q.PARENT_SUPPORT: ("tue", (19, 0), (20, 30), "free", "Free."),
}

# name, one-line description, venue key. Four or five per heading.
PROGRAMS = {
    q.CO_OPS: [
        ("Coastal Classical Co-op", "A Thursday co-op for K-8 families, Ormond Beach.", "ormond"),
        ("West Volusia Home Educators", "A long-running full-day co-op in DeLand.", "deland"),
        ("Spruce Creek Co-op", "Fridays in Port Orange, with a strong science bent.", "portorange"),
        ("Deltona Family Co-op", "Parent-led classes for K-12, Tuesdays in Deltona.", "deltona"),
        ("Rivertown Co-op", "A small co-op for younger children in New Smyrna.", "newsmyrna"),
    ],
    q.CORE_ACADEMICS: [
        ("Volusia Math Collaborative", "Algebra through calculus in small classes.", "deland"),
        ("The Writing Table", "Composition and literature for middle and high school.", "ormond"),
        ("Halifax Science Labs", "Hands-on biology and chemistry labs.", "daytona"),
        ("Latin at the Library", "Two years of Latin, Tuesdays in Orange City.", "orangecity"),
    ],
    q.MICROSCHOOLS: [
        ("Oak & Ember Microschool", "A four-day drop-off program for grades 3-8.", "deland"),
        ("Halifax Hybrid Academy", "Two days on campus, three days at home.", "portorange"),
        ("The Grove School", "A small microschool with a nature-study spine.", "debary"),
        ("Atlantic Hybrid High", "Grades 9-12, three days a week.", "newsmyrna"),
    ],
    q.ENRICHMENT: [
        ("Volusia Robotics Club", "Competitive robotics for grades 5-12.", "deltona"),
        ("Bread & Butter Baking", "An eight-week baking course for teens.", "ormond"),
        ("Compass Scouts", "Outdoor skills and service for boys and girls.", "deland"),
        ("The Reading Circle", "A monthly book club for middle-grade readers.", "portorange"),
        ("Mandarin for Beginners", "A first year of Mandarin, Wednesdays.", "orangecity"),
    ],
    q.SPORTS: [
        ("Coastal United Soccer", "Recreational and competitive youth soccer.", "ormond"),
        ("Volusia Homeschool Hoops", "Basketball for grades 4-12, JV and varsity.", "deland"),
        ("Halifax Rowing Juniors", "Learn-to-row and a competitive squad.", "daytona"),
        ("Spruce Creek Cross Country", "A friendly running club that races.", "portorange"),
        ("Deltona Volleyball Academy", "Skills classes and a travel team.", "deltona"),
    ],
    q.PERFORMING_ARTS: [
        ("Athens Youth Theatre", "Two full productions a year, ages 8-18.", "deland"),
        ("Seaside Strings", "Violin and cello lessons, group and private.", "newsmyrna"),
        ("Halifax Dance Collective", "Ballet, jazz and contemporary for homeschoolers.", "daytona"),
        ("Ormond Voice Studio", "Private voice lessons and a small chorus.", "ormond"),
    ],
    q.ARTS_AND_CRAFTS: [
        ("Clay & Kiln Studio", "Wheel-thrown pottery for ages 10 and up.", "portorange"),
        ("The Drawing Room", "Observational drawing and painting, all levels.", "deland"),
        ("Fiber Arts Friday", "Knitting, weaving and needlework for beginners.", "orangecity"),
        ("Coastal Printmaking", "Relief and screen printing in a working studio.", "ormond"),
    ],
    q.TUTORING: [
        ("Halifax Reading Clinic", "One-to-one reading help, Orton-Gillingham trained.", "daytona"),
        ("Volusia Math Tutors", "Pre-algebra through AP Calculus, in person or online.", "deland"),
        ("Write Right Tutoring", "Essay and application help for high schoolers.", "ormond"),
        ("Bridge Spanish Tutoring", "Conversational and exam-focused Spanish.", "deltona"),
    ],
    q.TESTING: [
        ("Volusia Annual Evaluations", "Portfolio reviews by a certified teacher.", "deland"),
        ("Halifax Testing Services", "Group standardized testing, twice a year.", "portorange"),
        ("Orange City Evaluations", "Evaluations in your home or ours.", "orangecity"),
        ("Coastal Assessment Partners", "Psychoeducational testing and reports.", "ormond"),
    ],
    q.SPECIAL_NEEDS: [
        ("Steady Steps Therapy", "Speech and occupational therapy for homeschoolers.", "deland"),
        ("Halifax Learning Support", "Support for dyslexia, dysgraphia and ADHD.", "daytona"),
        ("Bright Path Behavioral", "In-home behavioral support and parent coaching.", "deltona"),
        ("Coastal Inclusive Arts", "Adapted art and music for all abilities.", "newsmyrna"),
    ],
    q.FIELD_TRIPS: [
        ("Marine Science Center Tours", "Guided days at the seabird hospital.", "portorange"),
        ("DeBary Hall Living History", "Costumed tours of a Florida winter estate.", "debary"),
        ("Blue Spring Ranger Programs", "Manatee season talks and guided walks.", "orangecity"),
        ("Ponce Inlet Lighthouse Days", "Climb the light, tour the houses.", "portorange"),
        ("Volusia Field Trip Exchange", "A parent-run group that books the trips.", "deland"),
    ],
    q.PLAY_GROUPS: [
        ("Friday Park Day, West Volusia", "Open park day, all ages, every Friday.", "deland"),
        ("Beachside Homeschool Meetup", "A morning at the beach, weather permitting.", "newsmyrna"),
        ("Deltona Toddler Playgroup", "For under-fives and their grown-ups.", "deltona"),
        ("Teen Hangout Halifax", "Board games and a snack bar for ages 13-18.", "daytona"),
    ],
    q.PARENT_SUPPORT: [
        ("New to Homeschooling", "A monthly evening for first-year families.", "deland"),
        ("Special Needs Parent Circle", "Peer support for parents.", "portorange"),
        ("High School & Beyond", "Transcripts, testing and college applications.", "ormond"),
        ("Homeschool Dads of Volusia", "A monthly breakfast and a lot of coffee.", "deltona"),
    ],
}

FAITH_PATTERN = ["faith", "", "secular", "faith", ""]
DELIVERY_PATTERN = ["in_person", "hybrid", "online", "hybrid"]
SERVICE_AREAS = [
    "West Volusia, and Deltona by arrangement.",
    "Anywhere in Volusia County.",
    "Up to 30 miles from Daytona Beach.",
    "The beachside, from Ormond to New Smyrna.",
]
GRADES = ["K-5", "K-8", "grades 3-8", "grades 6-12", "high school only", "all ages"]
AGES = [(5, 11), (5, 14), (8, 14), (11, 18), (14, 18), (None, None)]


class Command(BaseCommand):
    help = "Create four or five example programs per heading, for looking at locally."

    def add_arguments(self, parser):
        parser.add_argument(
            "--clear",
            action="store_true",
            help="Remove the programs this command created, and nothing else. Matched "
            "on slug, so anything you have added by hand survives.",
        )

    def handle(self, *args, **options):
        if not settings.DEBUG:
            raise CommandError(
                "This only runs with DEBUG on. Fifty-seven invented programs in a live "
                "directory would be somebody's afternoon to undo."
            )

        slugs = [slugify(name)[:150] for names in PROGRAMS.values() for name, *_ in names]
        if options["clear"]:
            removed, _ = Program.objects.filter(slug__in=slugs).delete()
            self.stdout.write(self.style.SUCCESS(f"Removed {removed} rows."))
            return

        missing = set(q.EVERY) - set(Category.objects.values_list("slug", flat=True))
        if missing:
            raise CommandError(
                f"{len(missing)} headings are missing, so there is nothing to file these "
                "under. Run `manage.py seed_taxonomy` first."
            )

        with transaction.atomic():
            made = self._build()
        self.stdout.write(
            self.style.SUCCESS(f"Created {made} programs across {len(PROGRAMS)} headings.")
        )
        self.stdout.write("Run again with --clear to take them out.")

    def _build(self):
        headings = {
            category.slug: category
            for category in Category.objects.prefetch_related("tags__group")
        }
        made = 0
        for slug, entries in PROGRAMS.items():
            heading = headings[slug]
            for index, (name, one_liner, venue) in enumerate(entries):
                # Seeded per program, so re-running produces the same directory and
                # --clear can find everything by slug.
                dice = random.Random(f"{slug}:{name}")
                if self._program(heading, slug, index, name, one_liner, venue, dice):
                    made += 1
        return made

    def _program(self, heading, slug, index, name, one_liner, venue, dice):
        if Program.objects.filter(slug=slugify(name)[:150]).exists():
            return False

        days, start, end, cost_basis, cost_notes = SHAPES[slug]
        fields = {
            "highlights": self._highlights(name, heading),
            "cost_basis": cost_basis,
            "cost_notes": cost_notes,
            "meeting_days": days,
            "meeting_time_start": datetime.time(*start) if start else None,
            "meeting_time_end": datetime.time(*end) if end else None,
            "season_start": datetime.date(2026, 9, 1 + index),
            "season_end": datetime.date(2027, 5, 15 + index),
            "meeting_schedule": "No meetings the week of Thanksgiving." if index == 1 else "",
            "host_name": f"{name.split()[0]} Association" if index % 2 == 0 else "",
            "class_names": self._classes(slug),
            "instructor_info": (
                "Taught by two Florida-certified teachers, both homeschooling parents "
                "themselves. Background-checked and happy to talk before you enrol."
            ),
            "faith_basis": FAITH_PATTERN[index % len(FAITH_PATTERN)],
            "serves_grades": GRADES[index % len(GRADES)],
            "age_min": AGES[index % len(AGES)][0],
            "age_max": AGES[index % len(AGES)][1],
            "enrollment_opens": datetime.date(2026, 6, 1 + index),
            "enrollment_closes": datetime.date(2026, 8, 15 + index),
            "enrollment_notes": "Rolling after the start date, if there is room." if index else "",
            "step_up_direct_pay": index % 3 != 2,
            "step_up_pep": index % 3 == 0,
            "step_up_fes_ua": index % 4 == 0,
            "delivery": DELIVERY_PATTERN[index % len(DELIVERY_PATTERN)],
            "travels_to_student_home": index % 2 == 1,
            "service_area": SERVICE_AREAS[index % len(SERVICE_AREAS)],
        }
        # The whole point of reading the map rather than filling everything in: a
        # demo record shows exactly the questions its heading is asked, so a
        # scoping mistake is visible on the page rather than buried in a dict.
        asked = q.fields_for(slug)
        fields = {name_: value for name_, value in fields.items() if name_ in asked}

        program = Program.objects.create(
            name=name,
            slug=slugify(name)[:150],
            short_description=one_liner,
            description=self._description(name, one_liner, heading),
            category=heading,
            # One draft in the set, so the "not visible on the site" case is
            # visible in the admin too.
            status=Program.Status.DRAFT
            if (slug == q.TUTORING and index == 0)
            else Program.Status.PUBLISHED,
            is_featured=index == 0 and slug in {q.CO_OPS, q.SPORTS, q.MICROSCHOOLS},
            email=f"hello@{slugify(name)[:24]}.example.org",
            phone=f"386-555-{dice.randint(1000, 9999)}",
            website=f"https://{slugify(name)[:24]}.example.org" if index % 2 == 0 else "",
            facebook=(
                f"https://facebook.com/groups/{slugify(name)[:24]}" if index % 3 != 1 else ""
            ),
            last_verified_on=datetime.date(2026, 8, 1) if index % 2 == 0 else None,
            **fields,
        )
        program.tags.set(self._tags(heading, dice))
        self._places(program, venue, dice)
        return True

    def _tags(self, heading, dice):
        """One answer to each question this heading asks, and a subject tag or two."""
        chosen = []
        for group, answers in heading.question_groups():
            how_many = dice.randint(1, min(2, len(answers))) if group.allows_several else 1
            chosen.extend(dice.sample(answers, how_many))
        subjects = heading.plain_tags()
        if subjects:
            chosen.extend(dice.sample(subjects, min(2, len(subjects))))
        return chosen

    def _places(self, program, venue, dice):
        places = VENUES[venue]
        for order, (label, latitude, longitude, city, postcode) in enumerate(
            dice.sample(places, dice.randint(1, min(2, len(places))))
        ):
            ProgramLocation.objects.create(
                program=program,
                query=label,
                label=label,
                latitude=latitude,
                longitude=longitude,
                city=city,
                postcode=postcode,
                status=ProgramLocation.Status.RESOLVED,
                sort_order=order,
            )

    def _description(self, name, one_liner, heading):
        return (
            f"<p>{one_liner} {name} has been part of the {heading.name.lower()} "
            "landscape in Volusia County for several years, and families come to us "
            "from across the county.</p>"
            "<p>Everything here is invented for a local review of the directory. "
            "Nothing on this page describes a real program.</p>"
        )

    def _highlights(self, name, heading):
        return (
            f"Small groups, and the same people year after year — most of {name} is "
            "run by parents who are still homeschooling their own children.\n\n"
            "This text exists so that the 'what makes it special' section has "
            "something in it."
        )

    def _classes(self, slug):
        return {
            q.CORE_ACADEMICS: "Algebra I, Geometry, Biology with lab, American Literature",
            q.ARTS_AND_CRAFTS: "Wheel throwing, Hand building, Glaze chemistry",
            q.PERFORMING_ARTS: "Beginning ballet, Musical theatre, Group violin",
            q.ENRICHMENT: "Intro robotics, Competition team, Open build night",
        }.get(slug, "")
