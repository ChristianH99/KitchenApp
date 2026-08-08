from django.urls import path

from apps.pantry import views

app_name = "pantry"

urlpatterns = [
    path("", views.pantry_list, name="list"),
    path("save/", views.pantry_save, name="save"),
    path("add/", views.pantry_add, name="add"),
    path("remove/<slug:slug>/", views.pantry_remove, name="remove"),

    path("catalogue/", views.ingredient_list, name="catalogue"),
    # Before the slug pattern, or "new" is looked up as an ingredient called "new".
    path("catalogue/new/", views.ingredient_add, name="ingredient-add"),
    path("catalogue/<slug:slug>/", views.ingredient_edit, name="ingredient-edit"),
    path("catalogue/<slug:slug>/delete/", views.ingredient_delete, name="ingredient-delete"),
]
