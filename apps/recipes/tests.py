"""The recipe collection: the model rules, the pages, and the two properties
that are invisible until the collection is big enough to hurt."""

import io
import re
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from apps.recipes.forms import RecipeForm
from apps.recipes.images import clean_upload
from apps.recipes.models import Recipe, RecipeIngredient, Tag


# --------------------------------------------------------------------------
# Models
# --------------------------------------------------------------------------

class TestSlugs:
    def test_a_slug_is_built_from_the_title(self, db):
        assert Recipe.objects.create(title="Ofengemüse mit Feta").slug == "ofengemuse-mit-feta"

    def test_two_recipes_may_share_a_title(self, db):
        """Two Kartoffelsalats is an ordinary Tuesday in a family collection,
        and `unique=True` on the column turns the second into an
        IntegrityError from a form that looked fine."""
        first = Recipe.objects.create(title="Kartoffelsalat")
        second = Recipe.objects.create(title="Kartoffelsalat")
        assert first.slug != second.slug
        assert second.slug == "kartoffelsalat-2"

    def test_a_title_with_no_latin_characters_still_gets_a_slug(self, db):
        """slugify() of a title that is entirely punctuation is the empty
        string, which would be a URL of `/recipes//`."""
        assert Recipe.objects.create(title="???").slug.startswith("rezept")

    def test_renaming_a_recipe_keeps_its_url(self, db):
        """Somebody has the page open, or bookmarked. The slug is only
        generated when it is blank."""
        recipe = Recipe.objects.create(title="Suppe")
        recipe.title = "Etwas ganz anderes"
        recipe.save()
        assert recipe.slug == "suppe"


class TestAmountsAreWrittenAsPeopleWriteThem:
    @pytest.mark.parametrize("stored,shown", [
        ("250", "250"),
        ("250.000", "250"),
        ("1.5", "1.5"),
        ("0.125", "0.125"),
        ("1000", "1000"),
    ])
    def test_trailing_zeros_are_dropped_without_going_exponential(self, db, stored, shown):
        """`Decimal.normalize()` alone turns 250.000 into 2.5E+2, which is
        worse than the thing it was fixing."""
        item = RecipeIngredient(amount=Decimal(stored), name="Mehl")
        assert item.amount_display == shown

    def test_an_ingredient_with_no_amount_shows_none(self, db):
        """"Salz", "etwas Öl" are real lines. A zero would print "0 g Salz"."""
        assert RecipeIngredient(amount=None, name="Salz").amount_display == ""


class TestTotalTime:
    def test_it_adds_the_two_halves(self, db):
        assert Recipe(prep_minutes=20, cook_minutes=25).total_minutes == 45

    def test_it_is_none_when_nothing_was_recorded(self, db):
        """None rather than 0: "nought minutes" and "nobody wrote it down" are
        different claims and only one belongs on a recipe card."""
        assert Recipe().total_minutes is None

    def test_one_half_is_enough(self, db):
        assert Recipe(cook_minutes=90).total_minutes == 90


# --------------------------------------------------------------------------
# Pages
# --------------------------------------------------------------------------

class TestTheRecipeList:
    def test_it_finds_a_recipe_by_title(self, client, recipe):
        response = client.get(reverse("recipes:list"), {"q": "Kartoffel"})
        assert recipe in response.context["recipes"]

    def test_it_finds_a_recipe_by_an_ingredient(self, client, recipe):
        """The search that matters in a kitchen — "what can I do with fennel?"
        — and the one a title-only search silently fails to answer."""
        response = client.get(reverse("recipes:list"), {"q": "Salz"})
        assert recipe in response.context["recipes"]

    def test_it_finds_a_recipe_by_tag_name(self, client, recipe):
        response = client.get(reverse("recipes:list"), {"q": "Beilage"})
        assert recipe in response.context["recipes"]

    def test_a_recipe_matching_twice_is_listed_once(self, client, recipe, db):
        """A join across ingredients multiplies rows. Without `.distinct()` a
        recipe with two matching ingredients appears twice."""
        RecipeIngredient.objects.create(recipe=recipe, name="Kartoffelmehl")
        response = client.get(reverse("recipes:list"), {"q": "Kartoffel"})
        assert list(response.context["recipes"]).count(recipe) == 1

    def test_filtering_by_tag(self, client, recipe, db):
        other = Recipe.objects.create(title="Nudeln")
        response = client.get(reverse("recipes:list"), {"tag": "beilage"})
        assert recipe in response.context["recipes"]
        assert other not in response.context["recipes"]

    def test_an_unknown_ordering_falls_back_instead_of_failing(self, client, recipe):
        """`order_by` on a string straight off the query string would let a
        caller order by anything in the model — or raise."""
        response = client.get(reverse("recipes:list"), {"order": "created_by__password"})
        assert response.status_code == 200
        assert response.context["order"] == "title"

    def test_an_unknown_tag_is_not_a_500(self, client, recipe):
        response = client.get(reverse("recipes:list"), {"tag": "does-not-exist"})
        assert response.status_code == 200


