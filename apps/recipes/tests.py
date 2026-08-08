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

    def test_every_way_to_add_a_row_lives_on_the_card(self, client, recipe, db):
        """The three "add" controls are on the *card*, not on the canvas.

        The canvas grows hover "+" buttons between its cells, and for a while
        those were the only way to add a step *beside* another one — a sibling,
        same parent and same column, its own ingredients. That put the one
        gesture needed to build a branching recipe behind a mode the form does
        not open in: the Steps list is the default, and it had no such control
        at all. The household hit it twice, on the same recipe, and reported it
        as "I still can't add a step below Zerbröseln".

        So the rule is that the card carries every way to add a row, and the
        canvas's hover buttons are a shortcut on top of it. This test is
        deliberately about the *markup the server sends*, because that is what
        is mode-independent — anything asserted after JavaScript has run would
        pass in whichever mode the test happened to leave the page in, which is
        exactly how the gap survived being checked by hand.
        """
        # The shared fixture is an ingredients-only recipe, so the steps this
        # test is about have to be put there.
        RecipeStep.objects.create(recipe=recipe, position=0, text="Kartoffeln kochen")
        RecipeStep.objects.create(recipe=recipe, position=1, text="Abkühlen lassen")
        body = client.get(reverse("recipes:edit", args=[recipe.slug])).content.decode()
        steps = recipe.steps.count()
        assert steps == 2
        for control in ["data-step-add-line", "data-step-add-after", "data-step-add-beside"]:
            # One per real card, plus the blank <template> the "+ Step" button
            # clones — so strictly more than the number of steps.
            assert body.count(control) > steps, (
                f"{control} is missing from the step card: a recipe with {steps} "
                f"steps rendered it {body.count(control)} times"
            )

    @pytest.mark.parametrize("marker, prefix, extra", [
        ("data-ingredient-row", "ingredients", ["step_index", "alt_index", "position"]),
        ("data-step-row", "steps", ["parent_index", "position"]),
    ])
    def test_a_card_carries_its_whole_form(self, client, recipe, db, marker, prefix, extra):
        """The structural half of the rule, and the one worth pinning.

        The canvas *moves* a card into the cell it belongs in, and removal ticks
        that card's DELETE box and hides it. Both operate on the element, so
        every field of the form — the pk, the DELETE box and the three hidden
        fields that carry the structure — has to be *inside* it. A field
        rendered beside the cards (which is what ``{{ formset }}`` and most
        hand-written templates do) is one that both operations leave behind:
        the row that comes back has lost its identity, or its place.

        Split on the marker rather than matched with a regex over the
        surrounding markup, so this keeps testing the rule and not the layout
        that happens to be around it. The <template> holding the blank form
        carries the same marker and comes after the real cards, which is why
        only the first few are read.
        """
        body = client.get(reverse("recipes:edit", args=[recipe.slug])).content.decode()
        cards = body.split(marker)[1:]
        # The real rows, whatever there are of them. This used to assume one
        # step even on a recipe with none, because the formset always rendered a
        # spare blank row — it does not any more (`extra=0`), since a blank card
        # on the canvas is a cell in the tray that cannot be got rid of.
        count = (recipe.ingredients.count() if prefix == "ingredients"
                 else recipe.steps.count())
        assert len(cards) > count, "the blank form template is missing from the page"
        for index, card in enumerate(cards[:count]):
            for field in ["id", "DELETE"] + extra:
                assert f'name="{prefix}-{index}-{field}"' in card, (
                    f"{prefix} card {index} does not carry its own {field} field"
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

    def test_a_row_that_was_only_arranged_is_not_a_row_somebody_typed(self, client, db):
        """The trap the canvas walks into, and the reason the hidden fields
        say they never change.

        A formset validates and saves an extra row only when something in it
        changed. The canvas writes to every blank card it lays out — dragging a
        line past one renumbers its ``position``, and "+ Ingredient here"
        stamps a ``step_index`` onto a card before a single letter is in it. If
        those counted as edits, the blank card would be saved: a line on the
        recipe with no name, no amount and nothing to say where it came from.
        """
        client.post(reverse("recipes:add"), _post_data(**{
            **_management("ingredients", 2),
            "ingredients-1-id": "",
            "ingredients-1-amount": "", "ingredients-1-unit": "",
            "ingredients-1-name": "", "ingredients-1-note": "",
            # Everything the canvas would have written, and nothing a person
            # would have.
            "ingredients-1-step_index": "0",
            "ingredients-1-position": "5",
            "ingredients-0-position": "0",
            # The real line has to be wired into the step, or the completeness
            # check refuses the page before this one gets a chance to be
            # wrongly saved — which would pass the test for the wrong reason.
            "ingredients-0-step_index": "0",
            **_management("steps", 1),
            "steps-0-id": "", "steps-0-text": "bake", "steps-0-minutes": "",
            "steps-0-detail": "", "steps-0-parent_index": "",
        }))
        recipe = Recipe.objects.get(title="Ofengemüse")
        assert [line.name for line in recipe.ingredients.all()] == ["Kartoffeln"]

    def test_the_arrangement_is_saved_even_when_nothing_else_changed(self, client, recipe, db):
        """A drag that only moved things is still a change worth keeping.

        The mirror of the test above, and the reason the order is applied in
        ``wire_diagram`` rather than left to ``save()``: the same
        ``has_changed`` that stops a blank row being saved also stops Django
        saving a row whose *only* difference is where it sits. Without a pass of
        its own, dragging two lines into a new order would look saved and be
        back the way it was on the next load.
        """
        first, second = list(recipe.ingredients.all())[:2]
        client.post(reverse("recipes:edit", args=[recipe.slug]), _post_data(**{
            "title": recipe.title,
            **_management("ingredients", 2, initial=2),
            "ingredients-0-id": str(first.pk),
            "ingredients-0-amount": first.amount or "", "ingredients-0-unit": first.unit,
            "ingredients-0-name": first.name, "ingredients-0-note": first.note,
            "ingredients-0-position": "1",
            "ingredients-1-id": str(second.pk),
            "ingredients-1-amount": second.amount or "", "ingredients-1-unit": second.unit,
            "ingredients-1-name": second.name, "ingredients-1-note": second.note,
            "ingredients-1-position": "0",
            # "Salz und Pfeffer" has no amount and says so. Posting it without
            # the flag is now an invalid line, and the page would come back
            # unsaved — which this test would then read as "the order was not
            # kept" rather than as the validation error it is.
            "ingredients-1-no_amount": "on",
            **_management("steps", 0),
        }))
        assert [line.name for line in recipe.ingredients.all()][:2] == [second.name, first.name]

    def test_a_standing_instruction_keeps_the_place_it_was_left_in(self, client, db):
        """"Heat the oven" is *drawn* where somebody put it.

        A step with nothing going into it draws as a band, and which band comes
        where on the page is its ``position``. Nothing else pins the order of
        two roots.

        Where it is *cooked* is a separate question, and the answer changed:
        the walk-through now reads the diagram column by column and pulls a band
        one column left of the one it covers, so a band with no span — which
        covers everything — is asked for first however far down the page it is
        drawn. That is deliberate. You start the oven before you need it, and
        the household that asked for this ordering also asked for the band to
        be placeable over just part of the width so it can say *when*.
        ``TestHowFarAStandingInstructionReaches`` covers the narrowed case.
        """
        client.post(reverse("recipes:add"), _with_steps(**{
            **_management("steps", 3),
            "steps-2-id": "", "steps-2-text": "heat the oven",
            "steps-2-minutes": "", "steps-2-detail": "", "steps-2-parent_index": "",
            # After both of the others, which is the whole point.
            "steps-0-position": "0", "steps-1-position": "1", "steps-2-position": "2",
        }))
        recipe = Recipe.objects.get(title="Ofengemüse")
        oven = recipe.steps.get(text="heat the oven")
        assert oven.parent_id is None

        diagram = diagram_module.build(recipe)
        _assert_rectangular(diagram)
        # Its own block, last, and the full width of the table.
        assert len(diagram.blocks) == 2
        band = diagram.blocks[-1][0][0]
        assert band.step.pk == oven.pk
        assert band.colspan == diagram.columns
        # Drawn last — and cooked first, because a band with no span covers
        # every column and is pulled to the left of all of them. Both halves
        # asserted together, because it is the *difference* between them that
        # is easy to break by accident.
        assert [entry.step.text for entry in diagram.order][0] == "heat the oven"

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

    def test_an_index_naming_a_row_that_is_gone_is_refused(self, client, db):
        """An ingredient pointing at a step that is not there is a line in no step.

        This used to be tolerated and silently unassigned. It is refused now,
        because the same shape is produced by an ordinary edit — removing a
        step leaves every line that fed it pointing at nothing — and quietly
        dropping those lines out of the method is exactly the thing the
        household asked to be stopped from doing.

        ``wire_diagram`` still tolerates it (below), which is not redundant:
        that is the last line of defence for anything reaching the model by
        another road, and a dangling foreign key is a page that never renders.
        """
        response = client.post(reverse("recipes:add"), _with_steps(**{
            "ingredients-0-step_index": "7",
        }))
        assert response.status_code == 200
        assert not Recipe.objects.filter(title="Ofengemüse").exists()

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
            "ingredients-1-amount": "800", "ingredients-1-unit": "g",
            "ingredients-1-note": "", "ingredients-1-alt_index": "0",
            "ingredients-2-id": "", "ingredients-2-name": "Pastinaken",
            "ingredients-2-amount": "800", "ingredients-2-unit": "g",
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
            "ingredients-1-amount": "800", "ingredients-1-unit": "g",
            "ingredients-1-note": "", "ingredients-1-alt_index": "0",
        }))
        listing = client.get(reverse("recipes:list"))
        card = next(r for r in listing.context["recipes"] if r.title == "Ofengemüse")
        assert card.ingredient_count == 1


