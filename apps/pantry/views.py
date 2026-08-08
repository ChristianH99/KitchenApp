"""The cupboard and the catalogue.

Same two rules as ``apps/recipes/views.py``, for the same reasons: **a read
must not write**, and **the cost must not grow with the collection**. The
pantry page is the one that invites breaking the first — "while we are here,
mark everything checked" — and on SQLite that would take the single write lock
on a GET.

Who may change what is deliberately flat here, and it is a decision rather than
an omission. A recipe belongs to whoever wrote it; the cupboard belongs to the
household. Anybody signed in may say there is no more butter, because the
alternative is a shopping list that is wrong until one particular person gets
home.
"""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Count
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from apps.pantry import catalogue
from apps.pantry.forms import (
    AliasFormSet, IngredientForm, PantryAddForm, PantryItemForm, PurchaseSizeFormSet,
)
from apps.pantry.models import Ingredient, IngredientCategory, PantryItem


def _pantry_rows():
    """Everything in the cupboard, with what a row needs to render itself."""
    return (
        PantryItem.objects
        .select_related("ingredient")
        .prefetch_related("ingredient__purchase_sizes")
        .order_by("ingredient__name")
    )


@login_required
def pantry_list(request):
    """What is in the house, grouped the way a kitchen is."""
    items = list(_pantry_rows())

    # Grouped in Python over the list already fetched rather than with a query
    # per category: nine categories is nine queries for a page that has all the
    # rows in hand.
    labels = dict(IngredientCategory.choices)
    groups = {}
    for item in items:
        key = item.ingredient.category or ""
        groups.setdefault(key, []).append(item)
    ordered = [
        (labels.get(key, _("Uncategorised")), rows)
        for key, rows in sorted(groups.items(), key=lambda pair: _category_rank(pair[0]))
    ]

    return render(request, "pantry/pantry_list.html", {
        "groups": ordered,
        "item_count": len(items),
        "add_form": PantryAddForm(),
        "suggestions": catalogue.suggestions(),
    })


def _category_rank(key):
    """The order a shop is walked in, with the uncategorised rows last."""
    order = [value for value, _label in IngredientCategory.choices]
    return (order.index(key) if key in order else len(order), key)


@login_required
@require_POST
def pantry_add(request):
    """Put something in the cupboard, by name.

    The name is resolved through the catalogue and a name it does not know
    creates a row — which is how the catalogue learns what this household
    actually buys, rather than only what it cooks.
    """
    form = PantryAddForm(request.POST)
    if not form.is_valid():
        messages.error(request, _("That could not be added — check the name."))
        return redirect("pantry:list")

    name = form.cleaned_data["name"]
    with transaction.atomic():
        ingredient, created = catalogue.remember(
            name, form.cleaned_data.get("unit") or "", request.user,
        )
        if ingredient is None:
            messages.error(request, _("That could not be added — check the name."))
            return redirect("pantry:list")
        # update_or_create, not create: putting something in the cupboard twice
        # is somebody correcting the amount, not a second cupboard.
        PantryItem.objects.update_or_create(
            ingredient=ingredient,
            defaults={
                "amount": form.cleaned_data.get("amount"),
                "unit": form.cleaned_data.get("unit") or ingredient.default_unit,
            },
        )
    if created:
        messages.success(request, _("“%(name)s” was added, and is new to the catalogue.")
                         % {"name": ingredient.name})
    else:
        messages.success(request, _("“%(name)s” was updated.") % {"name": ingredient.name})
    return redirect("pantry:list")


