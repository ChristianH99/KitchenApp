"""The recipe pages: an overview, a searchable list, one recipe, and its form.

Two rules run through the file.

**A read must not write.** Every page here is a GET that renders and nothing
else. It sounds obvious and it is the thing that quietly stops being true —
"just stamp last_viewed while we're here" — and on SQLite a write inside a read
takes the one write lock every other request is waiting for.

**The cost must not grow with the collection.** The list resolves each recipe's
tags and ingredient count, and doing that per row is one query per recipe: fine
at thirty, a page of queries at four hundred, and nobody notices in between.
``prefetch_related`` and an annotation keep it flat, and
``apps/recipes/tests.py`` pins the count so it cannot drift back.
"""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Count, Prefetch, Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from apps.recipes import diagram as diagram_module
from apps.recipes.forms import (
    CookLogForm, IngredientFormSet, RecipeForm, StepFormSet,
    prime_diagram_indices, wire_diagram,
)
from apps.recipes.models import CookLog, CookPortion, Recipe, Tag

# How many past cookings the recipe page lists. A household that makes the same
# soup every fortnight has a hundred of them within four years, and a page that
# renders all of them grows without bound while the query count — which is what
# the cost tests pin — stays perfectly flat.
COOK_LOG_SHOWN = 10


def _visible_recipes():
    """The base queryset every listing page starts from.

    One place, so the two callers cannot disagree about what a recipe list is —
    and so the prefetches below are not re-derived (and forgotten) per view.
    """
    return (
        Recipe.objects
        .select_related("created_by")
        .prefetch_related("tags")
        .annotate(ingredient_count=Count(
            "ingredients", distinct=True,
            # Substitutes are ingredient rows too, and counting them tells the
            # card "9 ingredients" for a recipe with six and three "or use…"
            # lines. The count on a card is what somebody has to buy.
            filter=Q(ingredients__alternative_for__isnull=True),
        ))
    )


def _may_edit(user, recipe):
    """Who may change a recipe.

    The person who added it, or a staff user. Not "anyone signed in": a
    household collection is shared to *cook* from, and somebody quietly
    rewriting the family Rouladen recipe is the failure worth preventing.
    Staff is the escape hatch for the obvious cases — a typo in somebody
    else's, a recipe left behind by an account that is gone.
    """
    return user.is_staff or (recipe.created_by_id and recipe.created_by_id == user.id)


@login_required
def home(request):
    """The landing page: what is in the collection, and what is new in it."""
    recipes = _visible_recipes()
    return render(request, "recipes/home.html", {
        "recent": recipes.order_by("-created_at")[:6],
        "recipe_count": Recipe.objects.count(),
        "tag_count": Tag.objects.count(),
        # The tags actually in use, biggest first — the ones worth offering as
        # a way in. A tag with nothing on it is noise on a landing page.
        "top_tags": (
            Tag.objects.annotate(n=Count("recipes")).filter(n__gt=0).order_by("-n", "name")[:12]
        ),
    })


@login_required
def recipe_list(request):
    """Every recipe, searchable and filterable by tag."""
    query = (request.GET.get("q") or "").strip()
    tag_slug = (request.GET.get("tag") or "").strip()
    order = request.GET.get("order") or "title"

    recipes = _visible_recipes()

    if query:
        # Title, description and ingredient names — the last is the one that
        # matters in a kitchen ("what can I do with fennel?") and the one a
        # title-only search silently fails to answer.
        recipes = recipes.filter(
            Q(title__icontains=query)
            | Q(description__icontains=query)
            | Q(ingredients__name__icontains=query)
            | Q(tags__name__icontains=query)
        ).distinct()

    active_tag = None
    if tag_slug:
        active_tag = Tag.objects.filter(slug=tag_slug).first()
        if active_tag:
            recipes = recipes.filter(tags=active_tag)

    # A closed set, checked rather than interpolated: `order_by` on a string
    # straight off the query string lets a caller order by anything in the
    # model, related or not, which is a slow query somebody else chose.
    ordering = {"title": "title", "new": "-created_at", "time": "prep_minutes"}
    recipes = recipes.order_by(ordering.get(order, "title"))

    return render(request, "recipes/recipe_list.html", {
        "recipes": recipes,
        "query": query,
        "active_tag": active_tag,
        "order": order if order in ordering else "title",
        "tags": Tag.objects.annotate(n=Count("recipes")).filter(n__gt=0).order_by("name"),
    })


