# CLAUDE.md

Guidance for Claude Code (claude.ai/code) when working in this repository.

## Project

A self-hosted kitchen app for one household — recipes and a pantry, with the
shape to take meal planning and shopping lists later as sibling apps rather than
as bolt-ons. Django 6 (Python 3.13, `uv`), server-rendered, running as one
container on a Synology DS723+ behind that NAS's reverse proxy and
authenticating over OIDC.

The provider it was built against is the Synology SSO Server, and the code and
the run-book say so. **No user-facing page names a vendor** — they speak of SSO
and of "the identity provider", because nothing in the app actually depends on
which one it is.

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

The **ingredient catalogue arrives with the migrations** (`apps/pantry/starter.py`,
loaded by `pantry/0002`), so it is in every database including every test one —
which is a trap for a fixture that assumes an empty table. `manage.py
seed_catalogue --link --dry-run` is the opt-in half: it says which recipe lines
would be pointed at the catalogue and which new ingredients that would invent.

**Never delete `db.sqlite3`.** It is gitignored, which means it is not a build
artefact — it is the only copy of the household's own account and of whatever
half-finished recipe somebody is currently using to report a bug against. A
session once ran `rm db.sqlite3 && migrate && seed_demo` merely to re-check that
the seeder produced well-formed data, and destroyed the `admin` superuser and a
hand-typed test recipe with it; the owner found out by failing to log in. To
verify a seeder or a migration, point `DATA_DIR` at a temporary directory or let
`pytest` build its own database. The same goes for `flush`, for dropping
accounts, and for anything else whose undo is "retype it".

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
uv run pytest                                     # ~500 tests, ~155 s

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
- **A modal caps its height and scrolls its own body.** `.modal` is a flex
  column with `max-height: 100%`; `.modal-body` takes `overflow-y: auto` and
  `min-height: 0` — the same "never shrink below my content" default that
  catches the diagram column, in a flex column rather than a grid, and without
  it the cap does nothing. This is not cosmetic: `body.modal-open` sets
  `overflow: hidden`, so a panel taller than the viewport had its foot below the
  bottom edge with nothing able to reach it. The recipe form's help was
  unreadable from about halfway down.
- **`form.submit()` is never what you want** — it skips HTML5 validation *and*
  every submit listener. Use `requestSubmit()`.
- **A formset row is never taken out of the DOM, and its whole form lives inside
  the row.** A formset is an index range, not a list. Removal ticks `DELETE` and
  hides the row; the pk must be *inside* the row or the operation leaves it
  behind. The canvas raises the stakes: it *moves* each card into the cell it
  belongs in, so a field rendered beside the cards is left behind by that too,
  and emptying the canvas with cards still in it would take them out of the
  document altogether — which is why `render()` sends every card home first.
- **Django's `{# #}` is single-line only** — its lexer matches without DOTALL,
  so a comment that wraps is rendered onto the page. Use `{% comment %}`.
- **`_("…")` inside an f-string is never extracted.** xgettext does not look
  inside f-strings; bind it to a name first, then interpolate.
- **A `gettext()` string in JavaScript goes on one line, however long.** Split
  it with `+` across a line break and xgettext takes the msgid as far as the
  first literal and stops — the catalogue ends up holding
  `"…with the arrows on the "` while the call at runtime asks for the whole
  sentence. The lookup misses, and the string comes out in English on a page
  that is otherwise entirely German. Nothing fails: `makemessages` is quiet,
  the `.po` is valid, the completeness check in `config/tests.py` sees a
  translated entry. The same trap as the f-string above, in another language.
- **Preparing and Cooking are exclusive, so `.recipe-body` is not a grid.** It
  was `minmax(14rem, 20rem) 1fr` while the two sat side by side. Once they
  became panels, whichever one was showing landed in that first track — so the
  diagram drew itself inside 318px and grew scrollbars with 1100px of page
  empty beside it. Anything that reintroduces a column there brings that back.
- **A unit is a code, and no page may render the column.** `unit` holds a
  language-neutral value from `apps/pantry/units.py` — "tbsp", not "EL". A
  template writing `{{ item.unit }}` puts "tbsp" on a German page: not obviously
  wrong, only wrong, and it stays that way until a German reader notices. Use
  `item.unit_label` in Python or the `|unit_label` filter in a template. The
  same split is why the dropdown shows the *short* label ("g") and the group
  heading carries the meaning ("Weight") — the control is half of a cell about
  thirteen rems wide, and "millilitres" there is "millil…".
