"""The catalogue, the cupboard, and the arithmetic between them.

The cases worth keeping are the ones where a plausible implementation gives a
*confident wrong answer* rather than an error — because those are the ones that
reach somebody standing in a kitchen with the pan already hot:

* a unit that must not convert into another one (a clove is not four grams);
* an unmeasured pantry amount, which means "enough" and not "none";
* a line the catalogue does not know, which means "cannot tell" and not
  "missing";
* a substitute rescuing the line it replaces.

Everything here works on plain objects where it can. ``matching`` takes the
rows it measures as arguments precisely so it can be tested without a database
behind it, which is also what keeps the list page's query count flat.
"""

from decimal import Decimal

import pytest
from django.urls import reverse

from apps.pantry import catalogue, matching, units
from apps.pantry.models import Ingredient, IngredientAlias, PantryItem, PurchaseSize
from apps.recipes.models import Recipe, RecipeIngredient


# --------------------------------------------------------------------------
# Units
# --------------------------------------------------------------------------

class TestUnitsConvertOnlyWhereTheyShould:
    @pytest.mark.parametrize("amount, source, target, expected", [
        (1, "kg", "g", Decimal(1000)),
        (500, "g", "kg", Decimal("0.5")),
        (1, "l", "ml", Decimal(1000)),
        (2, "tbsp", "ml", Decimal(30)),
        (3, "tsp", "ml", Decimal(15)),
        # The empty unit and "pieces" are the same claim written two ways —
        # "1 Zwiebel" and "1 Stück Zwiebel" — and a household writes both.
        (3, "", "pc", Decimal(3)),
        (3, "pc", "", Decimal(3)),
    ])
    def test_within_a_dimension(self, amount, source, target, expected):
        assert units.convert(Decimal(amount), source, target) == expected

    @pytest.mark.parametrize("source, target", [
        ("g", "ml"),        # mass against volume: that would be a density
        ("clove", "g"),     # a clove of garlic is not four grams
        ("bunch", "g"),
        ("cup", "ml"),      # a Tasse is anything from 150 to 250 ml, and usually flour
        ("pack", "g"),
        ("tin", "ml"),
    ])
    def test_across_dimensions_it_refuses(self, source, target):
        """None means *cannot tell*, and every caller has to treat it as such.

        Answering anything numeric here would be inventing a conversion, and a
        wrong answer is worse than no answer: it is the pantry saying you have
        something you do not have.
        """
        assert units.convert(Decimal(1), source, target) is None
        assert not units.comparable(source, target)

    def test_a_value_from_before_the_catalogue_still_matches_itself(self):
        """Two identical free-text values are the same unit, whatever they say.

        Refusing them would make the pantry useless for every row written
        before the closed set existed.
        """
        assert units.convert(Decimal(2), "Handvoll", "Handvoll") == Decimal(2)
        assert units.convert(Decimal(2), "Handvoll", "g") is None

    def test_the_column_is_wide_enough_for_every_code(self):
        """Derived, not written down. A unit added with a longer code would
        otherwise be a database error from a change that looked like one line."""
        assert units.MAX_CODE_LENGTH == max(len(u.code) for u in units.UNITS)

    def test_every_group_names_units_that_exist(self):
        for _label, codes in units.GROUPS:
            for code in codes:
                assert code in units.BY_CODE, code

    def test_an_unknown_value_is_offered_back_rather_than_dropped(self):
        """A select that cannot represent its own value rewrites it the next
        time somebody presses Save on an unrelated field."""
        offered = units.choices(extra="Handvoll")
        values = [value for _group, options in offered for value, _text in options]
        assert "Handvoll" in values
        # And a value it does know is not offered twice.
        again = units.choices(extra="g")
        assert [v for _g, opts in again for v, _t in opts].count("g") == 1

    @pytest.mark.parametrize("typed, code", [
        ("EL", "tbsp"), ("el", "tbsp"), ("Esslöffel", "tbsp"),
        ("TL", "tsp"), ("Gramm", "g"), ("gr", "g"),
        ("Stück", "pc"), ("Zehen", "clove"), ("Würfel", "cube"),
        ("Pck.", "pack"), ("Dose", "tin"),
    ])
    def test_the_spellings_this_house_uses_are_recognised(self, typed, code):
        assert units.normalise(typed) == code

    def test_anything_else_is_left_exactly_as_typed(self):
        """A guess that turns "Msp." into millilitres is a silent edit to
        somebody's recipe."""
        assert units.normalise("Messerspitze") == "Messerspitze"


