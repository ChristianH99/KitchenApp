"""The closed set of units, and the only place that knows what converts to what.

This replaced a free-text column, and the reason is the pantry rather than
tidiness. "1 kg Zucker" in the cupboard answers "500 g Zucker" in a recipe only
if something can turn one into the other, and a column holding "g", "gr",
"Gramm" and "gramm" cannot do that for one substance — while looking perfectly
correct on every page it appears on. Every wrong answer the matching gives is a
unit it failed to recognise, so the set is closed and the recognition happens
once, here.

**A unit belongs to a dimension, and only units in the same dimension convert.**
Mass and volume each have several; everything else is its own dimension of one,
which is a deliberate way of saying *this never converts into anything*. A clove
of garlic is not 4 grams, a bunch of parsley is not 30, and a table that claims
otherwise turns "you have enough" into a wrong answer somebody only finds out
about with the pan already hot.

**``tsp`` and ``tbsp`` are volume; ``cup`` is not.** 5 ml and 15 ml are what a
measuring spoon is sold as, so those two are safe. A cup is not: German recipes
use it for anything between 150 and 250 ml, and — worse — it is usually a
measure of *flour*, which is a mass. Left unconvertible it produces "cannot
tell", which is the truth.

**The empty unit is a count.** "1 Zwiebel" and "3 Stück Zwiebeln" are the same
statement written two ways, and a household writes both. They share a dimension
so the pantry can answer one with the other.

The stored value is the **code**, which is language-neutral; what a page shows
is the label, which is not. That split is why ``unit`` is not simply the German
word: the same recipe has to read "2 EL" and "2 tbsp" without the database
changing, and the pantry has to compare the two rows regardless of who typed
them. ``apps/recipes/migrations/0003_*`` moves the values that predate this.
"""

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from django.utils.translation import gettext_lazy as _
from django.utils.translation import pgettext_lazy


@dataclass(frozen=True)
class Unit:
    """One unit: how it is stored, how it is shown, and what it is worth.

    ``factor`` is how many of the dimension's base one of these is — grams for
    mass, millilitres for volume, one for everything countable. A Decimal and
    not a float, because these multiply amounts that people read: 0.1 + 0.2 is
    a wrong number on a shopping list and an unanswerable bug report.
    """

    code: str
    label: object          # short, what a recipe line shows: "g", "EL"
    name: object           # long, what the dropdown shows: "grams", "tablespoon"
    dimension: str
    factor: Decimal = Decimal(1)

    @property
    def converts(self):
        """Whether anything else shares this unit's dimension."""
        return len(BY_DIMENSION[self.dimension]) > 1


# The order here is the order of the dropdown, and it is not alphabetical: it is
# how often a household reaches for each one. Grams and millilitres first, the
# packaging words last.
UNITS = (
    # The short label is deliberately **empty**. "1 Zwiebel" has no unit and
    # wants none printed; an em-dash there reads as a missing value rather than
    # as an absent one, and the diagram rendered "— Salz und Pfeffer". The
    # dropdown says "no unit" instead — that is `name`, and it is a label for a
    # *choice*, which is a different thing from a label for a quantity.
    Unit("", "", _("no unit"), "count"),

    Unit("g", pgettext_lazy("unit", "g"), _("grams"), "mass", Decimal(1)),
    Unit("kg", pgettext_lazy("unit", "kg"), _("kilograms"), "mass", Decimal(1000)),

    Unit("ml", pgettext_lazy("unit", "ml"), _("millilitres"), "volume", Decimal(1)),
    Unit("l", pgettext_lazy("unit", "l"), _("litres"), "volume", Decimal(1000)),

    # 5 ml and 15 ml are what a measuring spoon is sold as, in this kitchen and
    # in the recipes it copies from. Safe to convert; see the module docstring
    # for why a cup is not.
    Unit("tsp", pgettext_lazy("unit", "tsp"), _("teaspoon"), "volume", Decimal(5)),
    Unit("tbsp", pgettext_lazy("unit", "tbsp"), _("tablespoon"), "volume", Decimal(15)),
    Unit("cup", pgettext_lazy("unit", "cup"), _("cup"), "cup"),

    Unit("pc", pgettext_lazy("unit", "pc"), _("pieces"), "count"),
    Unit("pinch", pgettext_lazy("unit", "pinch"), _("pinch"), "pinch"),
    Unit("bunch", pgettext_lazy("unit", "bunch"), _("bunch"), "bunch"),
    Unit("clove", pgettext_lazy("unit", "clove"), _("clove"), "clove"),
    Unit("slice", pgettext_lazy("unit", "slice"), _("slice"), "slice"),
    Unit("leaf", pgettext_lazy("unit", "leaf"), _("leaf"), "leaf"),
    Unit("cube", pgettext_lazy("unit", "cube"), _("cube"), "cube"),
    Unit("drop", pgettext_lazy("unit", "drop"), _("drop"), "drop"),

    Unit("pack", pgettext_lazy("unit", "pack"), _("packet"), "pack"),
    Unit("tin", pgettext_lazy("unit", "tin"), _("tin"), "tin"),
    Unit("jar", pgettext_lazy("unit", "jar"), _("jar"), "jar"),
    Unit("bottle", pgettext_lazy("unit", "bottle"), _("bottle"), "bottle"),
)

BY_CODE = {unit.code: unit for unit in UNITS}

BY_DIMENSION = {}
for _unit in UNITS:
    BY_DIMENSION.setdefault(_unit.dimension, []).append(_unit)
del _unit

