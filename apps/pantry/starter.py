"""A catalogue to start from, so the suggestions are useful on the first day.

An empty catalogue is a working catalogue that suggests nothing, and an
autosuggest that suggests nothing is one people stop looking at before it has
had a chance to fill up. So the app ships knowing what a German household
kitchen has in it — including, and this is the whole point of the exercise,
that water is measured in millilitres and butter in grams.

**The names are German.** Not an oversight and not a candidate for the
translation catalogue: these are *data*, they are what somebody types into a
recipe, and the app's default language is German. An English household would
delete these and type its own — the catalogue is editable, which is exactly why
seeding it is safe.

**The purchase sizes are the common German pack.** 1 kg of flour, 250 g of
butter, a litre of milk. They exist so a shopping list can say "one bag" rather
than "800 g", and being slightly wrong for one shop is not a failure: the size
is editable per ingredient and only ever used to round a shortfall up.

Loaded by ``pantry/migrations/0002``, and again — idempotently, matching on the
name — by ``manage.py seed_catalogue`` for a database that predates it.
"""

# (name, usual unit, category, aliases, purchase sizes as (amount, unit, label))
STARTER = [
    # --- store cupboard ---------------------------------------------------
    ("Mehl", "g", "dry", ["Weizenmehl", "Mehl Type 405"], [(1, "kg", "Packung")]),
    ("Zucker", "g", "dry", ["Kristallzucker", "Haushaltszucker"], [(1, "kg", "Packung")]),
    ("Puderzucker", "g", "dry", [], [(250, "g", "Packung")]),
    ("Brauner Zucker", "g", "dry", [], [(500, "g", "Packung")]),
    ("Salz", "g", "spice", ["Kochsalz", "Meersalz"], [(500, "g", "Packung")]),
    ("Pfeffer", "g", "spice", ["schwarzer Pfeffer"], [(50, "g", "Mühle")]),
    ("Backpulver", "pack", "bakery", [], [(1, "pack", "Päckchen")]),
    ("Natron", "g", "bakery", [], [(50, "g", "Päckchen")]),
    ("Vanillezucker", "pack", "bakery", [], [(1, "pack", "Päckchen")]),
    ("Speisestärke", "g", "dry", ["Stärke", "Maisstärke"], [(400, "g", "Packung")]),
    ("Haferflocken", "g", "dry", [], [(500, "g", "Packung")]),
    ("Reis", "g", "dry", [], [(1, "kg", "Packung")]),
    ("Nudeln", "g", "dry", ["Pasta", "Spaghetti"], [(500, "g", "Packung")]),
    ("Spätzle", "g", "dry", [], [(500, "g", "Packung")]),
    ("Linsen", "g", "dry", ["Tellerlinsen"], [(500, "g", "Packung")]),
    ("Kichererbsen", "g", "dry", [], [(400, "g", "Dose")]),
    ("Semmelbrösel", "g", "bakery", ["Paniermehl"], [(400, "g", "Packung")]),
    ("Gemüsebrühe", "ml", "dry", ["Brühe", "Gemüsebrühe (Instant)"], []),
    ("Tomatenmark", "g", "dry", [], [(200, "g", "Dose")]),
    ("Passierte Tomaten", "ml", "dry", ["Tomatenpassata", "Passata"], [(500, "g", "Packung")]),
    ("Gehackte Tomaten", "g", "dry", ["Dosentomaten"], [(400, "g", "Dose")]),
    ("Honig", "g", "dry", [], [(500, "g", "Glas")]),
    ("Marmelade", "g", "dry", ["Konfitüre"], [(340, "g", "Glas")]),
    ("Erdnussbutter", "g", "dry", [], [(350, "g", "Glas")]),

    # --- oils, vinegars, sauces -------------------------------------------
    ("Sonnenblumenöl", "ml", "dry", ["Öl", "Pflanzenöl"], [(1, "l", "Flasche")]),
    ("Rapsöl", "ml", "dry", [], [(1, "l", "Flasche")]),
    ("Olivenöl", "ml", "dry", [], [(500, "ml", "Flasche")]),
    ("Weißweinessig", "ml", "dry", [], [(500, "ml", "Flasche")]),
    ("Rotweinessig", "ml", "dry", [], [(500, "ml", "Flasche")]),
    ("Balsamico", "ml", "dry", ["Balsamicoessig"], [(250, "ml", "Flasche")]),
    ("Senf", "g", "dry", ["mittelscharfer Senf"], [(200, "g", "Glas")]),
    ("Ketchup", "ml", "dry", [], [(500, "ml", "Flasche")]),
    ("Sojasauce", "ml", "dry", ["Sojasoße"], [(150, "ml", "Flasche")]),

    # --- dairy and eggs ----------------------------------------------------
    ("Milch", "ml", "dairy", ["Vollmilch", "H-Milch"], [(1, "l", "Packung")]),
    ("Butter", "g", "dairy", [], [(250, "g", "Stück")]),
    ("Margarine", "g", "dairy", [], [(500, "g", "Becher")]),
    ("Sahne", "ml", "dairy", ["Schlagsahne", "Schlagobers"], [(200, "ml", "Becher")]),
    ("Schmand", "g", "dairy", [], [(200, "g", "Becher")]),
    ("Crème fraîche", "g", "dairy", [], [(200, "g", "Becher")]),
    ("Saure Sahne", "g", "dairy", [], [(200, "g", "Becher")]),
    ("Joghurt", "g", "dairy", ["Naturjoghurt"], [(500, "g", "Becher")]),
    ("Quark", "g", "dairy", ["Speisequark"], [(500, "g", "Packung")]),
    ("Frischkäse", "g", "dairy", [], [(200, "g", "Becher")]),
    ("Mozzarella", "g", "dairy", [], [(125, "g", "Kugel")]),
    ("Parmesan", "g", "dairy", ["Parmigiano"], [(150, "g", "Stück")]),
    ("Gouda", "g", "dairy", [], [(200, "g", "Packung")]),
    ("Emmentaler", "g", "dairy", [], [(200, "g", "Packung")]),
    ("Feta", "g", "dairy", ["Schafskäse"], [(200, "g", "Packung")]),
    ("Eier", "pc", "dairy", ["Ei", "Hühnerei"], [(10, "pc", "Karton")]),
    ("Hefe", "cube", "bakery", ["frische Hefe", "Würfelhefe"], [(1, "cube", "Würfel")]),
    ("Trockenhefe", "pack", "bakery", [], [(1, "pack", "Päckchen")]),

    # --- fruit and vegetables ---------------------------------------------
    ("Kartoffeln", "g", "produce", ["Kartoffel", "festkochende Kartoffeln"],
     [(2, "kg", "Netz")]),
    ("Zwiebel", "pc", "produce", ["Zwiebeln", "Küchenzwiebel"], [(1, "kg", "Netz")]),
    ("Knoblauch", "clove", "produce", ["Knoblauchzehe", "Knoblauchzehen"],
     [(1, "pc", "Knolle")]),
    ("Lauch", "pc", "produce", ["Porree"], []),
    ("Möhren", "g", "produce", ["Karotten", "Möhre", "Karotte"], [(1, "kg", "Beutel")]),
    ("Sellerie", "g", "produce", ["Knollensellerie"], []),
    ("Suppengrün", "bunch", "produce", [], [(1, "bunch", "Bund")]),
    ("Tomaten", "g", "produce", ["Tomate"], [(500, "g", "Schale")]),
    ("Paprika", "pc", "produce", ["Paprikaschote"], []),
    ("Zucchini", "pc", "produce", [], []),
    ("Aubergine", "pc", "produce", [], []),
    ("Gurke", "pc", "produce", ["Salatgurke"], []),
    ("Champignons", "g", "produce", ["Pilze"], [(250, "g", "Schale")]),
    ("Spinat", "g", "produce", [], [(500, "g", "Packung")]),
    ("Brokkoli", "g", "produce", [], []),
    ("Blumenkohl", "pc", "produce", [], []),
    ("Weißkohl", "g", "produce", ["Weißkraut"], []),
    ("Salat", "pc", "produce", ["Kopfsalat"], []),
    ("Zitrone", "pc", "produce", ["Zitronen"], []),
    ("Apfel", "pc", "produce", ["Äpfel"], [(1, "kg", "Beutel")]),
    ("Banane", "pc", "produce", ["Bananen"], []),
    ("Zwetschgen", "g", "produce", ["Pflaumen", "Zwetschge"], [(1, "kg", "Schale")]),
    ("Beeren", "g", "produce", ["Himbeeren", "Heidelbeeren"], [(125, "g", "Schale")]),

    # --- herbs and spices --------------------------------------------------
    ("Petersilie", "bunch", "spice", [], [(1, "bunch", "Bund")]),
    ("Schnittlauch", "bunch", "spice", [], [(1, "bunch", "Bund")]),
    ("Basilikum", "bunch", "spice", [], []),
    ("Thymian", "g", "spice", [], []),
    ("Rosmarin", "g", "spice", [], []),
    ("Lorbeerblatt", "leaf", "spice", ["Lorbeer", "Lorbeerblätter"], []),
    ("Paprikapulver", "tsp", "spice", ["Paprikagewürz"], [(50, "g", "Streuer")]),
    ("Muskatnuss", "pinch", "spice", ["Muskat"], []),
    ("Zimt", "tsp", "spice", ["Zimtpulver"], [(50, "g", "Streuer")]),
    ("Kreuzkümmel", "tsp", "spice", ["Kumin"], []),
    ("Currypulver", "tsp", "spice", ["Curry"], []),
    ("Oregano", "tsp", "spice", [], []),

    # --- meat and fish -----------------------------------------------------
    ("Hackfleisch", "g", "meat", ["Gehacktes", "Rinderhack"], [(500, "g", "Packung")]),
    ("Hähnchenbrust", "g", "meat", ["Hühnerbrust"], [(400, "g", "Packung")]),
    ("Schweinefilet", "g", "meat", [], []),
    ("Rindfleisch", "g", "meat", [], []),
    ("Speck", "g", "meat", ["Bauchspeck", "Schinkenspeck"], [(200, "g", "Packung")]),
    ("Saitenwürste", "pc", "meat", ["Wiener", "Saitenwurst"], []),
    ("Lachs", "g", "meat", ["Lachsfilet"], [(250, "g", "Packung")]),
    ("Thunfisch", "g", "meat", [], [(150, "g", "Dose")]),

    # --- bread and baking --------------------------------------------------
    ("Brot", "g", "bakery", [], [(500, "g", "Laib")]),
    ("Brötchen", "pc", "bakery", ["Semmel", "Weckle"], []),
    ("Toastbrot", "slice", "bakery", ["Toast"], [(500, "g", "Packung")]),
    ("Schokolade", "g", "bakery", ["Zartbitterschokolade"], [(100, "g", "Tafel")]),
    ("Kakao", "g", "bakery", ["Kakaopulver"], [(125, "g", "Packung")]),
    ("Mandeln", "g", "bakery", ["gemahlene Mandeln"], [(200, "g", "Packung")]),
    ("Walnüsse", "g", "bakery", [], [(200, "g", "Packung")]),
    ("Rosinen", "g", "bakery", [], [(200, "g", "Packung")]),

    # --- drinks ------------------------------------------------------------
    ("Wasser", "ml", "drink", ["Leitungswasser", "Mineralwasser"], [(1, "l", "Flasche")]),
    ("Weißwein", "ml", "drink", [], [(750, "ml", "Flasche")]),
    ("Rotwein", "ml", "drink", [], [(750, "ml", "Flasche")]),
    ("Apfelsaft", "ml", "drink", [], [(1, "l", "Flasche")]),
]


