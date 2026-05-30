"""
URL configuration for brainboost project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/4.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.contrib.sitemaps.views import sitemap
from django.http import Http404
from django.http import HttpResponse
from django.urls import include, path
from django.conf import settings
from django.conf.urls.static import static

from core.sitemaps import StaticViewSitemap


def robots_txt(request):
    return HttpResponse(
        "User-agent: *\n"
        "Allow: /\n\n"
        "Sitemap: https://www.nachhilfe-brainboost.de/sitemap.xml\n",
        content_type="text/plain",
    )


def indexnow_key_file(request, indexnow_key):
    configured_key = settings.INDEXNOW_KEY
    if settings.DEBUG or not configured_key or indexnow_key != configured_key:
        raise Http404("IndexNow key file is not available.")
    return HttpResponse(configured_key, content_type="text/plain")


sitemaps = {
    "static": StaticViewSitemap,
}


urlpatterns = [
    path('admin/', admin.site.urls),
    path('i18n/', include('django.conf.urls.i18n')),
    path('sitemap.xml', sitemap, {'sitemaps': sitemaps}, name='django.contrib.sitemaps.views.sitemap'),
    path('robots.txt', robots_txt, name='robots_txt'),
    path('<str:indexnow_key>.txt', indexnow_key_file, name='indexnow_key_file'),
    path('', include('core.urls')),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
