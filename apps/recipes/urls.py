from django.urls import path

from apps.recipes import views

app_name = "recipes"

urlpatterns = [
    path("", views.home, name="home"),
    path("recipes/", views.recipe_list, name="list"),
    # Before the slug pattern, or "new" is looked up as a recipe called "new".
    path("recipes/new/", views.recipe_add, name="add"),
    path("recipes/<slug:slug>/", views.recipe_detail, name="detail"),
    path("recipes/<slug:slug>/edit/", views.recipe_edit, name="edit"),
    path("recipes/<slug:slug>/delete/", views.recipe_delete, name="delete"),
    path("tags/", views.tag_list, name="tags"),
]
