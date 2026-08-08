"""Turning what somebody typed into a row of the catalogue.

The recipe form lets people type. It has to: a recipe is written in one sitting
with the book open, and a page that asks somebody to go and create "Zwetschgen"
before they may write "1.5 kg Zwetschgen" is a page that ends with an empty
collection. So the name column stays free text and this module is what quietly
puts a substance behind it.

**Lookup is exact, after folding.** Case and surrounding whitespace are
ignored, aliases are searched, and *nothing else is guessed*. The tempting next
step — stripping plurals, matching prefixes, "Kartoffeln" ≈ "Kartoffelsalat" —
buys a handful of correct matches and pays for them with wrong ones, and a
wrong match is not a cosmetic error here: it is the pantry saying you have
1 kg of something you do not have. Anything unmatched becomes its own row,
which is visible, correctable, and never a lie.

**The catalogue grows by itself.** Saving a recipe mints the names it does not
know, because a catalogue somebody has to fill in first is a catalogue that
stays empty and an autosuggest with nothing to suggest. The cost is
near-duplicates — "Kartoffeln" beside "festkochende Kartoffeln" — and the
answer to those is the merge on the catalogue page, not a cleverer matcher
here.
"""

from django.db.models import Prefetch
from django.utils.text import slugify

from apps.pantry.models import Ingredient, IngredientAlias


def fold(name):
    """The key two spellings of one substance have in common.

    Case-folded and with runs of whitespace collapsed — nothing more. Every
    further rule is a guess; see the module docstring.
    """
    return " ".join((name or "").split()).casefold()


def index():
    """``{folded name: Ingredient}`` over the whole catalogue, names and aliases.

    One query pair for the entire collection, so the recipe save that resolves
    twenty lines does not run twenty lookups. A household catalogue is a few
    hundred rows; when it is not, this is the function to change.
    """
    found = {}
    for ingredient in Ingredient.objects.all():
        found[fold(ingredient.name)] = ingredient
    for alias in IngredientAlias.objects.select_related("ingredient"):
        # An alias never displaces a real name. Two ingredients where one's
        # alias is the other's name is a data problem, and resolving it in
        # favour of the name is the answer that surprises nobody.
        found.setdefault(fold(alias.name), alias.ingredient)
    return found


def lookup(name, known=None):
    """The catalogue row for ``name``, or None. ``known`` is a prebuilt index."""
    key = fold(name)
    if not key:
        return None
    return (known if known is not None else index()).get(key)


def remember(name, unit="", user=None, known=None):
    """The catalogue row for ``name``, creating one if there is none.

    Returns ``(ingredient, created)``. ``unit`` seeds the new row's usual unit
    from the line that first mentioned it, which is right far more often than
    blank — the first time anybody writes "Sahne" they write it in millilitres,
    and that is the suggestion the *second* time.
    """
    existing = lookup(name, known)
    if existing is not None:
        return existing, False

    clean = " ".join((name or "").split())[:120]
    if not clean:
        return None, False

    ingredient = Ingredient.objects.create(
        name=clean,
        slug=_free_slug(clean),
        default_unit=unit or "",
        created_by=user,
    )
    if known is not None:
        known[fold(clean)] = ingredient
    return ingredient, True


def resolve_lines(lines, user=None, create=True):
    """Point every line in ``lines`` at a catalogue row, in as few queries as possible.

    Called after a recipe's ingredients have been saved. Only the rows whose
    ``ingredient`` actually changes are written — the ordinary edit touches a
    title and leaves twenty lines already resolved, and re-saving all of them
    would be twenty writes taking SQLite's one write lock for nothing.
    """
    known = index()
    touched = []
    for line in lines:
        # A line already pointed somewhere is left alone. Re-resolving would
        # undo a correction somebody made by hand the moment they fixed a
        # spelling anywhere else on the recipe.
        if line.ingredient_id:
            continue
        if create:
            ingredient, _created = remember(line.name, line.unit, user, known)
        else:
            ingredient = lookup(line.name, known)
        if ingredient is not None:
            line.ingredient = ingredient
            touched.append(line)
    for line in touched:
        line.save(update_fields=["ingredient"])
    return touched


def suggestions():
    """What the autosuggest on the recipe form is built from.

    A plain list of dicts rather than a search endpoint. A household catalogue
    is a few hundred entries and a few kilobytes; embedding it in the page with
    ``json_script`` means the suggestion appears as fast as somebody types,
    with no request, no debounce, no race between two of them, and no new URL
    to keep behind the login. The moment this is measured in thousands, it
    becomes an endpoint — and the shape it returns is already this.
    """
    rows = Ingredient.objects.prefetch_related(
        Prefetch("aliases", queryset=IngredientAlias.objects.only("name", "ingredient")),
    ).only("id", "name", "default_unit", "category")
    return [
        {
            "id": row.id,
            "name": row.name,
            "unit": row.default_unit,
            "alt": [alias.name for alias in row.aliases.all()],
        }
        for row in rows
    ]


def _free_slug(name):
    base = slugify(name)[:120] or "zutat"
    candidate, n = base, 2
    taken = set(
        Ingredient.objects.filter(slug__startswith=base).values_list("slug", flat=True)
    )
    while candidate in taken:
        candidate = f"{base}-{n}"
        n += 1
    return candidate
