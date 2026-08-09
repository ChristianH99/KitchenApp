"""Two middlewares: the login gate, and a tamed OIDC session refresh."""

from urllib.parse import urlencode

from django.conf import settings
from django.shortcuts import redirect
from django.urls import reverse
from mozilla_django_oidc.middleware import SessionRefresh

from apps.accounts import pages, sso


class LoginRequiredMiddleware:
    """Refuse an anonymous request to anything not named in ``pages.OPEN``.

    The check runs in ``process_view`` rather than ``__call__`` because that is
    the first point at which ``resolver_match`` exists — and the pair it holds
    is what the open list is written in terms of. Matching on ``request.path``
    instead would mean a prefix comparison, and a prefix comparison is how
    ``/media/`` came to cover ``/mediathek/`` in somebody else's app.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_view(self, request, view_func, view_args, view_kwargs):
        if request.user.is_authenticated:
            return None
        match = request.resolver_match
        if match is not None and (match.app_name or "", match.url_name) in pages.OPEN:
            return None
        # The Django admin runs its own login; sending it through ours would
        # land a staff user on a page that cannot reach /admin/ afterwards.
        if request.path.startswith("/admin/"):
            return None
        login_url = reverse(settings.LOGIN_URL)
        return redirect(f"{login_url}?{urlencode({'next': request.get_full_path()})}")


class OIDCSessionRefresh(SessionRefresh):
    """Re-check the identity provider — but only for sessions that came from it.

    ``mozilla_django_oidc``'s own middleware asks one question: does this
    session carry an unexpired ``oidc_id_token_expiration``? Anything else is
    redirected to the authorization endpoint to find out. For an app where OIDC
    is the *only* way in, that is correct. Here it is wrong in both directions
    and each way is a real failure:

    * The **local fallback account** has no OIDC expiry, because it never spoke
      to a provider. Unpatched, it is bounced to the SSO server on its very
      first request — so the one account that exists to get in *while SSO is
      broken* is the account SSO breakage locks out. The fallback would look
      like it worked (the login succeeds) and then never render a page.
    * With **OIDC switched off entirely** — a fresh checkout, a development
      machine — there is no authorization endpoint to redirect to, and building
      the redirect from an empty setting raises rather than returning a page.

    So the refresh applies to a session that actually holds an OIDC token, and
    only while the provider is configured at all.

    The three properties below exist because ``SessionRefresh.__init__`` reads
    the provider's details **once, at process start**, and the configuration
    now lives in the database where a superuser can change it. Without them a
    changed SSO server would keep redirecting to the old one until the container
    was restarted — which is precisely the restart this whole feature exists to
    remove. Each has a setter that swallows the assignment the base class makes;
    the value it read at start-up is exactly the stale one being avoided.
    """

    @property
    def OIDC_OP_AUTHORIZATION_ENDPOINT(self):
        return sso.get_setting("OIDC_OP_AUTHORIZATION_ENDPOINT", "")

    @OIDC_OP_AUTHORIZATION_ENDPOINT.setter
    def OIDC_OP_AUTHORIZATION_ENDPOINT(self, value):
        pass

    @property
    def OIDC_RP_CLIENT_ID(self):
        return sso.get_setting("OIDC_RP_CLIENT_ID", "")

    @OIDC_RP_CLIENT_ID.setter
    def OIDC_RP_CLIENT_ID(self, value):
        pass

    @property
    def OIDC_RP_SCOPES(self):
        return sso.get_setting("OIDC_RP_SCOPES", "openid email")

    @OIDC_RP_SCOPES.setter
    def OIDC_RP_SCOPES(self, value):
        pass

    def is_refreshable_url(self, request):
        # The session check first, and it is free: without it every request from
        # a local-password session would cost a configuration lookup to answer a
        # question the session cookie had already settled.
        #
        # The session's own token, not the user's, and that distinction stopped
        # being theoretical the day the two kinds of account could be linked: a
        # person with both signs in through the local form on the morning the
        # provider is down, and has no provider session to renew. Asking the
        # *account* whether it is an SSO account would bounce them straight to
        # the SSO server they are working around.
        if not request.session.get("oidc_id_token"):
            return False
        if not sso.is_enabled():
            return False
        return super().is_refreshable_url(request)
