"""Where ``mozilla_django_oidc`` gets its settings from.

The library reads its configuration with ``import_from_settings``, which is
``getattr(settings, ...)`` — static for the life of the process. To let the
configuration live in the database instead, every class that reads it overrides
one hook:

    @staticmethod
    def get_settings(attr, *args):
        return sso.get_setting(attr, *args)

That hook is ``get_setting`` below, and it is deliberately narrow: it answers
for the handful of ``OIDC_*`` names this app actually lets somebody edit, and
hands everything else straight to Django's settings. A shim that tried to answer
for *every* setting would be a second settings system, and the first thing to
go wrong would be a typo silently resolving to ``None``.

The four classes that had to be subclassed are the backend, the two
authentication views (wired in through ``OIDC_AUTHENTICATE_CLASS`` and
``OIDC_CALLBACK_CLASS``, so the URL names stay exactly what the SSO server has
registered) and the session-refresh middleware. ``OIDCLogoutView`` was left
alone: it reads only ``LOGOUT_REDIRECT_URL``, which nobody configures here.

**Caching.** This is consulted on every request that carries an OIDC session, so
it is cached — but only for a few seconds. The app runs two gunicorn workers and
each has its own local-memory cache, so a save in one worker cannot invalidate
the other; a short time-to-live is what makes them converge. Thirty seconds is
the deal: a change is live within half a minute everywhere, and nobody is
waiting on a spinner in the meantime.
"""

import logging

from django.conf import settings
from django.core.cache import cache

log = logging.getLogger(__name__)

CACHE_KEY = "kitchen.sso.configuration"
# Long enough that this is not a query per request, short enough that the second
# gunicorn worker cannot serve a stale provider for meaningfully longer than it
# takes to notice.
CACHE_SECONDS = 30


def current(refresh=False):
    """The active configuration: the stored row, or one seeded from `.env`.

    Never raises. This is called from the login page and from middleware, so a
    database that is unavailable or a table that has not been migrated yet must
    degrade to "whatever the environment says" rather than to a 500 — the local
    login has to keep working precisely when things are broken.
    """
    if not refresh:
        cached = cache.get(CACHE_KEY)
        if cached is not None:
            return cached

    from apps.accounts.models import SSOConfiguration

    try:
        configuration = SSOConfiguration.load()
    except Exception:  # noqa: BLE001 - see the docstring
        log.exception("Could not read the SSO configuration; falling back to the environment")
        try:
            return SSOConfiguration.from_environment()
        except Exception:  # noqa: BLE001
            return None

    cache.set(CACHE_KEY, configuration, CACHE_SECONDS)
    return configuration


def invalidate():
    cache.delete(CACHE_KEY)


def is_enabled():
    """Whether to offer the Synology button at all.

    ``is_usable`` rather than ``enabled``: a button that leads to the provider's
    error page reads as the provider being broken, when what has actually
    happened is that somebody ticked the box before filling the form in.
    """
    configuration = current()
    return bool(configuration and configuration.is_usable)


# The names this app answers for. Everything else falls through to Django's
# settings — see the module docstring for why the list is closed.
def get_setting(attr, *args):
    configuration = current()
    if configuration is not None:
        endpoints = configuration.endpoints()
        answers = {
            "OIDC_RP_CLIENT_ID": configuration.client_id,
            "OIDC_RP_CLIENT_SECRET": configuration.client_secret,
            "OIDC_RP_SIGN_ALGO": configuration.sign_algo,
            "OIDC_RP_SCOPES": configuration.scopes,
            "OIDC_OP_AUTHORIZATION_ENDPOINT": endpoints["authorization"],
            "OIDC_OP_TOKEN_ENDPOINT": endpoints["token"],
            "OIDC_OP_USER_ENDPOINT": endpoints["user"],
            "OIDC_OP_JWKS_ENDPOINT": endpoints["jwks"],
            "OIDC_VERIFY_SSL": configuration.verify_ssl,
        }
        if attr in answers:
            return answers[attr]

    if args:
        return getattr(settings, attr, args[0])
    return getattr(settings, attr)
