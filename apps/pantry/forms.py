"""Forms for the catalogue and the cupboard — and the unit control the whole app shares.

``UnitSelect`` is the piece worth reading. It is used on four different forms
across two apps, and the reason it is a class rather than
``forms.Select(choices=units.choices())`` is the value that is *not* in the
list: a unit typed before the catalogue existed, or one edited straight into
the database. A plain select cannot hold such a value — the browser shows the
first option instead — and the row is then silently rewritten the next time
anybody presses Save on an unrelated field of the same form. "3 Handvoll
Petersilie" becoming "3 Petersilie" is not a change anybody asked for, and it
happens without an error, on a page that looked right.

So the choices are rebuilt per render, with the current value appended under
"As typed" when the catalogue does not know it.
"""

from django import forms
from django.core.exceptions import ValidationError
from django.forms import inlineformset_factory
from django.utils.translation import gettext_lazy as _

from apps.pantry import units
from apps.pantry.models import (
    Ingredient, IngredientAlias, IngredientCategory, PantryItem, PurchaseSize,
)


class UnitSelect(forms.Select):
    """A unit dropdown that can always represent what is already stored."""

    def __init__(self, attrs=None):
        # `data-unit-select` is how static/js/unit_typeahead.js finds these —
        # the browser's own incremental search on a <select> takes the first
        # label sharing a letter, so typing "g" could land on "Glas" instead of
        # on grams. Also what the stylesheet narrows: a unit is at most a few
        # characters and does not want half the page.
        attrs = dict(attrs or {})
        attrs.setdefault("data-unit-select", True)
        super().__init__(attrs=attrs, choices=units.choices())

    def render(self, name, value, attrs=None, renderer=None):
        # Rebuilt per render rather than per instance: one form class serves
        # every row of a formset, and a value cached on the widget would leak
        # the first row's unfamiliar unit into all of them.
        self.choices = units.choices(extra=value if isinstance(value, str) else None)
        return super().render(name, value, attrs=attrs, renderer=renderer)


class UnitField(forms.CharField):
    """The unit, as a field. Never required — most things have one, not everything."""

    widget = UnitSelect

    def __init__(self, **kwargs):
        kwargs.setdefault("required", False)
        kwargs.setdefault("max_length", units.MAX_CODE_LENGTH)
        super().__init__(**kwargs)

    def clean(self, value):
        value = super().clean(value)
        # A code we know, or a legacy value the select offered back unchanged.
        # Anything else is a hand-made POST, and the empty unit is the harmless
        # reading of it — refusing would fail a whole recipe over one field
        # nobody could have typed wrongly through the page.
        return value if value in units.BY_CODE or value else ""


class IngredientForm(forms.ModelForm):
    """One substance in the catalogue."""

    class Meta:
        model = Ingredient
        fields = ["name", "default_unit", "category", "note"]
        field_classes = {"default_unit": UnitField}
        widgets = {"default_unit": UnitSelect}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["category"].required = False
        self.fields["note"].required = False
        self.fields["category"].choices = (
            [("", _("no category"))] + list(IngredientCategory.choices)
        )

    def clean_name(self):
        name = " ".join((self.cleaned_data.get("name") or "").split())
        if not name:
            raise ValidationError(_("An ingredient needs a name."))
        # Matched case-insensitively, because the whole value of the catalogue
        # is that one substance is one row: "zucker" and "Zucker" as two
        # entries is the free-text column this replaced, wearing a table.
        clash = Ingredient.objects.filter(name__iexact=name).exclude(pk=self.instance.pk)
        if clash.exists():
            raise ValidationError(_("There is already an ingredient with this name."))
        other = IngredientAlias.objects.filter(name__iexact=name).exclude(
            ingredient=self.instance.pk or None
        )
        if other.exists():
            raise ValidationError(
                _("Another ingredient is already known by this name.")
            )
        return name