class TestTheDetailPage:
    def test_it_renders(self, client, recipe):
        response = client.get(recipe.get_absolute_url())
        assert response.status_code == 200
        assert "Kartoffeln" in response.content.decode()

    def test_the_unscaled_amount_rides_along_for_the_scaler(self, client, recipe):
        """recipe_scale.js multiplies `data-amount` rather than re-parsing the
        text it wrote last time, which accumulates rounding error."""
        body = client.get(recipe.get_absolute_url()).content.decode()
        assert 'data-amount="1000.000"' in body or 'data-amount="1000"' in body
        assert 'data-base-servings="4"' in body

    def test_a_missing_recipe_is_a_404(self, client, db):
        response = client.get("/recipes/nothing-here/")
        assert response.status_code == 404


class TestWhoMayEdit:
    def test_the_person_who_added_it_may(self, client, recipe):
        assert client.get(reverse("recipes:edit", args=[recipe.slug])).status_code == 200

    def test_somebody_else_may_not(self, other_user, recipe):
        """A household collection is shared to cook from. Somebody quietly
        rewriting the family recipe is the failure worth preventing."""
        from django.test import Client

        c = Client()
        c.force_login(other_user)
        assert c.get(reverse("recipes:edit", args=[recipe.slug])).status_code == 404

    def test_staff_may(self, staff, recipe):
        from django.test import Client

        c = Client()
        c.force_login(staff)
        assert c.get(reverse("recipes:edit", args=[recipe.slug])).status_code == 200

    def test_somebody_else_cannot_delete_it(self, other_user, recipe):
        from django.test import Client

        c = Client()
        c.force_login(other_user)
        assert c.post(reverse("recipes:delete", args=[recipe.slug])).status_code == 404
        assert Recipe.objects.filter(pk=recipe.pk).exists()

    def test_deleting_needs_a_post(self, client, recipe):
        """A destructive action reachable by GET is one a link preview, a
        prefetcher or a crawler can trigger."""
        assert client.get(reverse("recipes:delete", args=[recipe.slug])).status_code == 405
        assert Recipe.objects.filter(pk=recipe.pk).exists()


# --------------------------------------------------------------------------
# The form, and the formset
# --------------------------------------------------------------------------

def _post_data(recipe=None, **overrides):
    """A complete POST for the recipe form plus its ingredient formset."""
    data = {
        "title": "Ofengemüse",
        "servings": "4",
        "description": "", "prep_minutes": "", "cook_minutes": "",
        "instructions": "", "source": "", "source_url": "", "notes": "",
        "tags_text": "",
        "ingredients-TOTAL_FORMS": "1",
        "ingredients-INITIAL_FORMS": "0",
        "ingredients-MIN_NUM_FORMS": "0",
        "ingredients-MAX_NUM_FORMS": "1000",
        "ingredients-0-id": "",
        "ingredients-0-amount": "800",
        "ingredients-0-unit": "g",
        "ingredients-0-name": "Kartoffeln",
        "ingredients-0-note": "",
    }
    data.update(overrides)
    return data