- **A step's duration is two columns, and nothing reads either alone.**
  `minutes` and `seconds` are how it is *typed* — almost every step is a round
  number of minutes, and one box holding 2700 is a number nobody enters
  correctly twice. Everything that *reads* one goes through
  `RecipeStep.timer_seconds` (the total), `timer_display` ("1:30") or
  `duration_label` ("45 min" / "45 s" / "1:30 min"). The same split as the unit
  codes, and it fails the same quiet way: a template writing `{{ step.minutes }}`
  puts "1 min" on a step somebody set to a minute and a half, and a countdown
  reading it runs thirty seconds short — under-baked, with nothing on the page
  looking wrong. The cooking view gets `data-cook-seconds`, one attribute
  carrying the whole thing, so the browser never adds two numbers up.
- **A removed row is still read.** Its `DELETE` box is ticked and it stays in
  the DOM and in the POST, and a *reference* to it — another step's
  `parent_index` — is followed **through** it to whatever it pointed at.
  Reading it as "no parent" instead is how "+ Step after this" followed by
  deleting what it made pulled a recipe in half: the step above was left naming
  a row that is not saved, broke off as a block of its own with its ingredients
  inside it, and `validate_structure` then refused the whole page. Stated twice,
  in `forms._resolve_parent` and in `recipe_diagram.js`'s `model()`; if the
  picture and the saved recipe ever disagree about a deletion it is those six
  lines. The other half is in `recipe_form.js`: clearing an unsaved row on
  removal must keep `parent_index`, `step_index`, `alt_index`, `position` and
  the two spans — blanking them throws away the very answer. They report
  `has_changed` as False either way, so keeping them cannot make an untouched
  row look edited.
- **Only units in the same dimension convert, and the fourth verdict is the
  point.** Mass and volume each have several members; everything countable is a
  dimension of one, which is how the table says *this never converts*. A clove
  is not four grams. `apps/pantry/matching.py` answers `UNKNOWN` rather than
  guessing, `UNKNOWN` counts *against* "can be made now", and every page keeps
  it separate from "missing" — because "you can make this" is a promise somebody
  acts on by not going to the shop.
- **`Decimal` floor division truncates toward zero.** The usual `-(-a // b)`
  ceiling trick therefore gives one *too few* for Decimals, which in
  `_choose_packet` is a shopping list that buys one packet short — quietly, and
  only for the amounts that do not divide evenly. Written out longhand there.
- **A missing amount is a refusal, and `no_amount` is the way out.** The form
  will not save a line whose amount is blank; "Salz" says so with the
  `no_amount` box instead. Removing that box does not relax the rule, it makes
  salt unrecordable — and the way round it people find is typing 1. The box is
  on the face of the card and not inside "More" for the same reason: it is the
  answer to an error, and an escape hatch nobody can find while reading the
  error is not one.
- **A standing instruction is a root that produces nothing.** `validate_structure`
  refuses a second root that has children or ingredients — that is a branch
  somebody forgot to join — but must never catch "heat the oven", which has
  neither by construction. Both halves are pinned in `apps/recipes/tests.py`.
- **How far that band reaches is stored, and always clamped.** `span_from` /
  `span_to` are 1-based column numbers, null meaning the whole width — because
  a band over every column claims the step runs alongside the ones that came
  *before* it, and "heat the oven while the dough proves" does not. They are a
  hint against a geometry that is **derived**: the column count changes every
  time a step is added or removed, so a span recorded last month can name
  columns that no longer exist. `diagram._band_span` clamps into range and
  `bandSpan()` in the canvas repeats the same four lines — if the two ever
  disagree it will be about that. A raw span would give a `colspan` of zero
  (which collapses the row) or one past the end (which drags the table wider
  than its own header and shifts every row below it).