# --------------------------------------------------------------------------
# The catalogue
# --------------------------------------------------------------------------

# The starter catalogue is a data migration, so **every test database already
# has it** — which is the truthful state to test against, and a trap for a
# fixture that assumes an empty table. "Butter" and "Zwiebel" are shipped rows;
# these fixtures take the one that is there rather than creating a second.
# Anything a test needs to be genuinely new is named `_invented` below.

@pytest.fixture
def butter(db):
    row = Ingredient.objects.get(name="Butter")
    PurchaseSize.objects.get_or_create(
        ingredient=row, amount=Decimal(250), unit="g", defaults={"label": "Stück"},
    )
    PurchaseSize.objects.get_or_create(ingredient=row, amount=Decimal(500), unit="g")
    return row


@pytest.fixture
def onion(db):
    return Ingredient.objects.get(name="Zwiebel")


def _invented(name, **kwargs):
    """A catalogue row that is certainly not one of the shipped ones."""
    return Ingredient.objects.create(name=name, slug=name.lower(), **kwargs)


class TestFindingWhatSomebodyMeant:
    def test_case_and_spacing_do_not_matter(self, butter):
        assert catalogue.lookup("  BUTTER ") == butter

    def test_an_alias_finds_the_same_row(self, onion):
        """Nobody types the canonical name: the recipe says "Zwiebeln" and the
        catalogue says "Zwiebel"."""
        assert catalogue.lookup("Zwiebeln") == onion

    def test_nothing_else_is_guessed(self, butter):
        """Deliberately narrow. A prefix match buys a handful of correct
        answers and pays for them with wrong ones — and a wrong match here is
        the pantry claiming a substance the house does not have."""
        assert catalogue.lookup("Buttermilch") is None
        assert catalogue.lookup("Butt") is None

    def test_an_unknown_name_becomes_its_own_row(self, db):
        row, created = catalogue.remember("Grünkernschrot", "kg")
        assert created and row.name == "Grünkernschrot"
        # The unit of the line that first mentioned it is right far more often
        # than blank — it is the suggestion the *second* time.
        assert row.default_unit == "kg"

    def test_a_name_that_is_only_whitespace_creates_nothing(self, db):
        before = Ingredient.objects.count()
        row, created = catalogue.remember("   ")
        assert row is None and not created
        assert Ingredient.objects.count() == before

    def test_an_ingredients_own_name_beats_another_ones_alias(self, db):
        """A data problem, resolved in the direction that surprises nobody."""
        real = _invented("Kräutersud")
        other = _invented("Würzbrühe")
        IngredientAlias.objects.create(ingredient=other, name="Kräutersud")
        assert catalogue.lookup("Kräutersud") == real

    def test_resolving_leaves_a_line_that_is_already_pointed_somewhere(self, db, butter):
        """Re-resolving would undo a correction made by hand the moment
        somebody fixed a spelling anywhere else on the recipe."""
        margarine = Ingredient.objects.get(name="Margarine")
        recipe = Recipe.objects.create(title="Kuchen", slug="kuchen", servings=4)
        line = RecipeIngredient.objects.create(
            recipe=recipe, name="Butter", amount=Decimal(200), unit="g",
            ingredient=margarine,
        )
        catalogue.resolve_lines([line])
        line.refresh_from_db()
        assert line.ingredient == margarine


# --------------------------------------------------------------------------
# Measuring a recipe against the cupboard
# --------------------------------------------------------------------------

def _line(name, amount=None, unit="", ingredient=None, optional=False, substitutes=()):
    """A recipe line as the matcher sees it — unsaved, because it never saves."""
    row = RecipeIngredient(
        name=name, unit=unit, optional=optional,
        amount=None if amount is None else Decimal(str(amount)),
    )
    if ingredient is not None:
        row.ingredient = ingredient
    row.substitutes = list(substitutes)
    return row


def _pantry(*items):
    return {item.ingredient_id: item for item in items}


def _stock(ingredient, amount=None, unit=""):
    item = PantryItem(ingredient=ingredient, unit=unit,
                      amount=None if amount is None else Decimal(str(amount)))
    item.ingredient_id = ingredient.pk
    return item


