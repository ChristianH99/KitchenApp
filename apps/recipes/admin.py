"""The models in the Django admin.

The app's own screens are where recipes are written; this is the fallback
surface for the things they deliberately do not offer — merging two tags that
turned out to mean the same thing, reassigning a recipe left behind by an
account that is gone, looking at a row when a page is behaving oddly. Staff
only, and staff is granted from DSM (apps/accounts/oidc.py) or by hand.
"""

from django.contrib import admin

from apps.recipes.models import Recipe, RecipeIngredient, Tag


class RecipeIngredientInline(admin.TabularInline):
    model = RecipeIngredient
    extra = 1


@admin.register(Recipe)
class RecipeAdmin(admin.ModelAdmin):
    list_display = ("title", "servings", "total_minutes", "created_by", "updated_at")
    list_filter = ("tags", "created_by")
    search_fields = ("title", "description", "instructions", "ingredients__name")
    filter_horizontal = ("tags",)
    inlines = [RecipeIngredientInline]
    # Regenerated from the title only when blank (see Recipe.save), so an
    # existing recipe keeps the URL anybody has bookmarked.
    readonly_fields = ("slug", "created_at", "updated_at")


@admin.register(Tag)
class TagAdmin(admin.ModelAdmin):
    list_display = ("name", "recipe_count")
    search_fields = ("name",)
    readonly_fields = ("slug",)

    def get_queryset(self, request):
        from django.db.models import Count

        return super().get_queryset(request).annotate(_n=Count("recipes"))

    @admin.display(description="Recipes", ordering="_n")
    def recipe_count(self, obj):
        return obj._n
