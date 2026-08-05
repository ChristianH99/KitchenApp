"""Signing in: the OIDC claim handling, the local fallback, and the throttle.

The OIDC tests do not talk to a Synology box. They exercise the part that is
*this* app's decision — what a token means and who it lets in — by handing the
backend claim dictionaries, which is also the only way to test the cases that
matter: the DSM version that omits the group claim, the account that was
renamed, the token with no subject.
"""

import pytest
from django.contrib.auth.models import User
from django.core.cache import cache
from django.core.exceptions import SuspiciousOperation
from django.urls import reverse

from apps.accounts import throttle
from apps.accounts.oidc import SynologyOIDCBackend, _claim_groups, _display_name


@pytest.fixture(autouse=True)
def clear_throttle():
    cache.clear()
    yield
    cache.clear()


# --------------------------------------------------------------------------
# What a token means
# --------------------------------------------------------------------------

class TestIdentity:
    def test_a_user_is_matched_by_sub_not_by_email(self, db):
        """DSM lets two accounts share an address, and an address can be
        reassigned. Matching on it signs somebody in as somebody else."""
        existing = User.objects.create_user(username="sub-123", email="anna@example.org")
        backend = SynologyOIDCBackend()
        found = backend.filter_users_by_claims({"sub": "sub-123", "email": "other@example.org"})
        assert list(found) == [existing]

        by_email = backend.filter_users_by_claims({"sub": "sub-999", "email": "anna@example.org"})
        assert not by_email.exists()

    def test_a_renamed_dsm_account_keeps_its_recipes(self, db):
        """The identity is `sub`, which survives a rename; the human-readable
        name lives in first/last name where it can change freely."""
        backend = SynologyOIDCBackend()
        user = backend.create_user({"sub": "sub-1", "email": "c@example.org",
                                    "given_name": "Chris", "family_name": "H"})
        backend.update_user(user, {"sub": "sub-1", "email": "c@example.org",
                                   "given_name": "Christian", "family_name": "H"})
        user.refresh_from_db()
        assert user.username == "sub-1"
        assert user.first_name == "Christian"
        assert User.objects.count() == 1

    def test_an_sso_account_has_no_usable_password(self, db):
        """Otherwise the local login form is a second, unmanaged door into a
        DSM-managed account."""
        user = SynologyOIDCBackend().create_user({"sub": "sub-1"})
        assert not user.has_usable_password()

    def test_a_token_with_no_subject_is_refused(self, db):
        """Without it there is no stable identity and every login would create
        a new account."""
        with pytest.raises(SuspiciousOperation):
            SynologyOIDCBackend().verify_claims({"email": "a@example.org"})


class TestDisplayName:
    def test_given_and_family_name_are_preferred(self):
        assert _display_name({"given_name": "Anna", "family_name": "Müller"}) == ("Anna", "Müller")

    def test_a_single_name_claim_is_split_at_the_last_space(self):
        assert _display_name({"name": "Anna Maria Müller"}) == ("Anna Maria", "Müller")

    def test_a_one_word_name_is_kept_whole(self):
        assert _display_name({"name": "Anna"}) == ("Anna", "")

    def test_no_name_claim_at_all_is_fine(self):
        """Some DSM builds send only sub and email. Nothing depends on this
        being present."""
        assert _display_name({}) == ("", "")


class TestGroupClaims:
    @pytest.mark.parametrize("raw,expected", [
        (["users", "admins"], {"users", "admins"}),
        ("users admins", {"users", "admins"}),
        ("users,admins", {"users", "admins"}),
        (None, set()),
        ("", set()),
    ])
    def test_however_this_dsm_version_spells_them(self, raw, expected, settings):
        settings.OIDC_GROUPS_CLAIM = "groups"
        assert _claim_groups({"groups": raw}) == expected


