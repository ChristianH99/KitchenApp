"""Point every ingredient line at the catalogue, and put its unit into the closed set.

Three things happen here and **the order of them is load-bearing**.

The unit column is narrowing from 30 characters to the width of the longest
code, so the free-text values have to be translated *before* the column is
altered rather than after. SQLite does not enforce a CharField's length and
would let a stale "Packung" sit in a six-character column indefinitely — which
is exactly the kind of thing that works on the development machine for a year
and truncates on the first database that does enforce it.

The second is ``no_amount``. The form is about to start refusing a line whose
amount is blank, and every line already in the collection that has none —
"Salz und Pfeffer" — was written when that was simply how you said "to taste".
Left alone they would make every existing recipe unsaveable the next time
somebody opened it to fix a typo, so the flag is set for them here: the rule
arrives with the data already conforming to it.

The third is deliberately *not* done. Nothing here invents catalogue rows for
the names it finds. Matching "festkochende Kartoffeln" to a substance is a
judgement, ``apps/pantry`` has a page for making it, and a migration that
guesses leaves a catalogue full of near-duplicates nobody remembers agreeing
to. ``manage.py build_catalogue`` is the opt-in version of the same job.
"""

import django.db.models.deletion
from django.db import migrations, models

from apps.pantry import units


def normalise_units(apps, schema_editor):
    """Translate the free-text units into catalogue codes, in place.

    Anything ``units.normalise`` does not recognise is left exactly as typed —
    it will show in the dropdown under "As typed" and can be corrected by hand.
    Guessing harder here would be a silent edit to somebody's recipe.
    """
    RecipeIngredient = apps.get_model("recipes", "RecipeIngredient")
    for line in RecipeIngredient.objects.exclude(unit="").only("id", "unit").iterator():
        code = units.normalise(line.unit)
        if code != line.unit:
            RecipeIngredient.objects.filter(pk=line.pk).update(unit=code)


def flag_amountless_lines(apps, schema_editor):
    """Every line already written without an amount meant "to taste"."""
    RecipeIngredient = apps.get_model("recipes", "RecipeIngredient")
    RecipeIngredient.objects.filter(amount__isnull=True).update(no_amount=True)


def unflag(apps, schema_editor):
    RecipeIngredient = apps.get_model("recipes", "RecipeIngredient")
    RecipeIngredient.objects.update(no_amount=False)


class Migration(migrations.Migration):

    dependencies = [
        ('pantry', '0001_initial'),
        ('recipes', '0002_recipeingredient_alternative_for_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='recipeingredient',
            name='ingredient',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='used_in', to='pantry.ingredient', verbose_name='in the catalogue'),
        ),
        migrations.AddField(
            model_name='recipeingredient',
            name='no_amount',
            field=models.BooleanField(default=False, help_text='To taste — salt, pepper, a little oil.', verbose_name='no fixed amount'),
        ),
        # Before the column narrows, not after. See the module docstring.
        migrations.RunPython(normalise_units, migrations.RunPython.noop),
        migrations.RunPython(flag_amountless_lines, unflag),
        migrations.AlterField(
            model_name='recipeingredient',
            name='unit',
            field=models.CharField(blank=True, max_length=6, verbose_name='unit'),
        ),
    ]
