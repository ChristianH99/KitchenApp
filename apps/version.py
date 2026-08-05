"""Which build is running, for the page to say.

One line of context, and it exists because of the question asked immediately
after every update: *did it actually take?* The NAS answers "the container
restarted", the registry answers "a new image exists", and neither of those is
the same as the browser in somebody's hand having loaded the new code — a
cached page, a stale container kept alive by `restart: unless-stopped`, or an
update applied to the wrong compose project all look like success from the
outside.

So `deploy/Dockerfile` bakes the release tag in as ``KITCHEN_VERSION`` and the
sidebar prints it. `docker inspect` answers "what did I build"; this answers
"what am I looking at", and after a failed update those are different questions.

Empty in a checkout, and empty renders as nothing at all — a version string is
a claim, and the only honest claim a working copy can make is silence.
"""

from django.conf import settings


def context(request):
    return {"app_version": settings.KITCHEN_VERSION}
