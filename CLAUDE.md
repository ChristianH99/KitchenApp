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
uv run pytest                                     # ~260 tests, ~90 s

uv run python manage.py makemessages -l de --no-obsolete --no-wrap
uv run python manage.py makemessages -d djangojs -l de --no-obsolete --no-wrap
uv run python tools/fix_po.py
uv run python manage.py compilemessages -l de --ignore=.venv
```

**`--no-wrap` is not cosmetic.** Without it gettext breaks a long `msgstr`
across continuation lines — valid `.po`, and exactly what the completeness check
in `config/tests.py` reads as an *empty* translation. A fully translated catalog
then fails the suite and the obvious next move is to "fix" the check.

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
`.mo` older than its `.po`.

On Windows `makemessages` **reliably** emits a malformed `#:` reference line — a
wrapped reference continuing with a leading space instead of a second `#:`. It
is not occasional; it happens on every run that produces a long enough reference
block, `--no-wrap` does not prevent it, and `msgfmt` then refuses the whole file
and produces no `.mo` at all. The way that presents is the problem:
`compilemessages` prints an error most people scroll past and the app carries on
serving the *previous* catalog, so a session's translations look compiled and
are simply not there. `tools/fix_po.py` repairs it and
`config/tests.py::test_no_reference_line_was_wrapped` fails on it.

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

**`--noreload` also means templates are cached for the life of the process.**
Django uses the cached template loader, and `runserver`'s autoreloader is what
normally hides that in development. With `--noreload`, a template edit changes
nothing until the server is restarted — and the symptom is a page that renders
the old markup while the file on disk is plainly correct, which reads as a
browser cache problem and is not one. Restart after every template edit, or drop
`--noreload` when catalogs are not what you are working on.

Static files are served through the *manifest* storage even in development, so
`main.css` and every `.js` file must be re-`collectstatic`ed after an edit. The
URL is unhashed in DEBUG, so the browser will also happily serve its cached copy
— when a style change appears not to apply, check that before changing the CSS
again.

Chrome's per-origin zoom persists across navigations and cannot be reset from
the browser tooling. If screenshots come back magnified, read values out of the
page with `javascript_tool` instead of fighting it.

## Deployment: what is now known, and what is not

The **image** is no longer a guess. It has been built, started, and observed
serving `/healthz`, the login redirect and its own hashed static files, with
migrations applied on start-up; the release tarball has been produced, the image
deleted, and the whole thing restored with `docker load`. gunicorn cannot run on
Windows (`fcntl`), so any change to `deploy/` still has to be checked in a
container or in CI rather than through `runserver`.

The **NAS** is still entirely untested: no OIDC round trip has completed against
a real Synology SSO Server, and nothing has run on the DS723+. The endpoint
paths in `settings.py` are Synology's usual shape and are explicitly a guess —
DEPLOYMENT.md §3.1 says to read the real ones off the discovery document, and
that is not politeness.

OPEN-ITEMS.md §1 and §2 are the current line between the two.

## The build pipeline

Two workflows, both now run for real: CI is green on GitHub and `v0.1.0` was
built, published and installed from its own release attachment end to end.

- `.github/workflows/ci.yml` — push and pull request. Test suite, then a
  **build of the image and a smoke test that starts it**. That second job is the
  point: gunicorn cannot run on Windows, so CI is the only place the *server*
  is ever exercised rather than the WSGI callable.
- `.github/workflows/release.yml` — tags matching `v*`. Calls `ci.yml` as a
  reusable workflow (so a release cannot be verified by a stale copy of the
  checks), builds, smoke-tests **the image it is about to publish**, pushes to
  GHCR, and attaches a `docker load`-able tarball plus a version-pinned compose
  file to the release.

Things to keep in mind when touching them:

- **Nothing is built on the NAS any more.** `deploy/docker-compose.yml` still
  builds and is right on a laptop; `deploy/docker-compose.release.yml` pulls and
  is what ships. `config/tests.py::TestTheReleasePipelineAgreesWithItself` holds
  the three files that have to say the same thing about one image — a `build:`
  section creeping back into the release compose file, a placeholder the
  workflow does not substitute, a `--build-arg` the Dockerfile never declares.
  Each of those produces a release that is perfect in CI and undeployable.
- **The version is pinned, never `latest`.** A compose file saying `latest`
  cannot be rolled back by re-reading it.
- **A release asset must not be a dotfile.** `.env.example` is shipped as
  `env.example`, because a shell glob does not match dotfiles and `dist/*` would
  silently leave it out of both the checksums and the upload.
- **A script's executable bit does not survive this machine.** `core.filemode`
  is false on Windows, so git records nothing, and Docker Desktop hands
  everything 0755 when it copies from NTFS — so a broken mode is invisible here
  and fatal on Linux (`permission denied`, exit 126, no application log). This
  is what the first CI run caught. `deploy/entrypoint.sh` is now 0755 in git
  *and* chmodded in the Dockerfile; check `git ls-files -s` before adding
  another script.
- **`KITCHEN_VERSION` is baked in and shown in the sidebar** (`apps/version.py`).
  It exists for the question asked after every update — *did it take?* — which
  the NAS cannot answer: a container kept alive by `restart: unless-stopped` and
  a browser holding a cached page both look like success. It is deliberately
  absent from `/healthz`, which is unauthenticated.

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
  become one. `templates/recipes/recipe_detail.html` is the live example, and
  `_diagram.html` and `recipe_cook.html` now carry the same attribute — the
  scaler rewrites *every* `.ingredient-amount` on the page, because the same
  quantity can appear in three places at once and one of them lagging is worse
  than none of them moving.
