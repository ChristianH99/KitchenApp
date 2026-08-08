"""Fill an empty development database with something to look at.

Committed rather than kept as a scratch script, because every agent or developer
picking this repo up needs the same thing in the first five minutes: a signed-in
account and enough recipes that the list, the tag filter and the servings scaler
are actually exercising something. Four recipes is deliberately chosen to cover
the cases that differ:

* one with a **fractional** amount (1.5 kg of plums), which is what catches the
  ``|unlocalize`` bug — German renders it "1,5", ``parseFloat`` stops at the
  comma, and the scaler silently loses half the fruit;
* one with an **amount-less** line ("Salz und Pfeffer"), which must not scale
  and must not print "0 g";
* one with a **four-digit** amount, so the thousands separator shows;
* one **short** recipe, because a collection of long ones hides how the card
  grid handles a two-line description.

Three of the four also carry a **diagram** — steps that merge into one another,
which is what the recipe page draws as a Cooking-for-Engineers table and what
the cooking view walks through. Each covers a shape the others do not:

* *Kartoffelsalat* **branches**: the potatoes and the dressing are made
  separately and meet. A recipe that is one straight chain never exercises the
  column arithmetic that puts two branches side by side.
* *Zwetschgenkuchen* has a **standing instruction** with nothing flowing into
  it ("heat the oven"), which is the full-width row across the top of the
  reference diagram, and a step with a **duration**, which is what the cooking
  view offers a timer for.
* *Linsen mit Spätzle* deliberately has **no diagram at all**, because every
  recipe written before this feature existed is in that state and the page has
  to be right for them too.

Refuses to run unless DEBUG is on. It creates an account with a known password,
which is exactly the thing that must never reach a deployment.
"""

from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.pantry import catalogue, units
from apps.recipes.models import (
    CookLog, CookPortion, PortionSize, Recipe, RecipeIngredient, RecipeStep, Tag,
)


def _amount_and_unit(amount, unit):
    """The three columns an ingredient line's quantity actually needs.

    ``no_amount`` rather than merely a null amount: the form refuses a line that
    answers neither, so a seed that left it off would produce a collection every
    page of which comes back with an error the first time somebody opens it to
    fix a typo. A line with no amount in this table means "to taste", which is
    exactly what the flag says.
    """
    return {
        "amount": Decimal(amount) if amount is not None else None,
        "no_amount": amount is None,
        "unit": units.normalise(unit),
    }

DEV_USERNAME = "claude"
DEV_PASSWORD = "kitchen-dev-pass"

