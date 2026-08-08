"""URL configuration."""

import re

from django.conf import settings
from django.contrib import admin
from django.urls import include, path, re_path
from django.views.i18n import JavaScriptCatalog

from config.health import health
from config.media import serve_media


def favicon(request):
    """Send /favicon.ico to the real icon under /static/.

    A browser asks for this on its own whenever a page does not name an icon,
    and it asks *per origin*, not per page — so with nothing here every visit
    logged `WARNING Not Found: /favicon.ico`, and the interesting warnings sat
    in among them. base.html names the icon now, which stops most of these
    being made at all; this catches the rest — a bookmark, a browser that
    ignores the tag, anything fetching the origin root.

    The URL is resolved *inside* the view rather than at import time. `STORAGES`
    uses WhiteNoise's manifest storage in every mode, so asking for a static URL
    raises until `collectstatic` has run — and doing that while the URLconf is
    being imported would make every management command fail on a fresh
    checkout, `collectstatic` itself included.

    Permanent, because it is: this is where the icon lives. Browsers cache it
    and stop asking.
    """
    from django.contrib.staticfiles.storage import staticfiles_storage
    from django.shortcuts import redirect

    return redirect(staticfiles_storage.url('icons/favicon.svg'), permanent=True)


urlpatterns = [
    # Something a probe can be pointed at without a session; config/health.py
    # says why it answers so little. Ungated (apps/accounts/pages.py OPEN).
    path('healthz', health, name='health'),
    # Ungated too: the login page needs its own icon, and an icon is not
    # household data.
    path('favicon.ico', favicon, name='favicon'),
    path('admin/', admin.site.urls),
    # The topbar globe POSTs here to store the chosen language and redirect back.
    path('i18n/', include('django.conf.urls.i18n')),
    # Serves the compiled "djangojs" catalog as a script defining gettext() in
    # the browser, so static/js/*.js renders in the active language. Loaded from
    # base.html before any app script that calls it.
    path('jsi18n/', JavaScriptCatalog.as_view(), name='javascript-catalog'),
    path('accounts/', include('apps.accounts.urls')),
    path('pantry/', include('apps.pantry.urls')),
    # Un-namespaced on purpose — see apps/accounts/urls.py. This is what fixes
    # the callback URL at /oidc/callback/, which is the exact string that has to
    # be registered as the redirect URI in the Synology SSO client.
    path('oidc/', include('mozilla_django_oidc.urls')),
    path('', include('apps.recipes.urls')),
]

# Uploaded photographs. Unlike static files these are written at runtime, so
# WhiteNoise — which indexes STATIC_ROOT once at startup — cannot serve them and
# with DEBUG off every recipe image would 404. Routed through config/media.py so
# they stay behind the login. django.conf.urls.static.static() is not usable
# here: it returns nothing unless DEBUG.
if settings.SERVE_MEDIA:
    urlpatterns += [
        re_path(
            r'^%s(?P<path>.*)$' % re.escape(settings.MEDIA_URL.lstrip('/')),
            serve_media,
            {'document_root': settings.MEDIA_ROOT},
        ),
    ]
