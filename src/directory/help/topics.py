"""The guide's topics, in reading order (#39).

Kept apart from the views, and free of anything that needs Django set up,
because the admin sidebar's settings (`volusiahd/unfold.py`) read it too, and
settings load before the admin does.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Topic:
    slug: str
    title: str
    summary: str


TOPICS = [
    Topic(
        "submissions",
        "New submissions",
        "Registrations, referrals and suggested edits: approving, applying and rejecting.",
    ),
    Topic(
        "tag-suggestions",
        "Tag suggestions",
        "New words people suggested when they registered.",
    ),
    Topic(
        "programs",
        "Programs",
        "Editing a listing, putting it on the map, verifying it and taking it down.",
    ),
    Topic(
        "pages-and-search",
        "Pages and search",
        "The About and FAQ pages, and words search should treat as the same.",
    ),
    Topic(
        "vocabulary",
        "Categories, tags and questions",
        "The directory's vocabulary, and which changes to it last.",
    ),
    Topic(
        "people",
        "Who can sign in",
        "Adding and removing editors. Superusers only.",
    ),
]

BY_SLUG = {topic.slug: topic for topic in TOPICS}
