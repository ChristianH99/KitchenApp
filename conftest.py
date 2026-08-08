"""Shared fixtures.

The tests run in English. The app's default language is German, and a test that
asserts on a German string is a test that fails the day somebody improves the
wording of a translation — which is not the thing under test. So the language is
pinned here and assertions are written against the source strings.
"""

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.utils import translation


@pytest.fixture(autouse=True)
def forget_sso_configuration():
    """Drop the cached SSO configuration around every test.

    ``apps/accounts/sso.py`` caches it for thirty seconds so that the two
    gunicorn workers are not each running a query per request. In a test run
    that cache spans tests, so one test that saves a configuration decides what
    the next one sees — and the symptom is a suite that passes alone and fails
    in order, which is the worst kind to chase.
    """
    from django.core.cache import cache

    cache.clear()
    yield
    cache.clear()


@pytest.fixture(autouse=True)
def english(settings):
    """Both halves are needed.

    ``LANGUAGE_CODE`` is what ``LocaleMiddleware`` falls back to when a request
    carries no session, cookie or Accept-Language — which is every request the
    test client makes. Without it a rendered page comes back in German however
    much the code around it has overridden the active language, because the
    middleware resolves it again per request.

    ``translation.override`` covers everything outside a request: a form's error
    message, a model's verbose name, a string built in a helper.
    """
    settings.LANGUAGE_CODE = "en"
    with translation.override("en"):
        yield


@pytest.fixture
def user(db):
    """An ordinary signed-in member of the household."""
    return User.objects.create_user(username="anna", password="pw", first_name="Anna")


@pytest.fixture
def other_user(db):
    """Somebody else in the same household — used to check that "may edit"
    means the person who added it, not merely anybody with an account."""
    return User.objects.create_user(username="bernd", password="pw")


@pytest.fixture
def staff(db):
    return User.objects.create_user(username="chef", password="pw", is_staff=True)


@pytest.fixture
def client(user):
    """A client that is already signed in, since almost nothing is reachable
    otherwise. Tests about *being* signed out use `anon` below."""
    c = Client()
    c.force_login(user)
    return c


@pytest.fixture
def anon():
    return Client()


@pytest.fixture
def recipe(user, db):
    from decimal import Decimal

    from apps.recipes.models import Recipe, RecipeIngredient, Tag

    r = Recipe.objects.create(
        title="Kartoffelsalat",
        description="Der schwäbische, mit Brühe.",
        servings=4, prep_minutes=20, cook_minutes=25,
        instructions="Kartoffeln kochen.\n\nAbkühlen lassen.",
        created_by=user,
    )
    r.tags.add(Tag.objects.create(name="Beilage"))
    RecipeIngredient.objects.create(recipe=r, position=0, amount=Decimal("1000"),
                                    unit="g", name="Kartoffeln")
    # no_amount, not merely a null amount: "to taste" is now a thing a line
    # says rather than something inferred from a blank, because the form
    # refuses a line that answers neither. apps/recipes/forms.py explains the
    # trade, and recipes/migrations/0003 set the flag on every line already
    # written this way.
    RecipeIngredient.objects.create(recipe=r, position=1, amount=None, no_amount=True,
                                    unit="", name="Salz und Pfeffer")
    return r