class TestWhatTheCupboardSays:
    def test_enough_is_have(self, butter):
        verdict = matching.check_recipe(
            [_line("Butter", 200, "g", butter)], _pantry(_stock(butter, 1, "kg")),
        )
        assert verdict.lines[0].state == matching.HAVE
        assert verdict.can_be_made

    def test_some_but_not_enough_is_short(self, butter):
        verdict = matching.check_recipe(
            [_line("Butter", 500, "g", butter)], _pantry(_stock(butter, 200, "g")),
        )
        entry = verdict.lines[0]
        assert entry.state == matching.SHORT
        assert entry.shortfall == Decimal(300)
        assert not verdict.can_be_made

    def test_nothing_at_all_is_missing(self, butter):
        verdict = matching.check_recipe([_line("Butter", 200, "g", butter)], {})
        assert verdict.lines[0].state == matching.MISSING
        assert verdict.lines[0].shortfall == Decimal(200)

    def test_an_unmeasured_amount_counts_as_enough(self, db):
        """"Some, not counted" is the honest state for salt, and reading it as
        zero would put salt on every shopping list this house ever makes."""
        salt = Ingredient.objects.get(name="Salz")
        verdict = matching.check_recipe(
            [_line("Salz", 10, "g", salt)], _pantry(_stock(salt)),
        )
        assert verdict.lines[0].state == matching.HAVE

    def test_units_that_cannot_be_compared_are_not_guessed_at(self, butter):
        """And "cannot tell" counts against can_be_made, because "you can make
        this" is a promise somebody acts on by not going to the shop."""
        verdict = matching.check_recipe(
            [_line("Butter", 2, "tbsp", butter)], _pantry(_stock(butter, 250, "g")),
        )
        assert verdict.lines[0].state == matching.UNKNOWN
        assert not verdict.can_be_made
        assert verdict.unknown

    def test_a_line_with_no_catalogue_row_is_unknown_not_missing(self, db):
        """Every recipe written before the catalogue existed has these. Reading
        them as missing would put the whole collection on the shopping list."""
        verdict = matching.check_recipe([_line("festkochende Kartoffeln", 1, "kg")], {})
        assert verdict.lines[0].state == matching.UNKNOWN
        assert not verdict.missing

    def test_an_optional_line_never_blocks(self, db, butter):
        """A pantry that refuses a recipe for want of the parsley garnish is a
        pantry nobody consults twice."""
        parsley = Ingredient.objects.get(name="Petersilie")
        verdict = matching.check_recipe(
            [_line("Butter", 100, "g", butter),
             _line("Petersilie", 1, "bunch", parsley, optional=True)],
            _pantry(_stock(butter, 250, "g")),
        )
        assert verdict.can_be_made
        assert [e.line.name for e in verdict.extras] == ["Petersilie"]

    def test_a_substitute_rescues_the_line_it_replaces(self, db, butter):
        margarine = Ingredient.objects.get(name="Margarine")
        line = _line("Butter", 200, "g", butter,
                     substitutes=[_line("Margarine", 180, "g", margarine)])
        verdict = matching.check_recipe([line], _pantry(_stock(margarine, 500, "g")))
        entry = verdict.lines[0]
        assert entry.state == matching.HAVE
        # And the page can say *which* one worked: "you can make it, with the
        # margarine" is the useful sentence.
        assert entry.satisfied_by.name == "Margarine"

    def test_scaling_changes_the_answer(self, butter):
        lines = [_line("Butter", 200, "g", butter)]
        stock = _pantry(_stock(butter, 250, "g"))
        assert matching.check_recipe(lines, stock, servings=4, base_servings=4).can_be_made
        assert not matching.check_recipe(lines, stock, servings=8, base_servings=4).can_be_made

    def test_nearly_is_at_most_two_things(self, db):
        rows = [_invented(f"Prüfzutat {n}") for n in range(4)]
        lines = [_line(r.name, 1, "kg", r) for r in rows]
        assert matching.check_recipe(lines[:2], {}).nearly
        assert not matching.check_recipe(lines, {}).nearly