def _loaded_recipe(slug):
    """One recipe with everything a page about it needs, in three queries.

    The diagram, the ingredient list and the cooking walk-through all read the
    same two collections, so they are fetched once and handed round as lists —
    ``apps/recipes/diagram.py`` takes them as arguments for exactly this
    reason. Asking the ORM again per section would be a page whose cost grows
    with how many sections it has.
    """
    return get_object_or_404(
        Recipe.objects.select_related("created_by").prefetch_related(
            "tags", "ingredients", "steps",
        ),
        slug=slug,
    )


@login_required
def recipe_detail(request, slug):
    recipe = _loaded_recipe(slug)
    ingredients = list(recipe.ingredients.all())
    steps = list(recipe.steps.all())

    logs = list(
        recipe.cook_logs.select_related("cooked_by").prefetch_related("portions")
        [:COOK_LOG_SHOWN]
    )
    log_count = recipe.cook_logs.count()

    return render(request, "recipes/recipe_detail.html", {
        "recipe": recipe,
        # top_level() attaches each line's substitutes and drops the substitute
        # rows themselves — the plain list and the diagram must agree about
        # what a line is, so they are derived from one call.
        "ingredients": diagram_module.top_level(ingredients),
        "diagram": diagram_module.build(recipe, ingredients=ingredients, steps=steps),
        "logs": logs,
        "log_count": log_count,
        "log_more": max(0, log_count - len(logs)),
        "typical_minutes": _typical_minutes(logs),
        "may_edit": _may_edit(request.user, recipe),
    })


