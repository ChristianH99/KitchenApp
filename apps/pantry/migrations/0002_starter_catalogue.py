"""Fill the catalogue with what a kitchen has in it.

A data migration rather than a management command somebody has to know to run,
because the feature it turns on — suggesting an ingredient and its usual unit
while a recipe is being typed — is invisible until the catalogue has something
in it, and "install this, then run that, then it starts helping" is a step
nobody performs on a NAS.

Reversing it removes only the rows that are still exactly as shipped. A row
that has been edited, or that anything now points at, is left alone: a
migration that rolls back is fixing a deployment, and taking somebody's
corrected catalogue with it would be a far worse outcome than a few unused
rows.
"""

from django.db import migrations

from apps.pantry.starter import STARTER, load


def fill(apps, schema_editor):
    load(
        apps.get_model("pantry", "Ingredient"),
        apps.get_model("pantry", "IngredientAlias"),
        apps.get_model("pantry", "PurchaseSize"),
    )


def empty(apps, schema_editor):
    Ingredient = apps.get_model("pantry", "Ingredient")
    shipped = {name for name, *_rest in STARTER}
    for row in Ingredient.objects.filter(name__in=shipped):
        # Still pointed at by a recipe line or sitting in the cupboard: it has
        # been used, whatever it started as. `used_in` is reached through
        # getattr because the recipe side of that relation may not exist yet in
        # this migration's model state — recipes.0003 depends on pantry.0001,
        # not on this one, so the two can be applied in either order.
        used_in = getattr(row, "used_in", None)
        if used_in is not None and used_in.exists():
            continue
        if hasattr(row, "in_pantry"):
            continue
        row.delete()


class Migration(migrations.Migration):

    dependencies = [
        ("pantry", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(fill, empty),
    ]