# The longest code, which is what the model column has to hold. Derived rather
# than written down: a unit added below with a longer code would otherwise be a
# database error on the first save, from a change that looked like one line.
MAX_CODE_LENGTH = max(len(unit.code) for unit in UNITS)


# How the dropdown is grouped. Purely presentational — the dimensions above are
# what the arithmetic uses — but a flat list of twenty entries is one nobody
# reads to the bottom of, and the two people use most are at the top of it.
GROUPS = (
    (_("Weight"), ("g", "kg")),
    (_("Volume"), ("ml", "l", "tsp", "tbsp", "cup")),
    (_("By the piece"), ("pc", "pinch", "bunch", "clove", "slice", "leaf",
                         "cube", "drop")),
    (_("Packaging"), ("pack", "tin", "jar", "bottle")),
)


def choices(extra=None):
    """``[(label, [(code, name), …]), …]`` for a grouped ``<select>``.

    ``extra`` is a value already in the database that is not one of ours — a
    line typed before this file existed, or a hand-edited row. It is offered as
    an option of its own rather than dropped, because a select that silently
    cannot represent its own value saves a *different* value the moment
    somebody presses Save on an unrelated field, and "3 Handvoll Petersilie"
    becoming "3 Petersilie" is not a change anybody asked for.
    """
    # The *short* form in the options — "g", "EL" — not "grams" and
    # "tablespoon". On the recipe form this control is half of a cell about
    # thirteen rems wide, and a select showing "millilitres" there is a select
    # showing "millil…". The group headings carry the meaning instead, which is
    # what they are for: "g" under "Weight" needs no further explanation.
    out = [(None, [("", BY_CODE[""].name)])]
    for group, codes in GROUPS:
        out.append((group, [(code, BY_CODE[code].label) for code in codes]))
    if extra and extra not in BY_CODE:
        out.append((_("As typed"), [(extra, extra)]))
    return out


def label(code):
    """The short form a recipe line shows. Unknown values are shown as typed."""
    unit = BY_CODE.get(code)
    return unit.label if unit else code


def name(code):
    """The long form the dropdown and the catalogue show."""
    unit = BY_CODE.get(code)
    return unit.name if unit else code


def convert(amount, from_code, to_code):
    """``amount`` of ``from_code`` expressed in ``to_code``, or None.

    None means *cannot tell*, and every caller has to treat it as such rather
    than as zero or as a failure. Two units in different dimensions is the
    ordinary case for it — 200 g of butter against 2 tbsp — and answering
    anything numeric there would be inventing a density.
    """
    if amount is None:
        return None
    source, target = BY_CODE.get(from_code), BY_CODE.get(to_code)
    if source is None or target is None:
        # One of them is a legacy free-text value. Two *identical* free-text
        # values are still the same unit, though, and refusing that would make
        # the pantry useless for every row written before this file.
        return Decimal(amount) if from_code == to_code else None
    if source.dimension != target.dimension:
        return None
    try:
        return (Decimal(amount) * source.factor) / target.factor
    except (InvalidOperation, ArithmeticError):
        return None


def comparable(first, second):
    """Whether an amount in ``first`` can be measured against one in ``second``."""
    return convert(Decimal(1), first, second) is not None


def normalise(raw):
    """Best guess at the code for a unit somebody typed, or the text unchanged.

    Used by the migration that moves the pre-catalogue rows and by the importer
    for a recipe pasted in from elsewhere. It is deliberately narrow: it maps
    the spellings this household has actually produced and leaves anything else
    alone, because a guess that silently turns "Msp." into millilitres is worse
    than a value the dropdown shows under "As typed".
    """
    text = (raw or "").strip()
    if not text:
        return ""
    if text in BY_CODE:
        return text
    return SPELLINGS.get(text.casefold().rstrip("."), text)


# Everything this collection has been written with, in both languages, mapped
# to the code that means it. Keys are casefolded and stripped of a trailing
# full stop, which is the whole of the normalisation — see normalise() for why
# it is not cleverer than that.
SPELLINGS = {
    "gramm": "g", "gr": "g", "grams": "g", "gram": "g",
    "kilogramm": "kg", "kilo": "kg", "kilograms": "kg",
    "milliliter": "ml", "millilitre": "ml", "millilitres": "ml",
    "liter": "l", "litre": "l", "litres": "l", "ltr": "l",
    "el": "tbsp", "esslöffel": "tbsp", "essloeffel": "tbsp",
    "tablespoon": "tbsp", "tablespoons": "tbsp", "tbs": "tbsp",
    "tl": "tsp", "teelöffel": "tsp", "teaspoon": "tsp", "teaspoons": "tsp",
    "tasse": "cup", "tassen": "cup", "cups": "cup",
    "stück": "pc", "stk": "pc", "st": "pc", "piece": "pc", "pieces": "pc",
    "prise": "pinch", "prisen": "pinch", "pinches": "pinch",
    "bund": "bunch", "bünde": "bunch", "bunches": "bunch",
    "zehe": "clove", "zehen": "clove", "cloves": "clove",
    "scheibe": "slice", "scheiben": "slice", "slices": "slice",
    "blatt": "leaf", "blätter": "leaf", "leaves": "leaf",
    "würfel": "cube", "wuerfel": "cube", "cubes": "cube",
    "tropfen": "drop", "drops": "drop",
    "packung": "pack", "päckchen": "pack", "pck": "pack",
    "paket": "pack", "packet": "pack", "packets": "pack", "packs": "pack",
    "dose": "tin", "dosen": "tin", "tins": "tin", "can": "tin", "cans": "tin",
    "glas": "jar", "gläser": "jar", "jars": "jar",
    "flasche": "bottle", "flaschen": "bottle", "bottles": "bottle",
}
