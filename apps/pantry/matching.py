"""Measuring a recipe against the cupboard.

Two questions, one answer: *can this be made now* and *what would have to be
bought*. They are the same walk over the same lines, which is why they are one
function — the version with a fast boolean path beside a detailed one is the
version where the list page says "ready" and the recipe page then lists three
missing things.

**Four verdicts, and the fourth is the point.** A line is `HAVE`, `SHORT` (some
but not enough), `MISSING` (none), or `UNKNOWN`. Unknown is not a failure to be
rounded to one of the others: it is what "200 g Butter" against "2 Packungen
Butter" honestly is, and a matcher that guesses there will eventually tell
somebody they have enough flour when they do not. Everything that faces a page
keeps unknown separate and says so in words.

**An optional line is never missing.** ``optional`` is the difference between
"you need this" and "nice if you have it", and a pantry that refuses a recipe
for want of the parsley garnish is a pantry nobody consults twice. They are
counted and reported, but they do not decide whether something can be made.

**A substitute rescues the line it replaces.** "200 g Butter or 180 g
Margarine" is satisfied by either, so the alternatives are tried before the
line is called missing — and the one that worked is named, because "you can
make it, with the margarine" is the useful sentence.

Everything here is pure and takes the rows it works on as arguments. That is
not style: the list page runs it over every recipe, and a function that reached
for the ORM per line would turn one page into a thousand queries. The callers
in ``apps/recipes/views.py`` prefetch once and hand the lists in, exactly as
they already do for ``apps/recipes/diagram.py``.
"""

from dataclasses import dataclass, field
from decimal import Decimal

from apps.pantry import units

HAVE = "have"
SHORT = "short"
MISSING = "missing"
UNKNOWN = "unknown"


@dataclass
class LineVerdict:
    """What the cupboard says about one ingredient line."""

    line: object
    state: str
    # How much the recipe wants, at the servings it was measured for. Carried
    # so the shopping list can add two recipes' worth of the same thing up.
    needed: Decimal = None
    needed_unit: str = ""
    # What is there, in the line's own unit — so a page can say "you have 300
    # of the 500 g" without doing the conversion a second time and differently.
    available: Decimal = None
    # Set when an alternative is what rescued this line.
    satisfied_by: object = None

    @property
    def shortfall(self):
        """How much more is wanted. None when that cannot be worked out."""
        if self.needed is None:
            return None
        if self.state == MISSING:
            return self.needed
        if self.state == SHORT and self.available is not None:
            return self.needed - self.available
        return None


@dataclass
class RecipeVerdict:
    """What the cupboard says about a whole recipe."""

    lines: list = field(default_factory=list)

    @property
    def required(self):
        """The lines that actually decide the answer — the ones not optional."""
        return [v for v in self.lines if not v.line.optional]

    @property
    def missing(self):
        return [v for v in self.required if v.state in (MISSING, SHORT)]

    @property
    def unknown(self):
        return [v for v in self.required if v.state == UNKNOWN]

    @property
    def extras(self):
        """Optional lines that are not there. Worth saying, never blocking."""
        return [v for v in self.lines if v.line.optional and v.state in (MISSING, SHORT)]

    @property
    def can_be_made(self):
        """Everything required is there, and nothing required is a guess.

        Unknown counts against it deliberately. "You can make this" is a
        promise somebody acts on by not going to the shop, and a promise that
        rests on a unit nobody could convert is one that breaks in the kitchen.
        ``nearly`` below is where those land instead.
        """
        return not self.missing and not self.unknown

    @property
    def nearly(self):
        """Close enough to be worth showing: at most two things in the way."""
        return not self.can_be_made and len(self.missing) + len(self.unknown) <= 2

    @property
    def short_count(self):
        return len(self.missing) + len(self.unknown)


def pantry_by_ingredient(items):
    """``{ingredient_id: PantryItem}`` — the shape every call below wants."""
    return {item.ingredient_id: item for item in items}


def check_recipe(lines, pantry, servings=None, base_servings=None):
    """Measure ``lines`` — the recipe's top-level rows — against ``pantry``.

    ``lines`` must have been through ``apps/recipes/diagram.top_level``, so
    each one carries its substitutes as ``.substitutes`` and the substitute
    rows themselves are not in the list. Passing the raw queryset would count
    "180 g Margarine" as a seventh thing to buy for a recipe with six.

    ``servings`` scales the amounts before comparing, for the case where
    somebody is planning to cook eight of something written for four.
    """
    scale = _scale(servings, base_servings)
    return RecipeVerdict(lines=[_check_line(line, pantry, scale) for line in lines])


def _scale(servings, base_servings):
    if not servings or not base_servings or servings == base_servings:
        return Decimal(1)
    try:
        return Decimal(servings) / Decimal(base_servings)
    except (ArithmeticError, TypeError):
        return Decimal(1)


