"""The ingredient catalogue and what is in the house.

A sibling app rather than three more tables in ``apps/recipes``, because the
thing it describes is not a recipe. "Zucker" exists whether or not anything is
made from it, it is sold in 1 kg bags whatever a recipe asks for, and there is
either some in the cupboard or there is not. Recipes point *at* this; it does
not point back.

Four models, and the ordering between them is the whole design:

``Ingredient`` is the substance — one row per thing, however many recipes
mention it. It is what makes the rest possible: a recipe line saying "Zucker"
and a cupboard saying "Zucker" are only the same claim if they are literally
the same row, and matching them on spelling is how "zucker", "Zucker " and
"Rohrzucker" become three substances the household does not have.

``IngredientAlias`` is every other name for it. This is what the autosuggest
searches and what the importer resolves through, and it exists because nobody
types the canonical name: the recipe says "Zwiebeln", the catalogue says
"Zwiebel", and without the alias the second one is created as a new substance
the first time somebody writes a plural.

``PurchaseSize`` is how it is *sold* — sugar in 1 kg, milk in 1 l. Not a single
field on the ingredient, because there is more than one answer (butter comes in
250 g and in 500 g) and a shopping list that can only round up to the biggest
one is a shopping list that buys a kilo of yeast. It is also the answer to the
question a recipe cannot ask on its own: 800 g of flour is two bags, not "800".

``PantryItem`` is the cupboard. One row per ingredient, because "how much sugar
is in the house" has one answer — and a null amount means *some, unmeasured*,
which is the honest state for salt and is treated as "enough" by the matching
rather than as zero. ``apps/pantry/matching.py`` is where that is decided;
nothing here knows about recipes.
"""

from decimal import Decimal

from django.conf import settings
from django.db import models
from django.urls import reverse
from django.utils.text import slugify
from django.utils.translation import gettext_lazy as _

from apps.pantry import units


class IngredientCategory(models.TextChoices):
    """Roughly where it lives, which is roughly the order a shop is walked in.

    A closed set for the same reason the portion sizes are: this is what a
    shopping list groups by, and free text gives "Gemüse", "gemüse" and "Obst
    und Gemüse" as three aisles. Deliberately coarse — it is a heading on a
    list, not a taxonomy, and the household that wants "Asiatisch" wants a tag
    on a recipe rather than a shelf in the cupboard.
    """

    PRODUCE = "produce", _("fruit and vegetables")
    DAIRY = "dairy", _("dairy and eggs")
    MEAT = "meat", _("meat and fish")
    BAKERY = "bakery", _("bread and baking")
    DRY = "dry", _("store cupboard")
    SPICE = "spice", _("herbs and spices")
    FROZEN = "frozen", _("frozen")
    DRINK = "drink", _("drinks")
    OTHER = "other", _("other")


def _unit_field(verbose_name, **kwargs):
    """A unit column, in the one shape every one of them has.

    Written once because there are four of them across two apps and the length
    is derived from the catalogue — a unit added with a longer code has to
    widen every column at once or the next save is a database error from a
    change that looked like one line.
    """
    kwargs.setdefault("blank", True)
    return models.CharField(verbose_name, max_length=units.MAX_CODE_LENGTH, **kwargs)


class Ingredient(models.Model):
    """One substance the household cooks with, buys, and keeps."""

    name = models.CharField(_("name"), max_length=120, unique=True)
    slug = models.SlugField(max_length=140, unique=True)

    # What this is nearly always measured in — millilitres for milk, grams for
    # butter. Offered when the name is chosen on a recipe line, never imposed:
    # "2 EL Milch" is a real line and the suggestion has to get out of its way.
    default_unit = _unit_field(_("usual unit"))

    category = models.CharField(
        _("category"), max_length=10, blank=True,
        choices=IngredientCategory.choices,
        help_text=_("Used to group a shopping list."),
    )

    note = models.CharField(
        _("note"), max_length=200, blank=True,
        help_text=_("e.g. “the one in the blue bag”, “only the Italian”."),
    )

    # Kept because the catalogue grows by itself — every recipe saved mints the
    # names it does not know — and the tidy-up afterwards wants to know which
    # rows arrived that way rather than being typed on the catalogue page.
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, verbose_name=_("added by"),
        null=True, blank=True, on_delete=models.SET_NULL, related_name="ingredients",
    )

    class Meta:
        verbose_name = _("ingredient")
        verbose_name_plural = _("ingredients")
        ordering = ["name"]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = _unique_slug(Ingredient, self.name, self.pk)
        super().save(*args, **kwargs)

    def get_absolute_url(self):
        return reverse("pantry:ingredient-edit", args=[self.slug])

    @property
    def default_unit_label(self):
        return units.label(self.default_unit)


