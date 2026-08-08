"""The Synology SSO connection, stored so it can be edited from the app.

This is a **reversal of an earlier decision**, taken deliberately and with its
cost understood. The client secret used to live only in the environment, which
is where secrets belong: a value in `.env` is not in the database, not in a
`dumpdata`, and not in whatever copies the database. Moving it here puts it in
all three — most concretely in Hyper Backup, which copies `/data` nightly to
wherever that share is backed up to.

What made it worth doing anyway is that the *other* half of the OIDC setup is a
web page whatever we do: Synology's SSO Server has no supported way to create an
OIDC application except its own GUI. So the choice was never "config files or a
web UI", it was "one web UI plus an SSH session and a container restart", or one
web UI. The second is what somebody standing in front of a broken login at nine
in the evening can actually use.

Three things reduce the damage rather than pretend it away:

* **The secret is encrypted at rest** with a key derived from
  ``DJANGO_SECRET_KEY`` — which is still only in the environment. A stolen
  `db.sqlite3` on its own does not yield the secret. Someone with both the file
  and the environment has everything either way, so this buys exactly one
  thing: the backup copy is not enough.
* **It is never sent back to the browser.** The form takes a new value or leaves
  the stored one alone; there is no request that returns it.
* **Only a superuser may see the page**, which is a narrower door than the
  People page (staff), because this one decides how everybody authenticates.

The environment still works. Nothing here is required, and with no row in this
table the app reads exactly the settings it always did — which is what keeps a
fresh checkout, and the container's first boot, working with no database at all.
"""

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.accounts.secrets import decrypt, encrypt


class SignAlgorithm(models.TextChoices):
    RS256 = "RS256", _("RS256 — signed with the provider’s key (needs the JWKS endpoint)")
    HS256 = "HS256", _("HS256 — signed with the client secret (no key fetch)")


class SSOConfiguration(models.Model):
    """One row, or none. ``pk`` is pinned to 1.

    A singleton as a table rather than as a settings file, so it can be edited
    through a form and so the change is atomic — half-applied authentication
    settings are a locked-out household.
    """

    # Pinned rather than auto: two rows of this would be two answers to "how
    # does this app authenticate", and nothing would say which one wins.
    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)

    enabled = models.BooleanField(
        _("offer single sign-on"), default=False,
        help_text=_("With this off, the local password form is the only way in."),
    )

    # The provider. `base` is a convenience: the four endpoints below are
    # derived from it when they are left blank, using Synology's usual paths —
    # which are a starting guess and not a promise, hence the override fields.
    op_base = models.URLField(
        _("SSO server"), max_length=500, blank=True,
        help_text=_("e.g. https://sso.example.org — the four addresses below are derived from it."),
    )
    authorization_endpoint = models.URLField(_("authorisation endpoint"), max_length=500, blank=True)
    token_endpoint = models.URLField(_("token endpoint"), max_length=500, blank=True)
    user_endpoint = models.URLField(_("user info endpoint"), max_length=500, blank=True)
    jwks_endpoint = models.URLField(_("JWKS endpoint"), max_length=500, blank=True)

    client_id = models.CharField(_("client ID"), max_length=200, blank=True)
    # Fernet token, never the secret itself. See apps/accounts/secrets.py.
    client_secret_encrypted = models.TextField(blank=True, editable=False)

    sign_algo = models.CharField(
        _("signature algorithm"), max_length=10,
        choices=SignAlgorithm.choices, default=SignAlgorithm.RS256,
    )
    scopes = models.CharField(
        _("scopes"), max_length=200, default="openid profile email",
        help_text=_("Separated by spaces."),
    )

    allowed_groups = models.CharField(
        _("allowed groups"), max_length=300, blank=True,
        help_text=_("Separated by commas. Empty means anybody the SSO server authenticates."),
    )
    groups_claim = models.CharField(
        _("group claim"), max_length=100, default="groups",
        help_text=_("The claim the group names arrive in. Some providers send none at all."),
    )
    staff_group = models.CharField(
        _("administrator group"), max_length=200, blank=True,
        help_text=_("Members of this group may manage people. Re-applied at every sign-in."),
    )

    verify_ssl = models.BooleanField(
        _("verify the certificate"), default=True,
        help_text=_("Leave on. Verification is the whole point of putting the SSO server behind a real certificate."),
    )

    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="+",
    )

    class Meta:
        verbose_name = _("SSO configuration")
        verbose_name_plural = _("SSO configuration")

    def __str__(self):
        return self.op_base or "SSO"

    # -- the secret ------------------------------------------------------

    @property
    def client_secret(self):
        """The decrypted secret, or "" when there is none or it cannot be read.

        Returns "" rather than raising on an undecryptable value, because the
        realistic cause is a rotated ``DJANGO_SECRET_KEY`` — and the right
        behaviour then is a login that fails with a clear message on a page
        somebody can fix, not a 500 on every request.
        """
        return decrypt(self.client_secret_encrypted)

    def set_client_secret(self, raw):
        self.client_secret_encrypted = encrypt(raw) if raw else ""

    @property
    def has_client_secret(self):
        return bool(self.client_secret_encrypted)

    @property
    def secret_is_readable(self):
        """False when a secret is stored but the current key cannot open it."""
        return not self.client_secret_encrypted or bool(self.client_secret)

    # -- loading ---------------------------------------------------------

    @classmethod
    def load(cls):
        """The stored row, or an unsaved one seeded from the environment.

        **Never creates a row.** A GET that writes would take SQLite's single
        write lock on a read path, and this is consulted on requests that have
        nothing to do with configuring anything. The unsaved instance is what
        makes the settings page open pre-filled from `.env` on a system that has
        never used it — so the migration from environment to database is
        "open the page, press save".
        """
        existing = cls.objects.filter(pk=1).first()
        if existing is not None:
            return existing
        return cls.from_environment()

    @classmethod
    def from_environment(cls):
        """An unsaved row carrying whatever `.env` configured."""
        row = cls(
            id=1,
            enabled=settings.OIDC_ENABLED,
            op_base=settings.OIDC_OP_BASE,
            authorization_endpoint=settings.OIDC_OP_AUTHORIZATION_ENDPOINT,
            token_endpoint=settings.OIDC_OP_TOKEN_ENDPOINT,
            user_endpoint=settings.OIDC_OP_USER_ENDPOINT,
            jwks_endpoint=settings.OIDC_OP_JWKS_ENDPOINT,
            client_id=settings.OIDC_RP_CLIENT_ID,
            sign_algo=settings.OIDC_RP_SIGN_ALGO or SignAlgorithm.RS256,
            scopes=settings.OIDC_RP_SCOPES,
            allowed_groups=", ".join(settings.OIDC_ALLOWED_GROUPS),
            groups_claim=settings.OIDC_GROUPS_CLAIM,
            staff_group=settings.OIDC_STAFF_GROUP,
            verify_ssl=settings.OIDC_VERIFY_SSL,
        )
        row.set_client_secret(settings.OIDC_RP_CLIENT_SECRET)
        return row

    @property
    def is_stored(self):
        return SSOConfiguration.objects.filter(pk=1).exists()

    def save(self, *args, **kwargs):
        self.id = 1
        super().save(*args, **kwargs)
        # The resolver caches this for a few seconds; without this the worker
        # that just saved would keep serving the old configuration back to the
        # person who changed it.
        from apps.accounts import sso

        sso.invalidate()

    # -- derived values --------------------------------------------------

    def endpoints(self):
        """The four provider URLs, filling blanks in from ``op_base``.

        Synology's usual shape, and explicitly a guess — DSM has moved these
        between versions, which is why each one can be overridden and why the
        page offers to read them off the discovery document instead.
        """
        base = (self.op_base or "").rstrip("/")
        default = {
            "authorization": f"{base}/webman/sso/SSOOauth.cgi" if base else "",
            "token": f"{base}/webman/sso/SSOAccessToken.cgi" if base else "",
            "user": f"{base}/webman/sso/SSOUserInfo.cgi" if base else "",
            "jwks": "",
        }
        return {
            "authorization": self.authorization_endpoint or default["authorization"],
            "token": self.token_endpoint or default["token"],
            "user": self.user_endpoint or default["user"],
            "jwks": self.jwks_endpoint or default["jwks"],
        }

    @property
    def discovery_url(self):
        base = (self.op_base or "").rstrip("/")
        return f"{base}/webman/sso/.well-known/openid-configuration" if base else ""

    @property
    def allowed_group_list(self):
        return [name.strip() for name in (self.allowed_groups or "").split(",") if name.strip()]

    @property
    def is_usable(self):
        """Whether this could conceivably complete a login.

        Checked before the sign-in button is offered: a button that leads to a
        provider error page is worse than no button, because it reads as the
        provider being broken rather than as the app not being set up.
        """
        endpoints = self.endpoints()
        return bool(
            self.enabled
            and self.client_id
            and self.secret_is_readable
            and endpoints["authorization"]
            and endpoints["token"]
        )


