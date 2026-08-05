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

import requests
from django.contrib import messages
from django.shortcuts import redirect, render
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from apps.accounts.forms import SSOConfigurationForm
from apps.accounts.models import SSOConfiguration
from apps.accounts.permissions import superuser_required

log = logging.getLogger(__name__)

# Short, because somebody is watching the page. A provider that has not answered
# in five seconds is a provider that would have failed the login anyway.
TIMEOUT = 5
# Enough for any discovery document; a cap so a wrong address pointing at
# something enormous cannot be streamed into memory.
MAX_BYTES = 256 * 1024


@superuser_required
def sso_settings(request):
    configuration = SSOConfiguration.load()

    if request.method == "POST":
        form = SSOConfigurationForm(request.POST, instance=configuration)
        if form.is_valid():
            saved = form.save(updated_by=request.user)
            # Security-relevant, and the log is the only place it is recorded.
            # Deliberately says *what changed shape*, never the secret.
            log.info(
                "SSO configuration saved by %s: enabled=%s server=%s client_id=%s secret=%s",
                request.user.get_username(), saved.enabled, saved.op_base or "(none)",
                saved.client_id or "(none)", "set" if saved.has_client_secret else "(none)",
            )
            messages.success(request, _("The SSO settings were saved."))
            return redirect("accounts:sso")
    else:
        form = SSOConfigurationForm(instance=configuration)

    return render(request, "accounts/sso_settings.html", _page(request, configuration, form))


def _page(request, configuration, form):
    endpoints = configuration.endpoints()
    return {
        "form": form,
        "configuration": configuration,
        "endpoints": endpoints,
        # A row that has never been saved is the environment's values on screen,
        # which is worth saying: it explains why the form is already filled in
        # and what pressing save actually does.
        "from_environment": not configuration.is_stored,
        "callback_url": request.build_absolute_uri("/oidc/callback/"),
        # http anywhere means the client secret and the tokens cross the network
        # in the clear. Not refused — a LAN-only SSO server is a real setup — but
        # not passed over in silence either.
        "insecure": [
            url for url in (configuration.op_base, *endpoints.values())
            if url and url.startswith("http://")
        ],
    }


@superuser_required
@require_POST
def sso_discover(request):
    """Fetch the discovery document and fill the four endpoints in from it."""
    configuration = SSOConfiguration.load()
    url = configuration.discovery_url
    if not url:
        messages.error(request, _("Fill in the SSO server’s address first."))
        return redirect("accounts:sso")

    document, error = _fetch_json(url, configuration.verify_ssl)
    if error:
        messages.error(request, _(
            "Could not read the discovery document from %(url)s: %(error)s"
        ) % {"url": url, "error": error})
        return redirect("accounts:sso")

    found = {
        "authorization_endpoint": document.get("authorization_endpoint") or "",
        "token_endpoint": document.get("token_endpoint") or "",
        "user_endpoint": document.get("userinfo_endpoint") or "",
        "jwks_endpoint": document.get("jwks_uri") or "",
    }
    if not any(found.values()):
        messages.error(request, _(
            "That address answered, but with nothing that looks like a discovery document."
        ))
        return redirect("accounts:sso")

    # Shown in the form rather than saved. What came back is a claim by whatever
    # answered that URL, and it decides how this app authenticates everybody —
    # so it is put in front of a person to confirm, not written on their behalf.
    form = SSOConfigurationForm(instance=configuration, initial=found)
    for name, value in found.items():
        form.initial[name] = value
    messages.success(request, _(
        "Read from the server. Check the four addresses below, then save."
    ))
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
        checks.append((label, url, _reach(url, configuration.verify_ssl)))

    if not checks:
        messages.error(request, _("There is nothing configured to check yet."))
        return redirect("accounts:sso")

    context = _page(request, configuration, SSOConfigurationForm(instance=configuration))
    context["checks"] = checks
    return render(request, "accounts/sso_settings.html", context)


def _fetch_json(url, verify):
    document, error = None, None
    try:
        response = requests.get(
            url, timeout=TIMEOUT, verify=verify,
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
        error = f"TLS: {failure}"
    except requests.exceptions.RequestException as failure:
        error = str(failure)
    except ValueError:
        error = "the response was not JSON"
    return document, error


def _reach(url, verify):
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
            url, timeout=TIMEOUT, verify=verify, allow_redirects=False,
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
        return False, _(
            "timed out. If a browser can reach this address but the container cannot, "
            "it is the router not sending internal traffic back to itself — "
            "DEPLOYMENT.md §3.3."
        )
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