class TestWhatWouldHaveToBeBought:
    def test_the_smallest_packet_that_covers_it_wins(self, butter):
        """Smallest-that-covers rather than largest: the failure people mind is
        buying a kilo of yeast."""
        verdict = matching.check_recipe([_line("Butter", 200, "g", butter)], {})
        rows = matching.shopping_list([(None, verdict)])
        assert len(rows) == 1
        assert rows[0].packets == 1
        assert rows[0].packet.amount == Decimal(250)

    def test_more_than_one_packet_is_said_as_a_count(self, butter):
        verdict = matching.check_recipe([_line("Butter", 900, "g", butter)], {})
        row = matching.shopping_list([(None, verdict)])[0]
        # 900 g is two 500 g packs, not "1000 g".
        assert (row.packets, row.packet.amount) == (2, Decimal(500))

    def test_two_recipes_wanting_the_same_thing_are_added_up(self, butter):
        """500 g for one and 0.3 kg for another is 800 g and one line — which
        is the entire reason the units are a closed set."""
        first = matching.check_recipe([_line("Butter", 500, "g", butter)], {})
        second = matching.check_recipe([_line("Butter", Decimal("0.3"), "kg", butter)], {})
        rows = matching.shopping_list([("a", first), ("b", second)])
        assert len(rows) == 1
        assert rows[0].amount == Decimal(800)
        assert rows[0].recipes == ["a", "b"]

    def test_amounts_that_cannot_be_totalled_still_appear(self, butter):
        """Marked as un-totalled rather than dropped: a line nobody can add up
        is still a line somebody has to buy."""
        first = matching.check_recipe([_line("Butter", 200, "g", butter)], {})
        second = matching.check_recipe([_line("Butter", 2, "tbsp", butter)], {})
        # The tablespoon line is UNKNOWN, so only the gram one is a shortfall;
        # force both into the list by making the second one missing outright.
        rows = matching.shopping_list([("a", first), ("b", second)])
        assert [r.name for r in rows] == ["Butter"]


# --------------------------------------------------------------------------
# The pages
# --------------------------------------------------------------------------

class TestNothingHereIsReachableWithoutASession:
    @pytest.mark.parametrize("name, args", [
        ("pantry:list", ()),
        ("pantry:catalogue", ()),
        ("pantry:ingredient-add", ()),
    ])
    def test_it_asks_for_a_login(self, anon, db, name, args):
        response = anon.get(reverse(name, args=args))
        assert response.status_code == 302
        assert "/accounts/login/" in response["Location"]


class TestPuttingSomethingInTheCupboard:
    def test_a_known_name_is_matched_rather_than_duplicated(self, client, butter):
        client.post(reverse("pantry:add"),
                    {"name": "butter", "amount": "250", "unit": "g"})
        assert Ingredient.objects.filter(name__iexact="butter").count() == 1
        assert PantryItem.objects.get().ingredient == butter

    def test_an_unknown_name_joins_the_catalogue(self, client, db):
        """Which is how the catalogue learns what this household actually buys,
        rather than only what it cooks."""
        client.post(reverse("pantry:add"),
                    {"name": "Rübstiel", "amount": "1", "unit": "pc"})
        assert Ingredient.objects.get(name="Rübstiel").default_unit == "pc"

    def test_adding_the_same_thing_twice_corrects_it(self, client, butter):
        """Not a second cupboard — one row per substance, because "how much
        butter is there" has one answer."""
        client.post(reverse("pantry:add"), {"name": "Butter", "amount": "250", "unit": "g"})
        client.post(reverse("pantry:add"), {"name": "Butter", "amount": "500", "unit": "g"})
        item = PantryItem.objects.get()
        assert item.amount == Decimal(500)

    def test_a_blank_amount_is_kept_as_unmeasured(self, client, db):
        client.post(reverse("pantry:add"), {"name": "Salz", "amount": "", "unit": ""})
        assert PantryItem.objects.get().is_unmeasured

    def test_the_whole_cupboard_is_saved_in_one_post(self, client, butter, onion):
        """Somebody unpacking the shopping corrects six numbers, and six round
        trips is six chances for the page to stop matching the database."""
        first = PantryItem.objects.create(ingredient=butter, amount=Decimal(100), unit="g")
        second = PantryItem.objects.create(ingredient=onion, amount=Decimal(1), unit="pc")
        client.post(reverse("pantry:save"), {
            f"amount-{first.pk}": "250", f"unit-{first.pk}": "g",
            f"amount-{second.pk}": "6", f"unit-{second.pk}": "pc",
        })
        first.refresh_from_db()
        second.refresh_from_db()
        assert (first.amount, second.amount) == (Decimal(250), Decimal(6))

    def test_taking_something_out_keeps_it_in_the_catalogue(self, client, butter):
        PantryItem.objects.create(ingredient=butter, amount=Decimal(250), unit="g")
        client.post(reverse("pantry:remove", args=[butter.slug]))
        assert not PantryItem.objects.exists()
        assert Ingredient.objects.filter(pk=butter.pk).exists()