class TestRemovingAStepTakesItOutOfTheChain:
    """A step that has been removed is not "no parent" — it is a step taken out
    of the middle of something, and what fed it now feeds whatever it fed.

    The household found this the short way round: add a step beside
    "Zerbröseln", change your mind, delete it again — and the recipe came apart.
    "+ Step after this" rewires A → new → B, so removing the new one left A
    pointing at a row that no longer exists, A and its whole subtree broke off
    as a second block, and the ingredients under it went with them. The delete
    has to undo the insert and touch nothing else.

    Both halves are here because they fail differently. A step that was never
    saved is deleted by *clearing* the card, which used to blank the very field
    the answer is in; a saved one keeps its fields and only ticks DELETE.
    """

    def test_deleting_a_step_that_was_just_added_puts_the_chain_back(self, client, db):
        client.post(reverse("recipes:add"), _with_steps(**{
            **_management("steps", 3),
            # "+ Step after this" on "melt": the new row takes melt's parent,
            # and melt is pointed at the new row.
            "steps-0-parent_index": "2",
            "steps-2-id": "", "steps-2-text": "", "steps-2-minutes": "",
            "steps-2-detail": "", "steps-2-parent_index": "1",
            # ...and then removed again. static/js/recipe_form.js clears what
            # somebody typed and keeps where the row sat, which is what the
            # line above is.
            "steps-2-DELETE": "on",
        }))
        recipe = Recipe.objects.get(title="Ofengemüse")
        assert recipe.steps.count() == 2
        melt, bake = recipe.steps.get(text="melt"), recipe.steps.get(text="bake")
        assert melt.parent_id == bake.pk, "melt was left pointing at nothing"
        assert bake.parent_id is None

    def test_deleting_a_saved_step_rejoins_what_fed_it(self, client, db):
        """The same rule on a recipe that already exists.

        Without it, taking one box out of the middle of a chain detaches
        everything above it — and ``validate_structure`` then refuses the whole
        page for a branch that is not joined up, which is a save that fails and
        cannot be made to succeed without re-drawing the diagram by hand.
        """
        client.post(reverse("recipes:add"), _with_steps(**{
            **_management("steps", 3),
            "steps-0-parent_index": "2",
            "steps-2-id": "", "steps-2-text": "mix", "steps-2-minutes": "",
            "steps-2-detail": "", "steps-2-parent_index": "1",
        }))
        recipe = Recipe.objects.get(title="Ofengemüse")
        melt = recipe.steps.get(text="melt")
        mix = recipe.steps.get(text="mix")
        bake = recipe.steps.get(text="bake")
        assert melt.parent_id == mix.pk

        line = recipe.ingredients.get(name="Kartoffeln")
        response = client.post(reverse("recipes:edit", args=[recipe.slug]), _post_data(**{
            "title": recipe.title,
            **_management("ingredients", 1, initial=1),
            "ingredients-0-id": str(line.pk), "ingredients-0-amount": "800",
            "ingredients-0-unit": "g", "ingredients-0-name": "Kartoffeln",
            "ingredients-0-note": "", "ingredients-0-step_index": "0",
            **_management("steps", 3, initial=3),
            "steps-0-id": str(melt.pk), "steps-0-text": "melt", "steps-0-minutes": "",
            "steps-0-detail": "", "steps-0-parent_index": "2",
            "steps-1-id": str(bake.pk), "steps-1-text": "bake", "steps-1-minutes": "45",
            "steps-1-detail": "", "steps-1-parent_index": "",
            "steps-2-id": str(mix.pk), "steps-2-text": "mix", "steps-2-minutes": "",
            "steps-2-detail": "", "steps-2-parent_index": "1",
            "steps-2-DELETE": "on",
        }))
        assert response.status_code == 302, "the save was refused"
        melt.refresh_from_db()
        assert not recipe.steps.filter(pk=mix.pk).exists()
        assert melt.parent_id == bake.pk


