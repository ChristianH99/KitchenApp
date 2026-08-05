"""``/healthz`` — the one ungated URL in the app.

Two decisions are worth stating, because both look like omissions:

**It says almost nothing.** One word, or a 503. It is unauthenticated, so
anything richer — a version, a user count, whether SSO is reachable — would be
household state handed to whoever asks. A check does not need it.

**It runs one query.** A process that is listening while its database has gone
is exactly the failure a health check exists to catch, and a view that returns
"ok" without touching the database cannot see it. One ``SELECT 1`` is the
cheapest thing that can.

The container's HEALTHCHECK and any uptime probe point here over plain HTTP on
127.0.0.1, which is also why nothing in settings.py redirects it to HTTPS.
"""

from django.db import connection
from django.http import HttpResponse
from django.views.decorators.cache import never_cache


@never_cache
def health(request):
    try:
        with connection.cursor() as cursor:
            cursor.execute('SELECT 1')
            cursor.fetchone()
    except Exception:  # noqa: BLE001 - any failure at all is an unhealthy server
        return HttpResponse('unhealthy', status=503, content_type='text/plain')
    return HttpResponse('ok', content_type='text/plain')