# An ingredient is (amount, unit, name, note) with an optional fifth element:
# the index of the step in this recipe's "diagram" list that consumes it.
# "optional" names the lines the recipe works without, and "alternatives" holds
# (the line it replaces, amount, unit, name, note) — a substitute is a full
# ingredient row here, not a note, because "200 g Butter" is replaced by "180 g
# Margarine" and not by the word "margarine".
RECIPES = [
    {
        "title": "Kartoffelsalat",
        "description": "Der schwäbische, mit Brühe und Essig — ohne Mayonnaise.",
        "servings": 4, "prep": 20, "cook": 25,
        "tags": ["Beilage", "vegetarisch"],
        "steps": [
            "Kartoffeln waschen und in der Schale garen, bis sie weich sind.",
            "Noch warm pellen und in dünne Scheiben schneiden.",
            "Brühe erhitzen, mit Essig, Senf, Salz und Pfeffer abschmecken und über "
            "die warmen Kartoffeln gießen.",
            "Mindestens eine Stunde ziehen lassen, dann Öl und Zwiebeln unterheben.",
        ],
        # Branching: the potatoes and the dressing are made separately and meet
        # at "übergießen". `into` is an index into this list; None is a root.
        "diagram": [
            {"text": "in der Schale garen", "into": 1, "minutes": 25},   # 0
            {"text": "pellen, in Scheiben", "into": 3},                  # 1
            {"text": "verrühren, abschmecken", "into": 3},               # 2
            {"text": "übergießen, ziehen lassen", "into": 4, "minutes": 60,
             "detail": "Mindestens eine Stunde, damit die Kartoffeln die Brühe ziehen."},  # 3
            {"text": "Öl und Zwiebeln unterheben", "into": None},        # 4
        ],
        "ingredients": [
            ("1000", "g", "festkochende Kartoffeln", "", 0),
            ("250", "ml", "Gemüsebrühe", "heiß", 2),
            ("4", "EL", "Weißweinessig", "", 2),
            ("1", "TL", "mittelscharfer Senf", "", 2),
            (None, "", "Salz und Pfeffer", "", 2),
            ("2", "EL", "Sonnenblumenöl", "", 4),
            ("1", "", "Zwiebel", "fein gewürfelt", 4),
        ],
        "alternatives": [
            ("Sonnenblumenöl", "2", "EL", "Rapsöl", ""),
        ],
    },
    {
        "title": "Linsen mit Spätzle",
        "description": "Sonntagsessen. Schmeckt am zweiten Tag besser.",
        "servings": 6, "prep": 25, "cook": 90,
        "tags": ["Hauptgericht", "Sonntag"],
        "steps": [
            "Linsen mit Lorbeer und Suppengrün aufsetzen und weich kochen.",
            "Mehlschwitze aus Butter und Mehl bereiten, mit dem Linsensud ablöschen.",
            "Mit Essig und einem Löffel Senf abschmecken.",
            "Spätzle frisch schaben und mit den Saitenwürsten dazu servieren.",
        ],
        "ingredients": [
            ("500", "g", "Tellerlinsen", "über Nacht eingeweicht"),
            ("1", "Bund", "Suppengrün", "geputzt"),
            ("2", "EL", "Butter", ""),
            ("2", "EL", "Mehl", ""),
            ("3", "EL", "Rotweinessig", ""),
            ("6", "", "Saitenwürste", ""),
            ("500", "g", "Spätzle", ""),
        ],
    },
    {
        "title": "Zwetschgenkuchen",
        "description": "Blechkuchen mit Hefeteig, Ende August.",
        "servings": 12, "prep": 40, "cook": 45,
        "tags": ["Kuchen", "Sommer"],
        "steps": [
            "Hefeteig ansetzen und an einem warmen Ort gehen lassen.",
            "Teig auf ein Blech ausrollen, Zwetschgen dicht dachziegelartig belegen.",
            "Bei 180 °C etwa 45 Minuten backen.",
            "Noch warm mit Zimtzucker bestreuen.",
        ],
        # The shape from the reference diagram: a standing instruction with
        # nothing flowing into it, which draws as a full-width row across the
        # top, and a step with a duration for the cooking view to time.
        "diagram": [
            {"text": "Ofen auf 180 °C vorheizen", "into": None},                 # 0
            {"text": "Hefeteig ansetzen, gehen lassen", "into": 2, "minutes": 60},  # 1
            {"text": "ausrollen, dachziegelartig belegen", "into": 3},           # 2
            {"text": "backen", "into": 4, "minutes": 45},                        # 3
            {"text": "noch warm bestreuen", "into": None},                       # 4
        ],
        "ingredients": [
            ("500", "g", "Mehl", "", 1),
            ("1", "Würfel", "frische Hefe", "", 1),
            ("250", "ml", "Milch", "lauwarm", 1),
            ("80", "g", "Zucker", "", 1),
            # The fractional one. Scale this recipe to 18 servings and the
            # amount must read 2,25 — not 1,5.
            ("1.5", "kg", "Zwetschgen", "entsteint und halbiert", 2),
            ("2", "TL", "Zimtzucker", "", 4),
        ],
        "optional": ["Zimtzucker"],
        "alternatives": [
            ("Zwetschgen", "1.5", "kg", "Mirabellen", "entsteint"),
        ],
    },
    {
        "title": "Nudeln mit Pesto",
        "description": "Für die Abende, an denen niemand kochen will.",
        "servings": 2, "prep": 5, "cook": 12,
        "tags": ["Hauptgericht", "schnell", "vegetarisch"],
        "steps": [
            "Nudeln in reichlich Salzwasser al dente kochen.",
            "Basilikum, Pinienkerne, Knoblauch und Parmesan mit dem Öl mixen.",
            "Nudeln abgießen, etwas Kochwasser auffangen und das Pesto damit cremig rühren.",
        ],
        "diagram": [
            {"text": "al dente kochen", "into": 2, "minutes": 10},   # 0
            # The one step in the seed that is not a whole number of minutes.
            # It is here for the same reason Zwetschgenkuchen's 1,5 kg is: a
            # duration of 45 s is what makes a page that reads `minutes`
            # instead of `timer_seconds` visibly wrong ("0 min", or a countdown
            # that never starts), and a seed without one hides the whole class.
            {"text": "mixen", "into": 2, "seconds": 45},              # 1
            {"text": "cremig rühren", "into": None,
             "detail": "Etwas Kochwasser auffangen und nach und nach zugeben."},  # 2
        ],
        "ingredients": [
            ("250", "g", "Spaghetti", "", 0),
            ("60", "g", "Basilikum", "nur die Blätter", 1),
            ("30", "g", "Pinienkerne", "geröstet", 1),
            ("40", "g", "Parmesan", "gerieben", 1),
            ("80", "ml", "Olivenöl", "", 1),
            ("1", "Zehe", "Knoblauch", "", 1),
        ],
        "optional": ["Parmesan"],
        "alternatives": [
            ("Pinienkerne", "30", "g", "Walnüsse", "grob gehackt"),
        ],
    },
]

# A few evenings' worth of history, so the recipe page has something to show and
# the median-time calculation has more than one number to work with. Each entry
# is (recipe title, days ago, servings made, minutes, {size: how many}).
COOKINGS = [
    ("Kartoffelsalat", 4, 4, 55, {PortionSize.REGULAR: 2, PortionSize.TOGO: 1}),
    ("Kartoffelsalat", 25, 6, 50, {PortionSize.LARGE: 2, PortionSize.SMALL: 2}),
    ("Kartoffelsalat", 60, 4, 130, {PortionSize.REGULAR: 4}),
    ("Nudeln mit Pesto", 2, 2, 18, {PortionSize.REGULAR: 1, PortionSize.CHILD: 2}),
]


