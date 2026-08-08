"""The Synology SSO settings page.

Superuser only — apps/accounts/permissions.py says why this door is narrower
than the People page's.

Two helper actions sit beside the form, and both exist because the failures they
prevent are the ones that cost a whole evening on this stack:

* **Read the endpoints from the server.** DEPLOYMENT.md §3.1 says not to trust
  any endpoint URL written down anywhere, including this app's own defaults,
  because Synology has moved them between DSM versions. The instruction used to
  be "run this curl and copy four values into `.env`". This is that curl, run
  from inside the container, writing the four values into the form.
* **Check the connection.** The container has to reach the SSO server itself for
  the back-channel token exchange, and on a home network it frequently cannot —
  the browser gets there from outside while the container's request leaves the
  house, comes back to the WAN address and is dropped (DEPLOYMENT.md §3.3). The
  symptom is a login that gets all the way to the Synology password page and
  then fails with a connection error in a log nobody is reading. This asks the
  question directly, from the right machine, before anybody tries to sign in.

Both make an outbound request to an address a superuser typed. That is what
configuring an identity provider *is*, and it is worth being clear-eyed rather
than pretending otherwise: the mitigations here are that the right is limited to
superusers, that redirects are not followed, that the timeout is short and that
only a summary is shown — never the response body.
"""

import json
import logging
import re

import requests
from django.conf import settings
from django.contrib import messages
from django.shortcuts import redirect, render
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from apps.accounts.forms import SSOConfigurationForm
from apps.accounts.models import SSOConfiguration
from apps.accounts.permissions import superuser_required

log = logging.getLogger(__name__)

# Short, because somebody is watching the page. A provider that has not answered
# in five seconds is a provider that would have failed the login anyway. The
# configured timeout is allowed to raise it, up to this cap — a page that hangs
# for a minute because somebody typed 60 into the box is a page people reload,
# which starts the whole request again.
TIMEOUT = 5
TIMEOUT_CAP = 15
# Enough for any discovery document; a cap so a wrong address pointing at
# something enormous cannot be streamed into memory.
MAX_BYTES = 256 * 1024

# The form fields the discovery document fills in, and the keys it uses for
# them. Order matters only in that it is the order they are shown.
ENDPOINT_FIELDS = {
    "authorization_endpoint": "authorization_endpoint",
    "token_endpoint": "token_endpoint",
    "user_endpoint": "userinfo_endpoint",
    "jwks_endpoint": "jwks_uri",
}

# Addresses that are never the one to register with a provider.
LOCAL_HOSTS = {"localhost", "127.0.0.1", "0.0.0.0", "[::1]", "::1"}


@superuser_required
def sso_settings(request):
    configuration = SSOConfiguration.load()

    if request.method == "POST":
        # Discovery runs *before* validation, writing into the submitted data,
        # so that everything downstream — including the rule that RS256 needs a
        # JWKS address — sees the endpoints the server just gave us rather than
        # the blanks somebody was never asked to fill in.
        data = request.POST.copy()
        used, discovery_error = _autofill_endpoints(data, configuration)
        form = SSOConfigurationForm(data, instance=configuration)
        if form.is_valid():
            if used and not discovery_error:
                form.instance.endpoints_read_at = timezone.now()
            saved = form.save(updated_by=request.user)
            # Security-relevant, and the log is the only place it is recorded.
            # Deliberately says *what changed shape*, never the secret.
            log.info(
                "SSO configuration saved by %s: enabled=%s server=%s client_id=%s secret=%s",
                request.user.get_username(), saved.enabled, saved.op_base or "(none)",
                saved.client_id or "(none)", "set" if saved.has_client_secret else "(none)",
            )
            if discovery_error:
                messages.warning(request, _(
                    "Saved, but the endpoints could not be read from %(url)s: %(error)s. "
                    "Fill them in by hand under “Enter the endpoints by hand”."
                ) % {"url": used, "error": discovery_error})
            elif used:
                messages.success(request, _(
                    "Saved, and the endpoints were read from %(url)s."
                ) % {"url": used})
            else:
                messages.success(request, _("The SSO settings were saved."))
            return redirect("accounts:sso")
        if discovery_error:
            # The save is about to come back with errors of its own. Say why
            # the endpoints are still empty before somebody concludes the page
            # is ignoring them.
            messages.warning(request, _(
                "The endpoints could not be read from %(url)s: %(error)s"
            ) % {"url": used, "error": discovery_error})
    else:
        form = SSOConfigurationForm(instance=configuration)

    return render(request, "accounts/sso_settings.html", _page(request, configuration, form))