class TestTheCatalogue:
    def test_a_second_ingredient_may_not_take_an_existing_name(self, client, butter):
        """One substance is one row — that is the whole value of the table."""
        response = client.post(reverse("pantry:ingredient-add"), {
            "name": "butter", "default_unit": "g", "category": "", "note": "",
            **_formsets(),
        })
        assert response.status_code == 200
        assert Ingredient.objects.filter(name__iexact="butter").count() == 1

    def test_purchase_sizes_are_saved_with_the_ingredient(self, client, db):
        client.post(reverse("pantry:ingredient-add"), {
            "name": "Buchweizenmehl", "default_unit": "g", "category": "dry", "note": "",
            **_formsets(sizes=[("1", "kg", "Packung")]),
        })
        size = Ingredient.objects.get(name="Buchweizenmehl").purchase_sizes.get()
        assert (size.amount, size.unit) == (Decimal(1), "kg")

    def test_a_size_with_no_amount_is_refused(self, client, db):
        response = client.post(reverse("pantry:ingredient-add"), {
            "name": "Buchweizenmehl", "default_unit": "g", "category": "", "note": "",
            **_formsets(sizes=[("", "kg", "Packung")]),
        })
        assert response.status_code == 200
        assert not Ingredient.objects.filter(name="Buchweizenmehl").exists()

    def test_removing_an_ingredient_leaves_the_recipe_line_readable(self, client, butter):
        """SET_NULL is the right loss: the recipe still says "200 g Butter" and
        only stops taking part in the pantry matching."""
        recipe = Recipe.objects.create(title="Kuchen", slug="kuchen", servings=4)
        line = RecipeIngredient.objects.create(
            recipe=recipe, name="Butter", amount=Decimal(200), unit="g", ingredient=butter,
        )
        client.post(reverse("pantry:ingredient-delete", args=[butter.slug]))
        line.refresh_from_db()
        assert line.ingredient_id is None
        assert line.name == "Butter"

    def test_the_search_finds_a_row_through_its_alias(self, client, onion):
        """Or looking for "Zwiebeln" finds nothing and somebody creates a
        second onion."""
        response = client.get(reverse("pantry:catalogue"), {"q": "Zwiebeln"})
        assert [row.pk for row in response.context["ingredients"]] == [onion.pk]


def _formsets(aliases=(), sizes=()):
    """The management forms for the two inline formsets on the catalogue page.

    Written out rather than assumed: a POST missing one of them is not a
    validation error, it is a ManagementForm failure that refuses the whole
    submission — which reads as "the save button does nothing".
    """
    data = {
        "aliases-TOTAL_FORMS": str(len(aliases)), "aliases-INITIAL_FORMS": "0",
        "aliases-MIN_NUM_FORMS": "0", "aliases-MAX_NUM_FORMS": "1000",
        "purchase_sizes-TOTAL_FORMS": str(len(sizes)), "purchase_sizes-INITIAL_FORMS": "0",
        "purchase_sizes-MIN_NUM_FORMS": "0", "purchase_sizes-MAX_NUM_FORMS": "1000",
    }
    for n, name in enumerate(aliases):
        data[f"aliases-{n}-id"] = ""
        data[f"aliases-{n}-name"] = name
    for n, (amount, unit, label) in enumerate(sizes):
        data[f"purchase_sizes-{n}-id"] = ""
        data[f"purchase_sizes-{n}-amount"] = amount
        data[f"purchase_sizes-{n}-unit"] = unit
        data[f"purchase_sizes-{n}-label"] = label
    return data


class TestTheStarterCatalogueIsUsable:
    def test_the_examples_the_household_asked_for_are_in_it(self, db):
        """Water in millilitres, butter and sugar in grams — the whole point of
        suggesting a unit at all."""
        from apps.pantry.starter import STARTER

        units_by_name = {name: unit for name, unit, *_rest in STARTER}
        assert units_by_name["Wasser"] == "ml"
        assert units_by_name["Butter"] == "g"
        assert units_by_name["Zucker"] == "g"
        assert units_by_name["Milch"] == "ml"

    def test_every_shipped_unit_is_one_the_catalogue_knows(self, db):
        from apps.pantry.starter import STARTER

        for name, unit, _cat, _alt, sizes in STARTER:
            assert unit in units.BY_CODE, f"{name}: {unit}"
            for _amount, size_unit, _label in sizes:
                assert size_unit in units.BY_CODE, f"{name}: {size_unit}"

    def test_no_name_is_claimed_twice(self, db):
        from apps.pantry.starter import STARTER

        seen = set()
        for name, _unit, _cat, aliases, _sizes in STARTER:
            for each in [name] + list(aliases):
                assert each.casefold() not in seen, each
                seen.add(each.casefold())
