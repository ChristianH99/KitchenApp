# Kitchen

A self-hosted kitchen app for one household. Recipes first; the shape allows
meal planning, shopping lists and a pantry to arrive later as sibling apps
rather than as bolt-ons.

It runs as a single container on a Synology NAS, behind that NAS's own reverse
proxy, and authenticates against the **Synology SSO Server** over OIDC — so
there is one set of accounts, managed in DSM, and no passwords in this app.
Almost. There is deliberately one local fallback account; see below.

- Django 6 + server-rendered templates, no front-end framework
- SQLite, one file, WAL mode
- German by default, English from the topbar globe

## The other documents

| | |
|---|---|
| **[OPEN-ITEMS.md](OPEN-ITEMS.md)** | The state of the work: what has been run, what has **never** been run, what was left out on purpose, what is next. Read it before assuming any part of this is finished — the deployment half in particular has never been exercised. |
| **[DEPLOYMENT.md](DEPLOYMENT.md)** | The run-book for the NAS. §2 is the step that goes wrong. |
| **[docs/BRIEFING.md](docs/BRIEFING.md)** | The briefing this was built from, and the nine places this repository departs from it — each with its reason. Read before changing something back towards the brief. |
| **[CLAUDE.md](CLAUDE.md)** | Conventions and the rules that are load-bearing. |

## What it does

Recipes with **structured ingredients** — an amount, a unit and a name in
separate columns rather than a block of text. That is the one decision the rest
follows from: the recipe page can then scale a recipe from four servings to six
with a stepper, and a shopping list is reachable later without re-typing the
collection.

Beyond that: photographs (resized on upload), free tags, search that looks
inside ingredient lists as well as titles, and a note field for what you would
do differently next time.

## Running it locally

```
uv sync
uv run python manage.py migrate
uv run python manage.py collectstatic --noinput   # see the note below
uv run python manage.py seed_demo                 # account + sample recipes
uv run python manage.py runserver
```

`seed_demo` signs you in as **`claude` / `kitchen-dev-pass`** and adds four
recipes. It refuses to run with `DEBUG` off, because it creates an account with
a known password. For a real deployment, `createsuperuser` instead.

With no OIDC configured, `OIDC_ENABLED` defaults to off and the local login is
the only door — which is what you want on a laptop with no NAS in reach.

**`collectstatic` is a prerequisite of the test suite, not only of a
deployment.** `STORAGES` uses WhiteNoise's *manifest* storage in every mode, so
`{% static %}` resolves through `staticfiles/staticfiles.json`, which is
gitignored build output. A fresh checkout that has never run it fails most of
the suite with "Missing staticfiles manifest entry", because every page render
500s. The strictness is worth keeping — it is what turns a `{% static %}`
pointing at a file that does not exist into a failed test rather than a dead
page — but it does mean running it once after cloning, and again after adding a
static file.

## Commands

```
uv sync                                     # install/sync dependencies
uv run python manage.py runserver           # development server
uv run python manage.py migrate
uv run python manage.py createsuperuser
uv run python manage.py collectstatic       # required before pytest, see above
uv run pytest                               # ~180 tests, ~45 s

# After touching any translatable string:
uv run python manage.py makemessages -l de --no-obsolete
uv run python manage.py makemessages -d djangojs -l de --no-obsolete
uv run python manage.py compilemessages -l de --ignore=.venv
```

`compilemessages` needs GNU gettext on PATH. On Windows it ships with Git:
`$env:PATH = "C:\Program Files\Git\usr\bin;$env:PATH"`. Without it the command
fails with *"Can't find msguniq"*, which reads as gettext being missing rather
than merely unreachable.

After `makemessages`, **check for `#, fuzzy` entries before compiling**.
`msgmerge` guesses a translation from a similar msgid and marks it fuzzy;
gettext then ignores the entry at runtime, so the string comes out in English
while the file looks translated. `config/tests.py` fails on a fuzzy entry, on an
empty `msgstr`, and on a `.mo` older than its `.po`.

## Layout

