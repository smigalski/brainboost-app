from django.conf import settings
from django.contrib.sitemaps import Sitemap
from django.urls import reverse
from urllib.parse import urlsplit


def get_canonical_host():
    value = settings.CANONICAL_DOMAIN.strip()
    parsed = urlsplit(value if "://" in value else f"//{value}")
    return (parsed.netloc or parsed.path).strip("/")


def get_static_sitemap_urls():
    return [entry["location"] for entry in StaticViewSitemap().get_urls()]


class StaticViewSitemap(Sitemap):
    protocol = "https"
    changefreq = "weekly"

    priorities = {
        "landing_page": 1.0,
        "nachhilfe_anfrage": 0.9,
        "landing_eltern": 0.8,
        "landing_schuelerinnen": 0.8,
        "tutor_werden": 0.8,
    }

    def items(self):
        return tuple(self.priorities)

    def location(self, item):
        return reverse(item)

    def priority(self, item):
        return self.priorities[item]

    def get_domain(self, site=None):
        return get_canonical_host()
