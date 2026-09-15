"""Seed the category list from the plan. Safe to re-run; it never overwrites."""

from django.core.management.base import BaseCommand
from django.utils.text import slugify

from directory.models import Category

CATEGORIES = [
    ("Co-ops", 10, "groups", "Parent-run groups that meet regularly to learn together."),
    (
        "Tutorials and classes",
        20,
        "school",
        "Paid or volunteer-taught classes in specific subjects.",
    ),
    (
        "Sports and recreation",
        30,
        "sports_soccer",
        "Teams, leagues, and physical education options.",
    ),
    ("Arts and music", 40, "palette", "Lessons, ensembles, studios, and theatre."),
    ("Testing and evaluation", 50, "fact_check", "Annual evaluators and standardized testing."),
    (
        "Special needs support",
        60,
        "accessibility",
        "Services and groups for students with additional needs.",
    ),
    (
        "Umbrella and cover schools",
        70,
        "umbrella",
        "Private schools that enroll homeschoolers for reporting.",
    ),
    (
        "Field trips",
        80,
        "hiking",
        "Recurring group outings and destinations that host homeschool days.",
    ),
    (
        "Parent support groups",
        90,
        "diversity_3",
        "Places for parents to ask questions and compare notes.",
    ),
]


class Command(BaseCommand):
    help = "Create the starting set of categories if they do not already exist."

    def handle(self, *args, **options):
        created = 0
        for name, sort_order, icon, description in CATEGORIES:
            _, was_created = Category.objects.get_or_create(
                slug=slugify(name),
                defaults={
                    "name": name,
                    "sort_order": sort_order,
                    "icon": icon,
                    "description": description,
                },
            )
            created += was_created
        existing = len(CATEGORIES) - created
        self.stdout.write(
            self.style.SUCCESS(f"Categories: {created} created, {existing} already present.")
        )
