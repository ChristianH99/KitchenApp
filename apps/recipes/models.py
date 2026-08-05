"""The recipe collection.

One decision here is worth more than the rest of the file: **ingredients are
rows, not a block of text**. A ``TextField`` called ``ingredients`` is a third
of the work and it forecloses everything the app is eventually for — scaling a
recipe from four servings to six, building a shopping list from three of them,
asking what can be made from what is in the cupboard. None of those can be
retrofitted onto free text without re-typing the whole collection by hand,
which is the one migration nobody ever does.

So an amount, a unit and a name are separate columns, and ``Recipe.servings``
says what those amounts are *for* — without it a number is just a number and
scaling has nothing to divide by.

The other decision: a recipe is a household object, not a personal one.
``created_by`` records who typed it in, and anyone signed in may cook from it;
who may *edit* it is apps/recipes/views.py's business, not the model's.
"""

from decimal import Decimal

from django.conf import settings
from django.db import models
from django.urls import reverse
from django.utils.text import slugify
from django.utils.translation import gettext_lazy as _

from apps.recipes.images import recipe_image_path


class Tag(models.Model):
    """A free label — "Suppe", "vegetarisch", "schnell", "Weihnachten".

    Deliberately flat, with no category/subcategory hierarchy. A household's
    recipes get labelled along several axes at once (a course, a diet, a
    season, whose recipe it is) and any tree forces one of those to be the
    trunk. Flat labels compose; a tree makes you choose.
    """

    name = models.CharField(_("name"), max_length=60, unique=True)
    slug = models.SlugField(max_length=60, unique=True)

    class Meta:
        verbose_name = _("tag")
        verbose_name_plural = _("tags")
        ordering = ["name"]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = _unique_slug(Tag, self.name, self.pk)
        super().save(*args, **kwargs)


class Recipe(models.Model):
    title = models.CharField(_("title"), max_length=200)
    slug = models.SlugField(max_length=220, unique=True)
    description = models.TextField(
        _("description"), blank=True,
        help_text=_("One or two sentences — what this is, and when you would make it."),
    )

    # What the ingredient amounts below are stated for. Not nullable and not
    # zero: every amount in the recipe is divided by this to scale, and a
    # missing denominator is a page of empty quantities.
    servings = models.PositiveSmallIntegerField(_("servings"), default=4)

    prep_minutes = models.PositiveIntegerField(_("preparation time"), null=True, blank=True,
                                               help_text=_("In minutes."))
    cook_minutes = models.PositiveIntegerField(_("cooking time"), null=True, blank=True,
                                               help_text=_("In minutes."))

    instructions = models.TextField(_("instructions"), blank=True)

    image = models.ImageField(_("photograph"), upload_to=recipe_image_path, blank=True)

    # Where it came from: a person, a book, a website. Two fields rather than
    # one because "Omas Kochbuch, S. 112" and a URL are different things and
    # only one of them is a link.
    source = models.CharField(_("source"), max_length=200, blank=True,
                              help_text=_("A person, a cookbook, a magazine."))
    source_url = models.URLField(_("link"), max_length=500, blank=True)

    notes = models.TextField(_("notes"), blank=True,
                             help_text=_("What you would do differently next time."))

    tags = models.ManyToManyField(Tag, verbose_name=_("tags"), blank=True, related_name="recipes")

    # SET_NULL, not CASCADE: deleting the account of somebody who has left the
    # household must not delete the recipes they contributed to it.
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, verbose_name=_("added by"),
        null=True, blank=True, on_delete=models.SET_NULL, related_name="recipes",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("recipe")
        verbose_name_plural = _("recipes")
        ordering = ["title"]
        indexes = [
            # The list's default order, and the one query every page runs.
            models.Index(fields=["-created_at"], name="recipe_newest_idx"),
        ]

    def __str__(self):
        return self.title

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = _unique_slug(Recipe, self.title, self.pk)
        super().save(*args, **kwargs)

    def get_absolute_url(self):
        return reverse("recipes:detail", args=[self.slug])

    @property
    def total_minutes(self):
        """Preparation plus cooking, or None when neither is recorded.

        None rather than 0, because "nought minutes" and "nobody wrote it down"
        are different claims and only one of them belongs on a recipe card.
        """
        parts = [m for m in (self.prep_minutes, self.cook_minutes) if m]
        return sum(parts) if parts else None


class RecipeIngredient(models.Model):
    """One line of the ingredient list, for ``recipe.servings`` servings.

    ``amount`` is nullable, and that is not laziness: "Salz", "Pfeffer", "etwas
    Öl" are real ingredient lines with no quantity, and forcing a zero onto
    them would print "0 g Salz". A line with no amount simply does not scale.
    """

    recipe = models.ForeignKey(Recipe, on_delete=models.CASCADE, related_name="ingredients")

    # Explicit ordering rather than insertion order: an ingredient list is a
    # sequence somebody arranged (everything for the dough, then everything for
    # the filling), and a pk-ordered list loses that the first time a line is
    # inserted in the middle.
    position = models.PositiveSmallIntegerField(default=0)

    # Three decimal places carries 0.125 (an eighth of a litre) and 1.5 without
    # the binary-float surprise a FloatField would bring to a number people read.
    amount = models.DecimalField(_("amount"), max_digits=9, decimal_places=3,
                                 null=True, blank=True)
    unit = models.CharField(_("unit"), max_length=30, blank=True)
    name = models.CharField(_("ingredient"), max_length=120)
    note = models.CharField(_("note"), max_length=120, blank=True,
                            help_text=_("e.g. “finely chopped”, “at room temperature”."))

    class Meta:
        verbose_name = _("ingredient")
        verbose_name_plural = _("ingredients")
        ordering = ["position", "id"]

    def __str__(self):
        return " ".join(part for part in (self.amount_display, self.unit, self.name) if part)

    @property
    def amount_display(self):
        """The amount as somebody would write it: 250, 1.5, 0.125 — never
        250.000. Stored as a fixed-scale Decimal, which is right for the
        arithmetic and wrong on a recipe card."""
        if self.amount is None:
            return ""
        trimmed = self.amount.normalize()
        # normalize() turns 250.000 into 2.5E+2, which is worse than what it
        # replaced. Re-expand anything that came back in exponent form.
        if trimmed == trimmed.to_integral_value():
            return str(trimmed.quantize(Decimal(1)))
        return str(trimmed)


def _unique_slug(model, source, pk=None):
    """A URL-safe slug that is not already taken.

    Two recipes called "Kartoffelsalat" is an ordinary Tuesday in a family
    collection, and ``unique=True`` on the column turns the second one into an
    IntegrityError from a form that looked fine — so the collision is resolved
    here instead, by counting up.
    """
    base = slugify(source)[:200] or "rezept"
    candidate = base
    n = 2
    taken = model.objects.exclude(pk=pk) if pk else model.objects.all()
    while taken.filter(slug=candidate).exists():
        candidate = f"{base}-{n}"
        n += 1
    return candidate
