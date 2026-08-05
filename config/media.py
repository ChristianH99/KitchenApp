"""Serving uploaded recipe photographs — behind the login.

``django.views.static.serve`` is wired up directly in most projects, which
publishes MEDIA_ROOT to anybody who knows or guesses a path. Every other page in
this app needs a session and a photograph of somebody's kitchen table is no
different, so the same rule applies here. Guessing is not much of a barrier
either: the filenames are ours (see apps/recipes/images.py) but they are short
and enumerable, and "not linked from anywhere" has never been access control.

For a NAS with a real reverse proxy this can be handed over to it instead
(``DJANGO_SERVE_MEDIA=False``) — at the cost of exactly the gate above, which is
why it is not the default.
"""

from django.contrib.auth.decorators import login_required
from django.views.static import serve


@login_required
def serve_media(request, path, document_root=None):
    return serve(request, path, document_root=document_root)
