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

from apps.recipes import diagram as diagram_module
from apps.recipes.forms import RecipeForm
from apps.recipes.images import clean_upload
from apps.recipes.models import (
    CookLog, CookPortion, PortionSize, Recipe, RecipeIngredient, RecipeStep, Tag,
)


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

def _management(prefix, total, initial=0):
    """The four hidden fields every formset needs in the POST.

    Written out here because there are now two formsets on the recipe form and
    a POST missing one of their management forms is not a validation error on
    that formset — it is a ``ManagementForm`` failure that refuses the whole
    submission, which reads as "the save button does nothing".
    """
    return {
        f"{prefix}-TOTAL_FORMS": str(total),
        f"{prefix}-INITIAL_FORMS": str(initial),
        f"{prefix}-MIN_NUM_FORMS": "0",
        f"{prefix}-MAX_NUM_FORMS": "1000",
    }


def _post_data(recipe=None, **overrides):
    """A complete POST for the recipe form plus both of its formsets."""
    data = {
        "title": "Ofengemüse",
        "servings": "4",
        "description": "", "prep_minutes": "", "cook_minutes": "",
        "instructions": "", "source": "", "source_url": "", "notes": "",
        "tags_text": "",
        **_management("ingredients", 1),
        "ingredients-0-id": "",
        "ingredients-0-amount": "800",
        "ingredients-0-unit": "g",
        "ingredients-0-name": "Kartoffeln",
        "ingredients-0-note": "",
        **_management("steps", 0),
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
            **_management("ingredients", 2, initial=2),
            **_management("steps", 0),
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
# The diagram
# --------------------------------------------------------------------------

def _step(recipe, text, into=None, position=0, **kwargs):
    return RecipeStep.objects.create(recipe=recipe, text=text, parent=into,
                                     position=position, **kwargs)


def _line(recipe, name, step=None, position=0, **kwargs):
    return RecipeIngredient.objects.create(recipe=recipe, name=name, step=step,
                                           position=position, **kwargs)


def _occupancy(block, columns):
    """Lay one ``<tbody>`` out the way a browser does, and say what it covers.

    This is the table algorithm itself: place each cell at the first free
    column of its row, then mark every square its rowspan and colspan claim.
    Running it in the test is what turns "the diagram looks wrong" into a
    specific failure, because the two ways it goes wrong are both invisible in
    the model and obvious here — a square claimed twice (cells overlapping) and
    a square claimed by nobody (a hole, which shifts every cell after it one
    column to the left).
    """
    covered = {}
    for row_index, row in enumerate(block):
        column = 0
        for cell in row:
            while (row_index, column) in covered:
                column += 1
            for down in range(cell.rowspan):
                for across in range(cell.colspan):
                    square = (row_index + down, column + across)
                    assert square not in covered, f"two cells claim {square}"
                    assert square[1] < columns, f"{square} is off the right edge"
                    covered[square] = cell
            column += cell.colspan
    return covered


def _assert_rectangular(diagram):
    """Every square of every block is covered exactly once."""
    for block in diagram.blocks:
        covered = _occupancy(block, diagram.columns)
        for row in range(len(block)):
            for column in range(diagram.columns):
                assert (row, column) in covered, (
                    f"nothing covers row {row}, column {column} — the row is "
                    "short, so everything after it sits one column too far left"
                )


class TestTheDiagramGeometry:
    """The reference is the Cooking-for-Engineers brownie: a straight chain of
    operations, each one column further right than the deepest thing feeding
    it, with the ingredients that join late leaving empty squares behind them."""

    def test_a_recipe_with_no_steps_has_no_diagram(self, recipe):
        """Every recipe written before this feature existed is in this state,
        and those pages must show the ingredient list they always showed — not
        a one-column table pretending to be a diagram."""
        assert not diagram_module.build(recipe)

    def test_a_chain_puts_each_operation_one_column_further_right(self, recipe, db):
        recipe.ingredients.all().delete()
        # Read `into` as "feeds into", which is the direction the diagram is
        # drawn in: melt → mix → bake, with bake the finished dish.
        bake = _step(recipe, "bake", position=2)
        mix = _step(recipe, "mix", into=bake, position=1)
        melt = _step(recipe, "melt", into=mix, position=0)
        _line(recipe, "butter", step=melt, position=0)
        _line(recipe, "sugar", step=mix, position=1)
        _line(recipe, "flour", step=bake, position=2)

        built = diagram_module.build(recipe)
        # One column for the ingredients plus one per operation.
        assert built.columns == 4
        _assert_rectangular(built)

    def test_a_branch_stretches_to_meet_the_merge(self, recipe, db):
        """The shape a straight chain never exercises: two arms of different
        depths meeting. The shallow one has to span the columns between its own
        and its parent's, or the merge leaves a hole."""
        recipe.ingredients.all().delete()
        toss = _step(recipe, "toss", position=3)
        drain = _step(recipe, "drain", into=toss, position=1)
        boil = _step(recipe, "boil", into=drain, position=0)
        blend = _step(recipe, "blend", into=toss, position=2)

        _line(recipe, "pasta", step=boil, position=0)
        _line(recipe, "basil", step=blend, position=1)

        built = diagram_module.build(recipe)
        # boil(1) → drain(2) → toss(3), plus the ingredient column.
        assert built.columns == 4
        _assert_rectangular(built)

        # "blend" is one deep but its parent is three deep, so its cell has to
        # cover the two columns in between as well as its own.
        blend_cell = next(
            cell for block in built.blocks for row in block for cell in row
            if cell.kind == "step" and cell.step.pk == blend.pk
        )
        assert blend_cell.colspan == 2

    def test_a_step_with_nothing_going_into_it_spans_the_whole_width(self, recipe, db):
        """"Butter and flour an 8x8-in pan" — the full-width rows across the top
        of the reference diagram."""
        recipe.ingredients.all().delete()
        _step(recipe, "heat the oven", position=0)
        mix = _step(recipe, "mix", position=1)
        _line(recipe, "flour", step=mix)

        built = diagram_module.build(recipe)
        heading = built.blocks[0][0][0]
        assert heading.kind == "step"
        assert heading.colspan == built.columns
        _assert_rectangular(built)

    def test_an_ingredient_in_no_step_still_appears(self, recipe, db):
        """Losing a line because nobody assigned it is the failure that makes
        somebody distrust the whole page."""
        recipe.ingredients.all().delete()
        mix = _step(recipe, "mix", position=0)
        _line(recipe, "flour", step=mix, position=0)
        loose = _line(recipe, "salt", step=None, position=1)

        built = diagram_module.build(recipe)
        assert [item.name for item in built.unplaced] == ["salt"]
        names = [
            cell.ingredient.name
            for block in built.blocks for row in block for cell in row
            if cell.kind == "ingredient"
        ]
        assert loose.name in names
        _assert_rectangular(built)

    def test_a_substitute_is_not_a_row_of_its_own(self, recipe, db):
        """It replaces something that takes part in the merge; it does not take
        part itself. A row for it would put it in the table twice."""
        recipe.ingredients.all().delete()
        mix = _step(recipe, "mix", position=0)
        butter = _line(recipe, "butter", step=mix, position=0)
        _line(recipe, "margarine", position=1, alternative_for=butter)

        built = diagram_module.build(recipe)
        cells = [
            cell for block in built.blocks for row in block for cell in row
            if cell.kind == "ingredient"
        ]
        assert [cell.ingredient.name for cell in cells] == ["butter"]
        assert [alt.name for alt in cells[0].alternatives] == ["margarine"]
        _assert_rectangular(built)

    def test_a_loop_in_the_data_still_renders(self, recipe, db):
        """A cycle cannot be made through the form, but it can be made in the
        Django admin or by an edit straight to the database — and a cycle is an
        infinite loop inside a page render rather than a wrong picture."""
        recipe.ingredients.all().delete()
        first = _step(recipe, "a", position=0)
        second = _step(recipe, "b", into=first, position=1)
        first.parent = second
        first.save()

        built = diagram_module.build(recipe)     # must return at all
        _assert_rectangular(built)

    def test_the_cooking_order_puts_children_before_parents(self, recipe, db):
        recipe.ingredients.all().delete()
        bake = _step(recipe, "bake", position=2)
        mix = _step(recipe, "mix", into=bake, position=1)
        melt = _step(recipe, "melt", into=mix, position=0)

        order = [entry.step.pk for entry in diagram_module.build(recipe).order]
        assert order == [melt.pk, mix.pk, bake.pk]

    def test_the_cooking_order_numbers_from_one(self, recipe, db):
        _step(recipe, "boil", position=0)
        assert [e.number for e in diagram_module.build(recipe).order] == [1]


# --------------------------------------------------------------------------
# Wiring the diagram up from the form
# --------------------------------------------------------------------------

def _with_steps(**overrides):
    """A recipe POST carrying two steps and one ingredient wired into them."""
    data = _post_data(**{
        **_management("steps", 2),
        "steps-0-id": "", "steps-0-text": "melt", "steps-0-minutes": "",
        "steps-0-detail": "", "steps-0-parent_index": "1",
        "steps-1-id": "", "steps-1-text": "bake", "steps-1-minutes": "45",
        "steps-1-detail": "", "steps-1-parent_index": "",
        "ingredients-0-step_index": "0",
    })
    data.update(overrides)
    return data


class TestTheFormWiresTheDiagramUp:
    """The indices are the whole mechanism — on a recipe being typed in for the
    first time nothing has a primary key yet, so the page refers to a step by
    its row number and forms.py turns that into a foreign key after saving."""

    def test_a_new_recipe_saves_its_tree(self, client, db):
        client.post(reverse("recipes:add"), _with_steps())
        recipe = Recipe.objects.get(title="Ofengemüse")
        melt = recipe.steps.get(text="melt")
        bake = recipe.steps.get(text="bake")
        assert melt.parent_id == bake.pk
        assert bake.parent_id is None
        assert recipe.ingredients.get(name="Kartoffeln").step_id == melt.pk

    def test_the_order_on_the_page_is_the_order_of_the_steps(self, client, db):
        client.post(reverse("recipes:add"), _with_steps())
        recipe = Recipe.objects.get(title="Ofengemüse")
        assert [s.text for s in recipe.steps.all()] == ["melt", "bake"]

    def test_editing_does_not_flatten_a_diagram_it_did_not_touch(self, client, db):
        """The reverse translation. Without ``prime_diagram_indices`` the edit
        form comes back with every index empty, and pressing Save — having
        changed nothing — takes the diagram apart."""
        client.post(reverse("recipes:add"), _with_steps())
        recipe = Recipe.objects.get(title="Ofengemüse")
        melt, bake = recipe.steps.get(text="melt"), recipe.steps.get(text="bake")
        line = recipe.ingredients.get(name="Kartoffeln")

        page = client.get(reverse("recipes:edit", args=[recipe.slug])).content.decode()
        assert 'name="steps-0-parent_index" value="1"' in page
        assert 'name="ingredients-0-step_index" value="0"' in page

        client.post(reverse("recipes:edit", args=[recipe.slug]), _post_data(**{
            "title": recipe.title,
            **_management("ingredients", 1, initial=1),
            "ingredients-0-id": str(line.pk), "ingredients-0-amount": "800",
            "ingredients-0-unit": "g", "ingredients-0-name": "Kartoffeln",
            "ingredients-0-note": "", "ingredients-0-step_index": "0",
            **_management("steps", 2, initial=2),
            "steps-0-id": str(melt.pk), "steps-0-text": "melt", "steps-0-minutes": "",
            "steps-0-detail": "", "steps-0-parent_index": "1",
            "steps-1-id": str(bake.pk), "steps-1-text": "bake", "steps-1-minutes": "45",
            "steps-1-detail": "", "steps-1-parent_index": "",
        }))
        melt.refresh_from_db()
        assert melt.parent_id == bake.pk

    def test_an_index_naming_a_row_that_is_gone_becomes_unassigned(self, client, db):
        """Not an error. The only way to produce one is by hand-crafting a
        POST, and dropping the reference loses nothing while raising would lose
        the whole recipe."""
        client.post(reverse("recipes:add"), _with_steps(**{
            "ingredients-0-step_index": "7",
        }))
        recipe = Recipe.objects.get(title="Ofengemüse")
        assert recipe.ingredients.get(name="Kartoffeln").step_id is None

    def test_a_loop_in_the_post_is_broken_rather_than_stored(self, client, db):
        """A cycle reaching the database is a recipe whose page never returns."""
        client.post(reverse("recipes:add"), _with_steps(**{
            "steps-0-parent_index": "1",
            "steps-1-parent_index": "0",
        }))
        recipe = Recipe.objects.get(title="Ofengemüse")
        roots = [s for s in recipe.steps.all() if s.parent_id is None]
        assert roots, "every step claims a parent — the tree has no root"

    def test_a_step_cannot_be_its_own_parent(self, client, db):
        client.post(reverse("recipes:add"), _with_steps(**{"steps-1-parent_index": "1"}))
        recipe = Recipe.objects.get(title="Ofengemüse")
        assert recipe.steps.get(text="bake").parent_id is None

    def test_a_substitute_is_saved_without_a_step_of_its_own(self, client, db):
        client.post(reverse("recipes:add"), _with_steps(**{
            **_management("ingredients", 2),
            "ingredients-1-id": "", "ingredients-1-amount": "800",
            "ingredients-1-unit": "g", "ingredients-1-name": "Süßkartoffeln",
            "ingredients-1-note": "", "ingredients-1-alt_index": "0",
            "ingredients-1-step_index": "0",
        }))
        recipe = Recipe.objects.get(title="Ofengemüse")
        substitute = recipe.ingredients.get(name="Süßkartoffeln")
        assert substitute.alternative_for_id == recipe.ingredients.get(name="Kartoffeln").pk
        assert substitute.step_id is None

    def test_a_substitute_of_a_substitute_is_cut(self, client, db):
        """"Margarine instead of butter, and olive oil instead of the
        margarine" is a chain nothing renders and nobody means."""
        client.post(reverse("recipes:add"), _with_steps(**{
            **_management("ingredients", 3),
            "ingredients-1-id": "", "ingredients-1-name": "Süßkartoffeln",
            "ingredients-1-amount": "", "ingredients-1-unit": "",
            "ingredients-1-note": "", "ingredients-1-alt_index": "0",
            "ingredients-2-id": "", "ingredients-2-name": "Pastinaken",
            "ingredients-2-amount": "", "ingredients-2-unit": "",
            "ingredients-2-note": "", "ingredients-2-alt_index": "1",
        }))
        recipe = Recipe.objects.get(title="Ofengemüse")
        assert recipe.ingredients.get(name="Pastinaken").alternative_for_id is None

    def test_an_optional_line_is_kept_as_optional(self, client, db):
        client.post(reverse("recipes:add"), _post_data(**{"ingredients-0-optional": "on"}))
        assert Recipe.objects.get(title="Ofengemüse").ingredients.get().optional

    def test_a_duration_with_no_step_name_is_refused(self, client, db):
        """The mirror of the ingredient rule: a row somebody abandoned
        half-typed would be an empty box in the middle of the diagram."""
        response = client.post(reverse("recipes:add"), _post_data(**{
            **_management("steps", 1),
            "steps-0-id": "", "steps-0-text": "", "steps-0-minutes": "20",
            "steps-0-detail": "", "steps-0-parent_index": "",
        }))
        assert response.status_code == 200
        assert not Recipe.objects.filter(title="Ofengemüse").exists()

    def test_a_substitute_does_not_count_towards_the_ingredient_total(self, client, db):
        """The number on a card is what somebody has to buy."""
        client.post(reverse("recipes:add"), _with_steps(**{
            **_management("ingredients", 2),
            "ingredients-1-id": "", "ingredients-1-name": "Süßkartoffeln",
            "ingredients-1-amount": "", "ingredients-1-unit": "",
            "ingredients-1-note": "", "ingredients-1-alt_index": "0",
        }))
        listing = client.get(reverse("recipes:list"))
        card = next(r for r in listing.context["recipes"] if r.title == "Ofengemüse")
        assert card.ingredient_count == 1


# --------------------------------------------------------------------------
# Cooking, and what it fed
# --------------------------------------------------------------------------

class TestTheCookingView:
    def test_it_opens(self, client, recipe):
        assert client.get(recipe.get_cook_url()).status_code == 200

    def test_it_opens_for_a_recipe_with_no_diagram(self, client, recipe):
        """The timer and the portion record are useful either way, so it falls
        back to the ingredients and the prose rather than refusing."""
        body = client.get(recipe.get_cook_url()).content.decode()
        assert "Kartoffeln" in body

    def test_it_does_not_write(self, client, recipe):
        """Opening a page must not take SQLite's one write lock. The stopwatch
        lives in the browser for exactly this reason."""
        with CaptureQueriesContext(connection) as queries:
            client.get(recipe.get_cook_url())
        assert not _writes(queries), f"the cooking view writes on a GET: {_writes(queries)}"


class TestRecordingACooking:
    def _form(self, **overrides):
        data = {"servings_made": "4", "minutes": "55", "notes": "",
                "portion_regular": "2", "portion_togo": "1"}
        data.update(overrides)
        return data

    def test_it_records_the_portions(self, client, recipe, user):
        client.post(reverse("recipes:cooked", args=[recipe.slug]), self._form())
        log = recipe.cook_logs.get()
        assert log.cooked_by == user
        assert log.minutes == 55
        assert dict(log.portions.values_list("size", "count")) == {"regular": 2, "togo": 1}

    def test_an_entry_with_neither_a_time_nor_a_portion_is_refused(self, client, recipe):
        """A date, a name and nothing else is a row that makes the page longer
        and says nothing."""
        response = client.post(reverse("recipes:cooked", args=[recipe.slug]), self._form(
            minutes="", portion_regular="", portion_togo="",
        ))
        assert response.status_code == 200
        assert not recipe.cook_logs.exists()

    def test_the_measured_time_can_be_written_back_onto_the_recipe(self, client, recipe):
        client.post(reverse("recipes:cooked", args=[recipe.slug]),
                    self._form(apply_time="on"))
        recipe.refresh_from_db()
        assert recipe.cook_minutes == 55

    def test_somebody_who_may_not_edit_cannot_change_the_recipe_that_way(
        self, other_user, recipe, db
    ):
        """Anybody may record that they cooked it. Not everybody may change
        what it says — and 'apply the time' is an edit of the recipe."""
        from django.test import Client

        before = recipe.cook_minutes
        c = Client()
        c.force_login(other_user)
        c.post(reverse("recipes:cooked", args=[recipe.slug]), self._form(apply_time="on"))
        recipe.refresh_from_db()
        assert recipe.cook_minutes == before
        assert recipe.cook_logs.count() == 1        # the cooking itself is still recorded

    def test_the_person_who_recorded_it_may_remove_it(self, client, recipe, user):
        client.post(reverse("recipes:cooked", args=[recipe.slug]), self._form())
        log = recipe.cook_logs.get()
        client.post(reverse("recipes:cook-log-delete", args=[recipe.slug, log.pk]))
        assert not recipe.cook_logs.exists()

    def test_somebody_else_may_not(self, client, other_user, recipe, user):
        """Somebody's record of their own evening."""
        from django.test import Client

        client.post(reverse("recipes:cooked", args=[recipe.slug]), self._form())
        log = recipe.cook_logs.get()
        other = Client()
        other.force_login(other_user)
        assert other.post(
            reverse("recipes:cook-log-delete", args=[recipe.slug, log.pk])
        ).status_code == 404
        assert recipe.cook_logs.exists()


class TestWhatAPortionIsWorth:
    def test_the_sizes_add_up_to_ordinary_portions(self, recipe, db):
        log = CookLog.objects.create(recipe=recipe, servings_made=4)
        CookPortion.objects.create(log=log, size=PortionSize.LARGE, count=2)
        CookPortion.objects.create(log=log, size=PortionSize.SMALL, count=1)
        # 2 × 1.5 + 1 × 0.6
        assert log.portions_total == Decimal("3.6")

    def test_a_portion_taken_away_still_counts(self, recipe, db):
        """It was made, and somebody eats it. Counting it as nothing would say
        a recipe that fed two and filled a lunchbox served two."""
        log = CookLog.objects.create(recipe=recipe, servings_made=2)
        CookPortion.objects.create(log=log, size=PortionSize.TOGO, count=1)
        assert log.portions_total == Decimal("1")

    def test_the_page_shows_what_it_really_took(self, client, recipe, db):
        """The median of the recorded times, rounded to five minutes — not the
        mean, which one interrupted evening moves on its own."""
        for minutes in (50, 55, 130):
            CookLog.objects.create(recipe=recipe, servings_made=4, minutes=minutes)
        response = client.get(recipe.get_absolute_url())
        assert response.context["typical_minutes"] == 55

    def test_no_measurement_is_not_a_zero(self, client, recipe):
        assert client.get(recipe.get_absolute_url()).context["typical_minutes"] is None


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