- **The space beside a shortened band is a filler cell, never an omitted one.**
  Same rule as the ingredient rows: a `<tr>` that leaves columns out has
  silently moved everything after it one column left.
  `_assert_rectangular` catches it.
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
- **The order is a field, and the four structural fields never "change".**
  Reordering by renumbering the rows cannot work: it pushes a new, pk-less row
  below `INITIAL_FORMS`, where Django looks the row up by a primary key it does
  not have and `save_existing_objects` skips it in silence — the ingredient
  somebody just typed is simply not saved. So `position` is a field of its own.
  All four (`position` and the three indices) subclass `_StructureField`, whose
  `has_changed` is always **False**: a formset only validates and saves an extra
  row when something in it changed, and where a row *sits* is not something
  somebody typed. Without that, arranging the canvas around a blank card counts
  as editing it — a drag past it renumbers it, "+ Ingredient here" stamps a step
  onto it — and a nameless ingredient is written to the recipe. The consequence
  is that `formset.save()` never writes a purely structural change, which is why
  `wire_diagram()` applies the relations *and* the order itself, in one call so
  neither half can be forgotten.
- **An affordance lives on the card, not only on the canvas.** The builder has
  two modes and the form opens in **Steps**; the canvas is the other one. Hover
  "+" buttons between cells are built in `cellFor()`, which only
  `renderDiagram()` calls — so anything offered *only* that way does not exist
  in the mode most people are in. That is how "add a step beside this one" — the
  one gesture a branching recipe needs — was missing for two rounds of
  reporting while it looked present when checked by hand, because the browser
  doing the checking had `kitchen.builder-mode = "diagram"` in `localStorage`
  from earlier testing. The card carries `data-step-add-line`,
  `data-step-add-after` and `data-step-add-beside`; the canvas's hover buttons
  are a shortcut on top of those, never the only route.
  `apps/recipes/tests.py::test_every_way_to_add_a_row_lives_on_the_card` asserts
  it against the *server's* markup, which is the only mode-independent thing to
  assert against.

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
- **The OIDC client secret is in the database, and that was a reversal.** It
  used to be environment-only, which is where secrets belong. It was moved
  because the *other* half of the setup is a Synology web GUI and nothing else,
  so the real choice was never "config file or web page" but "one web page plus
  an SSH session and a container restart" versus one web page. The cost is
  real — it is now in `dumpdata` and in every Hyper Backup copy of `/data` —
  and three things bound it: it is **encrypted at rest** with a key derived
  from `DJANGO_SECRET_KEY` (so the backup copy alone is not enough; anyone with
  the running system has both and always did), it is **never rendered back** to
  a browser, and the page is **superuser-only**. `apps/accounts/models.py`
  states all of this at the top; do not quietly undo it in either direction.
- **The SSO settings page must never be able to lock everybody out.** The local
  password form stays reachable at `?local=1` whatever is configured, the button
  is only offered when the configuration could actually complete a login
  (`sso.is_enabled()` checks usability, not just the switch), and
  `sso.current()` swallows a database failure and falls back to the environment
  — because it is called while rendering the login page, and raising there
  turns "SSO needs reconfiguring" into "the app is down".
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

~500 cases in four files. The value is concentrated in the ones that **discover
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
  after it one column left). Three cases guard the canvas from the server's
  side, since nothing here runs JavaScript: a blank card that was only *arranged*
  must not be saved, an arrangement that changed nothing else must still be
  saved, and a standing instruction must keep the place it was left in.
  `TestARecipeMustBeJoinedUp` holds the completeness rules *and* the two things
  they must not catch: a recipe with no steps at all, and a standing
  instruction. Both of those are ordinary recipes, and a rule that refuses them
  is one somebody will delete rather than narrow.
- `apps/pantry/tests.py` keeps the cases where a plausible implementation gives
  a **confident wrong answer** rather than an error, because those are the ones
  that reach somebody with the pan already hot: a unit that must not convert, an
  unmeasured pantry amount (which means "enough", not "none"), a line the
  catalogue does not know (which means "cannot tell", not "missing"), and a
  substitute rescuing the line it replaces. `matching` takes the rows it
  measures as arguments, so most of it needs no database — which is also what
  keeps the list page's query count flat.
- `apps/accounts/tests.py` covers the claim handling by handing the backend
  dictionaries — which is the only way to test the provider version that omits
  the group claim, the renamed account, and the token with no subject — plus the
  account pages and the doors that must not close behind you.

