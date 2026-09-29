"""Which questions each kind of program is asked.

Thirteen kinds of program, and the site owner's field list for each one reads as
though they were thirteen different records. They are not. After normalising the
wording, nine questions are asked of all thirteen, four more of eleven or twelve,
and exactly one field in the whole document belongs to a single kind of program.
What varies is which questions get asked — not what shape the answers take.

So there is one `Program` table and one registration form, and this module is the
part that differs. It is a map from a field name to the headings that ask about
it. The form reads it to decide what to show, what to require, and what to
quietly discard when somebody fills in half the form and then changes the
heading; the program page reads nothing at all, because a field nobody answered
is simply blank.

Three deliberate omissions.

There is no per-heading wording. The document asks field trips about "certain
recurring dates" and asks a co-op about "meeting days", and those are the same
question; one neutral label that works everywhere beats thirteen labels that have
to agree with each other. The single form is why: a field can only carry one
label at a time.

There is nothing here about the answers to a question — beginner or advanced,
drop off or stay and help. Those are tags in a `TagGroup`, chosen in the admin
and filterable through the same code path as every other tag, so adding a
question costs a row rather than a deploy.

And this lives in code rather than in a table she can edit, because a field only
exists if a column exists. A screen offering to add a question we have nowhere to
put the answer to would be a screen that lies. A heading added in the admin
tomorrow is not broken by that: it falls through to `CORE`, gets asked the
thirteen questions everybody is asked, and can be tailored here later.
"""

# --- The thirteen headings --------------------------------------------------

CO_OPS = "co-ops"
CORE_ACADEMICS = "core-academics"
SPORTS = "sports-and-recreation"
ARTS_AND_CRAFTS = "arts-and-crafts"
TESTING = "testing-and-evaluation"
SPECIAL_NEEDS = "special-needs-support"
PERFORMING_ARTS = "performing-arts"
ENRICHMENT = "enrichment-and-clubs"
TUTORING = "tutoring"
FIELD_TRIPS = "field-trips"
PLAY_GROUPS = "play-and-social-groups"
PARENT_SUPPORT = "parent-support-groups"
MICROSCHOOLS = "microschools"

EVERY = frozenset(
    {
        CO_OPS,
        CORE_ACADEMICS,
        SPORTS,
        ARTS_AND_CRAFTS,
        TESTING,
        SPECIAL_NEEDS,
        PERFORMING_ARTS,
        ENRICHMENT,
        TUTORING,
        FIELD_TRIPS,
        PLAY_GROUPS,
        PARENT_SUPPORT,
        MICROSCHOOLS,
    }
)

# The names, icons and running order behind these slugs are seed content rather
# than structure, so they live in `manage.py seed_taxonomy` with the tag
# vocabulary. What matters here is only which heading asks what.


def _all_but(*slugs):
    missing = set(slugs) - EVERY
    assert not missing, f"not headings: {sorted(missing)}"
    return EVERY - set(slugs)


# The four types that list classes under a larger host: the listing is the class,
# and the host is a separate thing worth naming.
_CLASSES_UNDER_A_HOST = frozenset(
    {CORE_ACADEMICS, ARTS_AND_CRAFTS, PERFORMING_ARTS, ENRICHMENT}
)

# The three that come to you, or meet wherever suits, rather than at an address.
_COMES_TO_YOU = frozenset({TESTING, SPECIAL_NEEDS, TUTORING})


# --- The map ---------------------------------------------------------------