class TestHowLongAStepTakes:
    """A duration is minutes *and* seconds, and nothing reads either alone.

    Two columns because almost every step is a round number of minutes and
    typing 2700 for three quarters of an hour is a mistake in the direction
    that burns something. One property to read them by, because a page that
    reads ``minutes`` says "1 min" for a step set to 1:30 and a countdown that
    reads it runs thirty seconds short — neither of which looks wrong.
    """

    def test_minutes_alone(self, recipe, db):
        step = RecipeStep.objects.create(recipe=recipe, text="backen", minutes=45)
        assert step.timer_seconds == 45 * 60
        assert step.duration_label == "45 min"
        assert step.timer_display == "45:00"

    def test_seconds_alone(self, recipe, db):
        step = RecipeStep.objects.create(recipe=recipe, text="mixen", seconds=45)
        assert step.timer_seconds == 45
        assert step.duration_label == "45 s"
        assert step.timer_display == "0:45"

    def test_both(self, recipe, db):
        step = RecipeStep.objects.create(recipe=recipe, text="rühren", minutes=1, seconds=30)
        assert step.timer_seconds == 90
        assert step.duration_label == "1:30 min"
        assert step.timer_display == "1:30"

    def test_neither_is_no_timer_rather_than_a_zero_one(self, recipe, db):
        step = RecipeStep.objects.create(recipe=recipe, text="abkühlen")
        assert step.timer_seconds is None
        assert step.duration_label == ""

    def test_the_form_saves_a_duration_finer_than_a_minute(self, client, db):
        client.post(reverse("recipes:add"), _with_steps(**{
            "steps-1-minutes": "1", "steps-1-seconds": "30",
        }))
        assert Recipe.objects.get(title="Ofengemüse").steps.get(text="bake").timer_seconds == 90

    def test_seconds_alone_are_enough_to_make_it_a_step(self, client, db):
        """The mirror of the "a duration needs a step name" rule: a row with
        only seconds on it is still a row somebody put a duration on."""
        response = client.post(reverse("recipes:add"), _post_data(**{
            **_management("steps", 1),
            "steps-0-id": "", "steps-0-text": "", "steps-0-minutes": "",
            "steps-0-seconds": "20", "steps-0-detail": "", "steps-0-parent_index": "",
        }))
        assert response.status_code == 200
        assert not Recipe.objects.filter(title="Ofengemüse").exists()

    def test_the_cooking_view_counts_the_whole_duration(self, client, recipe, db):
        """One attribute holding the total, so the browser never adds two up —
        the day one of them is missing the countdown is quietly short."""
        RecipeStep.objects.create(recipe=recipe, text="rühren", minutes=1, seconds=30)
        body = client.get(recipe.get_cook_url()).content.decode()
        assert 'data-cook-seconds="90"' in body
        assert "data-cook-minutes" not in body


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