def _looks_public(host):
    """Whether ``host`` could be the name a provider redirects a browser to.

    A LAN address is not: it is not what the certificate is for, it is not
    reachable from wherever somebody signs in, and registering it produces a
    redirect loop with no error message anywhere.
    """
    name = (host or "").split(":")[0].strip().lower()
    if not name or name in LOCAL_HOSTS:
        return False
    if re.fullmatch(r"[0-9.]+", name) or name.startswith("["):
        return False
    return "." in name


def _public_origin():
    """The deployment's own public address, from its own settings.

    Read from ``CSRF_TRUSTED_ORIGINS`` first because it carries the scheme, and
    ``ALLOWED_HOSTS`` after it. Both are already required to be right for the
    app to work behind the proxy at all, which makes them a better source for
    this than the address somebody happens to be browsing on.
    """
    for origin in getattr(settings, "CSRF_TRUSTED_ORIGINS", []):
        host = origin.split("://", 1)[-1]
        if _looks_public(host):
            return origin.rstrip("/")
    for host in getattr(settings, "ALLOWED_HOSTS", []):
        if _looks_public(host):
            return f"https://{host}"
    return ""


def _callback(request):
    """The redirect URI to register, and whether it came from the address bar.

    This is the first value the provider asks for — before it will issue a
    client ID and secret — so it has to be right on the very first visit, and
    the very first visit is nearly always over ``http://<nas>:8000`` because
    the reverse proxy is the step after this one. Deriving it from the request
    then hands somebody the LAN address to register, and what that produces is
    DEPLOYMENT.md §2's silent redirect loop.

    So: trust the request only when it arrived somewhere a provider could
    plausibly send a browser back to, and otherwise use the app's own
    configured public address and say on the page that is what happened.
    """
    from_request = request.build_absolute_uri("/oidc/callback/")
    if _looks_public(request.get_host()) and request.is_secure():
        return {"callback_url": from_request, "callback_is_derived": False}
    origin = _public_origin()
    if not origin:
        return {"callback_url": from_request, "callback_is_derived": False}
    return {
        "callback_url": f"{origin}/oidc/callback/",
        "callback_is_derived": True,
        "callback_from_request": from_request,
    }


def _page(request, configuration, form):
    endpoints = configuration.endpoints()
    return {
        "form": form,
        "configuration": configuration,
        "endpoints": endpoints,
        # Shown read-only. Built here rather than in the template because the
        # labels are translated and the order is the order of the handshake.
        "endpoint_rows": [
            (_("Authorisation"), endpoints["authorization"]),
            (_("Token"), endpoints["token"]),
            (_("User info"), endpoints["user"]),
            (_("JWKS"), endpoints["jwks"]),
        ],
        # Whether anything was ever read off the discovery document, as opposed
        # to guessed from the address or typed in. Shown beside the endpoints.
        "endpoints_read_at": configuration.endpoints_read_at,
        "endpoints_overridden": any([
            configuration.authorization_endpoint, configuration.token_endpoint,
            configuration.user_endpoint, configuration.jwks_endpoint,
        ]),
        # A row that has never been saved is the environment's values on screen,
        # which is worth saying: it explains why the form is already filled in
        # and what pressing save actually does.
        "from_environment": not configuration.is_stored,
        **_callback(request),
        # http anywhere means the client secret and the tokens cross the network
        # in the clear. Not refused — a LAN-only SSO server is a real setup — but
        # not passed over in silence either.
        "insecure": [
            url for url in (configuration.op_base, *endpoints.values())
            if url and url.startswith("http://")
        ],
    }