def _typical_minutes(logs):
    """What it has actually taken, rounded to five minutes.

    The median rather than the mean: one evening that was interrupted by a
    phone call for forty minutes should not move the number the household
    plans around, and with four or five entries a mean is entirely at that
    evening's mercy.
    """
    measured = sorted(log.minutes for log in logs if log.minutes)
    if not measured:
        return None
    middle = measured[len(measured) // 2]
    return max(5, round(middle / 5) * 5)


@login_required
def recipe_add(request):
    if request.method == "POST":
        form = RecipeForm(request.POST, request.FILES)
        blank = Recipe()
        formset = IngredientFormSet(request.POST, instance=blank)
        steps = StepFormSet(request.POST, instance=blank)
        if form.is_valid() and formset.is_valid() and steps.is_valid():
            with transaction.atomic():
                recipe = form.save(commit=False)
                recipe.created_by = request.user
                recipe.save()
                form.save_tags(recipe)
                # Steps first: an ingredient's `step_index` can only become a
                # foreign key once the step it names has a primary key.
                steps.instance = recipe
                steps.save()
                formset.instance = recipe
                formset.save()
                wire_diagram(steps, formset)
            messages.success(request, _("“%(title)s” was added.") % {"title": recipe.title})
            return redirect(recipe.get_absolute_url())
    else:
        form = RecipeForm()
        blank = Recipe()
        formset = IngredientFormSet(instance=blank)
        steps = StepFormSet(instance=blank)

    prime_diagram_indices(steps, formset)
    return render(request, "recipes/recipe_form.html", {
        "form": form, "formset": formset, "steps": steps, "recipe": None,
    })


@login_required
def recipe_edit(request, slug):
    recipe = get_object_or_404(Recipe, slug=slug)
    if not _may_edit(request.user, recipe):
        # 404, not 403. The recipe is readable by everybody signed in, so
        # hiding its existence would be theatre — but a bare 403 page has no
        # way back, and this at least renders the app's own not-found page.
        raise Http404

    if request.method == "POST":
        form = RecipeForm(request.POST, request.FILES, instance=recipe)
        formset = IngredientFormSet(request.POST, instance=recipe)
        steps = StepFormSet(request.POST, instance=recipe)
        if form.is_valid() and formset.is_valid() and steps.is_valid():
            with transaction.atomic():
                form.save()
                steps.save()
                formset.save()
                wire_diagram(steps, formset)
            messages.success(request, _("“%(title)s” was saved.") % {"title": recipe.title})
            return redirect(recipe.get_absolute_url())
    else:
        form = RecipeForm(instance=recipe)
        formset = IngredientFormSet(instance=recipe)
        steps = StepFormSet(instance=recipe)

    prime_diagram_indices(steps, formset)
    return render(request, "recipes/recipe_form.html", {
        "form": form, "formset": formset, "steps": steps, "recipe": recipe,
    })


@login_required
@require_POST
def recipe_delete(request, slug):
    recipe = get_object_or_404(Recipe, slug=slug)
    if not _may_edit(request.user, recipe):
        raise Http404
    title = recipe.title
    recipe.delete()
    messages.success(request, _("“%(title)s” was deleted.") % {"title": title})
    return redirect("recipes:list")


# --------------------------------------------------------------------------
# Cooking
# --------------------------------------------------------------------------

@login_required
def recipe_cook(request, slug):
    """The guided walk through a recipe, one step at a time.

    A GET and nothing more. The stopwatch lives in the browser
    (static/js/recipe_cook.js, backed by localStorage) rather than as a
    "cooking session" row here, for two reasons that both matter on this
    hardware: opening a page would otherwise take SQLite's single write lock —
    the thing every other request in the house is queueing behind — and a
    session that only exists server-side is a session that ends when somebody's
    phone goes to sleep mid-recipe. The elapsed time arrives once, with the
    POST that records the cooking.
    """
    recipe = _loaded_recipe(slug)
    ingredients = list(recipe.ingredients.all())
    steps = list(recipe.steps.all())
    return render(request, "recipes/recipe_cook.html", _cook_context(
        request, recipe,
        diagram_module.build(recipe, ingredients=ingredients, steps=steps),
        diagram_module.top_level(ingredients),
        CookLogForm(initial={"servings_made": recipe.servings}),
    ))


def _cook_context(request, recipe, diagram, ingredients, log_form, invalid=False):
    return {
        "recipe": recipe,
        "diagram": diagram,
        "ingredients": ingredients,
        "log_form": log_form,
        # A failed save has to come back with the finish panel already open, or
        # the errors are behind a button somebody has to find again.
        "finish_open": invalid,
        "may_edit": _may_edit(request.user, recipe),
    }


@login_required
@require_POST
def recipe_cooked(request, slug):
    """Record that this was cooked: how long it took and how far it went."""
    recipe = _loaded_recipe(slug)
    form = CookLogForm(request.POST)

    if form.is_valid():
        with transaction.atomic():
            log = form.save(commit=False)
            log.recipe = recipe
            log.cooked_by = request.user
            log.save()
            CookPortion.objects.bulk_create([
                CookPortion(log=log, size=size, count=count)
                for size, count in form.portion_counts().items()
            ])
            minutes = form.cleaned_data.get("minutes")
            # Writing the measured time back onto the recipe is an *edit* of
            # the recipe, so it answers to the same question as the edit page:
            # anybody may record that they cooked it, not everybody may change
            # what it says.
            if form.cleaned_data.get("apply_time") and minutes and _may_edit(request.user, recipe):
                recipe.cook_minutes = minutes
                recipe.save(update_fields=["cook_minutes"])
        messages.success(request, _("Noted — “%(title)s” cooked.") % {"title": recipe.title})
        return redirect(recipe.get_absolute_url())

    ingredients = list(recipe.ingredients.all())
    steps = list(recipe.steps.all())
    return render(request, "recipes/recipe_cook.html", _cook_context(
        request, recipe,
        diagram_module.build(recipe, ingredients=ingredients, steps=steps),
        diagram_module.top_level(ingredients),
        form, invalid=True,
    ))


@login_required
@require_POST
def cook_log_delete(request, slug, pk):
    """Remove one entry from the history — the person who made it, or staff.

    Not tied to who may edit the *recipe*: a cooking is somebody's own record
    of their own evening, and the person who typed 'small portion' by mistake
    is the person who should be able to take it back.
    """
    log = get_object_or_404(CookLog, pk=pk, recipe__slug=slug)
    if not (request.user.is_staff or log.cooked_by_id == request.user.id):
        raise Http404
    log.delete()
    messages.success(request, _("The entry was removed."))
    return redirect("recipes:detail", slug=slug)


@login_required
def tag_list(request):
    """Every tag with something on it, and how much.

    Tags with no recipes are left out rather than shown as empty rows: they are
    left over from a recipe that was deleted or re-tagged, and a page of zeroes
    is a page asking to be tidied rather than one answering a question.
    """
    return render(request, "recipes/tag_list.html", {
        "tags": (
            Tag.objects
            .annotate(n=Count("recipes"))
            .filter(n__gt=0)
            .prefetch_related(Prefetch(
                "recipes",
                # Enough for a preview line, without dragging four hundred
                # titles into memory to show three of them per tag.
                queryset=Recipe.objects.only("title", "slug").order_by("title"),
            ))
            .order_by("-n", "name")
        ),
    })
