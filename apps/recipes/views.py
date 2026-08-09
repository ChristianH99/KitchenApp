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

from apps.accounts.models import Preferences
from apps.pantry import catalogue, matching
from apps.pantry.models import PantryItem
from apps.recipes import diagram as diagram_module
from apps.recipes.forms import (
    CookLogForm, IngredientFormSet, RecipeForm, RecipeOwnerForm, StepFormSet,
    person_label, prime_diagram_indices, validate_structure, wire_diagram,
)
from apps.recipes.models import CookLog, CookPortion, Recipe, Tag

# How many past cookings the recipe page lists. A household that makes the same
# soup every fortnight has a hundred of them within four years, and a page that
# renders all of them grows without bound while the query count — which is what
# the cost tests pin — stays perfectly flat.
COOK_LOG_SHOWN = 10

# The same bound on the history page, which lists cookings across every recipe
# rather than one. Larger because that page is the history — but still bounded,
# for the same reason: the question it answers is about the recent past.
HISTORY_SHOWN = 100


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

    The person who looks after it, or a staff user. Not "anyone signed in": a
    household collection is shared to *cook* from, and somebody quietly
    rewriting the family Rouladen recipe is the failure worth preventing.
    Staff is the escape hatch for the obvious cases — a typo in somebody
    else's, a recipe left behind by an account that is gone.

    ``owner``, not ``created_by``, and reading exactly one of the two is the
    point. ``owner`` starts as whoever typed the recipe in (Recipe.save fills
    it), so for a collection nobody has handed anything over in the two are the
    same answer; the moment one is transferred they are not, and a rule that
    accepted either would mean giving a recipe away without losing it. That is
    a share, not a transfer, and it would be invisible on the page — the old
    owner would simply still see Edit.
    """
    return user.is_staff or (recipe.owner_id and recipe.owner_id == user.id)


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

    have = (request.GET.get("have") or "").strip()
    recipes, pantry_ready, pantry_size = _apply_pantry_filter(recipes, have)

    return render(request, "recipes/recipe_list.html", {
        "recipes": recipes,
        "query": query,
        "active_tag": active_tag,
        "order": order if order in ordering else "title",
        "tags": Tag.objects.annotate(n=Count("recipes")).filter(n__gt=0).order_by("name"),
        "have": have if have in ("now", "nearly") else "",
        "pantry_size": pantry_size,
        # {recipe id: RecipeVerdict} for whatever is being listed, so a card can
        # say "2 missing" without asking again.
        "pantry_ready": pantry_ready,
    })


def _apply_pantry_filter(recipes, have):
    """Narrow a listing to what the cupboard can actually produce.

    Done in Python over the rows already fetched, not in SQL. The question is
    "is 500 g of flour covered by 1 kg in the cupboard" — a unit conversion,
    per line, with substitutes and optional lines to consider — and expressing
    that as a query means either putting the conversion table into the database
    or answering it wrongly. A household collection is a few hundred recipes;
    the cost that matters is the number of *queries*, and this adds three
    however long the list is.

    An empty pantry returns the listing untouched and says so. Filtering to
    nothing would be reading "nobody has filled the cupboard in" as "there is
    no food in this house".
    """
    pantry = list(PantryItem.objects.select_related("ingredient"))
    if not pantry:
        return recipes, {}, 0

    rows = list(
        recipes.prefetch_related("ingredients__ingredient__purchase_sizes")
    )
    by_ingredient = matching.pantry_by_ingredient(pantry)

    verdicts = {}
    for recipe in rows:
        lines = diagram_module.top_level(list(recipe.ingredients.all()))
        verdict = matching.check_recipe(lines, by_ingredient)
        verdicts[recipe.pk] = verdict
        # Hung on the object as well as kept in the map, so the shared card
        # partial can read it without a dictionary lookup Django's template
        # language cannot do by key. The landing page passes recipes with no
        # such attribute and the card guards on it.
        recipe.pantry = verdict

    if have == "now":
        rows = [r for r in rows if verdicts[r.pk].can_be_made]
    elif have == "nearly":
        rows = [r for r in rows if verdicts[r.pk].can_be_made or verdicts[r.pk].nearly]

    return rows, verdicts, len(pantry)


def _loaded_recipe(slug):
    """One recipe with everything a page about it needs, in three queries.

    The diagram, the ingredient list and the cooking walk-through all read the
    same two collections, so they are fetched once and handed round as lists —
    ``apps/recipes/diagram.py`` takes them as arguments for exactly this
    reason. Asking the ORM again per section would be a page whose cost grows
    with how many sections it has.
    """
    return get_object_or_404(
        Recipe.objects.select_related("created_by", "owner").prefetch_related(
            "tags", "steps",
            # The catalogue row and its packet sizes come along with the lines.
            # Without them the pantry check walks the ingredients and touches
            # the database twice per line — which is invisible on a recipe with
            # six and is the shape the cost tests exist to catch.
            "ingredients__ingredient__purchase_sizes",
        ),
        slug=slug,
    )


@login_required
def recipe_detail(request, slug):
    recipe = _loaded_recipe(slug)
    may_edit = _may_edit(request.user, recipe)
    ingredients = list(recipe.ingredients.all())
    steps = list(recipe.steps.all())

    logs = list(
        recipe.cook_logs.select_related("cooked_by").prefetch_related("portions")
        [:COOK_LOG_SHOWN]
    )
    log_count = recipe.cook_logs.count()

    # top_level() attaches each line's substitutes and drops the substitute
    # rows themselves — the plain list and the diagram must agree about what a
    # line is, so they are derived from one call.
    lines = diagram_module.top_level(ingredients)

    return render(request, "recipes/recipe_detail.html", {
        "recipe": recipe,
        "ingredients": lines,
        "diagram": diagram_module.build(recipe, ingredients=ingredients, steps=steps),
        "logs": logs,
        "log_count": log_count,
        "log_more": max(0, log_count - len(logs)),
        "typical_minutes": _typical_minutes(logs),
        "may_edit": may_edit,
        # Built here rather than in the template so the list of people is one
        # query and one place. Only for somebody who may edit: it is a control,
        # and rendering it disabled would be a page telling everybody else who
        # they could not give this recipe to.
        "owner_form": RecipeOwnerForm(instance=recipe) if may_edit else None,
        # One implementation of "what do we call this person", shared with the
        # dropdown above rather than written again as a {% firstof %}: an SSO
        # account's username is an opaque `sub`, and a page that falls back to
        # it in one place and to the e-mail in another is two names for one
        # person on one screen.
        "owner_label": person_label(recipe.owner) if recipe.owner_id else None,
        **_pantry_context(lines),
    })


def _pantry_context(lines):
    """What the cupboard says about this recipe, or nothing at all.

    An empty pantry means the whole section is left off the page rather than
    rendered as "everything is missing". Somebody who has not filled the
    cupboard in has not said they have nothing; they have said nothing, and a
    page that reads the second as the first is a page that is wrong for
    everybody who has not opted in.

    Two queries: the ingredients that appear on this recipe, and their purchase
    sizes. Flat in the number of lines, which is what the cost tests pin.
    """
    ids = [line.ingredient_id for line in lines if line.ingredient_id]
    for line in lines:
        ids.extend(alt.ingredient_id for alt in line.substitutes if alt.ingredient_id)
    if not ids or not PantryItem.objects.exists():
        return {"pantry_verdict": None, "shopping": []}

    items = PantryItem.objects.filter(ingredient_id__in=set(ids)).select_related("ingredient")
    verdict = matching.check_recipe(lines, matching.pantry_by_ingredient(items))
    return {
        "pantry_verdict": verdict,
        # The packet sizes turn "380 g short" into "one 500 g pack", which is
        # what is useful standing in a shop.
        "shopping": matching.shopping_list([(None, verdict)]),
    }


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
        # `and` would short-circuit and leave the second and third formsets
        # unvalidated, so a page with a bad title comes back with every other
        # error still hidden. Evaluated first, combined after.
        valid = [form.is_valid(), formset.is_valid(), steps.is_valid()]
        if all(valid) and validate_structure(steps, formset):
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
                _link_catalogue(recipe, request.user)
            messages.success(request, _("“%(title)s” was added.") % {"title": recipe.title})
            return redirect(recipe.get_absolute_url())
    else:
        form = RecipeForm()
        blank = Recipe()
        formset = IngredientFormSet(instance=blank)
        steps = StepFormSet(instance=blank)

    return _render_form(request, form, formset, steps, None)


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
        valid = [form.is_valid(), formset.is_valid(), steps.is_valid()]
        if all(valid) and validate_structure(steps, formset):
            with transaction.atomic():
                form.save()
                steps.save()
                formset.save()
                wire_diagram(steps, formset)
                _link_catalogue(recipe, request.user)
            messages.success(request, _("“%(title)s” was saved.") % {"title": recipe.title})
            return redirect(recipe.get_absolute_url())
    else:
        form = RecipeForm(instance=recipe)
        formset = IngredientFormSet(instance=recipe)
        steps = StepFormSet(instance=recipe)

    return _render_form(request, form, formset, steps, recipe)


def _render_form(request, form, formset, steps, recipe):
    """One place, so the add and edit pages cannot drift apart.

    ``prime_diagram_indices`` in particular has to run for both and after any
    failed save — without it an edit page comes back with an empty diagram and
    pressing Save flattens what was there.
    """
    prime_diagram_indices(steps, formset)
    return render(request, "recipes/recipe_form.html", {
        "form": form, "formset": formset, "steps": steps, "recipe": recipe,
        # The ingredient autosuggest reads this out of a json_script block.
        # apps/pantry/catalogue.py::suggestions says why it is embedded rather
        # than fetched.
        "suggestions": catalogue.suggestions(),
    })


def _link_catalogue(recipe, user):
    """Give every line of a just-saved recipe a catalogue row.

    Runs inside the same transaction as the save. The autosuggest has already
    linked whatever somebody picked from the list; this is for the names typed
    straight through — which is how the catalogue learns what this household
    actually cooks with, rather than only what it was shipped knowing.
    """
    catalogue.resolve_lines(list(recipe.ingredients.all()), user=user)


@login_required
@require_POST
def recipe_transfer(request, slug):
    """Hand a recipe to somebody else.

    A POST of its own rather than a field on the edit form: it is the one
    control on that page whose effect is on *this* page rather than on the
    recipe, and pressing it is usually the last thing the person pressing it
    can do here. It sits beside Delete for that reason — both are decisions
    about the recipe as an object — and above it, because only one of them is
    irreversible.

    Nothing special happens to the previous owner: they lose Edit because
    ``_may_edit`` reads one column. That is the whole of the feature, and the
    reason it is one column.
    """
    recipe = get_object_or_404(Recipe, slug=slug)
    if not _may_edit(request.user, recipe):
        raise Http404

    form = RecipeOwnerForm(request.POST, instance=recipe)
    if not form.is_valid():
        # The form has one field, so there is exactly one thing that can be
        # wrong with it and a whole page rendered around a single error is
        # ceremony. Said in the banner every other action here uses.
        for error in form.errors.get("owner", [_("Choose somebody to hand it to.")]):
            messages.error(request, error)
        return redirect(recipe.get_absolute_url())

    form.save()
    messages.success(request, _("“%(title)s” now belongs to %(who)s.") % {
        "title": recipe.title, "who": person_label(recipe.owner),
    })
    return redirect(recipe.get_absolute_url())


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
        # Which noise a finished step timer makes, chosen on the person's own
        # settings page. Read here rather than in the template so the page has
        # it before any script runs — a timer that finished while the tab was
        # backgrounded should ring the moment it is looked at.
        "timer_sound": Preferences.for_user(request.user).timer_sound,
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
def cook_history(request):
    """Everything the household has cooked, newest first.

    The page that makes the portion counts worth recording. One evening's entry
    on one recipe answers nothing; the same entry beside the last six answers
    "how often do we actually make this" and "does four really feed us".

    Bounded rather than paginated. A household cooking once a day fills a year
    with three hundred and sixty-five rows, and the question this page answers
    is about the recent past — "when did we last have this" — not about 2019.
    The count says what is not shown, so nothing is silently missing.
    """
    logs = list(
        CookLog.objects
        .select_related("recipe", "cooked_by")
        .prefetch_related("portions")
        .order_by("-cooked_at")[:HISTORY_SHOWN]
    )
    total = CookLog.objects.count()
    return render(request, "recipes/cook_history.html", {
        "logs": logs,
        "total": total,
        "more": max(0, total - len(logs)),
    })


def _may_edit_log(user, log):
    """Who may change a record of a cooking.

    The person who made it, or staff — deliberately *not* whoever may edit the
    recipe. A cooking is somebody's own record of their own evening, and the
    person who typed "small portion" by mistake is the person who should be
    able to take it back.
    """
    return user.is_staff or log.cooked_by_id == user.id


@login_required
def cook_log_edit(request, slug, pk):
    """Correct an entry after the fact.

    The reason this exists: how far a dish went is known the *next* day. The
    box in the fridge either got eaten at lunchtime or it did not, and the
    number typed while standing over the washing-up is a guess. Without this
    page the only way to fix it was to delete the entry and lose the date and
    the measured time with it.
    """
    log = get_object_or_404(
        CookLog.objects.select_related("recipe").prefetch_related("portions"),
        pk=pk, recipe__slug=slug,
    )
    if not _may_edit_log(request.user, log):
        raise Http404

    if request.method == "POST":
        form = CookLogForm(request.POST, instance=log)
        if form.is_valid():
            with transaction.atomic():
                saved = form.save()
                form.save_portions(saved)
            messages.success(request, _("The entry was updated."))
            return redirect(log.recipe.get_absolute_url())
    else:
        form = CookLogForm(instance=log)

    return render(request, "recipes/cook_log_form.html", {
        "form": form, "log": log, "recipe": log.recipe,
    })


@login_required
@require_POST
def cook_log_delete(request, slug, pk):
    """Remove one entry from the history — the person who made it, or staff.

    Not tied to who may edit the *recipe*: a cooking is somebody's own record
    of their own evening, and the person who typed 'small portion' by mistake
    is the person who should be able to take it back.
    """
    log = get_object_or_404(CookLog, pk=pk, recipe__slug=slug)
    if not _may_edit_log(request.user, log):
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