One trap worth stating: **the starter catalogue is a data migration, so every
test database already has it.** A fixture that does `Ingredient.objects.create(
name="Butter")` hits a unique constraint. Take the shipped row, or invent a name
the starter list does not use.

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
- **The SSO connection is edited in the app, not in `.env`.** Reversed from the
  original decision — see the Security section for the trade and its bounds.
  The environment still works and is what a row-less database reads; the moment
  the page is saved, the stored row is the whole truth. There is deliberately
  **no per-field fallback**: a page showing one thing while the app does another,
  with no way to tell which field came from where, is worse than either source
  on its own. `mozilla_django_oidc` is made to read it by overriding
  `get_settings` on the backend, the two authentication views (wired in via
  `OIDC_AUTHENTICATE_CLASS`/`OIDC_CALLBACK_CLASS`, so the URL names the SSO
  server has registered do not move) and the refresh middleware — whose
  `__init__` caches the endpoint at process start, hence the properties in
  `apps/accounts/middleware.py`.
- **No self-service password change.** Accounts come from DSM; the fallback is a
  superuser's job, done on the People page. Self-service would mean a second
  password store, which is the thing SSO is here to avoid — and the "set a new
  password" page deliberately has no *old password* box, because it exists for
  the case where somebody has forgotten theirs.
- **The People page only knows the DSM accounts that have signed in.** This app
  has no directory to read: a Synology identity exists here from the moment its
  first token arrives and not a second earlier. The page says so rather than
  leaving somebody to wonder where their sister is.
- **The diagram editor is the diagram, and that reverses an earlier decision.**
  It used to be a list of rows with a "feeds into" select on each, previewed as
  nested boxes rather than as the table — deliberately, because the geometry
  lives in `apps/recipes/diagram.py` where it is tested, and a second
  implementation in another language is the thing that quietly disagrees with
  the page it is previewing. Dropping a card *into a column* means knowing which
  column it is in, so that argument no longer holds and the layout now exists in
  `static/js/recipe_diagram.js` too. What bounds the cost is that only the
  **rule** is repeated, and it is one line — `column(step) = 1 + max(column(children), 0)`
  — stated within three lines of the top of both files. Everything else the
  Python does (how far a cell spans to reach its parent, the filler cells that
  keep a row rectangular) is the `<table>`'s problem and not the grid's, because
  an empty grid area is simply empty. If the two ever drift it will be about
  that one line.
- **The editor and the recipe page each offer two views, and neither is a
  copy.** On the form, Steps and Diagram *move the same form rows*; on the
  recipe page, Preparing and Cooking are both rendered and one is hidden. Two
  renderings of one set of data is a feature; two sets of data is the bug it
  turns into the moment somebody draws a second copy beside the first. The
  form's choice is remembered (it is a statement about how somebody works); the
  recipe page's Preparing/Cooking is not (opening a recipe is nearly always
  "what do I need").
- **A step is one or more lines, and `parts` is what pages render.** The first
  real recipe put "Topf in Ofen stellen" and "Ofen vorheizen" in one box,
  because they are two actions at one point in the flow rather than two boxes.
  `text` is a TextField; `parts` splits it and strips a leading dash; `headline`
  joins them for the places with room for one line. Truncating to the first part
  would make it a different instruction.
- **The cooking walk reads the diagram, column by column.** Left to right, top
  to bottom within a column — because that is how the table beside it is laid
  out, and a walk-through that disagrees with the picture is one nobody trusts
  twice. A **band is pulled one column left of what it covers**: you start the
  oven before you need it. A band with no span covers everything and so comes
  first, which is what every recipe written before spans did.
- **A band is a *root* with nothing going into it.** The "is it a root" half is
  load-bearing: a step in the middle of a chain can also have no ingredients and
  no children — "bring a pan of water to the boil", or this household's
  "Vormischen" — and ranking one as a band sends it to the front of the recipe.
- **No formset renders a spare row.** `extra=0` everywhere. A blank card on the
  canvas is a loose cell below the diagram that cannot be got rid of: deleting
  it makes the formset render another. Rows are minted by the "+" between the
  tiles, which puts one *where it is wanted* rather than at the end of a list
  somebody then drags it out of.
- **The ingredients in no step are not a section.** They are the cards after the
  last block and nothing else — no heading, no paragraph. It *was* a titled box
  called "Not in any step" and the household asked for it to go: a line with no
  box beside it is an ordinary line of the recipe, and a heading over it makes
  it look like a mistake to be corrected. The element survives because it is
  also where a line is **dropped** to take it out of a step, and a drop target
  that only exists once something is in it cannot be used to put the first thing
  there — so `main.css` draws it as a target *only while a drag is happening*
  (`.builder.is-dragging .builder-tray`), and the hint inside it is
  `display: none` rather than `hidden` so it leaves no blank line the rest of
  the time.
