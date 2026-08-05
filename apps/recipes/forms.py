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
selects are built from, and ``wire_diagram()`` below turns indices into
foreign keys once the objects exist. An index that names a deleted or
non-existent form resolves to "unassigned" rather than to an error: the only
way to produce one is by hand-crafting a POST, and dropping the reference loses
nothing while an exception would lose the whole recipe.
"""

from django import forms
from django.core.exceptions import ValidationError
from django.forms import inlineformset_factory
from django.utils.translation import gettext_lazy as _

from apps.recipes.images import clean_upload
from apps.recipes.models import (
    CookLog, PortionSize, Recipe, RecipeIngredient, RecipeStep, Tag,
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


class _IndexField(forms.IntegerField):
    """A reference to another row of this page, by its position in the formset.

    Hidden, because the control somebody actually uses is a ``<select>`` built
    in the browser from the rows that exist *now* — a server-rendered set of
    options would be stale the moment a row is added, which on this page is
    most of the time. static/js/recipe_diagram.js builds the select and writes
    the chosen index back here.
    """

    widget = forms.HiddenInput

    def __init__(self, **kwargs):
        kwargs.setdefault("required", False)
        kwargs.setdefault("min_value", 0)
        super().__init__(**kwargs)


class RecipeIngredientForm(forms.ModelForm):
    # Which step in the diagram uses this line, and — for a substitute — which
    # line it stands in for. See the module docstring for why these are indices.
    step_index = _IndexField()
    alt_index = _IndexField()

    class Meta:
        model = RecipeIngredient
        fields = ["amount", "unit", "name", "note", "optional"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.required = False

    def clean(self):
        """An amount with no ingredient is a row somebody abandoned.

        The formset renders several blank rows so there is always somewhere to
        type; a row with nothing in it is dropped by ``empty_permitted``. This
        catches the other case — "250 g" and no name — which would otherwise be
        saved as a nameless line that reads as a bug on the recipe page.
        """
        data = super().clean()
        name = (data.get("name") or "").strip()
        has_other = any(data.get(f) not in (None, "") for f in ("amount", "unit", "note"))
        if has_other and not name:
            raise ValidationError(_("Give this line an ingredient, or clear it."))
        amount = data.get("amount")
        if amount is not None and amount < 0:
            raise ValidationError(_("An amount cannot be negative."))
        return data


class RecipeStepForm(forms.ModelForm):
    """One box in the diagram."""

    parent_index = _IndexField()

    class Meta:
        model = RecipeStep
        fields = ["text", "detail", "minutes"]
        widgets = {"detail": forms.Textarea(attrs={"rows": 2})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.required = False

    def clean(self):
        """A step is its label. Everything else on the row describes it.

        The mirror of the ingredient rule below: a duration or a detail with no
        operation to attach them to is a row somebody abandoned half-typed, and
        saving it puts an empty box in the middle of the diagram.
        """
        data = super().clean()
        text = (data.get("text") or "").strip()
        has_other = data.get("minutes") is not None or (data.get("detail") or "").strip()
        if has_other and not text:
            raise ValidationError(_("Give this step a name, or clear it."))
        return data


class BaseStepFormSet(forms.BaseInlineFormSet):
    def save_new(self, form, commit=True):
        obj = super().save_new(form, commit=False)
        obj.position = self.forms.index(form)
        if commit:
            obj.save()
        return obj

    def save_existing(self, form, obj, commit=True):
        obj = super().save_existing(form, obj, commit=False)
        obj.position = self.forms.index(form)
        if commit:
            obj.save()
        return obj


class BaseIngredientFormSet(forms.BaseInlineFormSet):
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

        The form index *is* the order: rows are rendered in it, dragged into it
        and never removed from the DOM (removal ticks DELETE and hides the row
        — see the design note in CLAUDE.md), so the index and the visible
        sequence cannot drift apart.
        """
        return self.forms.index(form)


IngredientFormSet = inlineformset_factory(
    Recipe, RecipeIngredient,
    form=RecipeIngredientForm,
    formset=BaseIngredientFormSet,
    extra=4,
    can_delete=True,
)

StepFormSet = inlineformset_factory(
    Recipe, RecipeStep,
    form=RecipeStepForm,
    formset=BaseStepFormSet,
    extra=3,
    can_delete=True,
)


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
    """
    steps = _index_of_saved(step_formset)
    lines = _index_of_saved(ingredient_formset)

    for form in step_formset.forms:
        if form.instance.parent_id in steps:
            form.initial["parent_index"] = steps[form.instance.parent_id]
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
    """
    steps = _saved_by_index(step_formset)
    lines = _saved_by_index(ingredient_formset)

    _wire_step_parents(step_formset, steps)
    _wire_ingredients(ingredient_formset, steps, lines)


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


def _wire_step_parents(formset, steps):
    proposed = {}
    for index, step in steps.items():
        raw = formset.forms[index].cleaned_data.get("parent_index")
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