def _check_line(line, pantry, scale):
    verdict = _check_one(line, pantry, scale)
    if verdict.state == HAVE:
        return verdict

    # "200 g Butter or 180 g Margarine" is satisfied by either. Tried only once
    # the line itself has failed, and the first one that works wins — a page
    # saying "you can make it, with the margarine" needs one answer, not a
    # ranking.
    for alternative in getattr(line, "substitutes", ()):
        other = _check_one(alternative, pantry, scale)
        if other.state == HAVE:
            other.line = line
            other.satisfied_by = alternative
            return other
    return verdict


def _check_one(line, pantry, scale):
    """One row against the cupboard, substitutes not considered."""
    wanted = _wanted(line, scale)
    base = LineVerdict(line=line, state=UNKNOWN, needed=wanted, needed_unit=line.unit)

    if not line.ingredient_id:
        # A line typed as free text and never matched to the catalogue. Nothing
        # can be said about it — and saying "missing" would put every recipe
        # written before the catalogue existed on the shopping list.
        return base

    item = pantry.get(line.ingredient_id)
    if item is None:
        base.state = MISSING
        return base

    # Some, unmeasured — salt, pepper, the oil in the big tin. Enough.
    if item.amount is None:
        base.state = HAVE
        return base

    # The recipe asks for no particular amount, and there is some of it there.
    if wanted is None:
        base.state = HAVE
        return base

    have = units.convert(item.amount, item.unit, line.unit)
    if have is None:
        # Different dimensions, or a unit from before the catalogue. The honest
        # answer, and the one can_be_made refuses to call ready.
        return base

    base.available = have
    base.state = HAVE if have >= wanted else (MISSING if have <= 0 else SHORT)
    return base


def _wanted(line, scale):
    """What the line asks for at the servings being cooked, or None.

    None is "no particular amount" — a line for salt, or one whose amount was
    never filled in — and it is satisfied by having any of the thing at all.
    """
    if line.amount is None:
        return None
    return line.amount * scale


# --------------------------------------------------------------------------
# Adding several recipes up
# --------------------------------------------------------------------------

@dataclass
class ShoppingLine:
    """One entry on a shopping list: a substance, an amount, and what it is for."""

    ingredient: object
    amount: Decimal = None
    unit: str = ""
    unknown_amount: bool = False
    recipes: list = field(default_factory=list)
    # The smallest packet that covers the shortfall, when the catalogue knows
    # how this is sold. "800 g Mehl" is two bags, and the bag is what is bought.
    packet: object = None
    packets: int = 0

    @property
    def name(self):
        return self.ingredient.name if self.ingredient else ""


def shopping_list(verdicts):
    """Turn ``[(recipe, RecipeVerdict), …]`` into what has to be bought.

    Amounts for the same substance are added up, converting into the first
    unit seen for it — so 500 g of flour for one recipe and 0.3 kg for another
    is 800 g and one line, which is the entire reason the units are a closed
    set. Two shortfalls that cannot be converted into each other keep the
    amount off the line rather than adding numbers that do not mean the same
    thing; the line still appears, marked as an amount nobody can total.
    """
    out = {}
    for recipe, verdict in verdicts:
        for entry in verdict.missing:
            line = entry.line
            if not line.ingredient_id:
                continue
            row = out.get(line.ingredient_id)
            if row is None:
                row = ShoppingLine(ingredient=line.ingredient, unit=entry.needed_unit)
                out[line.ingredient_id] = row
            if recipe not in row.recipes:
                row.recipes.append(recipe)

            short = entry.shortfall
            if short is None:
                row.unknown_amount = True
                continue
            addable = units.convert(short, entry.needed_unit, row.unit)
            if addable is None:
                row.unknown_amount = True
                continue
            row.amount = (row.amount or Decimal(0)) + addable

    for row in out.values():
        _choose_packet(row)
    return sorted(out.values(), key=lambda row: row.name.casefold())


def _choose_packet(row):
    """The smallest recorded packet that covers the amount, and how many.

    Smallest-that-covers rather than largest, because the failure people
    actually mind is buying a kilo of yeast. When one packet is not enough the
    count says so — "2 × 500 g" — which is what somebody standing in the shop
    needs rather than "1000 g".
    """
    if row.ingredient is None or row.amount is None:
        return
    best = None
    for size in row.ingredient.purchase_sizes.all():
        covers = units.convert(size.amount, size.unit, row.unit)
        if covers is None or covers <= 0:
            continue
        # Written out rather than as the usual `-(-a // b)` ceiling trick, which
        # is wrong here: Decimal's floor division truncates *toward zero* where
        # int's floors, so -(-900 // 500) is 1 rather than 2. The symptom is a
        # shopping list that buys one packet too few — quietly, and only for the
        # amounts that do not divide evenly.
        whole = int(row.amount // covers)
        count = whole + (1 if row.amount % covers else 0)
        if count < 1:
            count = 1
        if best is None or (count, covers) < (best[0], best[1]):
            best = (count, covers, size)
    if best is not None:
        row.packets, row.packet = best[0], best[2]
