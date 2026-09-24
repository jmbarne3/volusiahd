from django.contrib.sitemaps import Sitemap
from django.urls import reverse

from .models import Category, Page, Program, Tag


class ProgramSitemap(Sitemap):
    changefreq = "monthly"
    priority = 0.8

    def items(self):
        return Program.objects.published()

    def lastmod(self, obj):
        return obj.updated_at


class CategorySitemap(Sitemap):
    changefreq = "weekly"
    priority = 0.6

    def items(self):
        return Category.objects.all()


class TagSitemap(Sitemap):
    """Lower priority than a category on purpose: there are many more of these,
    and a tag page is a narrower answer than a category page."""

    changefreq = "weekly"
    priority = 0.4

    def items(self):
        return Tag.objects.filter(programs__status=Program.Status.PUBLISHED).distinct()


class PageSitemap(Sitemap):
    changefreq = "yearly"
    priority = 0.5

    def items(self):
        return Page.objects.filter(is_published=True)

    def lastmod(self, obj):
        return obj.updated_at


class StaticSitemap(Sitemap):
    changefreq = "weekly"
    priority = 1.0

    def items(self):
        return [
            "directory:home",
            "directory:program_list",
            "directory:register",
            "directory:refer",
        ]

    def location(self, item):
        return reverse(item)


SITEMAPS = {
    "static": StaticSitemap,
    "programs": ProgramSitemap,
    "categories": CategorySitemap,
    "tags": TagSitemap,
    "pages": PageSitemap,
}