- **A grid or flex column that can hold something wide says `min-width: 0`.**
  The default is `min-width: auto`, which means "never shrink below my content".
  A column holding the diagram then keeps its full width, the grid grows past
  the viewport, and *the page* scrolls sideways — taking the topbar and sidebar
  with it. The rule lives above `.diagram-scroll` in `main.css`; the wide thing
  itself gets `overflow-x: auto` so the scrolling happens where there is a
  scrollbar for it.
- **A diagram reference is a row index, not a primary key.** A step's
  `parent_index` and an ingredient's `step_index`/`alt_index` name another *form
  of the same formset*, because on a recipe being typed in for the first time
  nothing has a pk yet. `forms.wire_diagram()` turns them into foreign keys
  after both formsets have saved, and `prime_diagram_indices()` is the reverse
  translation on the way out — without which an edit page comes back with an
  empty diagram and pressing Save flattens it.
- **A substitute is an ingredient row, not a note.** `alternative_for` is a
  self-FK, so a substitute carries its own amount and unit and scales with the
  rest. It never gets a row in the diagram and never a `step` of its own: it
  takes its place from the line it replaces. Anything counting ingredients has
  to filter it out — `_visible_recipes()` does, or a card says "9 ingredients"
  for a recipe with six.
- **A read must not write, and the cost must not grow with the collection.**
  Both are pinned in `apps/recipes/tests.py`; neither failure is visible until
  the collection is large. The cooking view is the one that invites breaking it
  — a "cooking session" row written when the page opens would take SQLite's
  single write lock on a *GET*. The stopwatch is in `localStorage` instead and
  the elapsed time crosses once, in the POST that records the cooking.
- **Everything written at runtime goes under `DATA_DIR`, never `BASE_DIR`.** The
  container's code is replaced wholesale by the next image.

## Security

- **A login is not authorisation.** `LoginRequiredMiddleware` gates on an
  enumerated open list (`apps/accounts/pages.py`) rather than per-view
  decorators, because a forgotten decorator leaves a page that answers to
  anybody and looks completely normal. Who may *edit* a recipe is a second
  question, answered in `apps/recipes/views._may_edit`; who may manage accounts
  is a third, answered by `@staff_required` in `apps/accounts/users.py`. Those
  two *are* per-view — the exposure a forgotten one would create is covered from
  the other side, by a test that walks the URLconf for `accounts:user-*` and
  refuses to let any of them answer an ordinary account.
- **The account pages must not close the door behind you.** Switching off,
  demoting or deleting yourself is refused, and so is removing the last active
  superuser: an app with none cannot be recovered without a shell on the NAS.
  Only a superuser may grant `is_superuser`, or "may manage accounts" is also
  "may grant yourself everything" one page later.
- **A Synology account is never offered a password field.** It has no usable
  password by construction (`set_unusable_password()` in the OIDC backend), and
  giving it one here would open exactly the second, unmanaged door into a
  DSM-managed identity that SSO exists to close. Its name and e-mail are
  disabled for a quieter reason: DSM re-applies them at every sign-in, so a
  value typed here looks saved and is gone by tomorrow.
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

~260 cases in three files. The value is concentrated in the ones that **discover
their own targets**, so a page added next month is covered the day it lands:

- `config/tests.py` walks the URLconf (the sidebar registry and the open list
  must both name routes that exist), every template (inline script/style,
  `onclick=`, multi-line `{# #}`, remote fonts), every `.js` file (brackets
  balance, no template syntax), every `.po` (fuzzy, untranslated, uncompiled,
  wrapped reference lines), and `main.css` (the closed scales).
- `apps/recipes/tests.py` holds the two cost ceilings, the formset structural
  check, and the diagram. The one to keep is `_assert_rectangular`: it runs the
  browser's own table algorithm over the laid-out cells and insists every square
  of every block is covered exactly once. Both ways the layout goes wrong are
  invisible in the model and obvious there — a square claimed twice (cells
  overlapping) and a square claimed by nobody (a hole, which shifts every cell
  after it one column left).
- `apps/accounts/tests.py` covers the claim handling by handing the backend
  dictionaries — which is the only way to test the DSM version that omits the
  group claim, the renamed account, and the token with no subject — plus the
  account pages and the doors that must not close behind you.

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
  superuser's job, done on the People page. Self-service would mean a second
  password store, which is the thing SSO is here to avoid — and the "set a new
  password" page deliberately has no *old password* box, because it exists for
  the case where somebody has forgotten theirs.
- **The People page only knows the DSM accounts that have signed in.** This app
  has no directory to read: a Synology identity exists here from the moment its
  first token arrives and not a second earlier. The page says so rather than
  leaving somebody to wonder where their sister is.
- **The diagram editor's preview is a nesting, not the table.** The real
  geometry lives in `apps/recipes/diagram.py`, in Python, where it is tested. A
  second implementation in JavaScript to draw the preview would be the thing
  that quietly disagrees with the page it is previewing; nested boxes say the
  same "these go into this, which goes into that" without re-deriving any of it.
- **Portion sizes are a closed set.** Large / regular / small / child / to-go,
  with fixed weights in `PORTION_WEIGHTS`. Free text would give "gross", "große
  Portion" and "XL" for one thing, and then nothing could be added up. A portion
  put in a box for tomorrow counts as a whole one: it was made, and somebody
  eats it.
- **No in-app export or backup.** The collection is one SQLite file plus
  `media/` under `/data`; Hyper Backup already covers that share.
- **Anyone signed in can read every recipe.** Editing is limited to whoever
  added it, or staff.
- **DSM itself should not be exposed to the internet**, whatever the original
  briefing said. DEPLOYMENT.md §1.
- **Instructions are plain text**, rendered with `linebreaks`. A rich-text
  editor is a second sanitiser to get right and a method is paragraphs.
