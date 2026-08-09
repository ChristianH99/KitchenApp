"""Forms for adding and editing a recipe, and for logging one that was cooked.

The part worth reading before changing anything is **how the diagram is wired
up**. An ingredient says which step consumes it and a step says which step it
feeds into, so both are foreign keys — and on a brand-new recipe neither target
has a primary key yet, because nothing has been saved. Referring to steps by pk
in the form therefore cannot work for the case that matters most (typing a
recipe in for the first time), and the version that half-works — save the steps,
re-render, then let somebody wire them up on a second pass — is two round trips
for one thought.

So the form refers to a step by its **index in the formset**: form 0, form 1,
form 2. The index exists before anything is saved, it is what the page's
canvas is built from, and ``wire_diagram()`` below turns indices into
foreign keys once the objects exist. An index that names a deleted or
non-existent form resolves to "unassigned" rather than to an error: the only
way to produce one is by hand-crafting a POST, and dropping the reference loses
nothing while an exception would lose the whole recipe.

The second thing worth reading is **why the order is a field of its own** and
not the form index. Dragging a row to a new place would otherwise mean
renumbering the whole range — and renumbering moves rows across the boundary at
``INITIAL_FORMS``, below which Django treats a form as an edit of an existing
object and looks its primary key up out of the POST. A brand-new row dragged
above an existing one lands there with no primary key to find, and
``save_existing_objects`` skips it without a word: the ingredient somebody just
typed is silently not saved. ``_OrderField`` keeps the index range exactly as
the server rendered it and says the order separately.
"""

from django import forms
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.forms import inlineformset_factory
from django.utils.translation import gettext_lazy as _

from apps.pantry.forms import UnitField
from apps.pantry.models import Ingredient
from apps.recipes.images import clean_upload
from apps.recipes.models import (
    CookLog, CookPortion, PortionSize, Recipe, RecipeIngredient, RecipeStep, Tag,
)


class RecipeForm(forms.ModelForm):
    """The recipe itself.

    Tags are a **text field**, not a multi-select. A household types "Suppe,
    vegetarisch, schnell" while it is thinking about the recipe; a multi-select
    asks it to first go somewhere else and create three Tag objects, which is
    the kind of small friction that ends with everything untagged. New names
    are created on save, existing ones matched case-insensitively so "Suppe"
    and "suppe" do not become two labels for one thing.
    """

    tags_text = forms.CharField(
        label=_("tags"), required=False,
        help_text=_("Separated by commas, e.g. “soup, vegetarian, quick”."),
    )

    class Meta:
        model = Recipe
        fields = [
            "title", "description", "servings", "prep_minutes", "cook_minutes",
            "instructions", "image", "source", "source_url", "notes",
        ]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 3}),
            "instructions": forms.Textarea(attrs={"rows": 12}),
            "notes": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # A recipe that is barely more than a title is still worth keeping —
        # "Nudeln mit Pesto" pinned to the tag "schnell" is a real entry. Only
        # the title and the servings are actually required.
        for name, field in self.fields.items():
            if name not in ("title", "servings"):
                field.required = False
        if self.instance.pk:
            self.fields["tags_text"].initial = ", ".join(
                self.instance.tags.values_list("name", flat=True)
            )

    def clean_servings(self):
        servings = self.cleaned_data.get("servings")
        if servings is not None and servings < 1:
            # Every ingredient amount is divided by this to scale. Zero is a
            # ZeroDivisionError on the detail page, which is a 500 for a value
            # somebody was allowed to type.
            raise ValidationError(_("A recipe is for at least one serving."))
        return servings

    def clean_image(self):
        """Verify and resize here rather than trusting the upload.

        ``ImageField`` checks that the bytes are an image; it does not bound
        the size, choose the stored filename or shrink a 6 MB phone photograph.
        apps/recipes/images.py does all four, and it is the only door.
        """
        uploaded = self.cleaned_data.get("image")
        # Falsy when cleared, and a plain FieldFile (not an upload) when the
        # form was submitted without touching the existing photograph — in
        # which case there is nothing to re-process.
        if not uploaded or not hasattr(uploaded, "content_type"):
            return uploaded
        return clean_upload(uploaded)

    def clean_tags_text(self):
        raw = self.cleaned_data.get("tags_text") or ""
        names, seen = [], set()
        for part in raw.split(","):
            name = " ".join(part.split())[:60]
            if name and name.casefold() not in seen:
                seen.add(name.casefold())
                names.append(name)
        return names

    def save(self, commit=True):
        recipe = super().save(commit=commit)
        if commit:
            self.save_tags(recipe)
        return recipe

    def save_tags(self, recipe):
        tags = []
        for name in self.cleaned_data.get("tags_text", []):
            # Matched case-insensitively, created with the capitalisation that
            # was typed. get_or_create on `name` alone would make "Suppe" and
            # "suppe" two tags that look identical in the sidebar.
            tag = Tag.objects.filter(name__iexact=name).first()
            if tag is None:
                tag = Tag.objects.create(name=name)
            tags.append(tag)
        recipe.tags.set(tags)