class TimerSound(models.TextChoices):
    """What a step timer does when it reaches nought.

    A closed set, and every one of them is *generated* in the browser rather
    than fetched — static/js/recipe_cook.js builds them with the Web Audio API.
    That is not cleverness for its own sake: an audio file would be a binary
    asset in the repository, a request the Content-Security-Policy has to allow,
    and a decode that a phone with the screen off may not have done yet when the
    bread is ready. A tone synthesised on the spot has none of those problems
    and is four lines.
    """

    CHIME = "chime", _("chime — two soft notes")
    BELL = "bell", _("bell — one long ring")
    BEEPS = "beeps", _("beeps — three short tones")
    ALARM = "alarm", _("alarm — insistent, for a noisy kitchen")
    NONE = "none", _("no sound")


class Preferences(models.Model):
    """One person's own settings.

    Per person and not per household, because this is about a body rather than
    about the food: which noise cuts through *your* kitchen, and whether you
    want one at all at six in the morning. The recipes are shared; this is not.

    Created on demand by ``for_user`` rather than by a signal on User. A signal
    would put a write inside every login — including the OIDC callback, where
    an exception means a sign-in that fails with no explanation — for a row
    that most people never change.
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="preferences",
    )
    timer_sound = models.CharField(
        _("timer sound"), max_length=10,
        choices=TimerSound.choices, default=TimerSound.CHIME,
        help_text=_("Played when a step’s timer reaches nought."),
    )

    class Meta:
        verbose_name = _("preferences")
        verbose_name_plural = _("preferences")

    def __str__(self):
        return f"preferences for {self.user_id}"

    @classmethod
    def for_user(cls, user):
        """The row for this person, without writing one on a read.

        Returns an unsaved instance for somebody who has never opened the
        settings page — the defaults are the defaults, and a GET that creates a
        row would take SQLite's one write lock to answer "no, you have not
        changed anything".
        """
        if not user or not user.is_authenticated:
            return cls()
        return cls.objects.filter(user=user).first() or cls(user=user)