class IngredientAliasForm(forms.ModelForm):
    class Meta:
        model = IngredientAlias
        fields = ["name"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["name"].required = False
        self.fields["name"].widget.attrs.setdefault(
            "placeholder", _("e.g. “Zwiebeln”")
        )

    def clean_name(self):
        name = " ".join((self.cleaned_data.get("name") or "").split())
        if not name:
            return name
        if Ingredient.objects.filter(name__iexact=name).exclude(
            pk=self.instance.ingredient_id
        ).exists():
            raise ValidationError(_("That is another ingredient’s own name."))
        return name


class PurchaseSizeForm(forms.ModelForm):
    """How it is sold. The amount is required here and optional everywhere else."""

    unit = UnitField(label=_("unit"))

    class Meta:
        model = PurchaseSize
        fields = ["amount", "unit", "label"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["amount"].required = False
        self.fields["label"].required = False
        self.fields["amount"].widget.attrs.setdefault("placeholder", _("Amount"))
        self.fields["label"].widget.attrs.setdefault("placeholder", _("Sold as"))
        # One kilo is stored as 1.000 and rendered by a German locale as
        # "1,000", which reads as a thousand. Trimmed to what somebody typed.
        if self.instance.pk and self.instance.amount is not None:
            self.initial["amount"] = self.instance.amount_display

    def clean(self):
        data = super().clean()
        amount = data.get("amount")
        # A row with only a label is one somebody started and abandoned; the
        # formset drops the entirely blank ones on its own.
        if (data.get("label") or data.get("unit")) and amount is None:
            raise ValidationError(_("A purchase size needs an amount."))
        if amount is not None and amount <= 0:
            raise ValidationError(_("A purchase size is more than nothing."))
        return data


# No spare rows. Two each was two empty "also called" boxes and two empty
# purchase sizes on every ingredient in the catalogue — most of which have one
# of each or none — so the page was mostly blank fields, and the number of
# names an ingredient could have was however many the server felt like
# rendering. A "+" adds one, an "x" takes one away, and the page shows what is
# actually there.
AliasFormSet = inlineformset_factory(
    Ingredient, IngredientAlias, form=IngredientAliasForm, extra=0, can_delete=True,
)

PurchaseSizeFormSet = inlineformset_factory(
    Ingredient, PurchaseSize, form=PurchaseSizeForm, extra=0, can_delete=True,
)


class PantryItemForm(forms.ModelForm):
    """How much of one thing is in the house."""

    unit = UnitField(label=_("unit"))

    class Meta:
        model = PantryItem
        fields = ["amount", "unit", "note"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.required = False
        self.fields["amount"].widget.attrs.setdefault("placeholder", _("Amount"))
        self.fields["note"].widget.attrs.setdefault("placeholder", _("Note"))

    def clean_amount(self):
        amount = self.cleaned_data.get("amount")
        if amount is not None and amount < 0:
            raise ValidationError(_("An amount cannot be negative."))
        return amount


class PantryAddForm(forms.Form):
    """The one-line "put something in the cupboard" control.

    A name rather than a select of the catalogue: the same argument as on the
    recipe form. Somebody unpacking shopping types what is on the packet, and
    a control that first asks them to find "Crème fraîche" in a list of four
    hundred is one they use once. The name is resolved through the catalogue —
    and a name it does not know creates a row, which is how the catalogue
    learns what this household actually buys.
    """

    name = forms.CharField(
        label=_("ingredient"), max_length=120,
        widget=forms.TextInput(attrs={
            "autocomplete": "off",
            "role": "combobox",
            "aria-expanded": "false",
            "aria-autocomplete": "list",
            "data-ingredient-input": True,
            # This form has no card around it, so the suggest script is told
            # where the unit control is rather than being left to find it.
            "data-unit-target": "#pantry-add-unit",
            "placeholder": _("e.g. Butter"),
        }),
    )
    amount = forms.DecimalField(label=_("amount"), max_digits=9, decimal_places=3,
                                required=False, min_value=0,
                                widget=forms.NumberInput(attrs={
                                    "step": "any", "placeholder": _("Amount"),
                                }))
    unit = UnitField(label=_("unit"))

    def clean_name(self):
        return " ".join((self.cleaned_data.get("name") or "").split())
