"""The URLs that do not need a session — and nothing else does.

This app's rule is the strict one: a login is required *everywhere*, and the
exceptions are enumerated here rather than opted into per view. The two shapes
give different failure modes and only one of them is safe. A decorator you have
to remember is a decorator somebody will forget, and the page they forget it on
looks completely normal — it just answers to anybody. A list, by contrast,
fails towards refusal: forget to add a URL here and the page asks for a login,
which is noticed in about four seconds.

Each entry is the ``(app_name, url_name)`` pair Django resolves, so a route
renamed without updating this file fails ``config/tests.py`` rather than
silently becoming reachable.
"""

OPEN = {
    # The health probe. Answers "ok" or 503 and deliberately nothing else.
    ("", "health"),

    # The site icon. A browser fetches it per *origin* and before anybody has
    # signed in — the login page wants it too — and an icon is not household
    # data. Gating it would replace a 404 in the log with a 302 in the log.
    ("", "favicon"),

    # The login page itself, and the local sign-in POST.
    ("accounts", "login"),

    # Signing out has to work from a session that is already broken, which is
    # exactly when it is needed.
    ("accounts", "logout"),

    # mozilla-django-oidc's three views. Included without a namespace, hence
    # the empty app_name. The callback in particular *cannot* be gated: the
    # user arriving at it is by definition not signed in yet, and gating it
    # produces an infinite redirect between this app and the SSO server that
    # looks exactly like a misconfigured client.
    ("", "oidc_authentication_init"),
    ("", "oidc_authentication_callback"),
    ("", "oidc_logout"),

    # The language switcher, which the login page carries: somebody who cannot
    # read the sign-in form cannot sign in to change the language.
    ("", "set_language"),
}