# --------------------------------------------------------------------------
# What a recipe has to say before it may be saved
# --------------------------------------------------------------------------

class TestARecipeMustBeJoinedUp:
    """The rules the household asked for, and the two exceptions that stop them
    making perfectly ordinary recipes unsaveable.

    Each of these refuses a page that used to save, which is the point: the
    failures they catch — butter with no amount, a line in no step, a branch
    wired to nothing — all produced a recipe that *looked* complete and could
    not then be shopped for, scaled, or cooked from.
    """

    def test_a_step_with_no_text_says_so_itself(self, client, db):
        """The complaint has to land on the box the fault is in.

        A step with no text is invisible to ``_live``, so the ingredient
        sitting in it looked unassigned and got "put this into one of the
        steps" — about a line that was already in one. Somebody then goes
        looking for a wiring fault that does not exist, while the empty box
        that caused it says nothing at all.
        """
        response = client.post(reverse("recipes:add"), _post_data(**{
            **_management("steps", 2),
            "steps-0-id": "", "steps-0-text": "", "steps-0-minutes": "",
            "steps-0-detail": "", "steps-0-parent_index": "1",
            "steps-1-id": "", "steps-1-text": "bake", "steps-1-minutes": "",
            "steps-1-detail": "", "steps-1-parent_index": "",
            "ingredients-0-step_index": "0",
        }))
        assert response.status_code == 200
        assert "text" in response.context["steps"].forms[0].errors
        # ...and *only* there. The same fault reported twice in two places is
        # two faults to hunt for.
        assert not response.context["formset"].forms[0].errors

    def test_a_step_another_step_feeds_needs_a_text_too(self, client, db):
        """The same rule from the other side — and the shape "+ Step" makes:
        it mints the joining step already wired up and empty."""
        response = client.post(reverse("recipes:add"), _post_data(**{
            **_management("steps", 2),
            "steps-0-id": "", "steps-0-text": "melt", "steps-0-minutes": "",
            "steps-0-detail": "", "steps-0-parent_index": "1",
            "steps-1-id": "", "steps-1-text": "", "steps-1-minutes": "",
            "steps-1-detail": "", "steps-1-parent_index": "",
            "ingredients-0-step_index": "0",
        }))
        assert response.status_code == 200
        assert "text" in response.context["steps"].forms[1].errors

    def test_a_blank_step_nobody_uses_is_not_an_error(self, client, db):
        """The exception that keeps the rule usable. A card nobody has typed
        into is where the next step gets typed, and a page that refuses to
        save because one is sitting there is a page nobody can leave."""
        response = client.post(reverse("recipes:add"), _post_data(**{
            **_management("steps", 2),
            "steps-0-id": "", "steps-0-text": "bake", "steps-0-minutes": "",
            "steps-0-detail": "", "steps-0-parent_index": "",
            "steps-1-id": "", "steps-1-text": "", "steps-1-minutes": "",
            "steps-1-detail": "", "steps-1-parent_index": "",
            "ingredients-0-step_index": "0",
        }))
        assert response.status_code == 302

    def test_a_line_with_no_amount_is_refused(self, client, db):
        response = client.post(reverse("recipes:add"), _post_data(**{
            "ingredients-0-amount": "",
        }))
        assert response.status_code == 200
        assert not Recipe.objects.filter(title="Ofengemüse").exists()

    def test_unless_it_says_it_has_none(self, client, db):
        """"Salz", "etwas Öl" — the escape hatch, without which the rule above
        makes them unrecordable and the way round it people find is typing 1."""
        client.post(reverse("recipes:add"), _post_data(**{
            "ingredients-0-amount": "", "ingredients-0-no_amount": "on",
        }))
        line = Recipe.objects.get(title="Ofengemüse").ingredients.get()
        assert line.amount is None and line.no_amount

    def test_an_amount_and_no_fixed_amount_together_are_refused(self, client, db):
        """A line that contradicts itself — the scaler would have to pick one."""
        response = client.post(reverse("recipes:add"), _post_data(**{
            "ingredients-0-amount": "800", "ingredients-0-no_amount": "on",
        }))
        assert response.status_code == 200
        assert not Recipe.objects.filter(title="Ofengemüse").exists()

    def test_an_ingredient_in_no_step_is_refused(self, client, db):
        """The Milch case: a line left in the tray while the recipe has a
        method is a line that will not be cooked."""
        response = client.post(reverse("recipes:add"), _with_steps(**{
            **_management("ingredients", 2),
            "ingredients-1-id": "", "ingredients-1-name": "Milch",
            "ingredients-1-amount": "200", "ingredients-1-unit": "ml",
            "ingredients-1-note": "",
            # ...and deliberately no step_index.
        }))
        assert response.status_code == 200
        assert not Recipe.objects.filter(title="Ofengemüse").exists()

    def test_but_a_recipe_with_no_steps_at_all_is_still_a_recipe(self, client, db):
        """A title and a list of ingredients has always been a perfectly good
        entry. It is a recipe *with* a method where one line was left out of it
        that is the mistake."""
        client.post(reverse("recipes:add"), _post_data())
        assert Recipe.objects.get(title="Ofengemüse").ingredients.count() == 1

    def test_a_disconnected_branch_is_refused(self, client, db):
        """The other half of the Brot case: "verkneten" typed but never wired
        to the two doughs. Two roots that both produce something are two halves
        of a recipe that never meet."""
        response = client.post(reverse("recipes:add"), _post_data(**{
            **_management("ingredients", 2),
            "ingredients-0-step_index": "0",
            "ingredients-1-id": "", "ingredients-1-name": "Milch",
            "ingredients-1-amount": "200", "ingredients-1-unit": "ml",
            "ingredients-1-note": "", "ingredients-1-step_index": "1",
            **_management("steps", 2),
            "steps-0-id": "", "steps-0-text": "Vorteig", "steps-0-minutes": "",
            "steps-0-detail": "", "steps-0-parent_index": "",
            "steps-1-id": "", "steps-1-text": "verkneten", "steps-1-minutes": "",
            "steps-1-detail": "", "steps-1-parent_index": "",
        }))
        assert response.status_code == 200
        assert not Recipe.objects.filter(title="Ofengemüse").exists()

    def test_two_arms_meeting_at_a_third_step_is_fine(self, client, db):
        """The Brot case done right — and the shape the whole diagram exists
        for. Both arms feed step 2, which is then the only root that produces
        anything."""
        client.post(reverse("recipes:add"), _post_data(**{
            **_management("ingredients", 3),
            "ingredients-0-step_index": "0",
            "ingredients-1-id": "", "ingredients-1-name": "Hefe",
            "ingredients-1-amount": "1", "ingredients-1-unit": "cube",
            "ingredients-1-note": "", "ingredients-1-step_index": "1",
            "ingredients-2-id": "", "ingredients-2-name": "Milch",
            "ingredients-2-amount": "200", "ingredients-2-unit": "ml",
            "ingredients-2-note": "", "ingredients-2-step_index": "2",
            **_management("steps", 3),
            "steps-0-id": "", "steps-0-text": "Vorteig ansetzen", "steps-0-minutes": "",
            "steps-0-detail": "", "steps-0-parent_index": "2",
            "steps-1-id": "", "steps-1-text": "Hefe ansetzen", "steps-1-minutes": "",
            "steps-1-detail": "", "steps-1-parent_index": "2",
            "steps-2-id": "", "steps-2-text": "verkneten", "steps-2-minutes": "",
            "steps-2-detail": "", "steps-2-parent_index": "",
        }))
        recipe = Recipe.objects.get(title="Ofengemüse")
        knead = recipe.steps.get(text="verkneten")
        assert {s.text for s in recipe.steps.filter(parent=knead)} == {
            "Vorteig ansetzen", "Hefe ansetzen",
        }
        # And it lays out: "verkneten" sits a column to the right of both arms.
        diagram = diagram_module.build(recipe)
        _assert_rectangular(diagram)
        assert diagram.columns == 3

    def test_a_standing_instruction_is_still_allowed_beside_the_dish(self, client, db):
        """"Heat the oven" has nothing feeding it and feeds nothing, and that
        is what it *is*. The rule above must not catch it."""
        client.post(reverse("recipes:add"), _with_steps(**{
            **_management("steps", 3),
            "steps-2-id": "", "steps-2-text": "Ofen vorheizen",
            "steps-2-minutes": "", "steps-2-detail": "", "steps-2-parent_index": "",
        }))
        recipe = Recipe.objects.get(title="Ofengemüse")
        assert recipe.steps.get(text="Ofen vorheizen").parent_id is None


