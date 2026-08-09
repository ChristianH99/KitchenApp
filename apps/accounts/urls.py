"""Account URLs — the local login only.

``mozilla_django_oidc``'s three views are **not** included here, and that is not
an oversight. This module declares ``app_name``, which namespaces everything
included through it; the library reverses ``oidc_authentication_callback`` by
that bare name from inside its own views, so a namespaced include breaks the
callback with a NoReverseMatch at the least helpful possible moment — mid-login,
on the provider's redirect back, with nothing but a 500 to go on. They are wired
up at the project level instead (config/urls.py), where no namespace applies.
"""

from django.urls import path

from apps.accounts import sso_views, users, views

app_name = "accounts"

urlpatterns = [
    path("login/", views.login_view, name="login"),
    path("logout/", views.logout_view, name="logout"),

    # The household's accounts. Every one of these is staff-only, enforced by
    # the decorator on the view and checked from the outside by
    # apps/accounts/tests.py, which walks the URLconf for `user-*` and refuses
    # to let any of them answer an ordinary account.
    path("users/", users.user_list, name="user-list"),
    path("users/new/", users.user_add, name="user-add"),
    path("users/<int:pk>/", users.user_edit, name="user-edit"),
    path("users/<int:pk>/password/", users.user_password, name="user-password"),
    path("users/<int:pk>/delete/", users.user_delete, name="user-delete"),
    path("users/<int:pk>/active/", users.user_toggle_active, name="user-active"),
    path("users/<int:pk>/unlink/", users.user_unlink_sso, name="user-unlink"),

    # The Synology connection. Superuser only — a narrower door than the pages
    # above, because this one decides how everybody authenticates and holds the
    # client secret.
    # One person's own settings. Not staff-only and not superuser-only: it is
    # about a body rather than about the household, and everybody has one.
    path("preferences/", views.preferences, name="preferences"),

    path("sso/", sso_views.sso_settings, name="sso"),
    path("sso/discover/", sso_views.sso_discover, name="sso-discover"),
    path("sso/check/", sso_views.sso_check, name="sso-check"),
]
