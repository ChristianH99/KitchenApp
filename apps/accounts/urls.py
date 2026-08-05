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

from apps.accounts import views

app_name = "accounts"

urlpatterns = [
    path("login/", views.login_view, name="login"),
    path("logout/", views.logout_view, name="logout"),
]