class TestHowFarAStandingInstructionReaches:
    """"Heat the oven" across the whole width is right when everything waits
    for it, and wrong when it happens *during* something — a band over every
    column claims it runs alongside the steps that came before it too."""

    def _recipe_with_a_band(self, client, span=None):
        client.post(reverse("recipes:add"), _with_steps(**{
            **_management("steps", 3),
            "steps-2-id": "", "steps-2-text": "Ofen vorheizen",
            "steps-2-minutes": "", "steps-2-detail": "", "steps-2-parent_index": "",
            **({"steps-2-span_from": str(span[0]), "steps-2-span_to": str(span[1])}
               if span else {}),
        }))
        return Recipe.objects.get(title="Ofengemüse")

    def test_by_default_it_still_spans_the_whole_width(self, client, db):
        """The reference diagram's band, and the behaviour every recipe written
        before the span existed must keep."""
        recipe = self._recipe_with_a_band(client)
        oven = recipe.steps.get(text="Ofen vorheizen")
        assert (oven.span_from, oven.span_to) == (None, None)
        diagram = diagram_module.build(recipe)
        _assert_rectangular(diagram)
        band = diagram.blocks[-1][0]
        assert len(band) == 1
        assert band[0].colspan == diagram.columns

    def test_a_span_puts_filler_either_side(self, client, db):
        """Filler, not omitted cells: a row that simply leaves the columns out
        has silently moved everything after it one column left."""
        recipe = self._recipe_with_a_band(client, span=(2, 2))
        oven = recipe.steps.get(text="Ofen vorheizen")
        assert (oven.span_from, oven.span_to) == (2, 2)
        diagram = diagram_module.build(recipe)
        _assert_rectangular(diagram)
        band = diagram.blocks[-1][0]
        assert [(c.kind, c.colspan) for c in band] == [
            ("filler", 1), ("step", 1), ("filler", diagram.columns - 2),
        ]

    def test_a_span_wider_than_the_recipe_is_clamped(self, recipe, db):
        """The numbers are a layout hint against a geometry that is *derived*.
        Add a step and the table grows a column; delete one and it shrinks. A
        colspan of zero collapses the row and one past the end drags the table
        wider than its own header."""
        step = RecipeStep.objects.create(
            recipe=recipe, position=0, text="Ofen vorheizen",
            span_from=9, span_to=99,
        )
        diagram = diagram_module.build(recipe)
        _assert_rectangular(diagram)
        assert step.span_from > diagram.columns          # the stored value is untouched
        band = diagram.blocks[-1][0]
        assert sum(c.colspan for c in band) == diagram.columns

    def test_the_ends_may_be_given_the_wrong_way_round(self, recipe, db):
        RecipeStep.objects.create(
            recipe=recipe, position=0, text="Ofen vorheizen", span_from=3, span_to=1,
        )
        diagram = diagram_module.build(recipe)
        _assert_rectangular(diagram)

    def test_a_span_survives_a_save_that_changed_nothing_else(self, client, db):
        """``_SpanField.has_changed`` is always False, so ``formset.save()``
        never reaches a row whose only difference is its span — which is why
        ``wire_diagram`` applies it in a pass of its own."""
        recipe = self._recipe_with_a_band(client, span=(2, 2))
        oven = recipe.steps.get(text="Ofen vorheizen")
        melt, bake = recipe.steps.get(text="melt"), recipe.steps.get(text="bake")
        line = recipe.ingredients.get(name="Kartoffeln")

        client.post(reverse("recipes:edit", args=[recipe.slug]), _post_data(**{
            "title": recipe.title,
            **_management("ingredients", 1, initial=1),
            "ingredients-0-id": str(line.pk), "ingredients-0-amount": "800",
            "ingredients-0-unit": "g", "ingredients-0-name": "Kartoffeln",
            "ingredients-0-note": "", "ingredients-0-step_index": "0",
            **_management("steps", 3, initial=3),
            "steps-0-id": str(melt.pk), "steps-0-text": "melt", "steps-0-minutes": "",
            "steps-0-detail": "", "steps-0-parent_index": "1",
            "steps-1-id": str(bake.pk), "steps-1-text": "bake", "steps-1-minutes": "45",
            "steps-1-detail": "", "steps-1-parent_index": "",
            "steps-2-id": str(oven.pk), "steps-2-text": "Ofen vorheizen",
            "steps-2-minutes": "", "steps-2-detail": "", "steps-2-parent_index": "",
            "steps-2-span_from": "2", "steps-2-span_to": "2",
        }))
        oven.refresh_from_db()
        assert (oven.span_from, oven.span_to) == (2, 2)

    def test_it_is_cleared_when_the_step_stops_standing_alone(self, client, db):
        """A stale span is what would draw a band across half the table the day
        somebody took the last ingredient back out of a step."""
        recipe = self._recipe_with_a_band(client, span=(2, 2))
        oven = recipe.steps.get(text="Ofen vorheizen")
        melt, bake = recipe.steps.get(text="melt"), recipe.steps.get(text="bake")
        line = recipe.ingredients.get(name="Kartoffeln")

        client.post(reverse("recipes:edit", args=[recipe.slug]), _post_data(**{
            "title": recipe.title,
            **_management("ingredients", 1, initial=1),
            "ingredients-0-id": str(line.pk), "ingredients-0-amount": "800",
            "ingredients-0-unit": "g", "ingredients-0-name": "Kartoffeln",
            "ingredients-0-note": "", "ingredients-0-step_index": "0",
            **_management("steps", 3, initial=3),
            "steps-0-id": str(melt.pk), "steps-0-text": "melt", "steps-0-minutes": "",
            "steps-0-detail": "", "steps-0-parent_index": "1",
            "steps-1-id": str(bake.pk), "steps-1-text": "bake", "steps-1-minutes": "45",
            "steps-1-detail": "", "steps-1-parent_index": "",
            "steps-2-id": str(oven.pk), "steps-2-text": "Ofen vorheizen",
            "steps-2-minutes": "", "steps-2-detail": "", "steps-2-parent_index": "1",
            # ...and the canvas sends no span for a step that is no longer a band.
        }))
        oven.refresh_from_db()
        assert (oven.span_from, oven.span_to) == (None, None)