- **A step card says each thing once.** There was a note on every standing
  instruction explaining that it stood on its own and that the ends of its band
  could be dragged; it is gone. The "feeds into" line directly above already
  says it in three words and the diagram colours the band. A card that explains
  itself twice is a card nobody finishes reading — and the removed sentence is
  why `markStanding` no longer exists.
- **Four ways to add a step, and they are four different sentences.**
  "+ Step after this" and the "+" on a tile's right edge insert into the chain
  (a new column). The "+" on a tile's *bottom* edge makes a **sibling** — same
  parent, same column, its own ingredients. That last one was missing, which is
  why the household's second dough "only stayed above the ingredients": every
  other route made a new column or a parentless root, and a root with nothing
  in it draws as a band across the top.

  The fourth is the only one that reads the *whole* diagram: the toolbar's
  "+ Step" and the "+" at the right-hand edge of the canvas both call
  `addJoiningStep`, which mints one step and points **every loose end** at it.
  A step has one parent, so each of the other three names one existing step —
  and combining two arms is a statement about all of them at once. Without it a
  recipe with two arms could be built and then not finished: there was no
  gesture that joined them, and `validate_structure` refuses two productive
  roots as "a branch somebody forgot to join". "+ Step" used to *append a
  parentless row*, which is that second root — so the one general control made
  the very shape the save refuses.

  **What it must not sweep up is a standing instruction.** `unfinished()`
  excludes a *bare* root — no children, no ingredients — because "Ofen
  vorheizen" is one by construction, and feeding it into the final step turns
  it into an input of the recipe, takes it out of its band and changes what the
  recipe says. The same test excludes a blank card nobody has typed into, which
  is how joining stops writing an unnamed step into the chain.

  Everything is joined by default and the ends can be dragged off again: with
  two arms that is always what was meant, and with more it is one drag per
  exception rather than one per inclusion.

  **Adding a step never moves one.** A new row is minted at the *end* of the
  formset, so anything that mints one has to say where it goes in `stepOrder` or
  it sorts last among its new siblings. "+ Step after this" inserts the new box
  immediately **before** the step it was pressed on, taking the slot that step
  is vacating; the sibling "+" puts it immediately after. Without the first of
  those, adding a step to the right of one sent that step — and its
  ingredients — to the bottom of the recipe while everything beside it rose,
  which is what the household reported. The card's button and the tile's "+" are
  one function for the same reason: they were two copies, and only one of them
  had ever placed the row.
- **A tile is resized by dragging its edge, and there is one handle per
  boundary.** Not one per step-side: the bottom of "Step 1" and the top of
  "Step 2" are the *same* line, and putting a control on each gave every
  boundary two of them eight pixels apart doing the same thing. The handle
  belongs to the tile above the boundary. It is anchored to the step's **own
  lines**, never to the rows its cell spans — a step's cell covers its whole
  subtree, so in a chain the second step's cell starts at row 1 and anything
  measured from the cell edge lands several rows off.
- **A tile with no ingredients still has an edge to pull.** The handle used to
  be anchored to the step's own line rows, so a step that had none got none —
  and the one gesture that would have given it its first ingredient was the one
  gesture not on offer. That is the whole of "I cannot attach Dinkelmehl, Salz
  and Zucker to Vormischen": the household could see the tile and there was
  nothing on it to drag. `neighboursOfStep` falls back to the last row of the
  step's own *cell*, which for a step with nothing in it is the single empty row
  the layout gave it, and `positionHandles` falls back to the bottom of that
  cell because there is no line cell on that row to measure. The drag's starting
  index is then **-1**, which is a real answer and not a failure — the boundary
  sits above every line in the block.
- **The alarm rings until it is stopped.** One chime is a chime you miss by
  being in the next room, and the bread carries on baking. `kitchenSounds.ring`
  repeats the tone and `silence` stops it; every sounding goes through a gain
  node of its own so that silencing cuts what is *already* playing rather than
  only the next repeat (an oscillator scheduled to start in a fifth of a second
  has not started, and `stop()` on one that has not started throws). While it
  rings, Start is hidden and the Stop button is relabelled — the button reading
  "Time is up" that silently restarted the countdown was the wrong answer to the
  only question being asked. The settings page *plays* rather than rings: it is
  a sample of the tone, and a settings page that starts an alarm nothing on it
  can stop is not one.
