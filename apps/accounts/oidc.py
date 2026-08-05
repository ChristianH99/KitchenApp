"""Signing in against the Synology SSO Server.

``mozilla_django_oidc`` does the protocol. This subclass does the three things
that are *this* deployment's business: deciding who a token describes, deciding
whether that person may come in, and keeping the local user row in step with
what DSM says today.

A note on the claims, because it is the part most likely to surprise you.
Synology's SSO Server has shipped different claim sets across DSM versions —
some builds return ``preferred_username``, some only ``sub`` and ``email``, and
the group claim has been absent entirely. So nothing here *requires* a claim
beyond ``sub``: the username falls back through three candidates, the display
name is optional, and the group check is opt-in (``OIDC_ALLOWED_GROUPS`` empty
means "anyone the SSO server authenticates", which is the right default for a
household and the wrong one the moment that server also serves guests).

``sub`` is the identity, not the username. A DSM account renamed from ``chris``
to ``christian`` keeps its ``sub``, and matching on the name instead would hand
that person a brand-new, empty account. So the local ``username`` *is* the
``sub``, and the human-readable name lives in ``first_name``/``last_name`` where
it can change freely. It reads oddly in the Django admin and it is the only
version of this that survives a rename.
"""

import logging

from django.conf import settings
from django.core.exceptions import SuspiciousOperation
from mozilla_django_oidc.auth import OIDCAuthenticationBackend

log = logging.getLogger(__name__)


def _claim_groups(claims):
    """The group names in a token, however this DSM version spells them.

    Returns a set of strings. A provider that sends no group claim at all — and
    some do not — is indistinguishable from one that sends an empty list, which
    is why the caller treats "no groups" as "cannot satisfy a group
    requirement" rather than as "no requirement".
    """
    raw = claims.get(settings.OIDC_GROUPS_CLAIM)
    if raw is None:
        return set()
    if isinstance(raw, str):
        # Some providers send a space- or comma-separated string rather than a list.
        return {part.strip() for part in raw.replace(",", " ").split() if part.strip()}
    if isinstance(raw, (list, tuple, set)):
        return {str(item).strip() for item in raw if str(item).strip()}
    return set()


def _display_name(claims):
    """(first_name, last_name) from whatever the token offers.

    ``given_name``/``family_name`` when they are there, otherwise ``name`` split
    once on the last space — which is wrong for compound surnames and right far
    more often than leaving the field empty. Nothing depends on it being
    correct; it is what the sidebar greets somebody with.
    """
    given = (claims.get("given_name") or "").strip()
    family = (claims.get("family_name") or "").strip()
    if given or family:
        return given[:150], family[:150]
    whole = (claims.get("name") or "").strip()
    if not whole:
        return "", ""
    first, _, last = whole.rpartition(" ")
    return (first or whole)[:150], last[:150] if first else ""


class SynologyOIDCBackend(OIDCAuthenticationBackend):
    def filter_users_by_claims(self, claims):
        """Find the local row for this identity — by ``sub``, never by e-mail.

        E-mail is the tempting key and it is the wrong one twice over: DSM lets
        two accounts share an address, and an address can be reassigned to a
        different person entirely. Either way, matching on it means signing
        somebody in as somebody else.
        """
        sub = (claims.get("sub") or "").strip()
        if not sub:
            return self.UserModel.objects.none()
        return self.UserModel.objects.filter(username=sub)

    def verify_claims(self, claims):
        """Whether this token may sign in at all.

        Two gates. ``sub`` must exist, because without it there is no stable
        identity and every login would create a new account. And when
        ``OIDC_ALLOWED_GROUPS`` is configured, the token must carry one of them
        — including the case where the provider sends *no* group claim, which
        is refused rather than waved through: an app told "only these groups"
        must not fall open because the claim it needs went missing.
        """
        if not (claims.get("sub") or "").strip():
            log.warning("OIDC token carried no 'sub' claim; refusing the login")
            raise SuspiciousOperation("The identity provider returned no subject claim.")

        allowed = set(settings.OIDC_ALLOWED_GROUPS)
        if allowed:
            groups = _claim_groups(claims)
            if not groups & allowed:
                log.info(
                    "Refused OIDC login for sub=%s: groups %s do not include any of %s",
                    claims.get("sub"), sorted(groups) or "(none in the token)", sorted(allowed),
                )
                return False
        return True

    def create_user(self, claims):
        user = self.UserModel.objects.create_user(
            username=claims["sub"],
            email=(claims.get("email") or "")[:254],
        )
        # No usable password. The account exists only as the local end of an
        # SSO identity, and `set_unusable_password` is what stops it from ever
        # being reachable through the local login form — which would otherwise
        # be a second, unmanaged door into a DSM-managed account.
        user.set_unusable_password()
        self._apply_claims(user, claims)
        log.info("Created a local account for OIDC sub=%s", claims["sub"])
        return user

    def update_user(self, user, claims):
        """Re-apply what DSM says on every sign-in.

        Including ``is_staff``: taking somebody out of the admin group in DSM
        has to actually take their admin away, and the only moment this app
        hears about that change is the next login.
        """
        self._apply_claims(user, claims)
        return user

    def _apply_claims(self, user, claims):
        user.email = (claims.get("email") or "")[:254]
        user.first_name, user.last_name = _display_name(claims)
        staff_group = settings.OIDC_STAFF_GROUP
        if staff_group:
            # Only managed when a group is configured. Otherwise a superuser
            # created by hand for the fallback login would be demoted by their
            # own first SSO sign-in.
            user.is_staff = staff_group in _claim_groups(claims)
        user.save()
