"""Top the catalogue up, and point existing recipe lines at it.

Two jobs that are usually wanted together and are separately safe to repeat:

``--starter`` re-runs the shipped list, which matters for a database created
before it existed or one somebody has thinned out and wants back.

``--link`` walks every recipe line that has no catalogue row and gives it one,
minting names the catalogue does not know. This is the migration's deliberate
omission made opt-in: turning "festkochende Kartoffeln" into a substance is a
judgement, and doing it silently during an upgrade leaves a catalogue full of
rows nobody agreed to. Run with ``--dry-run`` first — it prints exactly what it
would create, which is the point of it.

Unlike ``seed_demo`` this one is safe with DEBUG off: it invents no accounts and
no recipes, only the substances the recipes already name.
"""

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.pantry import catalogue, starter
from apps.pantry.models import Ingredient, IngredientAlias, PurchaseSize
from apps.recipes.models import RecipeIngredient


class Command(BaseCommand):
    help = "Fill the ingredient catalogue and link recipe lines to it."

    def add_arguments(self, parser):
        parser.add_argument("--starter", action="store_true",
                            help="Insert any of the shipped ingredients that are missing.")
        parser.add_argument("--link", action="store_true",
                            help="Give every unlinked recipe line a catalogue row.")
        parser.add_argument("--dry-run", action="store_true",
                            help="Say what would happen and change nothing.")

    def handle(self, *args, **options):
        # Neither switch means both: the bare command should do the obvious
        # thing rather than print usage at somebody who typed what they wanted.
        do_starter = options["starter"] or not options["link"]
        do_link = options["link"] or not options["starter"]
        dry = options["dry_run"]

        if do_starter:
            self._starter(dry)
        if do_link:
            self._link(dry)

    def _starter(self, dry):
        have = {name.casefold() for name in Ingredient.objects.values_list("name", flat=True)}
        missing = [row[0] for row in starter.STARTER if row[0].casefold() not in have]
        if dry:
            self.stdout.write(f"would add {len(missing)} shipped ingredients")
            for name in missing:
                self.stdout.write(f"  + {name}")
            return
        with transaction.atomic():
            added = starter.load(Ingredient, IngredientAlias, PurchaseSize)
        self.stdout.write(self.style.SUCCESS(f"added {added} shipped ingredients"))

    def _link(self, dry):
        lines = list(
            RecipeIngredient.objects
            .filter(ingredient__isnull=True)
            .exclude(name="")
            .select_related("recipe")
        )
        if dry:
            known = catalogue.index()
            matched, new = 0, {}
            for line in lines:
                if catalogue.lookup(line.name, known) is not None:
                    matched += 1
                else:
                    new.setdefault(catalogue.fold(line.name), line.name)
            self.stdout.write(
                f"would link {matched} lines to existing rows "
                f"and create {len(new)} new ones"
            )
            for name in sorted(new.values(), key=str.casefold):
                self.stdout.write(f"  + {name}")
            return

        with transaction.atomic():
            before = Ingredient.objects.count()
            touched = catalogue.resolve_lines(lines)
            created = Ingredient.objects.count() - before
        self.stdout.write(self.style.SUCCESS(
            f"linked {len(touched)} lines, creating {created} ingredients"
        ))