- **A timer is not owned by the page that started it.** `timer_watch.js` is
  loaded by `base.html` on *every* page and owns the one store
  (`kitchen.cook-timers.<recipe id>`); the cooking view writes a row into it and
  reads its own back, and every other page draws whatever is running as a card
  in the corner. A timer you have to stay on one page to hear is not a kitchen
  timer — somebody starts the bread and then looks up the next recipe. Three
  things follow, and each was a bug on the way here:
  - **An expired row is not deleted.** It used to be cleared the instant the
    countdown reached nought, so walking away from a ringing alarm silenced it —
    there was nothing left for the next page to ring. It stays until Stop is
    pressed, or twelve hours pass.
  - **The row carries what the card needs to say** — the step, the recipe's
    name, its cooking URL and the chosen sound — because the page that ends up
    ringing it may be the pantry, which has no reason to have asked the server
    any of that.
  - **The card is suppressed for the recipe the current page is already showing
    a timer for**, or the cooking view has two readings of one clock and the
    wrong one is whichever somebody happens to look at.

  What cannot be fixed from here: a browser will not let a page make a noise
  before that page has been touched. The first pointer or key event of any kind
  arms the audio, and the alarm is *also* a card and a `⏰` on the tab title,
  because those two always work.
- **The oven panel is watched for as it is typed.** A step whose text says
  "vorheizen", "Ofen", "backen" grows a temperature box and a mode select. That
  detection used to run only inside `refresh()` — and typing deliberately does
  *not* lay the canvas out again, so the panel appeared on the next drag or the
  next load after a save, which is exactly how it was reported. The input
  handler calls `ovenPanel` for the card being typed into; it is cheap when the
  answer has not changed, does nothing when a slot already holds a panel, and
  never touches a slot somebody's caret is in.

  **The mode is the anchor and the temperature hangs off it.** The select is the
  panel; the temperature box appears only once a mode has been chosen, because
  an empty box beside an empty dropdown is two questions where there is one.
  Clearing the mode clears the temperature with it — a number left in the hidden
  field would be saved with nothing on the page showing it. The one exception is
  a step that already *has* a temperature and no mode: the box stays, or a saved
  value would be on the recipe page with no way to see or change it on the form.

  The temperature is a **box, not a list** — a closed list of the settings a
  domestic oven has detents for is wrong the first time a recipe says a number
  that is not on it, and then there is nowhere to put it at all. Three things
  about it are load-bearing:
  - **`type="text"`, not `type="number"`.** A number input looks like the
    stricter choice and is the opposite: it accepts "e", "+", "-" and "." as
    you type and then reports `value` as an empty string, so the page cannot
    even tell you what you typed. That is precisely where a temperature "just
    went missing" on save. Non-digits are stripped on input instead — a
    character that never appears needs no explanation — with the caret put back
    where it was, or a typo in the middle of "180" becomes a fight.
  - **The complaint arrives on the keystroke, not on the save.** Out of range
    writes nothing through, shows a `.field-error` under the box *and* calls
    `setCustomValidity`, so the browser refuses the submission rather than
    saving a recipe with the value quietly absent.
  - **A removed card loses its complaint.** `clearOvenPanel` exists for one
    reason: a `display: none` row holding an invalid control makes Save do
    nothing at all, with an unfocusable-control warning in the console as the
    only clue. `ovenPanels` therefore walks *every* step row, not the live ones.
  - **The focus guard protects one element, not the whole slot.** It was "do
    nothing while the focus is anywhere in here", which is too broad by exactly
    the case the panel exists for: **choosing a mode leaves the focus on the
    select**, so the box it was meant to reveal did not appear until the next
    page load, and clearing the mode left the box there until a save. Only the
    element actually holding the focus is spared now. Worth knowing how that
    got past a check: a synthetic `change` event does not move the focus, so the
    test passed while every real click failed. Focus the control first, or the
    test is checking a state a person never reaches.

  The floor is 1, not 0. Zero is the one number that would be accepted and then
  dropped: `heats_the_oven` reads the column as a boolean and `_oven.html`
  renders it as one, so a step stored at 0 °C keeps the number and shows
  nothing. `clean_oven_celsius` still tolerates 0 from a hand-made POST and
  normalises it to None, which is the backstop rather than the rule.