def load(Ingredient, IngredientAlias, PurchaseSize):
    """Insert anything missing. Matched on the name, so it is safe to re-run.

    Takes the model classes rather than importing them, because the caller is
    a migration and a migration must work against the *historical* models —
    importing the live ones is how a data migration starts failing two releases
    later, when a field it never mentioned is added.
    """
    from django.utils.text import slugify

    existing = {name.casefold() for name in Ingredient.objects.values_list("name", flat=True)}
    aliased = {name.casefold() for name in IngredientAlias.objects.values_list("name", flat=True)}
    taken = set(Ingredient.objects.values_list("slug", flat=True))
    added = 0

    for name, unit, category, aliases, sizes in STARTER:
        if name.casefold() in existing or name.casefold() in aliased:
            continue
        slug, n = slugify(name)[:120] or "zutat", 2
        base = slug
        while slug in taken:
            slug = f"{base}-{n}"
            n += 1
        taken.add(slug)

        ingredient = Ingredient.objects.create(
            name=name, slug=slug, default_unit=unit, category=category,
        )
        existing.add(name.casefold())
        added += 1

        for alias in aliases:
            # An alias that is already some other ingredient's name is dropped
            # rather than raising: the household's own row wins, and losing one
            # suggestion is better than a migration that will not apply.
            if alias.casefold() in existing or alias.casefold() in aliased:
                continue
            IngredientAlias.objects.create(ingredient=ingredient, name=alias)
            aliased.add(alias.casefold())

        for amount, size_unit, label in sizes:
            PurchaseSize.objects.create(
                ingredient=ingredient, amount=amount, unit=size_unit, label=label,
            )
    return added
