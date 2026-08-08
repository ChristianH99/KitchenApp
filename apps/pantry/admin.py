"""Admin registrations, for the cases the app's own pages deliberately do not cover.

The catalogue page in this app is for the everyday edit — a name, the usual
unit, the sizes it is sold in. The admin is where a bulk tidy-up happens (two
rows for one substance, an alias pointing at the wrong thing), which is a job
worth doing once a year and not worth a page of its own.
"""

from django.contrib import admin

from apps.pantry.models import Ingredient, IngredientAlias, PantryItem, PurchaseSize


class AliasInline(admin.TabularInline):
    model = IngredientAlias
    extra = 1


class PurchaseSizeInline(admin.TabularInline):
    model = PurchaseSize
    extra = 1


@admin.register(Ingredient)
class IngredientAdmin(admin.ModelAdmin):
    list_display = ("name", "category", "default_unit")
    list_filter = ("category",)
    search_fields = ("name", "aliases__name")
    prepopulated_fields = {"slug": ("name",)}
    inlines = [AliasInline, PurchaseSizeInline]


@admin.register(PantryItem)
class PantryItemAdmin(admin.ModelAdmin):
    list_display = ("ingredient", "amount", "unit", "checked_at")
    list_select_related = ("ingredient",)
    search_fields = ("ingredient__name",)
