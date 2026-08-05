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

The same argument, one level up, is why a method can be a **tree** here rather
than only a wall of prose. ``RecipeStep`` rows point at the step they feed into
and ingredients point at the step that consumes them, which is the structure a
Cooking-for-Engineers diagram draws — and the structure the guided cooking view
walks. ``Recipe.instructions`` stays exactly as it was: the diagram says what
combines with what, the prose says how, and neither replaces the other.

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

    def get_cook_url(self):
        return reverse("recipes:cook", args=[self.slug])

    @property
    def total_minutes(self):
        """Preparation plus cooking, or None when neither is recorded.

        None rather than 0, because "nought minutes" and "nobody wrote it down"
        are different claims and only one of them belongs on a recipe card.
        """
        parts = [m for m in (self.prep_minutes, self.cook_minutes) if m]
        return sum(parts) if parts else None


class RecipeStep(models.Model):
    """One box in the diagram: an operation, and what it is done to.

    A step's *inputs* are the ingredients whose ``step`` points here plus the
    steps whose ``parent`` points here — so the recipe is a tree read from the
    leaves inwards, which is exactly what the Cooking-for-Engineers table draws
    and what ``apps/recipes/diagram.py`` lays out.

    ``parent`` is SET_NULL rather than CASCADE on purpose. Deleting "mix" must
    not silently delete "bake" and everything under it; the children become
    roots of their own and the loss is visible on the page instead of being a
    recipe that quietly lost half its method.
    """

    recipe = models.ForeignKey(Recipe, on_delete=models.CASCADE, related_name="steps")
    parent = models.ForeignKey(
        "self", verbose_name=_("feeds into"), null=True, blank=True,
        on_delete=models.SET_NULL, related_name="children",
    )

    # Order among the siblings feeding one parent — and, for a root, the order
    # the blocks are stacked in. The form index is what writes it, so the
    # sequence on the page and the sequence in the diagram cannot drift apart.
    position = models.PositiveSmallIntegerField(default=0)

    # Short on purpose: this is the text inside a table cell that may be one
    # column wide. "verrühren", "bei 180 °C 45 min backen". Anything longer is
    # what `detail` and the recipe's own instructions are for.
    text = models.CharField(_("step"), max_length=120)
    detail = models.TextField(
        _("detail"), blank=True,
        help_text=_("Shown in the cooking view when this step is the current one."),
    )

    # What the cooking view counts down. Distinct from Recipe.cook_minutes,
    # which is the whole dish: this is "45 minutes in the oven" for one box.
    minutes = models.PositiveIntegerField(_("duration"), null=True, blank=True,
                                          help_text=_("In minutes. Offers a timer while cooking."))

    class Meta:
        verbose_name = _("step")
        verbose_name_plural = _("steps")
        ordering = ["position", "id"]

    def __str__(self):
        return self.text


class RecipeIngredient(models.Model):
    """One line of the ingredient list, for ``recipe.servings`` servings.

    ``amount`` is nullable, and that is not laziness: "Salz", "Pfeffer", "etwas
    Öl" are real ingredient lines with no quantity, and forcing a zero onto
    them would print "0 g Salz". A line with no amount simply does not scale.

    Two of the columns below make a row something other than a plain line.
    ``optional`` is the difference between "you need this" and "nice if you
    have it" — a shopping list that cannot tell them apart is a shopping list
    that buys saffron every week. And ``alternative_for`` points at *another
    ingredient row*: a substitute is not a note, it is a second full line with
    its own amount and unit, because "200 g Butter" is replaced by "180 g
    Margarine" and not by the word "margarine".
    """

    recipe = models.ForeignKey(Recipe, on_delete=models.CASCADE, related_name="ingredients")

    # Which box in the diagram consumes this line. Null is the ordinary state
    # for a recipe that has no diagram — every recipe written before this
    # existed is one — and those lines simply render as the plain list they
    # always were.
    step = models.ForeignKey(
        RecipeStep, verbose_name=_("used in"), null=True, blank=True,
        on_delete=models.SET_NULL, related_name="ingredients",
    )

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

    optional = models.BooleanField(
        _("optional"), default=False,
        help_text=_("The recipe works without it."),
    )

    # A substitute for another line of the same recipe. Self-referential rather
    # than a table of its own: a substitute needs every column a line needs, and
    # a second model would be the same five fields with a different name on it.
    alternative_for = models.ForeignKey(
        "self", verbose_name=_("alternative for"), null=True, blank=True,
        on_delete=models.CASCADE, related_name="alternatives",
    )

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