class TestAddingARecipe:
    def test_it_saves_the_recipe_and_its_ingredients(self, client, user, db):
        response = client.post(reverse("recipes:add"), _post_data(), follow=True)
        assert response.status_code == 200
        recipe = Recipe.objects.get(title="Ofengemüse")
        assert recipe.created_by == user
        assert [i.name for i in recipe.ingredients.all()] == ["Kartoffeln"]

    def test_blank_ingredient_rows_are_dropped(self, client, db):
        """The formset renders spare rows so there is always somewhere to type."""
        data = _post_data(**{
            "ingredients-TOTAL_FORMS": "3",
            "ingredients-1-id": "", "ingredients-1-amount": "", "ingredients-1-unit": "",
            "ingredients-1-name": "", "ingredients-1-note": "",
            "ingredients-2-id": "", "ingredients-2-amount": "", "ingredients-2-unit": "",
            "ingredients-2-name": "", "ingredients-2-note": "",
        })
        client.post(reverse("recipes:add"), data)
        assert Recipe.objects.get(title="Ofengemüse").ingredients.count() == 1

    def test_an_amount_with_no_ingredient_is_refused(self, client, db):
        """"250 g" and no name would be saved as a nameless line that reads as
        a bug on the recipe page."""
        data = _post_data(**{"ingredients-0-name": "", "ingredients-0-amount": "250"})
        response = client.post(reverse("recipes:add"), data)
        assert response.status_code == 200            # re-rendered, not saved
        assert not Recipe.objects.filter(title="Ofengemüse").exists()

    def test_zero_servings_is_refused(self, client, db):
        """Every amount is divided by this to scale, so a zero is a
        ZeroDivisionError on a value somebody was allowed to type."""
        response = client.post(reverse("recipes:add"), _post_data(servings="0"))
        assert response.status_code == 200
        assert not Recipe.objects.filter(title="Ofengemüse").exists()

    def test_tags_are_created_and_reused_case_insensitively(self, client, db):
        Tag.objects.create(name="Suppe")
        client.post(reverse("recipes:add"), _post_data(tags_text="suppe, Ofen , suppe"))
        recipe = Recipe.objects.get(title="Ofengemüse")
        assert Tag.objects.filter(name__iexact="suppe").count() == 1
        assert sorted(recipe.tags.values_list("name", flat=True)) == ["Ofen", "Suppe"]

    def test_the_ingredient_order_is_the_order_on_the_page(self, client, db):
        data = _post_data(**{
            "ingredients-TOTAL_FORMS": "2",
            "ingredients-1-id": "", "ingredients-1-amount": "200",
            "ingredients-1-unit": "g", "ingredients-1-name": "Feta", "ingredients-1-note": "",
        })
        client.post(reverse("recipes:add"), data)
        recipe = Recipe.objects.get(title="Ofengemüse")
        assert [i.name for i in recipe.ingredients.all()] == ["Kartoffeln", "Feta"]


class TestRemovingAnIngredient:
    """The formset trap, tested from the outside because it is the one that
    keeps coming back: a formset is an index range, not a list."""

    def test_ticking_delete_removes_the_line(self, client, recipe, db):
        items = list(recipe.ingredients.all())
        data = {
            "title": recipe.title, "servings": "4",
            "description": "", "prep_minutes": "", "cook_minutes": "",
            "instructions": "", "source": "", "source_url": "", "notes": "",
            "tags_text": "",
            "ingredients-TOTAL_FORMS": "2",
            "ingredients-INITIAL_FORMS": "2",
            "ingredients-MIN_NUM_FORMS": "0",
            "ingredients-MAX_NUM_FORMS": "1000",
            "ingredients-0-id": str(items[0].pk),
            "ingredients-0-amount": "1000", "ingredients-0-unit": "g",
            "ingredients-0-name": "Kartoffeln", "ingredients-0-note": "",
            "ingredients-1-id": str(items[1].pk),
            "ingredients-1-amount": "", "ingredients-1-unit": "",
            "ingredients-1-name": "Salz und Pfeffer", "ingredients-1-note": "",
            "ingredients-1-DELETE": "on",
        }
        client.post(reverse("recipes:edit", args=[recipe.slug]), data)
        assert [i.name for i in recipe.ingredients.all()] == ["Kartoffeln"]

    def test_a_row_carries_its_whole_form(self, client, recipe, db):
        """The structural half of the rule, and the one worth pinning.

        Removal operates on the row element: it ticks that row's DELETE box and
        hides it. So every field belonging to the form — the pk included — has
        to be *inside* the row. A pk rendered beside the rows (which is what
        ``{{ formset }}`` and most hand-written templates do) is a field the
        operation leaves behind, and the row that comes back has lost its
        identity.
        """
        body = client.get(reverse("recipes:edit", args=[recipe.slug])).content.decode()
        rows = re.findall(
            r'<div class="ingredient-row"[^>]*>(.*?)(?=<div class="ingredient-row"|</div>\s*<p>)',
            body, flags=re.S,
        )
        assert len(rows) >= recipe.ingredients.count()
        for index, row in enumerate(rows[:recipe.ingredients.count()]):
            assert f'name="ingredients-{index}-id"' in row, (
                f"row {index} does not carry its own pk field"
            )
            assert f'name="ingredients-{index}-DELETE"' in row, (
                f"row {index} does not carry its own DELETE box, so removing it "
                "cannot mark it deleted"
            )


# --------------------------------------------------------------------------
# Photographs
# --------------------------------------------------------------------------