- **A script that reaches for another script's global must be loaded after it.**
  The settings page loaded `sound_preview.js` and not `timer_sounds.js`, so
  `window.kitchenSounds` was undefined, so the handler returned on its second
  line — Play buttons that did nothing at all, with no error and nothing to see.
  `config/tests.py::test_no_page_loads_a_script_without_what_it_reaches_for`
  walks every template for it; add the global to its one-line table when a new
  pair appears.
- **A resize commits on release, not as it moves.** Moving a line re-lays the
  canvas out, which destroys the element being dragged and with it the pointer
  capture. So the drag only draws a guide at the boundary it would land on, and
  the release applies the difference in one go — which also makes a drag that
  wanders and comes back a drag that did nothing.
- **Every handle answers the arrow keys.** A drag with no keyboard equivalent
  is a control half a household cannot use, and the pointer version is the one
  that cannot be offered to a screen reader at all. `role="separator"`,
  `tabindex="0"`, and ↑↓ (rows) or ←→ (a band's ends).
- **The list view exists because the drag says the sentence backwards.**
  Dropping card A onto card B means "A feeds into B", so building a recipe
  forwards — *this happens, and then this* — meant creating the later step first
  and dragging the earlier one onto it. "+ Step after this" and the "feeds into"
  select are the forward-reading versions. "+ Step after this" **always creates
  a step** and never guesses that an existing one was meant: the version that
  joined two parallel arms when it thought that was intended read well on the
  recipe it was written for and was unpredictable on every other.
- **The ingredients are on the recipe page once.** They used to be listed down
  the left *and* drawn inside the diagram beside it, with the scaler rewriting
  both. Preparing has the list; Cooking has the method, which already carries
  each line beside the step that consumes it. Anything that puts a second
  ingredient list on that page has undone this.
- **The cards are moved into the cells, not copied.** The formset rows *are* the
  cells: `render()` sends every card home to its holder and then moves the live
  ones into the layout. A canvas that drew its own boxes beside the form would
  be a second thing to keep in step, and the first edit to disagree with it
  would look like a save that did not take.
- **Portion sizes are a closed set.** Large / regular / small / child / to-go,
  with fixed weights in `PORTION_WEIGHTS`. Free text would give "gross", "große
  Portion" and "XL" for one thing, and then nothing could be added up. A portion
  put in a box for tomorrow counts as a whole one: it was made, and somebody
  eats it.
- **The catalogue grows by itself, and the matcher does not guess.** Saving a
  recipe mints the ingredient names it does not know, because a catalogue
  somebody has to fill in first is one that stays empty and an autosuggest with
  nothing to suggest. The cost is near-duplicates — "Kartoffeln" beside
  "festkochende Kartoffeln" — and the answer to those is the merge on the
  catalogue page, *not* a cleverer `catalogue.lookup`. Exact after folding,
  aliases searched, nothing else: a prefix match buys a handful of correct
  answers and pays for them with wrong ones, and a wrong one is the pantry
  claiming a substance the house does not have.
- **The suggestions are embedded, not fetched.** `catalogue.suggestions()` goes
  into the page through `json_script`. A household catalogue is a few hundred
  rows and a few kilobytes, and embedding it means no request, no debounce, no
  race between two responses and no new URL to keep behind the login. The shape
  it returns is already what an endpoint would return, so the day it is measured
  in thousands only the fetch has to be added.
- **The pantry belongs to the household, not to a person.** Anybody signed in
  may say there is no more butter — unlike a recipe, which belongs to whoever
  wrote it. The alternative is a shopping list that is wrong until one
  particular person gets home.
- **An empty pantry means "nobody has said", not "there is nothing".** The
  filter chips are not offered, the recipe page leaves the whole section out,
  and no card claims anything. A page that reads the first as the second is
  wrong for everybody who has not opted in.
- **Settings is a disclosure in the sidebar footer.** People, Sign-in and the
  Django admin are opened a handful of times a year and were sitting above the
  recipes somebody uses daily. It is a `<details>` — it opens with the keyboard
  and works before any script runs; shell.js only remembers the state, and the
  server forces it open when one of the pages inside is the current one.
- **No in-app export or backup.** The collection is one SQLite file plus
  `media/` under `/data`; Hyper Backup already covers that share.
- **Anyone signed in can read every recipe.** Editing is limited to whoever
  added it, or staff.
- **DSM itself should not be exposed to the internet**, whatever the original
  briefing said. DEPLOYMENT.md §1.
- **Instructions are plain text**, rendered with `linebreaks`. A rich-text
  editor is a second sanitiser to get right and a method is paragraphs.
