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

from apps.recipes.forms import IngredientFormSet, RecipeForm
from apps.recipes.models import Recipe, RecipeIngredient, Tag


def _visible_recipes():
    """The base queryset every listing page starts from.

    One place, so the two callers cannot disagree about what a recipe list is —
    and so the prefetches below are not re-derived (and forgotten) per view.
    """
    return (
        Recipe.objects
        .select_related("created_by")
        .prefetch_related("tags")
        .annotate(ingredient_count=Count("ingredients", distinct=True))
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


@login_required
def recipe_detail(request, slug):
    recipe = get_object_or_404(
        Recipe.objects.select_related("created_by").prefetch_related("tags", "ingredients"),
        slug=slug,
    )
    return render(request, "recipes/recipe_detail.html", {
        "recipe": recipe,
        "ingredients": recipe.ingredients.all(),
        "may_edit": _may_edit(request.user, recipe),
    })


@login_required
def recipe_add(request):
    if request.method == "POST":
        form = RecipeForm(request.POST, request.FILES)
        formset = IngredientFormSet(request.POST, instance=Recipe())
        if form.is_valid() and formset.is_valid():
            with transaction.atomic():
                recipe = form.save(commit=False)
                recipe.created_by = request.user
                recipe.save()
                form.save_tags(recipe)
                formset.instance = recipe
                formset.save()
            messages.success(request, _("“%(title)s” was added.") % {"title": recipe.title})
            return redirect(recipe.get_absolute_url())
    else:
        form = RecipeForm()
        formset = IngredientFormSet(instance=Recipe())

    return render(request, "recipes/recipe_form.html", {
        "form": form, "formset": formset, "recipe": None,
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
        if form.is_valid() and formset.is_valid():
            with transaction.atomic():
                form.save()
                formset.save()
            messages.success(request, _("“%(title)s” was saved.") % {"title": recipe.title})
            return redirect(recipe.get_absolute_url())
    else:
        form = RecipeForm(instance=recipe)
        formset = IngredientFormSet(instance=recipe)

    return render(request, "recipes/recipe_form.html", {
        "form": form, "formset": formset, "recipe": recipe,
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
