"""URL configuration."""

import re

from django.conf import settings
from django.contrib import admin
from django.urls import include, path, re_path
from django.views.i18n import JavaScriptCatalog

from config.health import health
from config.media import serve_media

urlpatterns = [
    # Something a probe can be pointed at without a session; config/health.py
    # says why it answers so little. Ungated (apps/accounts/pages.py OPEN).
    path('healthz', health, name='health'),
    path('admin/', admin.site.urls),
    # The topbar globe POSTs here to store the chosen language and redirect back.
    path('i18n/', include('django.conf.urls.i18n')),
    # Serves the compiled "djangojs" catalog as a script defining gettext() in
    # the browser, so static/js/*.js renders in the active language. Loaded from
    # base.html before any app script that calls it.
    path('jsi18n/', JavaScriptCatalog.as_view(), name='javascript-catalog'),
    path('accounts/', include('apps.accounts.urls')),
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
