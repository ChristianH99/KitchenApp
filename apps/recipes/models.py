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
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.urls import reverse
from django.utils.text import slugify
from django.utils.translation import gettext, gettext_lazy as _

from apps.pantry import units
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

    # One box of the diagram, and it may hold **more than one thing to do**.
    #
    # It was a 120-character CharField, on the reasoning that this is the text
    # inside a table cell that may be one column wide. The first real recipe
    # typed into this app broke that immediately: "Topf in Ofen stellen" and
    # "Ofen vorheizen" are two actions that happen at one point in the flow,
    # and the household wrote them as two dashed lines inside one box because
    # that is what they are. Splitting them into two steps would have been
    # wrong — they are not two boxes of the diagram — and the field simply took
    # the newline and said nothing.
    #
    # So a step is one or more lines. ``parts`` below is what pages render; the
    # column stays a single field because the lines have no identity of their
    # own — nothing points at one, they are not reordered independently, and a
    # second table would be a primary key per bullet point.
    text = models.TextField(_("step"))

    # What the oven is set to, when this step is the one that heats it. Two
    # columns rather than a sentence inside `text`, because "180 °C" is a
    # number a page can put an icon beside and a cooking view can shout — and
    # because "Ober-/Unterhitze" written by hand comes out four ways.
    oven_celsius = models.PositiveSmallIntegerField(
        _("oven temperature"), null=True, blank=True,
        help_text=_("In °C."),
    )
    oven_mode = models.CharField(_("oven mode"), max_length=12, blank=True)
    detail = models.TextField(
        _("detail"), blank=True,
        help_text=_("Shown in the cooking view when this step is the current one."),
    )

    # What the cooking view counts down. Distinct from Recipe.cook_minutes,
    # which is the whole dish: this is "45 minutes in the oven" for one box.
    #
    # Two columns rather than one of total seconds, and that is the way round it
    # is for the *editing*: almost every step of almost every recipe is a round
    # number of minutes, and asking somebody to type 2700 for "45 min" — or
    # offering one box that means minutes here and seconds there — is a mistake
    # waiting to be made in the direction that burns something. `seconds` is the
    # remainder, 0–59, and empty on nearly every row.
    #
    # Everything that *reads* a duration reads ``timer_seconds`` below, so no
    # page and no script ever adds the two up itself.
    minutes = models.PositiveIntegerField(_("duration"), null=True, blank=True,
                                          help_text=_("In minutes. Offers a timer while cooking."))
    seconds = models.PositiveSmallIntegerField(
        _("seconds"), null=True, blank=True,
        validators=[MinValueValidator(0), MaxValueValidator(59)],
        help_text=_("On top of the minutes, for a step measured more finely than that."),
    )

    # How far a *standing instruction* reaches across the table, as a 1-based
    # column range. Null on both means the whole width, which is the reference
    # diagram's "Preheat oven to 350°F" band and stays the default.
    #
    # It exists because a band across everything says the wrong thing about a
    # step that runs alongside only part of the recipe. "Heat the oven" while
    # the dough proves is parallel to *those* steps and not to the mixing that
    # came before them, and a reader who has to be told that in prose is a
    # reader the diagram has failed.
    #
    # Column *numbers*, not foreign keys to the steps they sit over. The column
    # a step occupies is derived from the tree and changes as the recipe is
    # edited, so either choice can go stale; a number is clamped into range at
    # render time (``diagram._band_span``) and the worst it can do is draw a
    # band a column too wide. A relation would need the same clamping plus a
    # migration every time somebody deleted a step.
    #
    # Meaningless on a step that has anything flowing into it — that step's
    # geometry is decided by its own subtree — and simply not read there.
    span_from = models.PositiveSmallIntegerField(null=True, blank=True)
    span_to = models.PositiveSmallIntegerField(null=True, blank=True)

    class Meta:
        verbose_name = _("step")
        verbose_name_plural = _("steps")
        ordering = ["position", "id"]

    def __str__(self):
        return self.headline

    @property
    def parts(self):
        """The things to do in this step, one per line.

        A leading "- " is stripped: it is how somebody writes a list by hand,
        and rendering it inside a `<li>` gives "- - Topf in Ofen stellen". Blank
        lines go too, so a stray Enter at the end is not an empty bullet.
        """
        lines = []
        for raw in (self.text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n"):
            line = raw.strip()
            if line.startswith(("- ", "* ", "• ")):
                line = line[2:].strip()
            elif line in ("-", "*", "•"):
                line = ""
            if line:
                lines.append(line)
        return lines

    @property
    def headline(self):
        """One line for the places that have room for exactly one.

        A page title, a select, an admin listing. Joined with " · " rather than
        truncated to the first part, because "Topf in Ofen stellen" alone is a
        different instruction from the pair.
        """
        return " · ".join(self.parts)

    @property
    def is_multipart(self):
        return len(self.parts) > 1

    OVEN_MODES = {
        # Stored short, shown in the page's language. The set is closed for the
        # same reason the portion sizes are: "Umluft", "umluft" and "Heißluft"
        # written by hand are three settings for one oven.
        "top_bottom": _("top and bottom heat"),
        "fan": _("fan"),
        "top": _("top heat"),
        "bottom": _("bottom heat"),
        "grill": _("grill"),
        "fan_grill": _("fan grill"),
    }

    @property
    def timer_seconds(self):
        """The whole duration, in seconds, or None when there is none.

        The one place the two columns are added together. Every page, and the
        cooking view's script, reads this rather than `minutes` — a template
        that reads `minutes` says "1 min" for a step somebody set to 1:30, and
        a countdown that reads it runs thirty seconds short, which is a
        difference nobody sees until something is under-baked.
        """
        total = (self.minutes or 0) * 60 + (self.seconds or 0)
        return total or None

    @property
    def timer_display(self):
        """The duration as a clock reads it — "45:00", "1:30", "0:20"."""
        total = self.timer_seconds
        if not total:
            return ""
        return "%d:%02d" % divmod(total, 60)

    @property
    def duration_label(self):
        """The duration in words, for the places that show it beside a step.

        Three shapes, because "0:45 min" for three quarters of a minute and
        "45:00 min" for three quarters of an hour are both worse than what
        somebody would write by hand.
        """
        total = self.timer_seconds
        if not total:
            return ""
        minutes, seconds = divmod(total, 60)
        if not seconds:
            return gettext("%(n)s min") % {"n": minutes}
        if not minutes:
            return gettext("%(n)s s") % {"n": seconds}
        return gettext("%(m)s:%(s)s min") % {"m": minutes, "s": "%02d" % seconds}

    @property
    def oven_mode_label(self):
        return self.OVEN_MODES.get(self.oven_mode, self.oven_mode)

    @property
    def heats_the_oven(self):
        """Whether this step is the one that sets the oven."""
        return bool(self.oven_celsius or self.oven_mode)


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

    # A code from apps/pantry/units.py, not free text. 30 characters was what a
    # free-text column needed; a code is at most a handful, and the width is
    # taken from the catalogue so a unit added there cannot outgrow it.
    unit = models.CharField(_("unit"), max_length=units.MAX_CODE_LENGTH, blank=True)

    name = models.CharField(_("ingredient"), max_length=120)

    # The catalogue row this line is about. Nullable, and it has to stay that
    # way: every line written before the catalogue existed has none, a recipe
    # pasted in at midnight should not be blocked on tidying one up, and the
    # name column remains what the recipe actually *says* — "festkochende
    # Kartoffeln" is the line, "Kartoffel" is the substance. Only lines that
    # have one take part in the pantry matching, and the rest are reported as
    # "cannot tell" rather than as missing.
    ingredient = models.ForeignKey(
        "pantry.Ingredient", verbose_name=_("in the catalogue"),
        null=True, blank=True, on_delete=models.SET_NULL, related_name="used_in",
    )

    note = models.CharField(_("note"), max_length=120, blank=True,
                            help_text=_("e.g. “finely chopped”, “at room temperature”."))

    optional = models.BooleanField(
        _("optional"), default=False,
        help_text=_("The recipe works without it."),
    )

    # "Salz", "Pfeffer", "etwas Öl" — a real line with no quantity, and the
    # reason the amount stays nullable. It is a *flag* rather than simply an
    # empty amount because the form now refuses to save a line whose amount was
    # left blank: without somewhere to say "there isn't one", that rule would
    # make salt unrecordable, and the way out people would find is typing 1.
    no_amount = models.BooleanField(
        _("no fixed amount"), default=False,
        help_text=_("To taste — salt, pepper, a little oil."),
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
        return " ".join(
            str(part) for part in (self.amount_display, self.unit_label, self.name) if part
        )

    @property
    def unit_label(self):
        """The unit as this page's language writes it — "EL" or "tbsp".

        The column holds a language-neutral code, so nothing may render
        ``unit`` directly; apps/pantry/units.py says why the two are separate.
        A value the catalogue does not know is shown exactly as it was typed,
        which is what keeps a line written before the catalogue readable.
        """
        return units.label(self.unit)

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