```
config/                Django project.
  settings.py          Everything from the environment. The proxy block is the
                       one to read first: SECURE_PROXY_SSL_HEADER +
                       USE_X_FORWARDED_HOST are what make the OIDC redirect_uri
                       come out as https, and without them the login is an
                       unexplained loop.
  csp.py               The Content-Security-Policy. `script-src 'self'`, no
                       nonce — which is why every script lives in static/js/.
  health.py            /healthz, the one ungated URL. One SELECT 1, one word of
                       output, and deliberately nothing else.
  media.py             /media/ behind the login. Uploaded photographs are not
                       public, and "not linked from anywhere" is not access
                       control.
  wsgi.py              Where a *server's* start-up rules live — currently the
                       refusal to start with DEBUG off and no ALLOWED_HOSTS.
                       Not in settings.py, because collectstatic legitimately
                       runs in exactly that state.
  tests.py             The cross-cutting checks. Several of them discover their
                       own targets (every URL, every template, every .js file),
                       so a page added next month is covered the day it lands.

apps/nav.py            Which sidebar entry is current, as a registry keyed on
                       the (app, url_name) pair Django resolved. A registry
                       rather than comparisons in the template because a
                       url_name is only unique *within* an app: two apps with a
                       `list` view mark two entries at once, and nobody notices.

apps/accounts/         Getting in.
  pages.py             The URLs that need no session — and nothing else does.
                       An enumerated list rather than per-view decorators,
                       because a forgotten decorator leaves a page that looks
                       normal and answers to anybody, while a forgotten entry
                       here asks for a login and is noticed in four seconds.
  middleware.py        The login gate, plus OIDCSessionRefresh — which exists
                       because mozilla-django-oidc's own version redirects
                       *every* session without an OIDC token to the provider,
                       and that is precisely the local fallback account.
  oidc.py              What a Synology token means here. Identity is `sub`,
                       never e-mail (DSM lets two accounts share an address, and
                       an address can be reassigned). Nothing beyond `sub` is
                       required, because DSM versions differ in what they send —
                       the group claim has been absent entirely.
  throttle.py          Failed *local* logins, counted per (username, IP) and per
                       IP. The second is what sees one host working through a
                       list of accounts, which the first cannot.
  views.py             The sign-in page: Synology as the button, the local
                       password folded away below it.

apps/recipes/          The collection.
  models.py            Recipe, RecipeIngredient, Tag. Ingredients are rows, not
                       text — see below.
  images.py            The one door an uploaded photograph comes through: size
                       cap, Pillow verification, a resize, and a filename *we*
                       generate. The upload's own name is never used; an
                       extension is what a server picks a Content-Type from.
  forms.py             The recipe form and its ingredient formset. Tags are a
                       comma-separated text field rather than a multi-select,
                       because a multi-select asks somebody to go elsewhere and
                       create three objects first — which is how collections end
                       up untagged.
  views.py             Four pages, all pure reads except the form. Two rules run
                       through it: a read must not write, and the cost must not
                       grow with the collection.
  management/commands/seed_demo.py
                       A development account and four sample recipes, chosen to
                       cover the cases that differ (a fractional amount, an
                       amount-less line, a four-digit amount, a short recipe).
                       Refuses to run with DEBUG off.

templates/, static/    The shell (sidebar, topbar, dialogs) and the pages.
locale/de/             The German catalogs. .mo files are committed — nothing at
                       runtime compiles a .po.
deploy/                Dockerfile, entrypoint, compose file. Never yet run; see
                       OPEN-ITEMS.md §2.
docs/BRIEFING.md       The brief this was built from and where it was departed
                       from.
```

## The decisions worth knowing

**Ingredients are rows, not a text field.** A `TextField` called `ingredients`
is a third of the work and forecloses everything the app is eventually for:
scaling servings, building a shopping list from three recipes, asking what can
be made from what is in the cupboard. None of those can be retrofitted onto free
text without re-typing the collection by hand, which is the migration nobody
ever does. `Recipe.servings` is the other half — it says what those amounts are
*for*, and without it a number is just a number and scaling has nothing to
divide by.

**There is a local login as well as SSO.** An OIDC-only app is unreachable —
including its administration — whenever the SSO server, its certificate or the
container's DNS is unhappy, which is exactly when somebody needs to get in and
fix it. So one local superuser exists, it is offered second and folded away, and
`apps/accounts/middleware.py` had to be written because the OIDC library's own
session refresh would otherwise bounce that account to the provider on its first
request.

**One container, SQLite.** See `deploy/docker-compose.yml`.

**Nothing on a page is inline.** No inline `<script>`, no `style="…"`, no
`onclick=`. A CSP with `script-src 'self'` cannot tell our inline script from an
injected one, so allowing ours allows the attack it exists to stop. Page data
crosses into JavaScript through `json_script`, strings through `gettext()` and
the djangojs catalog. `config/tests.py` fails on each of these, per template.

**Everything visual comes from the token block** at the top of
`static/css/main.css`: no raw colour, spacing, font size, duration or z-index
outside it. The scales are closed sets — a component that needs a step which is
not there means the *scale* is missing a step. Pinned by
`config/tests.py::TestTheScalesAreClosed`.

**Every dialog is the app's own.** Nothing calls `window.confirm`: a browser
dialog wears the OS's styling, cannot be translated by us, and puts the answer
behind a control that looks nothing like the page. `window.appConfirm()` /
`appAlert()` in `shell.js` fill the shared dialog and handle focus.

**A formset row is never taken out of the DOM.** A formset is an index range,
not a list: a form left out of the POST is a hole, and Django reads the absent
fields against that form's defaults, decides it changed, and validates it —
which is how a removed ingredient comes back wearing "This field is required".
Removal ticks `DELETE` and hides the row, and every field of the form (the pk
included) lives *inside* the row so the operation cannot leave one behind.

## Known and deliberate

- **No password change in the app.** Accounts come from DSM; the local fallback
  is a superuser's job. Adding self-service would mean a second password store,
  which is the thing SSO is here to avoid.
- **Exports and backups are the NAS's job.** The whole collection is one SQLite
  file plus a `media/` folder under `/data`; Hyper Backup already covers that
  share. An in-app export would be a second, worse copy of a working answer.
- **Anyone signed in can read every recipe.** Editing is limited to whoever
  added it (or staff). A household collection is shared to cook from; the
  failure worth preventing is somebody quietly rewriting the family recipe, not
  somebody reading it.