class TestTheCookingViewReadsTheDiagram:
    """The walk-through goes column by column from the left, top to bottom
    within a column — because that is how the table beside it is laid out, and
    a walk-through whose order disagrees with the picture is one nobody trusts
    twice."""

    def _chain(self, recipe, *names):
        """Steps in a straight chain, first feeding second and so on."""
        made = [RecipeStep.objects.create(recipe=recipe, position=n, text=name)
                for n, name in enumerate(names)]
        for earlier, later in zip(made, made[1:]):
            earlier.parent = later
            earlier.save(update_fields=["parent"])
        return made

    def test_two_arms_are_interleaved_by_column(self, recipe, db):
        """The old post-order walk finished one arm entirely before starting
        the other. In a kitchen you do the first thing in each column and move
        on."""
        left = self._chain(recipe, "chop", "fry")
        right = RecipeStep.objects.create(recipe=recipe, position=9, text="whisk")
        right.parent = left[1]
        right.save(update_fields=["parent"])
        RecipeIngredient.objects.create(recipe=recipe, position=8, amount=Decimal(1),
                                        unit="pc", name="Ei", step=right)

        order = [entry.step.text for entry in diagram_module.build(recipe).order]
        # "chop" and "whisk" are both column 1; "fry" is column 2 and comes
        # after both of them rather than straight after "chop".
        assert order.index("fry") > order.index("whisk")

    def test_a_step_with_no_ingredients_is_not_mistaken_for_a_band(self, recipe, db):
        """A step in the middle of a chain can have nothing going into it —
        "bring a pan of water to the boil" — and is *not* a standing
        instruction. Ranking one as a band sends it to the front of the recipe,
        which is what happened to the household's "Vormischen"."""
        first, second, third = self._chain(recipe, "dissolve", "premix", "stir")
        RecipeIngredient.objects.create(recipe=recipe, position=8, amount=Decimal(1),
                                        unit="g", name="Hefe", step=first)
        # `premix` has no ingredients and no children of its own.
        order = [entry.step.text for entry in diagram_module.build(recipe).order]
        assert order.index("dissolve") < order.index("premix") < order.index("stir")

    def test_a_narrowed_band_is_pulled_one_column_left_of_what_it_covers(self, recipe, db):
        """You start the oven before you need it, so a band over the baking
        column belongs before the step that shapes the loaf."""
        shape, bake = self._chain(recipe, "shape", "bake")
        RecipeIngredient.objects.create(recipe=recipe, position=8, amount=Decimal(1),
                                        unit="g", name="Mehl", step=shape)
        columns = diagram_module.build(recipe).columns
        oven = RecipeStep.objects.create(
            recipe=recipe, position=9, text="heat the oven",
            span_from=columns, span_to=columns,
        )
        order = [entry.step.text for entry in diagram_module.build(recipe).order]
        assert order.index("heat the oven") < order.index("bake")
        assert order.index("shape") < order.index("heat the oven")
        assert oven.parent_id is None

    def test_the_numbers_run_from_one_with_no_gaps(self, recipe, db):
        self._chain(recipe, "a", "b", "c")
        order = diagram_module.build(recipe).order
        assert [entry.number for entry in order] == list(range(1, len(order) + 1))