class RecipeOwnerForm(forms.ModelForm):
    """Handing a recipe to somebody else.

    A form of one field, kept apart from ``RecipeForm`` on purpose. Everything
    on that form is a statement about the *food*; this is a statement about who
    may change it, and the person pressing it usually loses the right to press
    anything on this recipe again. A select sitting among the servings and the
    tags is one somebody changes by accident on the way to saving a typo fix.

    Everybody active is offered — a local account and a Synology one are the
    same row here, and a household that signs in over SSO would otherwise have
    nobody to give a recipe to. Inactive accounts are not: handing a recipe to
    somebody who has left is a way of losing it, and the People page is where
    that state is managed.

    ``required``, and that is the guard rail. "Nobody" is a value the column
    accepts — it is what an account being deleted leaves behind — but choosing
    it here would be a member of the household removing a recipe from everybody
    who is not staff, which is not a transfer.
    """

    class Meta:
        model = Recipe
        fields = ["owner"]
        # The same small inline control the recipe form's cards use. No new
        # component and therefore no new step on any of the scales in
        # static/css/main.css — this is a label and a select on one line, which
        # is a thing the app already draws.
        widgets = {"owner": forms.Select(attrs={"class": "row-extra-select"})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        field = self.fields["owner"]
        field.required = True
        field.empty_label = None
        field.queryset = (
            get_user_model().objects.filter(is_active=True)
            .order_by("first_name", "last_name", "username")
        )
        # A Synology account's username is the provider's `sub` — a stable
        # opaque string. Offering that in a dropdown is offering a row of
        # gibberish, so the name DSM sent comes first and the e-mail second;
        # the username is the last resort, which is where a local account
        # without a name lands.
        field.label_from_instance = person_label

    def clean_owner(self):
        owner = self.cleaned_data.get("owner")
        # The queryset already refuses an inactive account; this is the message
        # for the one case somebody will actually hit, which is choosing
        # themselves and wondering why the page came back.
        if owner and self.instance.owner_id == owner.pk:
            raise ValidationError(_("This recipe is already theirs."))
        return owner


def person_label(person):
    """What to call somebody in a dropdown: their name, their e-mail, or their
    username — in that order, because the last of those is an opaque ``sub``
    for every account that came in over SSO."""
    return person.get_full_name().strip() or person.email or person.get_username()


class _StructureField(forms.IntegerField):
    """A hidden number the *canvas* writes, saying where this row belongs.

    Hidden, because the control somebody actually uses is the canvas in
    static/js/recipe_diagram.js: an ingredient is dropped onto the step that
    uses it and the number is written back here. A server-rendered ``<select>``
    would be stale the moment a row is added, which on this page is most of the
    time.

    ``has_changed`` is always False, and that is the load-bearing part.

    A formset validates and saves an extra row only when something in it
    changed, and where a row *sits* is not something somebody typed. Without
    this, arranging the canvas around a blank card counts as editing that card:
    dragging a line past it renumbers it, and "+ Ingredient here" stamps a step
    onto it before a single letter has been entered. Either one is enough to
    make the formset save a nameless ingredient — a line on the recipe with no
    name, no amount and no way to tell where it came from.

    The consequence is that a row whose only difference is structural is not
    saved by ``formset.save()`` at all. That is why ``wire_diagram`` below
    writes the relations *and* the order itself, in a pass of its own, over the
    rows that survived the save.
    """

    widget = forms.HiddenInput

    def __init__(self, **kwargs):
        kwargs.setdefault("required", False)
        kwargs.setdefault("min_value", 0)
        super().__init__(**kwargs)

    def has_changed(self, initial, data):
        return False


class _IndexField(_StructureField):
    """A reference to another row of this page, by its position in the formset."""


class _OrderField(_StructureField):
    """Where this row sits in the order somebody arranged on the page."""

    def __init__(self, **kwargs):
        # The column is a PositiveSmallIntegerField. No page can produce a
        # number near this; a hand-made POST can, and unbounded it would be a
        # database error on save rather than a validation error on the form.
        kwargs.setdefault("max_value", 32767)
        super().__init__(**kwargs)


class RecipeIngredientForm(forms.ModelForm):
    # Which step in the diagram uses this line, and — for a substitute — which
    # line it stands in for. See the module docstring for why these are indices.
    step_index = _IndexField()
    alt_index = _IndexField()
    position = _OrderField()

    # The unit is a closed set now, not free text. apps/pantry/units.py says
    # why, and apps/pantry/forms.py::UnitSelect is what keeps a value written
    # before that from being silently rewritten on the next save.
    unit = UnitField(label=_("unit"))

    # Which catalogue row this line is about, written by the autosuggest in
    # static/js/ingredient_suggest.js. Hidden, because the control somebody
    # uses is the name field beside it: picking "Butter" from the list is what
    # sets this, and a second visible select saying the same thing again is a
    # question nobody should be asked twice.
    #
    # queryset rather than a plain integer so a hand-made POST naming a row
    # that does not exist is a validation error rather than an IntegrityError
    # — and `required=False` because a typed name that matches nothing is the
    # ordinary case, not a failure.
    ingredient = forms.ModelChoiceField(
        queryset=Ingredient.objects.all(), required=False,
        widget=forms.HiddenInput,
    )

    # The canvas gives each field a cell rather than a labelled column, so the
    # caption has to travel with the control. Set here rather than in `widgets`
    # so each field keeps the widget its model field chose — overriding
    # `amount` with a plain NumberInput would drop the decimal `step` that lets
    # a phone offer 1.5 without complaining.
    PLACEHOLDERS = {
        "amount": _("Amount"),
        "name": _("Ingredient"),
        "note": _("Note"),
    }

    class Meta:
        model = RecipeIngredient
        fields = ["amount", "unit", "name", "ingredient", "note", "optional", "no_amount"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.required = False
        for name, text in self.PLACEHOLDERS.items():
            self.fields[name].widget.attrs.setdefault("placeholder", text)
        # Set here rather than written into the template, so the markup keeps
        # using {{ row.name }} and cannot drift from the widget the field
        # chose. Rendered before any script runs, which matters: a field that
        # only announces itself as a combobox once JavaScript has loaded
        # announces itself as nothing to somebody who arrives mid-load.
        self.fields["name"].widget.attrs.update({
            "autocomplete": "off",
            "role": "combobox",
            "aria-expanded": "false",
            "aria-autocomplete": "list",
            "data-ingredient-input": True,
        })
        # The column is a fixed-scale Decimal, so one and a half kilos renders
        # as "1.500" — and a browser in a German locale then draws that number
        # input as "1,500", which reads as fifteen hundred. The value is
        # correct either way (`valueAsNumber` is 1.5) and it round-trips; it
        # simply looks like a different quantity. Trimmed to what somebody
        # would have typed, which is what `amount_display` does on the page.
        if self.instance.pk and self.instance.amount is not None:
            self.initial["amount"] = self.instance.amount_display

    def clean(self):
        """What makes a line complete enough to save.

        The formset renders a blank row so there is always somewhere to type,
        and a row with nothing in it is dropped by ``empty_permitted``. The
        three rules below are about the rows that do have something in them.

        **A line needs a name.** "250 g" and nothing else is a row somebody
        abandoned, and saving it puts a nameless line on the recipe that reads
        as a bug.

        **A line needs an amount, or an explicit statement that it has none.**
        This is the rule the household asked for — a recipe saved with the
        butter left blank is one that cannot be shopped for or scaled, and the
        blank looks exactly like a deliberate "to taste". So "to taste" now has
        somewhere to be said: the ``no_amount`` box. Without that escape hatch
        the rule would make "Salz" unrecordable, and the way round it people
        would find is typing 1, which is worse than the blank it replaced.

        **The two cannot both be true.** An amount *and* "no fixed amount" is a
        line that contradicts itself, and the scaler would have to pick one.
        """
        data = super().clean()
        name = (data.get("name") or "").strip()
        amount = data.get("amount")
        no_amount = data.get("no_amount")

        has_other = any(
            data.get(f) not in (None, "", False)
            for f in ("amount", "unit", "note", "optional", "no_amount")
        )
        if has_other and not name:
            raise ValidationError(_("Give this line an ingredient, or clear it."))

        if amount is not None and amount < 0:
            raise ValidationError(_("An amount cannot be negative."))

        if name and amount is None and not no_amount:
            raise ValidationError(_(
                "How much %(name)s? Give an amount, or tick “no fixed amount” "
                "for something added to taste."
            ) % {"name": name})

        if amount is not None and no_amount:
            raise ValidationError(
                _("This line has an amount, so “no fixed amount” cannot also be true.")
            )
        return data


class _SpanField(_StructureField):
    """One end of the column range a standing instruction covers.

    A ``_StructureField`` like the rest: where a band reaches to is a fact
    about the *layout*, not something typed into the row, so it must not make
    a blank card count as edited. 1-based, and bounded here only against a
    hand-made POST — ``diagram._band_span`` clamps it into the recipe's actual
    width at render, because the number of columns is not knowable at save
    time.
    """

    def __init__(self, **kwargs):
        kwargs.setdefault("min_value", 1)
        kwargs.setdefault("max_value", 64)
        super().__init__(**kwargs)


class RecipeStepForm(forms.ModelForm):
    """One box in the diagram."""

    parent_index = _IndexField()
    position = _OrderField()
    span_from = _SpanField()
    span_to = _SpanField()

    PLACEHOLDERS = {
        "text": _("What happens here"),
        # No placeholder on `minutes`: the card puts a clock beside it and the
        # word "min" after it, so a placeholder saying "min" as well showed the
        # same word twice, once greyed and once not.
        "detail": _("Detail — shown while cooking"),
    }

    class Meta:
        model = RecipeStep
        fields = ["text", "detail", "minutes", "seconds", "oven_celsius", "oven_mode"]
        widgets = {
            "detail": forms.Textarea(attrs={"rows": 2}),
            # A textarea for a 120-character CharField, because on the canvas
            # this field *is* the box the diagram is read by, and a box a step
            # column wide shows about twelve characters of an input before it
            # scrolls: "ausrollen, dachziegelartig belegen" reads as
            # "ausrollen, da". Wrapping is what the rendered cell does with the
            # same text, so the editor now shows what the page will.
            "text": forms.Textarea(attrs={"rows": 2}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.required = False
        for name, text in self.PLACEHOLDERS.items():
            self.fields[name].widget.attrs.setdefault("placeholder", text)
        # Hidden, and written by the panel static/js/recipe_diagram.js offers
        # when somebody types "vorheizen" or "preheat" into a step. A pair of
        # ordinary fields on every card would put an oven temperature box on
        # the twenty steps of a recipe that have nothing to do with an oven.
        self.fields["oven_celsius"].widget = forms.HiddenInput()
        self.fields["oven_mode"].widget = forms.HiddenInput()
        # The two halves of one duration, so the card can put them either side
        # of a colon. `max` is what stops "1 min 90 s" being typed rather than
        # "2:30" — the model's validators refuse it either way, but a spinner
        # that will not go past 59 says so before anybody presses Save.
        self.fields["seconds"].widget.attrs.setdefault("min", 0)
        self.fields["seconds"].widget.attrs.setdefault("max", 59)
        self.fields["minutes"].widget.attrs.setdefault("min", 0)

    def clean_seconds(self):
        seconds = self.cleaned_data.get("seconds")
        # 0 and "nothing typed" are the same thing here, and storing the one
        # would make `timer_seconds` say a step has a duration when it has not.
        return seconds or None

    def clean_minutes(self):
        return self.cleaned_data.get("minutes") or None

    def clean_oven_celsius(self):
        celsius = self.cleaned_data.get("oven_celsius")
        # The same range the box on the card accepts, so nothing it lets
        # somebody type is refused here — that used to be 30 at the bottom while
        # the control was a list of temperatures nobody could go outside of. The
        # column is a PositiveSmallIntegerField, so the upper bound is what
        # keeps 70000 a form error rather than a database one.
        if celsius is not None and not (0 <= celsius <= 500):
            raise ValidationError(_("An oven temperature is between 0 and 500 °C."))
        # Zero is "no temperature", not a setting — and storing it would be
        # worse than dropping it: `heats_the_oven` reads the column as a
        # boolean, so a step at 0 °C would keep the number and stop drawing as
        # an oven step at all.
        return celsius or None

    def clean_oven_mode(self):
        mode = (self.cleaned_data.get("oven_mode") or "").strip()
        # A closed set, so "Umluft", "umluft" and "Heißluft" cannot become three
        # settings for one oven. Anything else is dropped rather than refused:
        # the only way to produce one is by hand, and losing the mode is a
        # smaller loss than losing the recipe.
        return mode if mode in RecipeStep.OVEN_MODES else ""

    def clean(self):
        """A step is its label. Everything else on the row describes it.

        The mirror of the ingredient rule below: a duration or a detail with no
        operation to attach them to is a row somebody abandoned half-typed, and
        saving it puts an empty box in the middle of the diagram.
        """
        data = super().clean()
        text = (data.get("text") or "").strip()
        has_other = (
            data.get("minutes") is not None
            or data.get("seconds") is not None
            or (data.get("detail") or "").strip()
            or data.get("oven_celsius") is not None
            or data.get("oven_mode")
        )
        if has_other and not text:
            raise ValidationError(_("Give this step a name, or clear it."))
        return data


class BaseOrderedFormSet(forms.BaseInlineFormSet):
    """A formset whose rows carry their own order.

    One class for both of this page's formsets. The version with the logic
    copied and renamed is how the two drift apart: the fix lands in one of them
    and the broken one is whichever formset nobody was looking at that week.
    """

    def add_fields(self, form, index):
        super().add_fields(form, index)
        # The rows are rendered in stored order (the models order by
        # ``position``), so the index is the right starting value — and it is
        # what a POST that never touched the canvas falls back to.
        form.initial.setdefault("position", index or 0)

    def save_new(self, form, commit=True):
        obj = super().save_new(form, commit=False)
        obj.position = self._position_for(form)
        if commit:
            obj.save()
        return obj

    def save_existing(self, form, obj, commit=True):
        obj = super().save_existing(form, obj, commit=False)
        obj.position = self._position_for(form)
        if commit:
            obj.save()
        return obj

    def _position_for(self, form):
        """Keep the order the page shows.

        The row says where it sits; the form index is the fallback for a POST
        that carries no ``position`` at all, which is every request made
        without the canvas — the test suite's, and a browser with no
        JavaScript.
        """
        given = form.cleaned_data.get("position")
        return self.forms.index(form) if given is None else given


# **No** blank rows. This was one each, and before that four and three.
#
# On a list a spare row is an invitation to type. On the canvas it is a *cell*:
# a blank ingredient card sits in the "not in any step" tray and a blank step
# draws as a band, and neither can be got rid of — deleting it makes the formset
# render another, so the household reported "I always see one empty step and
# ingredient below the diagram, no matter how often I delete it". They were
# right, and the answer is not to render one.
#
# What replaces it is the "+" that appears between the tiles on hover, which
# mints a row *where it is wanted* rather than at the end of a list somebody
# then has to drag it out of.
IngredientFormSet = inlineformset_factory(
    Recipe, RecipeIngredient,
    form=RecipeIngredientForm,
    formset=BaseOrderedFormSet,
    extra=0,
    can_delete=True,
)

StepFormSet = inlineformset_factory(
    Recipe, RecipeStep,
    form=RecipeStepForm,
    formset=BaseOrderedFormSet,
    extra=0,
    can_delete=True,
)


# --------------------------------------------------------------------------
# Is the diagram actually joined up?
# --------------------------------------------------------------------------

def validate_structure(step_formset, ingredient_formset):
    """Refuse a recipe whose parts are not attached to each other.

    This is a *cross-formset* rule, which is why it is a function and not a
    ``clean()``: an ingredient's ``step_index`` names a form of the other
    formset, and neither one can see the other from inside its own validation.
    The views call it once both have passed their own checks.

    Errors are attached to the individual forms rather than collected into a
    banner, because "something is not connected" at the top of a page with
    twenty cards on it is not an error message, it is a puzzle. The offending
    card says so itself.

    Three rules, and the third is the one with a real judgement in it.

    **An ingredient must go into a step** — but only once the recipe has any
    steps at all. A recipe that is a title and a list of ingredients is a
    perfectly good recipe and always has been; it is a recipe with a method
    where one line was left out of it that is the mistake.

    **A substitute is exempt.** It takes its place from the line it replaces
    and deliberately has no step of its own.

    **A second root that produces something is a disconnected branch.** A step
    with nothing feeding it and nothing it feeds into is a *standing
    instruction* — "heat the oven" — and is meant to stand alone. But a root
    that has ingredients going into it, or other steps feeding it, is half a
    recipe that never joins the other half: exactly the shape of the Brot case
    where "verkneten" was typed but never wired to the two doughs. One such
    root is the finished dish. Two is an unfinished edit.

    Returns True when the page is consistent.
    """
    steps = _live(step_formset, "text")
    lines = _live(ingredient_formset, "name")
    if not steps and not lines:
        return True

    step_indices = set(steps)
    ok = True

    # --- a step something goes into needs to say what it is ---------------
    #
    # A step with no text is invisible to ``_live``, which is right for a card
    # nobody has typed into yet and wrong the moment something points at it.
    # The complaint then landed on the *ingredient* — "put this into one of the
    # steps", about a line that was already in one — while the box the fault
    # was actually in said nothing at all. The step is what is unfinished, so
    # the step is what says so.
    unnamed = {}
    removed = set(step_formset.deleted_forms)
    for index, form in enumerate(step_formset.forms):
        if index in steps or form in removed or not form.is_valid():
            continue
        unnamed[index] = form

    # Only the ones being relied on. A blank card with nothing pointing at it
    # is still where the next step gets typed, and refusing the page for it
    # would make an empty canvas unsaveable.
    wanted = set()
    for form in lines.values():
        if form.cleaned_data.get("step_index") in unnamed:
            wanted.add(form.cleaned_data["step_index"])
    for index, form in steps.items():
        parent = form.cleaned_data.get("parent_index")
        if parent in unnamed and parent != index:
            wanted.add(parent)

    for index in sorted(wanted):
        unnamed[index].add_error("text", _(
            "Write what happens in this step — something already goes into it."
        ))
        ok = False

    # --- every line goes somewhere ---------------------------------------
    if steps:
        for index, form in lines.items():
            if form.cleaned_data.get("alt_index") in lines:
                continue                      # a substitute; it has no step
            # Assigned to a step that has no text yet: the step is carrying
            # that error, and saying it twice in two places sends somebody
            # looking for a second fault that is not there.
            if form.cleaned_data.get("step_index") in wanted:
                ok = False
                continue
            if form.cleaned_data.get("step_index") not in step_indices:
                form.add_error(None, _(
                    "Put this into one of the steps — every ingredient has to "
                    "be used somewhere once the recipe has a method."
                ))
                ok = False

    # --- every step is part of one tree ----------------------------------
    children = {index: [] for index in steps}
    roots = []
    for index, form in steps.items():
        # Past the rows that have been removed — see ``_resolve_parent``. A step
        # whose parent was deleted is still joined to the recipe through what
        # that parent fed, and refusing the page for it would be refusing an
        # edit that took something out of the middle of a chain.
        parent = _resolve_parent(
            step_formset, form.cleaned_data.get("parent_index"), step_indices, index
        )
        if parent in step_indices:
            children[parent].append(index)
        else:
            roots.append(index)

    fed_by_lines = set()
    for form in lines.values():
        target = form.cleaned_data.get("step_index")
        if target in step_indices:
            fed_by_lines.add(target)

    producing = [
        index for index in roots
        if children[index] or index in fed_by_lines
    ]
    if len(producing) > 1:
        # The last one is left alone: it is the one the layout treats as the
        # finished dish, and marking every branch including the good one is
        # how an error message stops telling anybody what to do.
        for index in producing[:-1]:
            steps[index].add_error(None, _(
                "This step is not joined to the rest of the recipe. Say what "
                "it feeds into — with the “feeds into” box in the step list, "
                "or by dragging it onto that step on the diagram."
            ))
        ok = False

    return ok


def _live(formset, required_field):
    """``{index: form}`` for the rows that are really there.

    A row is live when it has not been deleted and has something in the field
    that makes it a row at all — a name for a line, a label for a step. The
    blank card the formset always renders is not a mistake to report; it is
    where the next thing gets typed.
    """
    deleted = set(formset.deleted_forms)
    out = {}
    for index, form in enumerate(formset.forms):
        if form in deleted or not form.is_valid():
            continue
        if (form.cleaned_data.get(required_field) or "").strip():
            out[index] = form
    return out


# --------------------------------------------------------------------------
# Turning row indices into foreign keys
# --------------------------------------------------------------------------

def prime_diagram_indices(step_formset, ingredient_formset):
    """Fill the hidden index fields in from the saved relations.

    The reverse of ``wire_diagram``: the database holds foreign keys, the page
    works in row indices, and this is the translation on the way out. Called
    before rendering, for both a fresh form (where it does nothing) and an edit
    (where without it every existing recipe comes back with an empty diagram
    and saving flattens it).

    The order needs no translation in this direction: the rows are queried in
    ``position`` order, so the index they are rendered at already *is* their
    place, which is what ``add_fields`` seeds the hidden field with.
    """
    steps = _index_of_saved(step_formset)
    lines = _index_of_saved(ingredient_formset)

    for form in step_formset.forms:
        if form.instance.parent_id in steps:
            form.initial["parent_index"] = steps[form.instance.parent_id]
        # The span is a real column on the model, but the form field is a
        # plain one — it is deliberately not in ``Meta.fields``, so that
        # ``formset.save()`` cannot write it and ``_apply_spans`` is the only
        # thing that does. Nothing fills its initial value in for us.
        if form.instance.span_from is not None:
            form.initial["span_from"] = form.instance.span_from
        if form.instance.span_to is not None:
            form.initial["span_to"] = form.instance.span_to
    for form in ingredient_formset.forms:
        if form.instance.step_id in steps:
            form.initial["step_index"] = steps[form.instance.step_id]
        if form.instance.alternative_for_id in lines:
            form.initial["alt_index"] = lines[form.instance.alternative_for_id]


def wire_diagram(step_formset, ingredient_formset):
    """Resolve every ``*_index`` into a relation, after both formsets have saved.

    Runs second on purpose: an index can only be turned into a foreign key once
    the row it names has a primary key, which for a new recipe is only true
    after ``formset.save()``. Everything here is an UPDATE on a row that was
    just written, and only on the rows whose relations actually changed.

    The order is applied here too rather than being left to ``save()``. It is
    one call so it cannot be half-made: a page that saved the structure and not
    the arrangement would come back with every drag undone and no error to
    explain it.
    """
    steps = _saved_by_index(step_formset)
    lines = _saved_by_index(ingredient_formset)

    _wire_step_parents(step_formset, steps)
    _wire_ingredients(ingredient_formset, steps, lines)
    _apply_order(step_formset, steps)
    _apply_order(ingredient_formset, lines)
    _apply_spans(step_formset, steps)


def _index_of_saved(formset):
    """``{pk: form index}`` for the forms that have an instance in the database."""
    return {
        form.instance.pk: index
        for index, form in enumerate(formset.forms)
        if form.instance.pk
    }


def _saved_by_index(formset):
    """``{form index: instance}`` for the rows that survived this save.

    A form marked for deletion is left out, which is what makes a reference to
    a row somebody has just removed resolve to "unassigned" instead of to a
    foreign key pointing at nothing.
    """
    deleted = set(formset.deleted_forms)
    return {
        index: form.instance
        for index, form in enumerate(formset.forms)
        if form.instance.pk and form not in deleted
    }


def _resolve_parent(formset, raw, live, start):
    """Follow a ``parent_index`` past the rows that are no longer there.

    A step that has been removed is not "no parent" — it is a step taken *out
    of a chain*, and what fed it now feeds whatever it fed. Reading the
    reference as None instead is what made adding a step and then deleting it
    again break the recipe apart: "+ Step after this" rewires A → new → B, so
    removing the new one left A pointing at a row that is not saved, A became a
    root of its own, and ``validate_structure`` then refused the whole page for
    a branch that is not joined up. Following the chain puts A back on B.

    ``live`` is whatever the caller counts as still being there — the rows that
    survived the save, or the rows that are complete enough to validate. The
    walk is bounded by a seen-set, because a hand-made POST can name a ring of
    removed rows.

    static/js/recipe_diagram.js repeats this in ``model()``; if the picture and
    the saved recipe ever disagree about a deletion it will be about these six
    lines.
    """
    seen = {start}
    while raw is not None and raw not in live:
        if raw in seen or not (0 <= raw < len(formset.forms)):
            return None
        seen.add(raw)
        raw = getattr(formset.forms[raw], "cleaned_data", {}).get("parent_index")
    return None if raw == start else raw


def _wire_step_parents(formset, steps):
    proposed = {}
    for index, step in steps.items():
        raw = _resolve_parent(
            formset, formset.forms[index].cleaned_data.get("parent_index"), steps, index
        )
        parent = steps.get(raw)
        proposed[step.pk] = parent.pk if parent is not None and parent.pk != step.pk else None

    _break_cycles(proposed)

    for step in steps.values():
        if step.parent_id != proposed[step.pk]:
            step.parent_id = proposed[step.pk]
            step.save(update_fields=["parent"])


def _wire_ingredients(formset, steps, lines):
    # First pass: what each row *asked* for.
    asked = {}
    for index, item in lines.items():
        raw = formset.forms[index].cleaned_data.get("alt_index")
        target = lines.get(raw)
        asked[index] = target if target is not None and target.pk != item.pk else None

    by_pk = {item.pk: index for index, item in lines.items()}

    for index, item in lines.items():
        alternative = asked[index]
        # One level only. "Margarine instead of butter, and olive oil instead
        # of the margarine" is a chain nothing renders and nobody means; the
        # second link is dropped rather than drawn.
        if alternative is not None and asked.get(by_pk.get(alternative.pk)) is not None:
            alternative = None

        # A substitute takes its place in the diagram from the line it
        # replaces. Letting it carry a step of its own would put it in the
        # table twice — once as a row, once under the line it stands in for.
        step = None if alternative is not None else steps.get(
            formset.forms[index].cleaned_data.get("step_index")
        )

        changed = []
        if item.alternative_for_id != (alternative.pk if alternative else None):
            item.alternative_for = alternative
            changed.append("alternative_for")
        if item.step_id != (step.pk if step else None):
            item.step = step
            changed.append("step")
        if changed:
            item.save(update_fields=changed)


def _apply_order(formset, rows):
    """Write the arrangement the page was left in.

    Separate from ``save()`` because ``_OrderField.has_changed`` is always
    False: a row whose only difference is where it sits is not a row somebody
    edited, so ``save_existing_objects`` never reaches it. Without this pass a
    drag that only moved something would look saved and be gone on the next
    load.
    """
    for index, obj in rows.items():
        given = formset.forms[index].cleaned_data.get("position")
        if given is not None and obj.position != given:
            obj.position = given
            obj.save(update_fields=["position"])


def _apply_spans(formset, rows):
    """Write how far each standing instruction reaches across the table.

    Its own pass for the same reason as ``_apply_order``: ``_SpanField`` never
    reports a change, so ``save_existing_objects`` never reaches a row whose
    only difference is its span, and an adjustment would look saved and be gone
    on the next load.

    Cleared — not left — when a step has stopped being a standing instruction.
    A span is meaningless on a step with inputs, and a stale one lying in the
    column is what would draw a band across half the table the day somebody
    took the last ingredient back out of it.
    """
    for index, obj in rows.items():
        data = formset.forms[index].cleaned_data
        start, end = data.get("span_from"), data.get("span_to")
        if start is not None and end is not None and end < start:
            start, end = end, start
        if (obj.span_from, obj.span_to) != (start, end):
            obj.span_from, obj.span_to = start, end
            obj.save(update_fields=["span_from", "span_to"])


def _break_cycles(parents):
    """Make ``{pk: parent pk}`` acyclic, in place, by cutting the closing edge.

    The page cannot produce a cycle — the selects leave out anything that would
    make one — but a POST is not a page, and a cycle reaching the database is
    a recipe whose detail view never returns. Cheaper to refuse it here than to
    defend every reader of the tree.
    """
    for pk in list(parents):
        seen, current = {pk}, parents.get(pk)
        while current is not None:
            if current in seen:
                parents[pk] = None
                break
            seen.add(current)
            current = parents.get(current)


# --------------------------------------------------------------------------
# Logging a cooking
# --------------------------------------------------------------------------

class CookLogForm(forms.ModelForm):
    """"I made this, it took this long, and it fed this many."

    The portion counts are one field per size rather than a formset. There are
    five of them, they are always all present, and they are a row of number
    inputs on the page — a formset would be management-form machinery for a
    fixed, closed set.
    """

    apply_time = forms.BooleanField(
        label=_("Use this as the recipe’s cooking time"), required=False,
    )

    class Meta:
        model = CookLog
        fields = ["servings_made", "minutes", "notes"]
        widgets = {
            "notes": forms.Textarea(attrs={"rows": 3}),
            # Followed by the servings stepper on the cooking page, so nobody
            # has to type the number they just chose a second time.
            "servings_made": forms.NumberInput(attrs={"data-servings-made": True}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["notes"].required = False
        self.fields["minutes"].required = False
        for value, label in PortionSize.choices:
            self.fields[f"portion_{value}"] = forms.IntegerField(
                label=label, required=False, min_value=0, max_value=99,
            )
        # Editing an entry that already exists: fill the five counts in from
        # the rows behind it. Without this the form renders empty and saving
        # it — to correct the *time* — silently takes the portions away, which
        # is the worst version of an edit page.
        if self.instance.pk:
            for portion in self.instance.portions.all():
                field = f"portion_{portion.size}"
                if field in self.fields:
                    self.initial.setdefault(field, portion.count)

    def portion_fields(self):
        """The five portion inputs, for a template that lays them out in a row."""
        return [self[f"portion_{value}"] for value, _label in PortionSize.choices]

    def portion_counts(self):
        """``{size: count}`` for the sizes somebody actually entered."""
        counts = {}
        for value, _label in PortionSize.choices:
            count = self.cleaned_data.get(f"portion_{value}") or 0
            if count:
                counts[value] = count
        return counts

    def save_portions(self, log):
        """Make the rows behind this entry say what the form says.

        Written as a reconciliation rather than "delete them all and insert the
        new ones", because the second version rewrites five rows every time
        somebody corrects a note — and on SQLite each of those takes the one
        write lock. A count set to zero is a row removed: "no small portions"
        and "a row saying nought small portions" are the same claim, and only
        one of them should be on the page.
        """
        wanted = self.portion_counts()
        existing = {portion.size: portion for portion in log.portions.all()}

        for size, count in wanted.items():
            row = existing.get(size)
            if row is None:
                CookPortion.objects.create(log=log, size=size, count=count)
            elif row.count != count:
                row.count = count
                row.save(update_fields=["count"])

        gone = [row.pk for size, row in existing.items() if size not in wanted]
        if gone:
            CookPortion.objects.filter(pk__in=gone).delete()

    def clean_servings_made(self):
        servings = self.cleaned_data.get("servings_made")
        if servings is not None and servings < 1:
            raise ValidationError(_("A cooking is for at least one serving."))
        return servings

    def clean(self):
        data = super().clean()
        if not self.portion_counts() and not data.get("minutes"):
            # Neither half recorded is an empty entry: a date, a name and
            # nothing else, which is a row that makes the recipe page longer
            # and says nothing.
            raise ValidationError(
                _("Record how long it took, how far it went, or both.")
            )
        return data
