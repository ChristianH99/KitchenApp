"""Forms for the household's accounts page.

There are two kinds of account in this app and the difference is the whole
reason these forms exist rather than a link to the Django admin.

A **local** account was created here: it has a username somebody chose and a
password this app stores. A **Synology** account is the local end of an OIDC
identity — its ``username`` is the provider's ``sub`` (a stable opaque string,
never an e-mail: apps/accounts/oidc.py says why), its display name and e-mail
are re-applied from the token on every sign-in, and it has no usable password
by construction.

``has_usable_password()`` is what tells them apart, and it is not a heuristic:
``SynologyOIDCBackend.create_user`` calls ``set_unusable_password()`` precisely
so that a DSM-managed account can never also be reachable through the local
form. So the rule is exact in the direction that matters — an account with no
usable password must not be offered a password field, because giving it one
would open the second door SSO exists to close.

What follows from that is what these forms refuse: a Synology account's
username, name and e-mail are DSM's to change, and anything typed here would be
overwritten at the next login while looking as though it had been saved.
"""

from django import forms
from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from apps.accounts.models import Preferences, SSOConfiguration, SignAlgorithm


def is_sso_account(user):
    """Whether this row is the local end of a Synology identity."""
    return not user.has_usable_password()


class _PasswordPair(forms.Form):
    """The two password boxes, and the checks both of them need.

    Shared rather than written twice: the "create an account" form and the
    "set a new password" form ask exactly the same question, and the version
    where only one of them runs ``validate_password`` is the version that lets
    a weak password in through whichever page nobody looked at.
    """

    password1 = forms.CharField(
        label=_("Password"), widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
    )
    password2 = forms.CharField(
        label=_("Repeat password"),
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
    )

    def clean_password2(self):
        first = self.cleaned_data.get("password1")
        second = self.cleaned_data.get("password2")
        if first and second and first != second:
            raise ValidationError(_("The two passwords do not match."))
        return second

    def _check_strength(self, user):
        password = self.cleaned_data.get("password1")
        if not password:
            return
        try:
            # Handed the user object, not just the string: that is what lets
            # UserAttributeSimilarityValidator refuse a password that is the
            # username with a digit on the end.
            validate_password(password, user)
        except ValidationError as error:
            self.add_error("password1", error)


