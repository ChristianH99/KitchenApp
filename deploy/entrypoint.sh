#!/bin/sh
# Container start-up: bring the database up to date, then hand over to gunicorn.
#
# `exec` at the end matters. Without it this shell stays as PID 1 and gunicorn
# is its child, so Docker's SIGTERM on `docker stop` goes to the shell, which
# ignores it — and every restart waits out the ten-second kill timeout before
# the process is shot. With it, gunicorn *is* PID 1 and shuts down cleanly.
set -e

echo "→ applying migrations"
python manage.py migrate --noinput

# Deliberately not `collectstatic`: it ran at build time (see the Dockerfile).
# Running it here would write into the image on every boot, and on a read-only
# filesystem it would fail the container outright.

# A first-run hint rather than an automatic account. Creating a superuser
# non-interactively would mean a password in the environment — and this is the
# one account that exists to survive an SSO outage, so it should be typed by a
# person, once:
#
#   docker exec -it kitchen python manage.py createsuperuser
if [ "$(python manage.py shell -c 'from django.contrib.auth.models import User; print(User.objects.count())' 2>/dev/null)" = "0" ]; then
    echo "→ no accounts yet. Create the fallback administrator with:"
    echo "     docker exec -it <container> python manage.py createsuperuser"
fi

echo "→ starting $*"
exec "$@"