class PortionSize(models.TextChoices):
    """How much of what was made one person actually ate.

    "It serves four" is a guess somebody made once; this is the household's own
    measurement of the same dish, and the two disagree often enough to be worth
    recording. The sizes are a closed set for the same reason the CSS scales
    are: free text would give "gross", "große Portion" and "XL" for one thing,
    and nothing could then be added up.
    """

    LARGE = "large", _("large portion")
    REGULAR = "regular", _("portion")
    SMALL = "small", _("small portion")
    CHILD = "child", _("child’s portion")
    TOGO = "togo", _("portion to take away")


# What each size is worth as a fraction of one ordinary portion. Deliberately
# coarse — these turn a count of portions back into a comparable number, and a
# household's "large" is not a measurement to two decimal places. A portion put
# in a box for tomorrow is a whole portion: it was made, and somebody eats it.
PORTION_WEIGHTS = {
    PortionSize.LARGE: Decimal("1.5"),
    PortionSize.REGULAR: Decimal("1"),
    PortionSize.SMALL: Decimal("0.6"),
    PortionSize.CHILD: Decimal("0.5"),
    PortionSize.TOGO: Decimal("1"),
}


class CookLog(models.Model):
    """One occasion on which somebody actually cooked this.

    Three things it records that a recipe cannot: how long it really took, how
    many servings it was scaled to on the night, and how far that went. The
    third is the one the household asked for — "it says four, it fed two of us
    and left a box for Thursday" is the useful sentence, and it needs the
    portions rather than a number of people.
    """

    recipe = models.ForeignKey(Recipe, on_delete=models.CASCADE, related_name="cook_logs")
    cooked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, verbose_name=_("cooked by"),
        null=True, blank=True, on_delete=models.SET_NULL, related_name="cook_logs",
    )
    cooked_at = models.DateTimeField(_("cooked on"), auto_now_add=True)

    # What the recipe was scaled to that evening — not Recipe.servings, which
    # is what the amounts are written for. Without it the portion counts below
    # cannot be compared between two evenings that cooked different quantities.
    servings_made = models.PositiveSmallIntegerField(_("servings made"), default=1)

    minutes = models.PositiveIntegerField(
        _("time taken"), null=True, blank=True,
        help_text=_("In minutes. Measured by the cooking view, or typed in."),
    )
    notes = models.TextField(_("notes"), blank=True)

    class Meta:
        verbose_name = _("cooking")
        verbose_name_plural = _("cookings")
        ordering = ["-cooked_at"]
        indexes = [
            models.Index(fields=["recipe", "-cooked_at"], name="cooklog_recent_idx"),
        ]

    def __str__(self):
        return f"{self.recipe_id} @ {self.cooked_at:%Y-%m-%d}"

    @property
    def portions_total(self):
        """What it fed, counted in ordinary portions.

        A Decimal rather than a float because the weights are Decimals and
        mixing the two is how "2.9999999999999996 portions" reaches a page.
        """
        total = Decimal(0)
        for portion in self.portions.all():
            total += PORTION_WEIGHTS.get(portion.size, Decimal(1)) * portion.count
        return total

    @property
    def portions_display(self):
        """"2 × large portion, 1 × portion to take away", in the page's language."""
        return [
            (portion.count, portion.get_size_display())
            for portion in self.portions.all() if portion.count
        ]


class CookPortion(models.Model):
    """How many portions of one size came out of one cooking."""

    log = models.ForeignKey(CookLog, on_delete=models.CASCADE, related_name="portions")
    size = models.CharField(_("size"), max_length=10, choices=PortionSize.choices)
    count = models.PositiveSmallIntegerField(_("how many"), default=0)

    class Meta:
        verbose_name = _("portion")
        verbose_name_plural = _("portions")
        ordering = ["size"]
        constraints = [
            # One row per size per cooking. Two "large portion" rows would both
            # be right and the page would show the size twice.
            models.UniqueConstraint(fields=["log", "size"], name="one_row_per_portion_size"),
        ]

    def __str__(self):
        return f"{self.count} × {self.get_size_display()}"


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