class TestWhoMaySignIn:
    def test_with_no_group_configured_anybody_authenticated_may(self, db, settings):
        settings.OIDC_ALLOWED_GROUPS = []
        assert SynologyOIDCBackend().verify_claims({"sub": "sub-1"}) is True

    def test_a_member_of_an_allowed_group_may(self, db, settings):
        settings.OIDC_ALLOWED_GROUPS = ["haushalt"]
        settings.OIDC_GROUPS_CLAIM = "groups"
        assert SynologyOIDCBackend().verify_claims({"sub": "s", "groups": ["haushalt"]}) is True

    def test_somebody_outside_it_may_not(self, db, settings):
        settings.OIDC_ALLOWED_GROUPS = ["haushalt"]
        settings.OIDC_GROUPS_CLAIM = "groups"
        assert SynologyOIDCBackend().verify_claims({"sub": "s", "groups": ["gaeste"]}) is False

    def test_a_missing_group_claim_is_refused_rather_than_waved_through(self, db, settings):
        """The case that actually happens: a DSM version that does not send the
        claim at all. An app told "only these groups" must not fall open
        because the claim it needs went missing."""
        settings.OIDC_ALLOWED_GROUPS = ["haushalt"]
        settings.OIDC_GROUPS_CLAIM = "groups"
        assert SynologyOIDCBackend().verify_claims({"sub": "s"}) is False


class TestStaffFollowsDsm:
    def test_membership_grants_it(self, db, settings):
        settings.OIDC_STAFF_GROUP = "admins"
        settings.OIDC_GROUPS_CLAIM = "groups"
        user = SynologyOIDCBackend().create_user({"sub": "s", "groups": ["admins"]})
        assert user.is_staff

    def test_losing_membership_takes_it_away_again(self, db, settings):
        """Removing somebody from the admin group in DSM has to actually remove
        their admin, and the next login is the only moment this app hears
        about the change."""
        settings.OIDC_STAFF_GROUP = "admins"
        settings.OIDC_GROUPS_CLAIM = "groups"
        backend = SynologyOIDCBackend()
        user = backend.create_user({"sub": "s", "groups": ["admins"]})
        backend.update_user(user, {"sub": "s", "groups": ["users"]})
        user.refresh_from_db()
        assert not user.is_staff

    def test_with_no_group_configured_a_local_superuser_is_not_demoted(self, db, settings):
        """The fallback administrator signing in through SSO once must not lose
        the access the fallback exists to provide."""
        settings.OIDC_STAFF_GROUP = ""
        user = User.objects.create_user(username="s", is_staff=True, is_superuser=True)
        SynologyOIDCBackend().update_user(user, {"sub": "s"})
        user.refresh_from_db()
        assert user.is_staff


# --------------------------------------------------------------------------
# The local fallback
# --------------------------------------------------------------------------

class TestTheLocalLogin:
    def test_a_correct_password_signs_in(self, anon, user):
        response = anon.post(reverse("accounts:login"),
                             {"username": "anna", "password": "pw"})
        assert response.status_code == 302
        assert response["Location"] == "/"

    def test_a_wrong_password_does_not(self, anon, user):
        response = anon.post(reverse("accounts:login"),
                             {"username": "anna", "password": "nope"})
        assert response.status_code == 200
        assert not response.wsgi_request.user.is_authenticated

    def test_the_refusal_does_not_reveal_whether_the_account_exists(self, anon, user):
        """Two different messages would be an account-enumeration oracle."""
        real = anon.post(reverse("accounts:login"),
                         {"username": "anna", "password": "nope"}).content
        fake = anon.post(reverse("accounts:login"),
                         {"username": "nobody", "password": "nope"}).content
        assert b"Wrong username or password" in real
        assert b"Wrong username or password" in fake

    def test_an_off_site_next_is_ignored(self, anon, user):
        """An unchecked ?next= on a login page is the useful kind of open
        redirect: a link that really does sign somebody in, then drops them
        somewhere of the attacker's choosing having just typed a password."""
        response = anon.post(reverse("accounts:login"), {
            "username": "anna", "password": "pw", "next": "https://evil.example/",
        })
        assert response["Location"] == "/"

    def test_an_in_app_next_is_honoured(self, anon, user):
        response = anon.post(reverse("accounts:login"), {
            "username": "anna", "password": "pw", "next": "/recipes/",
        })
        assert response["Location"] == "/recipes/"

    def test_the_sso_button_is_offered_only_when_it_is_configured(self, anon, db, settings):
        settings.OIDC_ENABLED = False
        assert b"Sign in with Synology" not in anon.get(reverse("accounts:login")).content
        settings.OIDC_ENABLED = True
        assert b"Sign in with Synology" in anon.get(reverse("accounts:login")).content

    def test_the_local_form_is_always_reachable(self, anon, db, settings):
        """The whole point of the fallback: it has to be there when SSO is
        the thing that is broken."""
        settings.OIDC_ENABLED = True
        body = anon.get(reverse("accounts:login"), {"local": "1"}).content
        assert b'name="password"' in body