def _discover(configuration):
    """(found, where, error) — the endpoints, read off the discovery document.

    ``where`` is the address that answered, or — when none did — every address
    that was tried, because "it did not work" without saying what was asked is
    the least useful thing this page could report.
    """
    candidates = configuration.discovery_candidates()
    if not candidates:
        return None, "", _("there is no SSO server address to look at")

    last_error = None
    for url in candidates:
        document, error = _fetch_json(url, configuration.verify_ssl, configuration.request_timeout)
        if error:
            last_error = error
            continue
        found = {field: document.get(key) or "" for field, key in ENDPOINT_FIELDS.items()}
        if any(found.values()):
            return found, url, None
        last_error = _("it answered, but with nothing that looks like a discovery document")
    return None, " / ".join(candidates), last_error


def _int(value, fallback):
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def _autofill_endpoints(data, configuration):
    """Read the endpoints off the server as part of saving. Mutates ``data``.

    Returns ``(where, error)``, both None when nothing was attempted.

    This is what makes the SSO server's address the only address anybody has to
    type. It runs before validation rather than after saving, so that the rules
    in ``SSOConfigurationForm.clean`` — in particular "RS256 needs a JWKS
    address" — are applied to what the server said rather than to boxes nobody
    was asked to fill in. Getting that backwards is what made Save appear to do
    nothing at all.

    Only attempted when it would not throw away an answer somebody gave: either
    the endpoint boxes are all empty, or the SSO server address has changed —
    in which case endpoints belonging to the *old* server are worse than none.
    Typing an override and saving without touching the address leaves it alone,
    which is the whole point of an override.
    """
    op_base = (data.get("op_base") or "").strip()
    if not op_base:
        return None, None

    changed = op_base.rstrip("/") != (configuration.op_base or "").strip().rstrip("/")
    typed = any((data.get(field) or "").strip() for field in ENDPOINT_FIELDS)
    if typed and not changed:
        return None, None

    probe = SSOConfiguration(
        op_base=op_base,
        verify_ssl=bool(data.get("verify_ssl")),
        request_timeout=_int(data.get("request_timeout"), 10),
    )
    found, where, error = _discover(probe)
    if error:
        return where, error
    for field, value in found.items():
        data[field] = value
    return where, None


@superuser_required
@require_POST
def sso_discover(request):
    """Fetch the discovery document and fill the four endpoints in from it.

    Still here after discovery became part of saving, because the two answer
    different questions: this one is "read them again", which is what a DSM
    update that moved an endpoint needs, and it works without changing the
    address that would otherwise be the trigger.
    """
    configuration = SSOConfiguration.load()
    found, where, error = _discover(configuration)
    if error:
        messages.error(request, _(
            "Could not read the discovery document from %(url)s: %(error)s"
        ) % {"url": where or _("(nowhere — no address is set)"), "error": error})
        return redirect("accounts:sso")

    # Shown in the form rather than saved. What came back is a claim by whatever
    # answered that URL, and it decides how this app authenticates everybody —
    # so it is put in front of a person to confirm, not written on their behalf.
    form = SSOConfigurationForm(instance=configuration, initial=found)
    for name, value in found.items():
        form.initial[name] = value
    messages.success(request, _(
        "Read from %(url)s. Check the addresses below, then save."
    ) % {"url": where})
    return render(request, "accounts/sso_settings.html", _page(request, configuration, form))


@superuser_required
@require_POST
def sso_check(request):
    """Can this container actually reach the provider? See §3.3."""
    configuration = SSOConfiguration.load()
    endpoints = configuration.endpoints()

    checks = []
    for label, url in (
        (_("SSO server"), configuration.op_base),
        (_("authorisation endpoint"), endpoints["authorization"]),
        (_("token endpoint"), endpoints["token"]),
        (_("JWKS endpoint"), endpoints["jwks"]),
    ):
        if not url:
            continue
        checks.append((label, url, _reach(url, configuration.verify_ssl, configuration.request_timeout)))

    if not checks:
        messages.error(request, _("There is nothing configured to check yet."))
        return redirect("accounts:sso")

    context = _page(request, configuration, SSOConfigurationForm(instance=configuration))
    context["checks"] = checks
    return render(request, "accounts/sso_settings.html", context)