@login_required
@require_POST
def pantry_save(request):
    """Write back every amount the page was left showing, in one pass.

    One POST for the whole cupboard rather than one per row. Somebody
    unpacking the shopping corrects six numbers, and six round trips is six
    chances for the page they are reading to stop matching the database.
    """
    items = {item.pk: item for item in PantryItem.objects.all()}
    changed = []
    for key, raw in request.POST.items():
        if not key.startswith("amount-"):
            continue
        try:
            pk = int(key.removeprefix("amount-"))
        except ValueError:
            continue
        item = items.get(pk)
        if item is None:
            continue
        form = PantryItemForm(
            {"amount": raw, "unit": request.POST.get(f"unit-{pk}", item.unit),
             "note": request.POST.get(f"note-{pk}", item.note)},
            instance=item,
        )
        if form.is_valid() and form.has_changed():
            changed.append(form)

    with transaction.atomic():
        for form in changed:
            form.save()
    if changed:
        messages.success(request, _("The pantry was updated."))
    return redirect("pantry:list")


@login_required
@require_POST
def pantry_remove(request, slug):
    """Take something out of the cupboard. The catalogue row stays."""
    item = get_object_or_404(PantryItem, ingredient__slug=slug)
    name = item.ingredient.name
    item.delete()
    messages.success(request, _("“%(name)s” is no longer in the pantry.") % {"name": name})
    return redirect("pantry:list")


# --------------------------------------------------------------------------
# The catalogue
# --------------------------------------------------------------------------

@login_required
def ingredient_list(request):
    """Every substance the app knows about, and what it is used in."""
    query = (request.GET.get("q") or "").strip()
    rows = (
        Ingredient.objects
        .prefetch_related("aliases", "purchase_sizes")
        .select_related("in_pantry")
        .annotate(recipe_count=Count("used_in__recipe", distinct=True))
    )
    if query:
        # The aliases are searched too, or looking for "Zwiebeln" finds nothing
        # and somebody creates a second onion.
        rows = rows.filter(name__icontains=query) | rows.filter(aliases__name__icontains=query)
        rows = rows.distinct()

    return render(request, "pantry/ingredient_list.html", {
        "ingredients": rows.order_by("name"),
        "query": query,
        "total": Ingredient.objects.count(),
    })


@login_required
def ingredient_add(request):
    return _ingredient_form(request, Ingredient())


@login_required
def ingredient_edit(request, slug):
    return _ingredient_form(request, get_object_or_404(Ingredient, slug=slug))


def _ingredient_form(request, ingredient):
    """One page for both, because they are one form.

    The version with an add view and an edit view is the version where a field
    added to one of them is missing from the other, and the way that shows up
    is a value that can be set but never changed.
    """
    creating = ingredient.pk is None

    if request.method == "POST":
        form = IngredientForm(request.POST, instance=ingredient)
        aliases = AliasFormSet(request.POST, instance=ingredient)
        sizes = PurchaseSizeFormSet(request.POST, instance=ingredient)
        if form.is_valid() and aliases.is_valid() and sizes.is_valid():
            with transaction.atomic():
                saved = form.save(commit=False)
                if creating:
                    saved.created_by = request.user
                saved.save()
                aliases.instance = saved
                aliases.save()
                sizes.instance = saved
                sizes.save()
            messages.success(request, _("“%(name)s” was saved.") % {"name": saved.name})
            return redirect("pantry:catalogue")
    else:
        form = IngredientForm(instance=ingredient)
        aliases = AliasFormSet(instance=ingredient)
        sizes = PurchaseSizeFormSet(instance=ingredient)

    return render(request, "pantry/ingredient_form.html", {
        "form": form, "aliases": aliases, "sizes": sizes,
        "ingredient": None if creating else ingredient,
        "used_in": [] if creating else list(
            ingredient.used_in.select_related("recipe").order_by("recipe__title")[:20]
        ),
    })


@login_required
@require_POST
def ingredient_delete(request, slug):
    """Remove a substance from the catalogue.

    The recipe lines that pointed at it keep their text and lose the link
    (``SET_NULL``), which is the right loss: the recipe still says "200 g
    Butter" and only stops taking part in the pantry matching.
    """
    ingredient = get_object_or_404(Ingredient, slug=slug)
    name = ingredient.name
    ingredient.delete()
    messages.success(request, _("“%(name)s” was removed from the catalogue.") % {"name": name})
    return redirect("pantry:catalogue")
