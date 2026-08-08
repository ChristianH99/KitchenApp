"""The sign-in page, and signing out.

One page offering two doors, in the order they should be used: **Synology SSO**
as the button, the local password behind a disclosure below it. That ordering
is the whole design — the fallback has to be present, because an OIDC-only app
is unreachable exactly when SSO is broken, and it has to be visibly secondary,
because a household that gets into the habit of using it is a household with
passwords in two places.

With ``OIDC_ENABLED`` off (a fresh checkout, a development machine) the local
form is simply the only thing on the page, opened.
"""

import logging

from django.conf import settings
from django.contrib.auth import login as auth_login, logout as auth_logout
from django.contrib.auth.forms import AuthenticationForm
from django.shortcuts import redirect, render
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext as _
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from apps.accounts import sso, throttle
from apps.accounts.forms import PreferencesForm
from apps.accounts.models import Preferences

log = logging.getLogger(__name__)


def _safe_next(request, raw):
    """Where to go after signing in — only ever somewhere in this app.

    An unchecked ``?next=`` is an open redirect, and on a login page it is the
    useful kind for an attacker: a link that really does sign somebody in and
    then drops them on a page of somebody else's choosing, having just typed a
    password.
    """
    if raw and url_has_allowed_host_and_scheme(
        raw, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return raw
    return str(settings.LOGIN_REDIRECT_URL)


@never_cache
def login_view(request):
    """The local sign-in form, throttled.

    The refusal is deliberately the same sentence whether the username exists
    or not. Two different messages are an account enumeration oracle, and on an
    app whose users are a family that is a small thing — but it costs nothing
    to not have it.
    """
    next_url = _safe_next(request, request.POST.get("next") or request.GET.get("next"))

    if request.user.is_authenticated:
        return redirect(next_url)

    form = AuthenticationForm(request)
    locked_out = False

    if request.method == "POST":
        username = (request.POST.get("username") or "").strip()
        ip = throttle.client_ip(request)
        if throttle.is_locked_out(username, ip):
            locked_out = True
            log.info("Refused a throttled local login for %r from %s", username, ip)
        else:
            form = AuthenticationForm(request, data=request.POST)
            if form.is_valid():
                throttle.note_success(username, ip)
                auth_login(request, form.get_user())
                return redirect(next_url)
            throttle.note_failure(username, ip)

    oidc_enabled = sso.is_enabled()
    return render(request, "accounts/login.html", {
        "form": form,
        "next": next_url,
        "locked_out": locked_out,
        "lockout_minutes": max(1, settings.LOGIN_LOCKOUT_SECONDS // 60),
        # `is_enabled`, not the raw switch: it also asks whether the
        # configuration could conceivably complete a login. A button that leads
        # to the provider's error page reads as the provider being broken, when
        # what happened is that somebody ticked the box before filling the form
        # in — and this page is where they will come back to fix it.
        "oidc_enabled": oidc_enabled,
        # Open the local form when it is the only way in, when somebody asked
        # for it (?local=1), or when they have just been refused by it — coming
        # back to a page with the form folded away again would read as the
        # attempt having vanished.
        "show_local": (
            not oidc_enabled
            or request.method == "POST"
            or bool(request.GET.get("local"))
        ),
    })


@require_POST
def logout_view(request):
    """Sign out here, and — for an SSO session — at the provider too.

    A local logout only drops this app's cookie. The Synology session is still
    live, so the next click on "Sign in with Synology" goes straight back in
    without a prompt, which does not look like signing out at all. Handing over
    to ``oidc_logout`` is what makes the button mean what it says.
    """
    if request.session.get("oidc_id_token") and sso.is_enabled():
        # Handed over *before* logging out locally, because that view needs the
        # session it is ending: it reads the id token to build the provider's
        # end-session URL, and clearing the session here first would leave it
        # with nothing to send.
        return redirect(reverse("oidc_logout"))
    auth_logout(request)
    return redirect(settings.LOGOUT_REDIRECT_URL)


@login_required
def preferences(request):
    """One person's own settings — currently just what the kitchen timer does.

    Per person rather than per household: which noise cuts through *your*
    kitchen, and whether you want one at all at six in the morning, is about a
    body and not about the food. The recipes are shared; this is not.

    A GET does not create a row. ``Preferences.for_user`` hands back an unsaved
    instance for somebody who has never opened this page, because taking
    SQLite's one write lock to answer "no, you have not changed anything" is
    the read-must-not-write rule broken for nothing.
    """
    current = Preferences.for_user(request.user)

    if request.method == "POST":
        form = PreferencesForm(request.POST, instance=current)
        if form.is_valid():
            saved = form.save(commit=False)
            saved.user = request.user
            saved.save()
            messages.success(request, _("Your settings were saved."))
            return redirect("accounts:preferences")
    else:
        form = PreferencesForm(instance=current)

    return render(request, "accounts/preferences.html", {"form": form})
