"""WSGI entry point — and the place a *server's* own startup rules live.

The ALLOWED_HOSTS check is here rather than in settings.py on purpose.
``collectstatic`` is a required release step and it runs with DEBUG off and no
hosts configured, quite legitimately; only something that is about to answer
requests needs the setting. Left empty, the process starts and then refuses
every request with DisallowedHost — which from a phone in the kitchen reads as
"the app is broken", with nothing in the container log saying otherwise.
"""

import os

from django.core.exceptions import ImproperlyConfigured
from django.core.wsgi import get_wsgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

application = get_wsgi_application()

from django.conf import settings  # noqa: E402  (must follow setup)

if not settings.DEBUG and not settings.ALLOWED_HOSTS:
    raise ImproperlyConfigured(
        'DJANGO_ALLOWED_HOSTS is empty and DEBUG is False, so this server would '
        'refuse every request it receives. Set it to the hostname the reverse '
        'proxy forwards, e.g. DJANGO_ALLOWED_HOSTS=kitchen.haeusslerr.de'
    )
