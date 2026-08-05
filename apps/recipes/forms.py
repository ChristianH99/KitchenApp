"""Forms for adding and editing a recipe."""

from django import forms
from django.core.exceptions import ValidationError
from django.forms import inlineformset_factory
from django.utils.translation import gettext_lazy as _

from apps.recipes.images import clean_upload
from apps.recipes.models import Recipe, RecipeIngredient, Tag


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


class RecipeIngredientForm(forms.ModelForm):
    class Meta:
        model = RecipeIngredient
        fields = ["amount", "unit", "name", "note"]

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