class TestAStepCanHoldSeveralThingsToDo:
    """The first real recipe typed into this app put two actions in one box —
    "- Topf in Ofen stellen / - Ofen vorheizen" — because that is what they
    are: two things done at one point in the flow, not two boxes."""

    def test_the_lines_are_split_and_their_dashes_taken_off(self, recipe, db):
        step = RecipeStep.objects.create(
            recipe=recipe, position=0,
            text="- Topf in Ofen stellen\r\n- Ofen vorheizen",
        )
        assert step.parts == ["Topf in Ofen stellen", "Ofen vorheizen"]
        assert step.is_multipart

    def test_blank_lines_do_not_become_empty_bullets(self, recipe, db):
        step = RecipeStep.objects.create(recipe=recipe, position=0,
                                         text="mix\n\n  \n- rest\n-\n")
        assert step.parts == ["mix", "rest"]

    def test_a_plain_step_is_one_part(self, recipe, db):
        step = RecipeStep.objects.create(recipe=recipe, position=0, text="verrühren")
        assert step.parts == ["verrühren"]
        assert not step.is_multipart
        assert step.headline == "verrühren"

    def test_the_headline_keeps_every_part(self, recipe, db):
        """Truncating to the first line would make "Topf in Ofen stellen" a
        different instruction from the pair it belongs to."""
        step = RecipeStep.objects.create(recipe=recipe, position=0, text="- a\n- b")
        assert step.headline == "a · b"


class TestTheCatalogueLearnsFromASavedRecipe:
    def test_a_typed_name_becomes_an_ingredient(self, client, db):
        """Which is how the catalogue fills with what this household actually
        cooks, rather than only what it was shipped knowing."""
        from apps.pantry.models import Ingredient

        client.post(reverse("recipes:add"), _post_data(**{
            "ingredients-0-name": "Pastinakenpüree",
        }))
        line = Recipe.objects.get(title="Ofengemüse").ingredients.get()
        assert line.ingredient == Ingredient.objects.get(name="Pastinakenpüree")

    def test_a_name_the_catalogue_knows_is_matched_not_duplicated(self, client, db):
        from apps.pantry.models import Ingredient

        client.post(reverse("recipes:add"), _post_data(**{"ingredients-0-name": "Butter"}))
        assert Ingredient.objects.filter(name__iexact="Butter").count() == 1


