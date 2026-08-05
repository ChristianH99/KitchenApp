"""Throttling and logging the *local* login.

Only the local one. The SSO login never reaches this app until the provider has
already authenticated somebody, so rate-limiting it is Synology's job — and
counting failures we cannot see would be a counter that never moves.

Counted two ways, because they answer different questions. Per
``(username, IP)`` is somebody guessing one person's password. Per IP across
every username is somebody working through a list of accounts, which the first
counter cannot see at all: ten usernames tried once each is ten attempts and
nine untouched counters.

Counters live in the local-memory cache, so a restart forgets them. Acceptable:
this is one process, an attacker cannot cause a restart, and the alternative is
a database row per attempt.
"""

import logging

from django.conf import settings
from django.core.cache import cache

log = logging.getLogger(__name__)

_USER_KEY = "login-fail:%s:%s"
_HOST_KEY = "login-fail-host:%s"


def client_ip(request):
    """The caller's address, taking the proxy's word for it only when settings
    say the proxy is trusted. On a direct connection ``X-Forwarded-For`` is a
    client-supplied header — believing it there would let anybody reset their
    own throttle counter by sending a different value each time."""
    if getattr(settings, "USE_X_FORWARDED_HOST", False):
        forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "") or "unknown"


def _keys(username, ip):
    return _USER_KEY % (username.lower(), ip), _HOST_KEY % ip


def is_locked_out(username, ip):
    user_key, host_key = _keys(username, ip)
    return (
        cache.get(user_key, 0) >= settings.LOGIN_MAX_ATTEMPTS
        or cache.get(host_key, 0) >= settings.LOGIN_MAX_ATTEMPTS_PER_HOST
    )


def note_failure(username, ip):
    for key in _keys(username, ip):
        # `add` then `incr`: incr on a missing key raises, and setting the value
        # outright would reset the window on every attempt — which is a counter
        # that can never reach its limit.
        cache.add(key, 0, settings.LOGIN_LOCKOUT_SECONDS)
        try:
            cache.incr(key)
        except ValueError:
            # The key expired between the add and the incr. One lost attempt.
            cache.set(key, 1, settings.LOGIN_LOCKOUT_SECONDS)
    log.info("Failed local login for %r from %s", username, ip)


def note_success(username, ip):
    user_key, _ = _keys(username, ip)
    cache.delete(user_key)
    # The per-host counter is deliberately *not* cleared: one successful login
    # among fifty failures is what a working attack looks like.
    log.info("Local login by %r from %s", username, ip)