class Command(BaseCommand):
    help = "Create a development account and a handful of recipes. DEBUG only."

    def handle(self, *args, **options):
        if not settings.DEBUG:
            raise CommandError(
                "seed_demo creates an account with a known password and refuses to "
                "run with DEBUG off. If you meant to create the fallback "
                "administrator on a deployment, use `createsuperuser`."
            )

        user, created = User.objects.get_or_create(username=DEV_USERNAME)
        user.set_password(DEV_PASSWORD)
        user.is_staff = True
        user.is_superuser = True
        user.save()
        self.stdout.write(
            f"{'created' if created else 'reset'} {DEV_USERNAME} / {DEV_PASSWORD}"
        )

        added = 0
        for spec in RECIPES:
            if Recipe.objects.filter(title=spec["title"]).exists():
                continue
            recipe = Recipe.objects.create(
                title=spec["title"], description=spec["description"],
                servings=spec["servings"], prep_minutes=spec["prep"],
                cook_minutes=spec["cook"],
                instructions="\n\n".join(spec["steps"]), created_by=user,
            )
            for name in spec["tags"]:
                # Case-insensitive, the same rule the form uses — so re-running
                # this does not create a second "Vegetarisch".
                tag = Tag.objects.filter(name__iexact=name).first() or Tag.objects.create(name=name)
                recipe.tags.add(tag)
            # Steps first: an ingredient names the step that consumes it, and
            # a step names the step it feeds into — both by index into the
            # spec, which only becomes a foreign key once the row exists.
            steps = [
                RecipeStep.objects.create(
                    recipe=recipe, position=position, text=entry["text"],
                    detail=entry.get("detail", ""), minutes=entry.get("minutes"),
                    seconds=entry.get("seconds"),
                )
                for position, entry in enumerate(spec.get("diagram", []))
            ]
            for step, entry in zip(steps, spec.get("diagram", [])):
                if entry.get("into") is not None:
                    step.parent = steps[entry["into"]]
                    step.save(update_fields=["parent"])

            optional = set(spec.get("optional", []))
            lines = {}
            for position, row in enumerate(spec["ingredients"]):
                amount, unit, name, note = row[:4]
                into = row[4] if len(row) > 4 else None
                lines[name] = RecipeIngredient.objects.create(
                    recipe=recipe, position=position,
                    # The table above is written the way somebody writing a
                    # recipe writes it — "EL", "Bund", "Würfel". The column
                    # holds a code, so it goes through the same normalisation
                    # recipes/0003 used on the rows that predate the catalogue.
                    **_amount_and_unit(amount, unit),
                    name=name, note=note,
                    optional=name in optional,
                    step=steps[into] if into is not None else None,
                )

            for offset, (replaces, amount, unit, name, note) in enumerate(
                spec.get("alternatives", [])
            ):
                RecipeIngredient.objects.create(
                    recipe=recipe, position=len(spec["ingredients"]) + offset,
                    **_amount_and_unit(amount, unit),
                    name=name, note=note,
                    # A substitute has no step of its own: it takes its place in
                    # the diagram from the line it replaces.
                    alternative_for=lines[replaces],
                )

            # Point the lines at the ingredient catalogue, exactly as saving the
            # form does. Without it the seeded collection is the one thing in
            # the app that cannot answer "what can I cook" — which is a poor
            # demonstration of a feature.
            catalogue.resolve_lines(list(recipe.ingredients.all()), user=user)
            added += 1

        cooked = self._add_cookings(user)

        self.stdout.write(self.style.SUCCESS(
            f"{added} recipes added ({Recipe.objects.count()} in total, "
            f"{Tag.objects.count()} tags, {cooked} cookings recorded)"
        ))

    def _add_cookings(self, user):
        """Past evenings, so the recipe page has a history to show.

        ``cooked_at`` is ``auto_now_add``, which is right for the app — the
        moment somebody presses save is the moment they cooked — and means the
        date has to be moved afterwards with a queryset update rather than
        passed to ``create``.
        """
        if CookLog.objects.exists():
            # Checked once for the whole set rather than per recipe: three of
            # the four entries below are the *same* recipe on three different
            # evenings, which is the point of them, and a per-recipe guard
            # would keep only the first.
            return 0

        added = 0
        for title, days_ago, servings, minutes, portions in COOKINGS:
            recipe = Recipe.objects.filter(title=title).first()
            if recipe is None:
                continue
            log = CookLog.objects.create(
                recipe=recipe, cooked_by=user, servings_made=servings, minutes=minutes,
            )
            CookPortion.objects.bulk_create([
                CookPortion(log=log, size=size, count=count)
                for size, count in portions.items()
            ])
            CookLog.objects.filter(pk=log.pk).update(
                cooked_at=timezone.now() - timedelta(days=days_ago)
            )
            added += 1
        return added