class TestTheThrottle:
    def test_a_run_of_failures_locks_the_pair_out(self, settings):
        settings.LOGIN_MAX_ATTEMPTS = 3
        for _ in range(3):
            throttle.note_failure("anna", "10.0.0.1")
        assert throttle.is_locked_out("anna", "10.0.0.1")

    def test_a_different_address_is_not_affected(self, settings):
        settings.LOGIN_MAX_ATTEMPTS = 3
        settings.LOGIN_MAX_ATTEMPTS_PER_HOST = 50
        for _ in range(3):
            throttle.note_failure("anna", "10.0.0.1")
        assert not throttle.is_locked_out("anna", "10.0.0.2")

    def test_one_host_working_through_a_list_of_accounts_is_caught(self, settings):
        """The per-(user, IP) counter cannot see this at all: ten usernames
        tried once each is ten attempts and nine untouched counters."""
        settings.LOGIN_MAX_ATTEMPTS = 10
        settings.LOGIN_MAX_ATTEMPTS_PER_HOST = 5
        for name in ("a", "b", "c", "d", "e"):
            throttle.note_failure(name, "10.0.0.9")
        assert throttle.is_locked_out("someone-else", "10.0.0.9")

    def test_signing_in_clears_the_users_counter_but_not_the_hosts(self, settings):
        """One success among fifty failures is what a working attack looks
        like."""
        settings.LOGIN_MAX_ATTEMPTS = 3
        settings.LOGIN_MAX_ATTEMPTS_PER_HOST = 4
        for name in ("a", "b", "c", "d"):
            throttle.note_failure(name, "10.0.0.9")
        throttle.note_success("a", "10.0.0.9")
        assert throttle.is_locked_out("a", "10.0.0.9")

    def test_a_locked_out_caller_is_refused_even_with_the_right_password(self, anon, user, settings):
        settings.LOGIN_MAX_ATTEMPTS = 2
        for _ in range(2):
            anon.post(reverse("accounts:login"), {"username": "anna", "password": "nope"})
        response = anon.post(reverse("accounts:login"), {"username": "anna", "password": "pw"})
        assert response.status_code == 200
        assert b"Too many failed attempts" in response.content

    def test_a_forwarded_address_is_only_believed_behind_a_trusted_proxy(self, rf, settings):
        """On a direct connection X-Forwarded-For is a client-supplied header;
        believing it would let anybody reset their own counter by sending a
        different value each time."""
        request = rf.get("/", HTTP_X_FORWARDED_FOR="1.2.3.4", REMOTE_ADDR="10.0.0.1")
        settings.USE_X_FORWARDED_HOST = False
        assert throttle.client_ip(request) == "10.0.0.1"
        settings.USE_X_FORWARDED_HOST = True
        assert throttle.client_ip(request) == "1.2.3.4"


class TestSigningOut:
    def test_it_needs_a_post(self, client):
        """A GET logout is triggerable by any <img> tag on any page."""
        assert client.get(reverse("accounts:logout")).status_code == 405

    def test_a_local_session_ends_here(self, client, user):
        response = client.post(reverse("accounts:logout"))
        assert response.status_code == 302
        assert not response.wsgi_request.user.is_authenticated

    def test_an_sso_session_is_handed_to_the_provider(self, client, user, settings):
        """A local logout only drops this app's cookie — the Synology session
        is still live, so the next click on "Sign in with Synology" goes
        straight back in without a prompt, which is not signing out."""
        settings.OIDC_ENABLED = True
        session = client.session
        session["oidc_id_token"] = "token"
        session.save()
        response = client.post(reverse("accounts:logout"))
        assert response["Location"] == reverse("oidc_logout")


# --------------------------------------------------------------------------
# Managing the household's accounts
# --------------------------------------------------------------------------