class UserCreateForm(_PasswordPair, forms.ModelForm):
    """A new local account, made here rather than in DSM.

    Offered because not everybody who cooks in this house has a DSM account,
    and because the fallback administrator has to be creatable when SSO is the
    thing that is broken.
    """

    class Meta:
        model = User
        fields = ["username", "first_name", "last_name", "email",
                  "is_active", "is_staff", "is_superuser"]
        help_texts = {
            "username": _("What they type to sign in. Letters, digits and . @ + - _"),
        }

    def __init__(self, *args, editor=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.editor = editor
        self.fields["email"].required = False
        # Only a superuser may make one. Otherwise the "manage accounts" right
        # is also the right to grant yourself everything, one page later.
        if editor is not None and not editor.is_superuser:
            del self.fields["is_superuser"]

    def clean(self):
        data = super().clean()
        self._check_strength(User(
            username=data.get("username") or "",
            first_name=data.get("first_name") or "",
            last_name=data.get("last_name") or "",
            email=data.get("email") or "",
        ))
        return data

    def save(self, commit=True):
        user = super().save(commit=False)
        user.set_password(self.cleaned_data["password1"])
        if commit:
            user.save()
        return user


class UserEditForm(forms.ModelForm):
    """An existing account.

    ``username`` is absent on purpose, for both kinds. For a Synology account
    it *is* the identity — renaming it would sign that person in as a brand-new
    empty account at their next visit. For a local one, a rename is a
    different, rarer operation than "fix the spelling of their surname", and
    putting it on the same page is how the two get done by accident together.
    """

    class Meta:
        model = User
        fields = ["first_name", "last_name", "email",
                  "is_active", "is_staff", "is_superuser"]

    def __init__(self, *args, editor=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.editor = editor
        self.fields["email"].required = False

        if is_sso_account(self.instance):
            # DSM owns these three. A value typed here survives until the next
            # sign-in and is then silently replaced, which is worse than the
            # field not being there: it looks like it worked.
            for name in ("first_name", "last_name", "email"):
                self.fields[name].disabled = True
                self.fields[name].help_text = _("Managed by the identity provider.")

        if editor is not None and not editor.is_superuser:
            del self.fields["is_superuser"]

    def clean_is_active(self):
        active = self.cleaned_data.get("is_active")
        if not active and self.editor is not None and self.instance.pk == self.editor.pk:
            raise ValidationError(_("You cannot switch off your own account."))
        return active

    def clean_is_staff(self):
        staff = self.cleaned_data.get("is_staff")
        if not staff and self.editor is not None and self.instance.pk == self.editor.pk:
            # The page that manages accounts is behind this flag, so clearing
            # it on yourself is a one-way door out of the page you are standing
            # on — and there may be nobody else to let you back in.
            raise ValidationError(_("You cannot take the administrator right off your own account."))
        return staff

    def clean(self):
        data = super().clean()
        if self.instance.pk and not self._would_leave_an_administrator(data):
            raise ValidationError(_(
                "This is the last active administrator. Give somebody else the "
                "right first."
            ))
        return data

    def _would_leave_an_administrator(self, data):
        """Whether anybody can still reach this page after this change.

        Checked against ``is_superuser`` rather than ``is_staff``: a superuser
        is the account that can always fix things through the Django admin as
        well, and an app with none is an app that cannot be recovered without a
        shell on the NAS.
        """
        keeps_it = data.get("is_superuser", self.instance.is_superuser) and data.get("is_active", True)
        if keeps_it:
            return True
        return User.objects.filter(is_superuser=True, is_active=True).exclude(pk=self.instance.pk).exists()


class SetPasswordForm(_PasswordPair):
    """A new password for a local account, set by an administrator.

    There is no "old password" box and that is deliberate: this is the
    household's recovery path, used when somebody has forgotten theirs, and
    requiring the old one would make it useless for the only case it is for.
    Self-service password change stays absent — see CLAUDE.md.
    """

    def __init__(self, *args, account=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.account = account

    def clean(self):
        data = super().clean()
        if self.account is not None:
            self._check_strength(self.account)
        return data


class SSOConfigurationForm(forms.ModelForm):
    """The Synology connection, as a form.

    The secret is the only interesting part. It is a write-only field: the value
    is never rendered back (``render_value`` stays at its default of False, and
    the field's initial is never populated), so there is no request anywhere in
    this app that returns the client secret to a browser. Leaving it blank keeps
    what is stored; clearing it needs the explicit checkbox, because "I left
    that box empty" and "I want no secret" are different intentions and only one
    of them is what an empty password field usually means.
    """

    client_secret = forms.CharField(
        label=_("Client secret"), required=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "off"}),
        help_text=_("Leave empty to keep the one already stored."),
    )
    clear_client_secret = forms.BooleanField(
        label=_("Remove the stored secret"), required=False,
    )

    class Meta:
        model = SSOConfiguration
        fields = [
            "enabled", "op_base",
            "authorization_endpoint", "token_endpoint", "user_endpoint", "jwks_endpoint",
            "client_id", "sign_algo", "scopes",
            "allowed_groups", "groups_claim", "staff_group", "verify_ssl",
            "request_timeout",
        ]

    def clean(self):
        data = super().clean()
        if not data.get("enabled"):
            # Half-filled settings are fine while SSO is off — that is what
            # saving a work in progress looks like. The checks below are about
            # switching it on.
            return data

        keeping = self.instance.has_client_secret and not data.get("clear_client_secret")
        if not data.get("client_secret") and not keeping:
            self.add_error("client_secret", _(
                "A client secret is needed before Synology sign-in can be switched on."
            ))
        if not data.get("client_id"):
            self.add_error("client_id", _(
                "A client ID is needed before Synology sign-in can be switched on."
            ))

        # Resolved rather than checked field by field, because the four
        # endpoints may legitimately be blank when the SSO server address is
        # filled in — that is what deriving them from it means.
        probe = SSOConfiguration(
            op_base=data.get("op_base") or "",
            authorization_endpoint=data.get("authorization_endpoint") or "",
            token_endpoint=data.get("token_endpoint") or "",
        )
        endpoints = probe.endpoints()
        if not endpoints["authorization"] or not endpoints["token"]:
            self.add_error("op_base", _(
                "Give the SSO server’s address, or fill the authorisation and token "
                "endpoints in by hand."
            ))

        if data.get("sign_algo") == SignAlgorithm.RS256:
            jwks = data.get("jwks_endpoint") or ""
            if not jwks:
                # RS256 verifies the token against the provider's published key,
                # and without somewhere to fetch it the exchange fails with a
                # signature error that reads like a wrong secret.
                #
                # The error goes on `op_base`, not on `jwks_endpoint`, and that
                # placement is the point: the JWKS field lives inside a closed
                # disclosure now, and an error on a control nobody can see is a
                # Save that does nothing for no stated reason. The address is
                # the field somebody would actually act on — discovery is what
                # fills the JWKS in, so a bad address is the real fault.
                self.add_error("op_base", _(
                    "RS256 verifies the token against the provider’s key, so the JWKS "
                    "address is needed and could not be read from this server. Check the "
                    "address, fill the endpoints in by hand below, or choose HS256, which "
                    "signs with the client secret instead."
                ))
        return data

    def save(self, commit=True, updated_by=None):
        configuration = super().save(commit=False)
        if self.cleaned_data.get("clear_client_secret"):
            configuration.set_client_secret("")
        elif self.cleaned_data.get("client_secret"):
            configuration.set_client_secret(self.cleaned_data["client_secret"])
        # else: the stored value rides along untouched on the instance.
        if updated_by is not None:
            configuration.updated_by = updated_by
        if commit:
            configuration.save()
        return configuration


class PreferencesForm(forms.ModelForm):
    """One person's own settings.

    A radio group rather than a select: there are five choices, each wants a
    "play it" button beside it, and a dropdown hides four of the five behind a
    click on the one page whose whole purpose is comparing them.
    """

    class Meta:
        model = Preferences
        fields = ["timer_sound"]
        widgets = {"timer_sound": forms.RadioSelect}
