"""Who may reach the administration pages in this app.

Two levels, and the difference is deliberate rather than decorative:

* ``staff_required`` — the People page. Managing who is in the household.
* ``superuser_required`` — the SSO page. Deciding *how everybody
  authenticates*, and holding the client secret. A narrower door, because
  "may add an account" and "may repoint the whole app at a different identity
  provider" are not the same right, and the second one can be used to take the
  first.

Both answer **404, not 403**, the same choice the recipe edit view makes: a bare
403 is a dead end with no way back into the app, while a 404 renders the app's
own not-found page — and there is nothing to conceal either way, since the links
are only in the sidebar for the people who may follow them.

This is per-view authorisation, which is the opposite of the rule in
apps/accounts/pages.py — and the two are answering different questions. Whether
a page needs a *session* is a property of the whole app, so it is enumerated
centrally and fails towards refusal. Whether a signed-in member of the household
may see *these* pages is a property of the pages. The exposure a forgotten
decorator would create is covered from the other side, by tests that walk the
URLconf and refuse to let any of these routes answer an account without the
right.
"""

from functools import wraps

from django.http import Http404


def staff_required(view):
    @wraps(view)
    def guarded(request, *args, **kwargs):
        if not request.user.is_staff:
            raise Http404
        return view(request, *args, **kwargs)

    return guarded


def superuser_required(view):
    @wraps(view)
    def guarded(request, *args, **kwargs):
        if not request.user.is_superuser:
            raise Http404
        return view(request, *args, **kwargs)

    return guarded