def _people_urls():
    """Every account-management route, discovered rather than listed.

    The point of finding them by walking the URLconf is that a page added to
    apps/accounts/users.py next month is covered the day it lands. The failure
    story becomes "you forgot the decorator" instead of "you forgot to write a
    test about the decorator", which is the difference that matters — a
    forgotten check leaves a page that looks completely normal and answers to
    anybody who is signed in.
    """
    from django.urls import get_resolver

    found = []
    for pattern in get_resolver().url_patterns:
        if getattr(pattern, "app_name", None) != "accounts":
            continue
        for entry in pattern.url_patterns:
            if entry.name and entry.name.startswith("user-"):
                args = [1] if ":pk>" in str(entry.pattern) else []
                found.append((entry.name, reverse("accounts:" + entry.name, args=args)))
    return found


@pytest.fixture
def boss(db):
    """A signed-in superuser — the account these pages are actually used from."""
    from django.test import Client

    person = User.objects.create_superuser(username="chefin", password="pw-that-is-long")
    session = Client()
    session.force_login(person)
    return session, person


class TestOnlyStaffMayManageAccounts:
    def test_every_route_refuses_an_ordinary_account(self, client, user, db):
        urls = _people_urls()
        assert urls, "no accounts:user-* routes were found — has the prefix changed?"
        for name, url in urls:
            for response in (client.get(url), client.post(url)):
                assert response.status_code in (404, 405), (
                    name + " answers " + str(response.status_code) + " to somebody "
                    "who is signed in but not staff"
                )

    def test_staff_may_see_the_list(self, staff, db):
        from django.test import Client

        session = Client()
        session.force_login(staff)
        assert session.get(reverse("accounts:user-list")).status_code == 200


class TestTellingTheTwoKindsOfAccountApart:
    """``has_usable_password()`` is not a heuristic here: the OIDC backend calls
    ``set_unusable_password()`` on creation precisely so a DSM-managed account
    can never also be reachable through the local form."""

    def test_an_sso_account_is_recognised(self, db):
        from apps.accounts.forms import is_sso_account

        person = SynologyOIDCBackend().create_user({"sub": "sub-abc", "email": "a@x.org"})
        assert is_sso_account(person)

    def test_a_local_account_is_not(self, user):
        from apps.accounts.forms import is_sso_account

        assert not is_sso_account(user)

    def test_an_sso_account_is_offered_no_password_page(self, boss, db):
        """Giving it one would open the second, unmanaged door into a
        DSM-managed identity that SSO exists to close."""
        session, _ = boss
        person = SynologyOIDCBackend().create_user({"sub": "sub-abc", "email": ""})
        assert session.get(
            reverse("accounts:user-password", args=[person.pk])
        ).status_code == 404

    def test_dsm_owns_an_sso_account_s_name(self, db):
        """A value typed here would survive until the next sign-in and then be
        silently replaced, which is worse than the field not being there."""
        from apps.accounts.forms import UserEditForm

        person = SynologyOIDCBackend().create_user({"sub": "sub-abc", "email": ""})
        form = UserEditForm(instance=person)
        assert form.fields["first_name"].disabled
        assert form.fields["email"].disabled


class TestCreatingALocalAccount:
    def _payload(self, **overrides):
        data = {
            "username": "mira", "first_name": "Mira", "last_name": "", "email": "",
            "password1": "kirschkuchen-42", "password2": "kirschkuchen-42",
            "is_active": "on",
        }
        data.update(overrides)
        return data

    def test_it_creates_one_that_can_sign_in(self, boss, db):
        session, _ = boss
        session.post(reverse("accounts:user-add"), self._payload())
        person = User.objects.get(username="mira")
        assert person.check_password("kirschkuchen-42")
        assert person.has_usable_password()

    def test_two_different_passwords_are_refused(self, boss, db):
        session, _ = boss
        session.post(reverse("accounts:user-add"),
                     self._payload(password2="kirschkuchen-43"))
        assert not User.objects.filter(username="mira").exists()

    def test_a_weak_password_is_refused(self, boss, db):
        """The same validators as everywhere else. The version where only one
        of the two password pages runs them is the version that lets a weak
        password in through whichever page nobody looked at."""
        session, _ = boss
        session.post(reverse("accounts:user-add"),
                     self._payload(password1="1234", password2="1234"))
        assert not User.objects.filter(username="mira").exists()

    def test_somebody_who_is_not_a_superuser_cannot_grant_one(self, staff, db):
        """Otherwise "may manage accounts" is also "may grant yourself
        everything", one page later."""
        from django.test import Client

        session = Client()
        session.force_login(staff)
        session.post(reverse("accounts:user-add"), self._payload(is_superuser="on"))
        assert not User.objects.get(username="mira").is_superuser