def _fetch_json(url, verify, timeout=TIMEOUT):
    document, error = None, None
    try:
        response = requests.get(
            url, timeout=max(1, min(timeout or TIMEOUT, TIMEOUT_CAP)), verify=verify,
            # Not followed: a redirect here would mean the address configured is
            # not the address answering, and quietly accepting that is how a
            # provider gets swapped without anybody noticing.
            allow_redirects=False,
        )
        if response.status_code != 200:
            return None, f"HTTP {response.status_code}"
        document = json.loads(response.content[:MAX_BYTES].decode("utf-8", "replace"))
        if not isinstance(document, dict):
            return None, "not a JSON object"
    except requests.exceptions.SSLError as failure:
        log.info("SSO discovery: TLS failure for %s: %s", url, failure)
        error = _("the certificate was refused — wrong hostname, or an internal CA")
    except requests.exceptions.RequestException as failure:
        # Named, never stringified. The same rule as `_reach` below, and it
        # matters more here: this text now reaches the page on every save that
        # cannot read the discovery document. A `requests` connection error
        # stringifies to several hundred characters of nested retry machinery
        # with no spaces in it — unreadable, identical whatever went wrong, and
        # wide enough to break the layout of the banner carrying it.
        log.info("SSO discovery: request failure for %s: %s", url, failure)
        error = _cause(failure)
    except ValueError:
        error = _("the response was not JSON")
    return document, error


def _cause(failure):
    """The three network failures that actually happen here, as a sentence."""
    if isinstance(failure, requests.exceptions.Timeout):
        # Word for word what `_reach` says, so the catalogue holds one entry and
        # the two buttons cannot end up describing the same fault differently.
        #
        # It named hairpin NAT before. That is one cause of a timeout and not
        # the common one — a resolver handing back the public address, a
        # firewall, a provider that is simply slow all look identical from
        # here — and a confident wrong diagnosis sends somebody to reconfigure
        # a router that was never at fault. The address is printed beside this;
        # what to do about it is not something this code knows.
        return _("timed out")
    text = str(failure)
    if "getaddrinfo" in text or "NameResolution" in text or "Name or service" in text:
        return _("the name could not be resolved from inside the container")
    if "refused" in text.lower():
        return _("the connection was refused — nothing is listening on that port")
    return _("no connection could be made")


def _reach(url, verify, timeout=TIMEOUT):
    """(ok, detail) — whether this container can get an answer from ``url``.

    ``detail`` is a sentence, not the exception. A ``requests`` connection error
    stringifies to several hundred characters of nested retry machinery with no
    spaces in it — which is unreadable, is the same text whatever went wrong,
    and broke this page's layout the first time one arrived. The three causes
    that actually happen here are named instead, and the full text goes to the
    log where somebody can go looking for it.
    """
    try:
        response = requests.head(
            url, timeout=max(1, min(timeout or TIMEOUT, TIMEOUT_CAP)),
            verify=verify, allow_redirects=False,
        )
        # Any HTTP answer at all is the thing being asked about. A 404 from the
        # token endpoint still proves the network path and the certificate work,
        # which is what §3.3 is about; whether the path is right is what the
        # discovery document above is for.
        return True, _("answered, HTTP %(code)s") % {"code": response.status_code}
    except requests.exceptions.SSLError as failure:
        log.info("SSO check: TLS failure for %s: %s", url, failure)
        return False, _("the certificate was refused — wrong hostname, or an internal CA")
    except requests.exceptions.ConnectTimeout:
        log.info("SSO check: timeout for %s", url)
        return False, _("timed out")
    except requests.exceptions.ConnectionError as failure:
        log.info("SSO check: connection failure for %s: %s", url, failure)
        text = str(failure)
        if "getaddrinfo" in text or "NameResolution" in text or "Name or service" in text:
            return False, _("the name could not be resolved from inside the container")
        if "refused" in text.lower():
            return False, _("the connection was refused — nothing is listening on that port")
        return False, _("no connection could be made")
    except requests.exceptions.RequestException as failure:
        log.info("SSO check: request failure for %s: %s", url, failure)
        return False, _("the request failed")
