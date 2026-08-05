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

Refuses to run unless DEBUG is on. It creates an account with a known password,
which is exactly the thing that must never reach a deployment.
"""

from decimal import Decimal

from django.conf import settings
from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError

from apps.recipes.models import Recipe, RecipeIngredient, Tag

DEV_USERNAME = "claude"
DEV_PASSWORD = "kitchen-dev-pass"

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
        "ingredients": [
            ("1000", "g", "festkochende Kartoffeln", ""),
            ("250", "ml", "Gemüsebrühe", "heiß"),
            ("4", "EL", "Weißweinessig", ""),
            ("2", "EL", "Sonnenblumenöl", ""),
            ("1", "", "Zwiebel", "fein gewürfelt"),
            ("1", "TL", "mittelscharfer Senf", ""),
            (None, "", "Salz und Pfeffer", ""),
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
        "ingredients": [
            ("500", "g", "Mehl", ""),
            ("1", "Würfel", "frische Hefe", ""),
            ("250", "ml", "Milch", "lauwarm"),
            ("80", "g", "Zucker", ""),
            # The fractional one. Scale this recipe to 18 servings and the
            # amount must read 2,25 — not 1,5.
            ("1.5", "kg", "Zwetschgen", "entsteint und halbiert"),
            ("2", "TL", "Zimtzucker", ""),
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
        "ingredients": [
            ("250", "g", "Spaghetti", ""),
            ("60", "g", "Basilikum", "nur die Blätter"),
            ("30", "g", "Pinienkerne", "geröstet"),
            ("40", "g", "Parmesan", "gerieben"),
            ("80", "ml", "Olivenöl", ""),
            ("1", "Zehe", "Knoblauch", ""),
        ],
    },
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
            for position, (amount, unit, name, note) in enumerate(spec["ingredients"]):
                RecipeIngredient.objects.create(
                    recipe=recipe, position=position,
                    amount=Decimal(amount) if amount is not None else None,
                    unit=unit, name=name, note=note,
                )
            added += 1

        self.stdout.write(self.style.SUCCESS(
            f"{added} recipes added ({Recipe.objects.count()} in total, "
            f"{Tag.objects.count()} tags)"
        ))