class IngredientAlias(models.Model):
    """Another name for the same substance.

    "Zwiebeln" for "Zwiebel", "Puderzucker" for "Zucker" if that is how this
    house thinks of it. Unique across the whole table and not only within one
    ingredient, because the point is to answer "what did they mean?" — and a
    name that means two things answers nothing.
    """

    ingredient = models.ForeignKey(
        Ingredient, on_delete=models.CASCADE, related_name="aliases",
        verbose_name=_("ingredient"),
    )
    name = models.CharField(_("also called"), max_length=120, unique=True)

    class Meta:
        verbose_name = _("other name")
        verbose_name_plural = _("other names")
        ordering = ["name"]

    def __str__(self):
        return self.name


class PurchaseSize(models.Model):
    """How this ingredient is sold: sugar in 1 kg, milk in 1 l.

    The reason it is a table and not a pair of columns: butter is sold in 250 g
    and in 500 g, and a shopping list that knows only the largest rounds a
    300 g recipe up to half a kilo. With both recorded it can say "one 250 g
    and one 100 g" or, more usefully, "one 500 g pack covers it".

    ``amount`` is required here where it is optional everywhere else in the
    app. A packet whose size is unknown is not a packet size.
    """

    ingredient = models.ForeignKey(
        Ingredient, on_delete=models.CASCADE, related_name="purchase_sizes",
        verbose_name=_("ingredient"),
    )
    amount = models.DecimalField(_("amount"), max_digits=9, decimal_places=3)
    unit = _unit_field(_("unit"))
    label = models.CharField(
        _("sold as"), max_length=60, blank=True,
        help_text=_("e.g. “bag”, “bottle”, “block”."),
    )

    class Meta:
        verbose_name = _("purchase size")
        verbose_name_plural = _("purchase sizes")
        ordering = ["amount"]
        constraints = [
            # Two identical sizes would both be right and the page would offer
            # the same packet twice.
            models.UniqueConstraint(
                fields=["ingredient", "amount", "unit"], name="one_row_per_purchase_size",
            ),
        ]

    def __str__(self):
        return f"{self.amount_display} {units.label(self.unit)}".strip()

    @property
    def amount_display(self):
        return _trimmed(self.amount)


class PantryItem(models.Model):
    """What is actually in the house, and how much of it.

    One row per ingredient — "how much sugar is there" has one answer, however
    many bags it is spread over. ``amount`` may be null and that is the
    interesting state rather than a gap: it means *some, not measured*, which
    is the truthful thing to record about salt, and the matching reads it as
    "enough" rather than as nothing.
    """

    ingredient = models.OneToOneField(
        Ingredient, on_delete=models.CASCADE, related_name="in_pantry",
        verbose_name=_("ingredient"),
    )
    amount = models.DecimalField(_("amount"), max_digits=9, decimal_places=3,
                                 null=True, blank=True)
    unit = _unit_field(_("unit"))
    note = models.CharField(_("note"), max_length=200, blank=True)

    # Not auto_now: this is when somebody last *looked*, and it is the column
    # that makes "checked in March" readable as the warning it is. A save that
    # only corrects a spelling should not claim the cupboard was counted.
    checked_at = models.DateTimeField(_("last checked"), auto_now=True)

    class Meta:
        verbose_name = _("pantry item")
        verbose_name_plural = _("pantry")
        ordering = ["ingredient__name"]

    def __str__(self):
        return str(self.ingredient)

    @property
    def amount_display(self):
        return _trimmed(self.amount)

    @property
    def unit_label(self):
        return units.label(self.unit)

    @property
    def is_unmeasured(self):
        """Some, but nobody wrote down how much. Counts as enough."""
        return self.amount is None


def _trimmed(amount):
    """250 rather than 250.000, 1.5 rather than 1.500.

    The same rule as ``RecipeIngredient.amount_display`` and for the same
    reason: a fixed-scale Decimal is right for the arithmetic and wrong on a
    page somebody reads. Kept in both apps rather than imported across, because
    a display helper is not a dependency worth having between them.
    """
    if amount is None:
        return ""
    trimmed = amount.normalize()
    # normalize() turns 250.000 into 2.5E+2, which is worse than what it
    # replaced. Re-expand anything that came back in exponent form.
    if trimmed == trimmed.to_integral_value():
        return str(trimmed.quantize(Decimal(1)))
    return str(trimmed)


def _unique_slug(model, source, pk=None):
    base = slugify(source)[:120] or "zutat"
    candidate = base
    n = 2
    taken = model.objects.exclude(pk=pk) if pk else model.objects.all()
    while taken.filter(slug=candidate).exists():
        candidate = f"{base}-{n}"
        n += 1
    return candidate
