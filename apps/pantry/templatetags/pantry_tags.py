"""Template helpers for units.

Both of these exist because ``unit`` holds a language-neutral **code** and no
page may render it directly. Without them a template writes ``{{ item.unit }}``
and the recipe reads "tbsp" in German — which is not obviously wrong on the
page, only wrong, and stays that way until somebody who reads German notices.

``unit_select`` is an inclusion tag rather than a form widget because two of
the four places a unit is chosen are not form fields at all: the pantry page
names its inputs after each row's primary key, so a formset would be management
machinery for a list nobody reorders. One tag means one set of options and one
place where the grouping is decided.
"""

from django import template

from apps.pantry import units

register = template.Library()


@register.filter
def unit_label(code):
    """The short form a recipe line shows — "g", "EL". Unknown values as typed."""
    return units.label(code)


@register.filter
def unit_name(code):
    """The long form — "grams", "tablespoon"."""
    return units.name(code)


@register.inclusion_tag("pantry/_unit_select.html")
def unit_select(name, selected="", element_id="", label="", suffix=""):
    """A grouped unit dropdown that can always show the value it is given.

    ``suffix`` is appended to both the name and the id, for the pantry page —
    whose rows are named after their primary key. It is a parameter rather than
    something the caller concatenates because Django's ``add`` filter refuses
    to join a string to an integer and returns the empty string when it does,
    which would give every row on the page the same field name and post one
    unit for all of them.
    """
    full = f"{name}{suffix}" if suffix != "" else name
    return {
        "name": full,
        "element_id": element_id or full,
        "selected": selected or "",
        "label": label,
        "groups": units.choices(extra=selected if isinstance(selected, str) else None),
    }
