from django.contrib.sitemaps import Sitemap
from django.urls import reverse


class StaticSitemap(Sitemap):
    """Public pages that should be indexed by search engines."""

    priority = 1.0
    changefreq = "weekly"

    def items(self):
        return ["landing"]

    def location(self, item):
        return reverse(item)