ASKED_BY = {
    # Asked of everybody. A directory listing with none of these is not a listing.
    "program_name": EVERY,
    "short_description": EVERY,
    "description": EVERY,
    "category": EVERY,
    "tags": EVERY,
    "highlights": EVERY,
    "website": EVERY,
    "facebook": EVERY,
    "email": EVERY,
    "phone": EVERY,
    "locations": EVERY,
    "cost_basis": EVERY,
    "cost_notes": EVERY,
    "logo": EVERY,
    "submitter_name": EVERY,
    "submitter_email": EVERY,
    "submitter_role": EVERY,
    "is_authorized": EVERY,
    # Days of the week and the run of dates, asked of every heading at the site
    # owner's instruction — a testing service has a season and an hour too, and a
    # family filtering for Saturdays should not have whole headings hidden from
    # them because we decided in advance that they do not meet on one.
    "meeting_days": EVERY,
    "meeting_time_start": EVERY,
    "meeting_time_end": EVERY,
    "season_start": EVERY,
    "season_end": EVERY,
    "meeting_schedule": EVERY,
    # A parent support group serves the adults. Asking it what grades it takes is
    # the sort of question that makes a form feel like it was written for
    # somebody else.
    "serves_grades": _all_but(PARENT_SUPPORT),
    "age_min": _all_but(PARENT_SUPPORT),
    "age_max": _all_but(PARENT_SUPPORT),
    # Step Up pays for instruction. A park day and a parent coffee morning are
    # not instruction, and asking them to tick a scholarship box invites a wrong
    # answer we would then have to publish.
    "step_up_direct_pay": _all_but(PLAY_GROUPS, PARENT_SUPPORT),
    "step_up_pep": _all_but(PLAY_GROUPS, PARENT_SUPPORT),
    "step_up_fes_ua": _all_but(PLAY_GROUPS, PARENT_SUPPORT),
    "host_name": _CLASSES_UNDER_A_HOST,
    "class_names": _CLASSES_UNDER_A_HOST,
    # Who teaches, asked wherever somebody is being taught rather than met.
    "instructor_info": frozenset(
        {
            CORE_ACADEMICS,
            SPORTS,
            ARTS_AND_CRAFTS,
            TESTING,
            SPECIAL_NEEDS,
            PERFORMING_ARTS,
            ENRICHMENT,
            TUTORING,
        }
    ),
    "faith_basis": frozenset(
        {
            CO_OPS,
            CORE_ACADEMICS,
            SPORTS,
            ARTS_AND_CRAFTS,
            SPECIAL_NEEDS,
            PERFORMING_ARTS,
            ENRICHMENT,
            MICROSCHOOLS,
        }
    ),
    # Enrollment windows belong to programs you join for a term. A field trip
    # destination and a park day have nothing to open or close.
    "enrollment_opens": frozenset(
        {CO_OPS, CORE_ACADEMICS, SPORTS, ARTS_AND_CRAFTS, PERFORMING_ARTS, ENRICHMENT, MICROSCHOOLS}
    ),
    "enrollment_closes": frozenset(
        {CO_OPS, CORE_ACADEMICS, SPORTS, ARTS_AND_CRAFTS, PERFORMING_ARTS, ENRICHMENT, MICROSCHOOLS}
    ),
    "enrollment_notes": frozenset(
        {CO_OPS, CORE_ACADEMICS, SPORTS, ARTS_AND_CRAFTS, PERFORMING_ARTS, ENRICHMENT, MICROSCHOOLS}
    ),
    "delivery": _COMES_TO_YOU,
    "travels_to_student_home": _COMES_TO_YOU,
    "service_area": _COMES_TO_YOU | {FIELD_TRIPS},
}

# What a heading gets asked when nobody has said otherwise — a new heading added
# in the admin, or a program filed under none at all. The questions everyone
# answers, and nothing speculative.
CORE = frozenset(name for name, headings in ASKED_BY.items() if headings == EVERY)

# Required wherever it is asked. Deliberately short: a registration that cannot
# be published on approval defeats the point of the long form, and everything
# beyond that list is better answered badly later than not answered now.
REQUIRED = frozenset(
    {
        "program_name",
        "short_description",
        "description",
        "locations",
        "serves_grades",
        "submitter_name",
        "submitter_email",
        "is_authorized",
    }
)


def fields_for(slug):
    """Every field name the given heading asks about.

    An unknown heading — or none at all, which is what the form sees before
    anybody picks one — gets the core questions. Not an empty set: a form that
    asks nothing until you have chosen a heading is a form that looks broken with
    JavaScript switched off.
    """
    if slug not in EVERY:
        return CORE
    return frozenset(name for name, headings in ASKED_BY.items() if slug in headings)


def required_for(slug):
    """What that heading must answer: the required list, minus what it is not asked."""
    return REQUIRED & fields_for(slug)


def headings_asking(field):
    """Which headings ask about a field. Everything, for a field nobody scoped."""
    return ASKED_BY.get(field, EVERY)


def is_asked(field, slug):
    return field in fields_for(slug)
