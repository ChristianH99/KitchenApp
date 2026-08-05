# CLAUDE.md

Guidance for Claude Code (claude.ai/code) when working in this repository.

## Project

A self-hosted kitchen app for one household — recipes first, with the shape to
take meal planning, shopping lists and a pantry later as sibling apps rather
than as bolt-ons. Django 6 (Python 3.13, `uv`), server-rendered, running as one
container on a Synology DS723+ behind that NAS's reverse proxy and
authenticating against the Synology SSO Server over OIDC.

## Where to start

Four documents, and the order matters if you are picking this up cold:

1. **OPEN-ITEMS.md** — the state of the work. What has actually been *run*, what
   has never been run (the Docker image has not been built once; no OIDC round
   trip has ever completed against a real Synology), what was left out on
   purpose, and what is worth doing next. Read this before believing anything is
   finished.
2. **README.md** — orientation and the layout map.
3. **DEPLOYMENT.md** — the run-book for the NAS.
4. **docs/BRIEFING.md** — the briefing this was built from and the nine places
   this repository departs from it, each with its reason. Read it before
   "correcting" something back towards the brief.

This file is what is not in any of them.

**Keep OPEN-ITEMS.md current.** It is the one document that goes stale in a way
that actively misleads: everything else describes code that is present and can
be checked, while §1 and §2 there are claims about what somebody did or did not
observe, and nothing in the repository contradicts them when they rot.

## Stack

- Django 6.0, Python 3.13, `uv` (`pyproject.toml` / `uv.lock`)
- SQLite in WAL mode, one file under `DATA_DIR`
- `mozilla-django-oidc` for the Synology SSO handshake
- WhiteNoise (manifest storage), gunicorn, Pillow
- No JS framework — vanilla JS, one file per page under `static/js/`
- German default, English via the topbar globe; `.mo` files are committed

## Getting a working checkout

```
uv sync
uv run python manage.py migrate
uv run python manage.py collectstatic --noinput   # prerequisite of pytest, see below
uv run python manage.py seed_demo                 # DEBUG only; account + 4 recipes
uv run python manage.py runserver
```

`seed_demo` creates **`claude` / `kitchen-dev-pass`** (superuser) and four
recipes chosen to cover the cases that differ: a fractional amount, an
amount-less line, a four-digit amount and a short recipe. It refuses to run with
`DEBUG` off. Use it rather than inventing fixtures — the fractional one
(Zwetschgenkuchen, 1.5 kg) is what makes the `|unlocalize` bug visible, and a
seed without it hides a whole class of error.

To see the app working, scale that recipe from 12 servings to 18 and check the
plums read **2,25 kg**. If they read 1,5 the localisation rule below has been
broken again.

## Commands

```
uv run pytest                                     # ~180 tests, ~45 s

uv run python manage.py makemessages -l de --no-obsolete
uv run python manage.py makemessages -d djangojs -l de --no-obsolete
uv run python manage.py compilemessages -l de --ignore=.venv
```

`collectstatic` is a **prerequisite of the test suite**, not only of a
deployment: `STORAGES` uses WhiteNoise's manifest storage in every mode, so
`{% static %}` resolves through the gitignored `staticfiles/staticfiles.json`
and a checkout that has never run it fails most of the suite with "Missing
staticfiles manifest entry".

GNU gettext is not on PATH on the development machine; it ships with Git:
`$env:PATH = "C:\Program Files\Git\usr\bin;$env:PATH"`.

After `makemessages`, check for `#, fuzzy` before compiling — gettext ignores a
fuzzy entry at runtime, so the string comes out in English while the file looks
translated. `config/tests.py` fails on a fuzzy entry, an empty `msgstr`, and a
`.mo` older than its `.po`. On Windows `makemessages` has also been seen to emit
a malformed `#:` reference line (a wrapped reference continuing with a leading
space instead of a second `#:`), which makes `msgfmt` refuse the whole file and
silently produce no `.mo` at all; `msgfmt --check -o /dev/null <file>.po` is how
to see it.

## Verifying a change

The test suite is fast and covers the invariants, but three things it cannot
see — layout, whether a page *looks* broken, and anything the browser does — are
worth checking by hand for any UI change:

```
uv run python manage.py runserver 8100 --noreload
```

Sign in as `claude` / `kitchen-dev-pass`. If the port is already held, kill the
listener by PID rather than by image name (`Get-NetTCPConnection -LocalPort 8100
-State Listen`); a stale `runserver` answering on the port while a new one fails
to bind is how a session ends up testing yesterday's translations. `--noreload`
matters when catalogs change: translations are loaded once per process.

Chrome's per-origin zoom persists across navigations and cannot be reset from
the browser tooling. If screenshots come back magnified, read values out of the
page with `javascript_tool` instead of fighting it.

## Deployment is written but unexercised

`deploy/` has never been run. No image has been built, gunicorn has never served
this app (it cannot run on Windows), and no OIDC round trip has completed
against a real Synology SSO Server. The endpoint paths in `settings.py` are
Synology's usual shape and are explicitly a guess — DEPLOYMENT.md §3.1 says to
read the real ones off the discovery document, and that is not politeness.

Treat the first `docker build` as work, not as a formality. OPEN-ITEMS.md §2 has
the full list of what is and is not known to work.

## The rules that are load-bearing

Each of these is here because breaking it produces a page that still renders.

