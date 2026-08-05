"""Which sidebar entry is the current page.

The obvious version of this lives in the template — each entry comparing
``request.resolver_match.url_name`` against the name it expects. It works until
two apps use the same ``url_name``, which they will (``list``, ``detail``,
``settings`` are nobody's private words), and then two entries light up at once
and the bug is invisible until somebody notices the sidebar looks odd.

So the mapping lives here, keyed on the ``(app_name, url_name)`` *pair* Django
has already resolved. Two properties make the whole class of bug go away, and
``config/tests.py::TestTheSidebarMarksOnePage`` holds both:

* the sets are pairwise disjoint, so no URL can mark two entries;
* every pair named here exists in the URLconf, so a renamed route fails a test
  instead of quietly marking nothing.

``accounts:*`` is deliberately absent: the user block sits in the sidebar
footer, outside the nav, and is never marked.
"""

# Sidebar entry id -> the (app_name, url_name) pairs that make it current.
# Dotted ids are nested under the parent named before the dot.
ITEMS = {
    "home": {("recipes", "home")},

    "recipes.list": {
        ("recipes", name)
        for name in ("list", "detail", "add", "edit", "delete")
    },
    "recipes.tags": {("recipes", "tags")},
}

# Parent entry -> the entries nested under it. A parent is a link too, but never
# to a page of its own: it is marked when any of its children is.
PARENTS = {
    "recipes": ("recipes.list", "recipes.tags"),
}


def current(match):
    """The sidebar entries a resolved URL marks: the entry itself and, when it
    is nested, its parent. Empty for a page with no entry — or an unresolved
    one, which is the case on an error page."""
    if match is None:
        return frozenset()
    key = (match.app_name, match.url_name)
    active = {name for name, urls in ITEMS.items() if key in urls}
    for parent, children in PARENTS.items():
        if not active.isdisjoint(children):
            active.add(parent)
    return frozenset(active)


def context(request):
    """Template context processor: ``nav_current``, which base.html asks with
    ``{% if 'recipes.list' in nav_current %}``."""
    return {"nav_current": current(getattr(request, "resolver_match", None))}
