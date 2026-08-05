"""Content-Security-Policy.

A CSP is the difference between "an injected string reached the page" and "an
injected string ran". This app has real injection surface — a recipe title, an
ingredient name and a block of instructions, all typed by people and all
rendered into every page that lists them — and each of those is escaped at its
own door. The CSP is what is left when one of those doors is wrong.

The policy is strict, and that is why every script lives in ``static/js/``:
``script-src 'self'`` cannot tell our inline ``<script>`` from an injected one,
so allowing the first allows the second, and a policy with ``'unsafe-inline'``
in it does not stop the attack it exists for. Same for ``style-src`` and the
``style="…"`` attribute.

Deliberately no nonce. A nonce would let inline blocks stay, at the cost of a
per-request value threaded through every template — and it fails *open* the
moment somebody forgets one, which is the quiet kind of regression: the page
still renders. Files cannot be forgotten.

Notes on individual directives:

* ``img-src`` allows ``data:`` because that is how a client-side image preview
  arrives before the photograph is uploaded; the stored ones are same-origin.
* ``form-action 'self'`` means a form on our page cannot be made to post
  somewhere else — which is what an injected ``<form>`` would be for. The OIDC
  login is *not* a form post to the provider (it is a redirect the browser
  follows), so this does not need widening for Synology SSO.
* ``frame-ancestors 'none'`` is the modern X-Frame-Options, which settings.py
  also sets for older browsers.
"""

POLICY = "; ".join([
    "default-src 'self'",
    "script-src 'self'",
    "style-src 'self'",
    "img-src 'self' data:",
    "font-src 'self'",
    "connect-src 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",
    "base-uri 'none'",
    "object-src 'none'",
])

HEADER = "Content-Security-Policy"
REPORT_ONLY_HEADER = "Content-Security-Policy-Report-Only"


class ContentSecurityPolicyMiddleware:
    """Attach the policy to every response.

    ``CSP_REPORT_ONLY`` (env ``DJANGO_CSP_REPORT_ONLY``) sends it as
    *Report-Only* instead: nothing is blocked, violations are reported to the
    browser console. That is for finding out what a new page broke — it is not
    a setting to deploy with.

    The Django admin is exempt. It ships its own inline scripts and styles, we
    do not control them, and it is a staff-only fallback surface rather than
    part of this app's attack surface.
    """

    def __init__(self, get_response):
        from django.conf import settings

        self.get_response = get_response
        self.header = (
            REPORT_ONLY_HEADER if getattr(settings, "CSP_REPORT_ONLY", False) else HEADER
        )

    def __call__(self, request):
        response = self.get_response(request)
        if request.path.startswith("/admin/"):
            return response
        response.headers.setdefault(self.header, POLICY)
        return response