- **Nothing on a page may be inline.** No inline `<script>`, no `style="…"`, no
  `onclick=`. The CSP is `script-src 'self'` with no nonce (`config/csp.py`),
  because a nonce fails *open* the moment somebody forgets one. Page data
  crosses over through `json_script` (`window.pageData(id)`), strings through
  `gettext()` and the djangojs catalog.
- **Everything visual comes from the token block** at the top of
  `static/css/main.css`. No raw colour, spacing, font size, transition duration
  or z-index outside it; the scales are closed sets. A component needing a step
  that is not there means the *scale* is missing a step.
- **A focus ring is never removed, only quietened.** `form input:focus` outranks
  `input:focus-visible` on specificity, so a bare `outline: none` in a component
  strips the keyboard indicator app-wide. Scope it with
  `:focus:not(:focus-visible)`.
- **Every dialog is the app's own** and goes through `modalController`. Nothing
  calls `window.confirm`/`alert`/`prompt`.
- **`form.submit()` is never what you want** — it skips HTML5 validation *and*
  every submit listener. Use `requestSubmit()`.
- **A formset row is never taken out of the DOM, and its whole form lives inside
  the row.** A formset is an index range, not a list. Removal ticks `DELETE` and
  hides the row; the pk must be *inside* the row or the operation leaves it
  behind.
- **Django's `{# #}` is single-line only** — its lexer matches without DOTALL,
  so a comment that wraps is rendered onto the page. Use `{% comment %}`.
- **`_("…")` inside an f-string is never extracted.** xgettext does not look
  inside f-strings; bind it to a name first, then interpolate.
- **A value read by JavaScript must be `|unlocalize`d.** The page's default
  language is German, so an un-unlocalized `data-amount` of 1.5 renders as
  "1,5", `parseFloat` stops at the comma, and one and a half kilos silently
  become one. `templates/recipes/recipe_detail.html` is the live example.
- **A read must not write, and the cost must not grow with the collection.**
  Both are pinned in `apps/recipes/tests.py`; neither failure is visible until
  the collection is large.
- **Everything written at runtime goes under `DATA_DIR`, never `BASE_DIR`.** The
  container's code is replaced wholesale by the next image.

## Security

- **A login is not authorisation.** `LoginRequiredMiddleware` gates on an
  enumerated open list (`apps/accounts/pages.py`) rather than per-view
  decorators, because a forgotten decorator leaves a page that answers to
  anybody and looks completely normal. Who may *edit* a recipe is a second
  question, answered in `apps/recipes/views._may_edit`.
- **Validation belongs to the door the untrusted value comes through.**
  `apps/recipes/images.py` is that door for photographs: size cap, Pillow
  verification, and a filename **we** generate — an extension is what a server
  picks a Content-Type from, so a `.html` under `/media/` would be HTML from
  this app's own origin.
- **`/media/` is behind the login** (`config/media.py`). "Not linked from
  anywhere" has never been access control.
- **Identity is the OIDC `sub`, never e-mail.** DSM lets two accounts share an
  address and an address can be reassigned; matching on it signs somebody in as
  somebody else.
- **The OIDC group check fails closed.** With `OIDC_ALLOWED_GROUPS` set and the
  group claim absent — which real DSM versions do — the login is refused rather
  than waved through.

## Tests

~180 cases in three files. The value is concentrated in the ones that **discover
their own targets**, so a page added next month is covered the day it lands:

- `config/tests.py` walks the URLconf (the sidebar registry and the open list
  must both name routes that exist), every template (inline script/style,
  `onclick=`, multi-line `{# #}`, remote fonts), every `.js` file (brackets
  balance, no template syntax), and `main.css` (the closed scales).
- `apps/recipes/tests.py` holds the two cost ceilings and the formset structural
  check.
- `apps/accounts/tests.py` covers the claim handling by handing the backend
  dictionaries — which is the only way to test the DSM version that omits the
  group claim, the renamed account, and the token with no subject.

Two harness notes: the `english` fixture in `conftest.py` sets **both**
`LANGUAGE_CODE` and `translation.override`, because `LocaleMiddleware` resolves
the language again per request and would otherwise render every page in German;
and `django_assert_num_queries` asserts a count rather than capturing one — use
`CaptureQueriesContext` when comparing two runs.

## Standing decisions

Things that look like gaps, have an answer, and are listed so the next pass
recognises them as decided rather than missed.

- **One container, SQLite, no Redis.** A household collection is a few thousand
  rows read by four people. Postgres later is a dumpdata/loaddata, not a
  rewrite. See `deploy/docker-compose.yml`.
- **There is a local login as well as SSO.** An OIDC-only app is unreachable —
  including its administration — exactly when SSO is broken. It is offered
  second and folded away. `apps/accounts/middleware.OIDCSessionRefresh` exists
  solely because the library's own version would bounce that account to the
  provider on its first request.
- **No self-service password change.** Accounts come from DSM; the fallback is a
  superuser's job. Self-service would mean a second password store, which is the
  thing SSO is here to avoid.
- **No in-app export or backup.** The collection is one SQLite file plus
  `media/` under `/data`; Hyper Backup already covers that share.
- **Anyone signed in can read every recipe.** Editing is limited to whoever
  added it, or staff.
- **DSM itself should not be exposed to the internet**, whatever the original
  briefing said. DEPLOYMENT.md §1.
- **Instructions are plain text**, rendered with `linebreaks`. A rich-text
  editor is a second sanitiser to get right and a method is paragraphs.
