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
that person a brand-new, empty account. The ``sub`` lives in an
``SSOIdentity`` row, and the human-readable name in
``first_name``/``last_name`` where it can change freely.

An account this app created for a token is *also* called by its ``sub``, which
reads oddly in the Django admin and is what every such row looked like before
those identity rows existed — ``filter_users_by_claims`` still answers to it, so
nothing created by an earlier version needs migrating. What it cannot do any
more is carry the identity on its own: a person who signs in **both ways** has a
username they chose, so the ``sub`` has to be somewhere that is not the username
column.

That is the other thing this file now does: a token whose e-mail address matches
exactly one local account is attached to it rather than given an account of its
own, and both ways in keep working. ``_account_to_link`` is where the hedges
are, and there are four of them, because an e-mail address is the tempting key
and the wrong one — this is the app's one deliberate exception to that, not a
change of mind about it.
"""

import logging

from django.core.exceptions import SuspiciousOperation
from mozilla_django_oidc.auth import OIDCAuthenticationBackend
from mozilla_django_oidc.views import (
    OIDCAuthenticationCallbackView,
    OIDCAuthenticationRequestView,
)

from apps.accounts import sso
from apps.accounts.models import SSOIdentity

log = logging.getLogger(__name__)


def _claim_groups(claims, claim_name=None):
    """The group names in a token, however this DSM version spells them.

    Returns a set of strings. A provider that sends no group claim at all — and
    some do not — is indistinguishable from one that sends an empty list, which
    is why the caller treats "no groups" as "cannot satisfy a group
    requirement" rather than as "no requirement".
    """
    if claim_name is None:
        configuration = sso.current()
        claim_name = configuration.groups_claim if configuration else "groups"
    raw = claims.get(claim_name)
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
    @staticmethod
    def get_settings(attr, *args):
        """Read the provider's details from the configuration, not from settings.

        The library's own version is ``getattr(settings, attr)``, which is fixed
        for the life of the process — and this app lets a superuser change the
        client secret from a page. apps/accounts/sso.py answers for the handful
        of names that are editable and passes everything else through.
        """
        return sso.get_setting(attr, *args)

    def filter_users_by_claims(self, claims):
        """Find the local row for this identity.

        Three questions, in this order, and the order is the safety:

        1. **Which account carries this ``sub``?** The identity, and the only
           answer that survives a rename or a change of address.
        2. **Is there an account whose username is this ``sub``?** Every SSO
           account created before ``SSOIdentity`` existed. ``update_user``
           mints the missing row, so each of them answers question 1 from its
           next sign-in onwards.
        3. **Is there exactly one local account with this e-mail address?**
           This is the automatic link, and it is the only question here that
           is not about identity — see ``_account_to_link`` for the four things
           that have to be true before it counts.

        A read, and only a read: the link is *recorded* in ``update_user``,
        which is the method that already writes.
        """
        sub = (claims.get("sub") or "").strip()
        if not sub:
            return self.UserModel.objects.none()

        by_subject = self.UserModel.objects.filter(sso_identity__subject=sub)
        if by_subject.exists():
            return by_subject

        # Named by the sub and carrying no identity row: an account from before
        # this app kept the two apart. Not `is_sso_account` — a *local* account
        # that happens to be called the same as a sub is a coincidence nobody
        # in a household will produce, and it would still be the right row.
        legacy = self.UserModel.objects.filter(username=sub, sso_identity__isnull=True)
        if legacy.exists():
            return legacy

        return self._account_to_link(claims)

    def _account_to_link(self, claims):
        """The local account this token should be attached to, if any.

        **This is the one place in the app that treats an e-mail address as
        something like an identity, and it is deliberately hedged about.** The
        objection is real and has not gone away: DSM lets two accounts share an
        address, an address can be reassigned to somebody else entirely, and an
        address is a claim the provider asserts rather than something this app
        can check. Matching on it carelessly means signing somebody in as
        somebody else, which is why questions 1 and 2 above are asked first and
        why this one is asked *only* about an account that has never been
        linked to anything.

        What it buys is the thing it was asked for: one person, one account,
        both ways in. Somebody who has a local password from before SSO was set
        up, or who keeps one so that a broken provider is not a locked door,
        does not end up with two half-accounts, two collections of recipes and
        two names in the People list.

        Four things have to be true, and each one is a shape a shared address
        could otherwise produce:

        * **The token carries an address, and does not say it is unverified.**
          Only an explicit ``email_verified: false`` refuses — the claim is
          absent on DSM builds that send nothing but ``sub`` and ``email``, and
          requiring it would mean linking never happens on the very provider
          this was written for.
        * **Exactly one active account has it.** Two is the ambiguous case and
          the dangerous one, and there is no tie-break worth inventing: the
          login carries on and creates its own account, which is visible and
          undoable rather than silent and wrong.
        * **That account has a local password.** An account without one is
          another *provider* identity that happens to share an address — a
          second DSM account, not the same person — and attaching this token to
          it would be exactly the mix-up the objection above describes.
        * **It is not already linked.** One account, one identity. The second
          ``sub`` to arrive with a shared address gets an account of its own.

        Nothing about it is undone silently: the link is a row a staff user can
        see on the account page and remove there.
        """
        email = (claims.get("email") or "").strip()
        if not email:
            return self.UserModel.objects.none()
        if claims.get("email_verified") is False:
            log.info("Not linking sub=%s by e-mail: the provider marked it unverified",
                     claims.get("sub"))
            return self.UserModel.objects.none()

        candidates = self.UserModel.objects.filter(email__iexact=email, is_active=True)
        if candidates.count() != 1:
            if candidates.count() > 1:
                log.warning(
                    "Not linking sub=%s by e-mail: %s active accounts share that address",
                    claims.get("sub"), candidates.count(),
                )
            return self.UserModel.objects.none()

        person = candidates.first()
        if not person.has_usable_password():
            log.info("Not linking sub=%s to %s: that account is itself a provider identity",
                     claims.get("sub"), person.get_username())
            return self.UserModel.objects.none()
        if getattr(person, "sso_identity", None) is not None:
            log.warning("Not linking sub=%s to %s: it is already linked to another identity",
                        claims.get("sub"), person.get_username())
            return self.UserModel.objects.none()

        return candidates

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

        configuration = sso.current()
        allowed = set(configuration.allowed_group_list if configuration else [])
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
        self._remember_identity(user, claims)
        log.info("Created a local account for OIDC sub=%s", claims["sub"])
        return user

    def update_user(self, user, claims):
        """Re-apply what DSM says on every sign-in.

        Including ``is_staff``: taking somebody out of the admin group in DSM
        has to actually take their admin away, and the only moment this app
        hears about that change is the next login.

        This is also where a link is *written*, because it is the method that
        already writes. ``filter_users_by_claims`` decides which account a token
        belongs to and does nothing else; a lookup that quietly attaches an
        identity as a side effect of being asked a question is the kind of
        thing that is discovered from a log file months later.
        """
        self._remember_identity(user, claims)
        self._apply_claims(user, claims)
        return user

    def _remember_identity(self, user, claims):
        """Record which DSM identity this account answers to, once.

        Three cases arrive here and only one of them is new. A brand-new
        account gets its row alongside itself; an account created before
        ``SSOIdentity`` existed gets the row it never had, which moves it off
        the username fallback for good; and an account matched by e-mail gets
        the link, which is the one worth a line in the log at a level somebody
        will actually see.
        """
        sub = (claims.get("sub") or "").strip()
        if not sub or getattr(user, "sso_identity", None) is not None:
            return
        linked = user.has_usable_password()
        SSOIdentity.objects.create(user=user, subject=sub, matched_by_email=linked)
        if linked:
            log.warning(
                "Linked OIDC sub=%s to the existing account %s by the e-mail address %s. "
                "Both ways of signing in now reach it.",
                sub, user.get_username(), user.email,
            )

    def _apply_claims(self, user, claims):
        user.email = (claims.get("email") or "")[:254]
        user.first_name, user.last_name = _display_name(claims)
        configuration = sso.current()
        staff_group = configuration.staff_group if configuration else ""
        if staff_group:
            # Only managed when a group is configured. Otherwise a superuser
            # created by hand for the fallback login would be demoted by their
            # own first SSO sign-in.
            user.is_staff = staff_group in _claim_groups(claims)
        user.save()


# ---------------------------------------------------------------------------
# The two views that read the provider's details
# ---------------------------------------------------------------------------
#
# Subclassed for one reason: the same `get_settings` override as the backend
# above. They are wired in through the library's own `OIDC_AUTHENTICATE_CLASS`
# and `OIDC_CALLBACK_CLASS` settings rather than by writing new URL patterns,
# which keeps `mozilla_django_oidc.urls` in charge of the paths and the names —
# and `/oidc/callback/` in particular is a string registered in the SSO server's
# client configuration, so it is not ours to move.
#
# `OIDCLogoutView` is deliberately not subclassed: it reads only
# LOGOUT_REDIRECT_URL and OIDC_OP_LOGOUT_URL_METHOD, neither of which is
# editable here.

class ConfiguredAuthenticationRequestView(OIDCAuthenticationRequestView):
    """Starts the login. Note that the base class reads the authorisation
    endpoint and client ID in ``__init__`` — which is per request, because
    ``as_view()`` builds a new instance each time."""

    @staticmethod
    def get_settings(attr, *args):
        return sso.get_setting(attr, *args)


class ConfiguredAuthenticationCallbackView(OIDCAuthenticationCallbackView):
    @staticmethod
    def get_settings(attr, *args):
        return sso.get_setting(attr, *args)