class TestTheUnitIsShownInThePagesLanguage:
    def test_the_stored_code_is_never_what_is_rendered(self, client, recipe, db):
        """The column holds a language-neutral code. A template rendering it
        directly writes "tbsp" onto a German page — not obviously wrong, only
        wrong, and it stays that way until a German reader notices."""
        RecipeIngredient.objects.create(
            recipe=recipe, position=2, amount=Decimal(2), unit="tbsp", name="Essig",
        )
        line = recipe.ingredients.get(name="Essig")
        assert line.unit == "tbsp"
        assert str(line.unit_label) == "tbsp"          # English, per conftest
        assert "Essig" in client.get(recipe.get_absolute_url()).content.decode()

    @pytest.mark.parametrize("page", ["detail", "cook"])
    def test_every_page_that_shows_a_unit_translates_it(self, client, recipe, db, page):
        """Rendered in German and checked for the raw code.

        Three templates were rendering `{{ item.unit }}` directly — the
        diagram, the cooking view twice over — so a German page read
        "20 cube Test" where it should have said "20 Würfel". Nothing caught
        it: the page renders, the value is right, and only the word is wrong.
        This is the check that would have.
        """
        from django.utils import translation

        step = RecipeStep.objects.create(recipe=recipe, position=0, text="verrühren")
        RecipeIngredient.objects.create(
            recipe=recipe, position=2, amount=Decimal(1), unit="cube",
            name="Hefe", step=step,
        )
        url = recipe.get_absolute_url() if page == "detail" else recipe.get_cook_url()
        with translation.override("de"):
            body = client.get(url, headers={"accept-language": "de"}).content.decode()
        assert "Würfel" in body, "the unit was not rendered in German"
        # The code itself must not reach the page. Guarded against a false pass
        # from some unrelated occurrence by checking it next to its amount.
        assert "cube" not in body, "the raw unit code reached the page"


# --------------------------------------------------------------------------
# The cooking history
# --------------------------------------------------------------------------

class TestEditingACookingAfterTheFact:
    """How far a dish went is known the *next* day. Before this page the only
    way to correct it was to delete the entry, which took the date and the
    measured time with it."""

    def _log(self, client, recipe):
        client.post(reverse("recipes:cooked", args=[recipe.slug]), {
            "servings_made": "4", "minutes": "55", "notes": "",
            "portion_regular": "2", "portion_togo": "1",
        })
        return recipe.cook_logs.get()

    def test_the_form_comes_back_carrying_the_portions(self, client, recipe):
        """Rendering it empty would mean that saving it to fix the *time*
        silently took the portions away."""
        log = self._log(client, recipe)
        response = client.get(reverse("recipes:cook-log-edit", args=[recipe.slug, log.pk]))
        assert response.context["form"].initial["portion_regular"] == 2

    def test_a_corrected_count_replaces_the_old_one(self, client, recipe):
        log = self._log(client, recipe)
        client.post(reverse("recipes:cook-log-edit", args=[recipe.slug, log.pk]), {
            "servings_made": "4", "minutes": "55", "notes": "",
            "portion_regular": "3", "portion_togo": "1",
        })
        assert dict(log.portions.values_list("size", "count")) == {"regular": 3, "togo": 1}

    def test_a_count_set_to_nothing_removes_the_row(self, client, recipe):
        """"No portions to take away" and "a row saying nought" are the same
        claim, and only one of them belongs on the page."""
        log = self._log(client, recipe)
        client.post(reverse("recipes:cook-log-edit", args=[recipe.slug, log.pk]), {
            "servings_made": "4", "minutes": "55", "notes": "",
            "portion_regular": "2", "portion_togo": "",
        })
        assert dict(log.portions.values_list("size", "count")) == {"regular": 2}

    def test_the_date_survives_the_edit(self, client, recipe):
        log = self._log(client, recipe)
        before = log.cooked_at
        client.post(reverse("recipes:cook-log-edit", args=[recipe.slug, log.pk]), {
            "servings_made": "6", "minutes": "60", "notes": "mehr Salz",
            "portion_regular": "2",
        })
        log.refresh_from_db()
        assert log.cooked_at == before
        assert (log.servings_made, log.minutes, log.notes) == (6, 60, "mehr Salz")

    def test_somebody_else_may_not_edit_it(self, client, other_user, recipe):
        """A cooking is somebody's own record of their own evening — a
        different question from who may edit the recipe."""
        from django.test import Client

        log = self._log(client, recipe)
        other = Client()
        other.force_login(other_user)
        assert other.get(
            reverse("recipes:cook-log-edit", args=[recipe.slug, log.pk])
        ).status_code == 404


class TestTheHistoryPage:
    def test_it_lists_cookings_across_every_recipe(self, client, recipe, db):
        CookLog.objects.create(recipe=recipe, servings_made=4, minutes=50)
        response = client.get(reverse("recipes:history"))
        assert response.status_code == 200
        assert [log.recipe_id for log in response.context["logs"]] == [recipe.pk]

    def test_it_does_not_write(self, client, recipe, db):
        CookLog.objects.create(recipe=recipe, servings_made=4, minutes=50)
        with CaptureQueriesContext(connection) as queries:
            client.get(reverse("recipes:history"))
        assert not _writes(queries), f"the history page writes on a GET: {_writes(queries)}"