class TestTheDoorsThatMustNotCloseBehindYou:
    def test_you_cannot_switch_your_own_account_off(self, boss):
        session, person = boss
        session.post(reverse("accounts:user-active", args=[person.pk]))
        person.refresh_from_db()
        assert person.is_active

    def test_you_cannot_delete_your_own_account(self, boss):
        session, person = boss
        session.post(reverse("accounts:user-delete", args=[person.pk]))
        assert User.objects.filter(pk=person.pk).exists()

    def test_you_cannot_take_your_own_administration_right_away(self, boss):
        """The page that manages accounts is behind this flag, so clearing it
        on yourself is a one-way door out of the page you are standing on."""
        session, person = boss
        session.post(reverse("accounts:user-edit", args=[person.pk]), {
            "first_name": "", "last_name": "", "email": "", "is_active": "on",
        })
        person.refresh_from_db()
        assert person.is_staff

    def test_the_last_administrator_cannot_be_switched_off(self, staff, db):
        """An app with no active superuser cannot be recovered without a shell
        on the NAS."""
        from django.test import Client

        only = User.objects.create_superuser(username="einzige", password="pw-long-enough")
        session = Client()
        session.force_login(staff)
        session.post(reverse("accounts:user-active", args=[only.pk]))
        only.refresh_from_db()
        assert only.is_active

    def test_one_of_two_administrators_may_go(self, boss, db):
        session, _ = boss
        second = User.objects.create_superuser(username="zweite", password="pw-long-enough")
        session.post(reverse("accounts:user-delete", args=[second.pk]))
        assert not User.objects.filter(pk=second.pk).exists()


class TestSettingAPassword:
    def test_an_administrator_can_set_one_without_the_old_password(self, boss, user):
        """This is the household's way back in when somebody has forgotten
        theirs; asking for the old one would make it useless for the only case
        it exists for."""
        session, _ = boss
        session.post(reverse("accounts:user-password", args=[user.pk]), {
            "password1": "haferflocken-77", "password2": "haferflocken-77",
        })
        user.refresh_from_db()
        assert user.check_password("haferflocken-77")

    def test_changing_your_own_does_not_sign_you_out(self, boss):
        """Rotating the session hash ends every session including this one, and
        being thrown to the login page by your own successful action reads as a
        failure."""
        session, person = boss
        session.post(reverse("accounts:user-password", args=[person.pk]), {
            "password1": "haferflocken-77", "password2": "haferflocken-77",
        })
        assert session.get(reverse("accounts:user-list")).status_code == 200


class TestSwitchingAnAccountOff:
    def test_it_is_reversible_and_keeps_their_recipes(self, boss, user, db):
        from apps.recipes.models import Recipe

        session, _ = boss
        Recipe.objects.create(title="Griesbrei", created_by=user)
        session.post(reverse("accounts:user-active", args=[user.pk]))
        user.refresh_from_db()
        assert not user.is_active
        assert Recipe.objects.get(title="Griesbrei").created_by == user

        session.post(reverse("accounts:user-active", args=[user.pk]))
        user.refresh_from_db()
        assert user.is_active

    def test_deleting_keeps_the_recipes_but_loses_the_name(self, boss, user, db):
        """SET_NULL, not CASCADE: deleting the account of somebody who has left
        must not delete the recipes they contributed."""
        from apps.recipes.models import Recipe

        session, _ = boss
        Recipe.objects.create(title="Griesbrei", created_by=user)
        session.post(reverse("accounts:user-delete", args=[user.pk]))
        assert Recipe.objects.get(title="Griesbrei").created_by is None