def _png_bytes(size=(40, 30)):
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", size, (200, 120, 60)).save(buffer, format="PNG")
    return buffer.getvalue()


class TestUploadedPhotographs:
    def test_a_file_that_is_not_an_image_is_refused(self, db):
        upload = SimpleUploadedFile("recipe.png", b"not an image at all",
                                    content_type="image/png")
        with pytest.raises(ValidationError):
            clean_upload(upload)

    def test_the_stored_name_is_ours(self, db):
        """The upload's own name is never used. An extension is what a server
        chooses a Content-Type from, so a file called `recipe.html` served from
        /media/ would be HTML from this app's origin."""
        upload = SimpleUploadedFile("evil.html", _png_bytes(), content_type="image/png")
        cleaned = clean_upload(upload)
        assert cleaned.name.endswith(".png")
        assert "evil" not in cleaned.name

    def test_a_large_photograph_is_shrunk(self, db, settings):
        from PIL import Image

        settings.IMAGE_MAX_EDGE = 100
        upload = SimpleUploadedFile("big.png", _png_bytes((400, 300)), content_type="image/png")
        cleaned = clean_upload(upload)
        assert max(Image.open(cleaned).size) == 100

    def test_a_small_photograph_is_not_enlarged(self, db, settings):
        from PIL import Image

        settings.IMAGE_MAX_EDGE = 1600
        upload = SimpleUploadedFile("small.png", _png_bytes((40, 30)), content_type="image/png")
        assert Image.open(clean_upload(upload)).size == (40, 30)

    def test_an_oversized_upload_is_refused_before_it_is_decoded(self, db, settings):
        settings.IMAGE_MAX_UPLOAD_BYTES = 100
        upload = SimpleUploadedFile("big.png", _png_bytes((400, 300)), content_type="image/png")
        with pytest.raises(ValidationError):
            clean_upload(upload)

    def test_editing_without_touching_the_photograph_keeps_it(self, client, recipe, db):
        """The form re-cleans `image` on every save. A FieldFile is not an
        upload and must be passed through, or every edit strips the picture."""
        recipe.image.save("existing.png", SimpleUploadedFile("x.png", _png_bytes()), save=True)
        before = recipe.image.name
        form = RecipeForm(instance=recipe)
        assert form.initial["title"] == recipe.title
        recipe.refresh_from_db()
        assert recipe.image.name == before


# --------------------------------------------------------------------------
# The two properties that only fail when the collection is large
# --------------------------------------------------------------------------

class TestTheCostDoesNotGrowWithTheCollection:
    """Anything resolved per row — tags, an ingredient count — is one query per
    recipe: fine at thirty, a page of queries at four hundred, and nobody
    notices in between."""

    def _fill(self, user, n):
        for i in range(n):
            recipe = Recipe.objects.create(title=f"Rezept {i}", created_by=user)
            recipe.tags.add(Tag.objects.get_or_create(name=f"tag{i % 3}")[0])
            RecipeIngredient.objects.create(recipe=recipe, name="Mehl", amount=Decimal(i + 1))

    @pytest.mark.parametrize("name", ["recipes:list", "recipes:home"])
    def test_it_costs_the_same_at_five_and_at_forty(self, client, user, db, name):
        self._fill(user, 5)
        with CaptureQueriesContext(connection) as small:
            client.get(reverse(name))

        self._fill(user, 35)
        with CaptureQueriesContext(connection) as large:
            client.get(reverse(name))

        assert len(large) == len(small), (
            f"{name} costs {len(small)} queries at 5 recipes and {len(large)} at 40 — "
            "something is being resolved per row"
        )


class TestAReadDoesNotWrite:
    """A GET that writes takes the one SQLite write lock every other request is
    waiting for — and on a NAS that lock is the scarce resource."""

    @pytest.mark.parametrize("name", ["recipes:home", "recipes:list", "recipes:tags"])
    def test_no_write_on_a_get(self, client, recipe, name):
        with CaptureQueriesContext(connection) as queries:
            client.get(reverse(name))
        assert not _writes(queries), f"{name} writes on a GET: {_writes(queries)}"

    def test_no_write_on_the_detail_page(self, client, recipe):
        with CaptureQueriesContext(connection) as queries:
            client.get(recipe.get_absolute_url())
        assert not _writes(queries), f"the detail page writes on a GET: {_writes(queries)}"


def _writes(captured):
    return [
        query["sql"] for query in captured.captured_queries
        if query["sql"] and query["sql"].strip().split()[0].upper()
        in ("INSERT", "UPDATE", "DELETE")
    ]
