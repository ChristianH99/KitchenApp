# Kitchen

A self-hosted kitchen app for one household. Recipes and a pantry; the shape
allows meal planning and shopping lists to arrive later as sibling apps rather
than as bolt-ons.

It runs as a single container on a Synology NAS, behind that NAS's own reverse
proxy, and authenticates against an **OpenID Connect provider** — a Synology SSO
Server is what it was built for, but nothing user-facing names one. So there is
one set of accounts, managed by the provider, and no passwords in this app.
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

## Releases

A tag builds the image; nothing is built on the NAS.

```
git tag v1.2.0 && git push origin v1.2.0
```

`.github/workflows/release.yml` runs the suite, builds `linux/amd64`, **starts
the image it just built** and checks it answers `/healthz` with the version it
claims to be, then pushes `ghcr.io/christianh99/kitchenapp:<version>` and
attaches four files to the GitHub release: the image as a `docker load`-able
tarball, a `docker-compose.yml` pinned to that version, `env.example` and
`SHA256SUMS`. The attachment path needs no registry and no credentials, which
is the point of having it as well as the registry. DEPLOYMENT.md §8.

`.github/workflows/ci.yml` runs on every push and pull request: the test suite,
plus a build of the image and a smoke test that starts the container and checks
what it serves. That second job is the one that matters most here — gunicorn
cannot run on the Windows machine this is developed on, so CI is the only place
the *server* is ever exercised.

## What it does

Recipes with **structured ingredients** — an amount, a unit and a name in
separate columns rather than a block of text. That is the one decision the rest
follows from: the recipe page can then scale a recipe from four servings to six
with a stepper, and a shopping list is reachable later without re-typing the
collection. A line can be marked **optional**, and a substitute is a full
ingredient line of its own (`alternative_for`) rather than a note, so "180 g
margarine instead of 200 g butter" scales like everything else.

A method can also be a **diagram** — the Cooking-for-Engineers table, with the
ingredients down the left and the operations merging to the right. Steps point
at the step they feed into and ingredients point at the step that consumes them,
which is a tree; `apps/recipes/diagram.py` turns it into the rowspan/colspan
geometry. The prose instructions stay exactly as they were and are shown
alongside: the table says *what combines with what*, the paragraphs say *how*.

That diagram is also how a recipe is **written**, and the form offers two ways
of writing the same thing. **Steps** is a numbered list where each row says what
it feeds into and each line says which step uses it — the recipe read in the
direction it is spoken. **Diagram** is the canvas: the cards laid out as the
table they will become, arranged by dragging a line onto the step that uses it
or a step onto the step it feeds into. They are not two copies; the switch moves
the *same* form rows, so nothing can be entered in one and missing from the
other. Every drag has an arrow-key equivalent on the card's handle.

"+ Step after this" on a step card is the move the drag makes hard to find:
dropping A onto B means "A feeds into B", so building a recipe forwards used to
mean creating the later step first and dragging the earlier one backwards onto
it. A step with nothing going into it is a standing instruction, "heat the
oven", drawn as a band wherever you leave it in the order.

**A tile is resized by dragging its edge.** The bottom edge of a step is the
boundary between the ingredients that go into it and the ones that go into the
next step — drag it and the boundary moves, one ingredient at a time. A
standing instruction's band has the same on its left and right ends, which is
how "heat the oven" comes to sit over just the two steps it actually runs
alongside instead of claiming to run alongside the mixing that came before
them. Nothing moves until the drag is released; while it moves, a guide shows
where the edge would land. Every handle is focusable and answers the arrow
keys.

A recipe **will not save half-finished**. Every ingredient needs an amount or an
explicit "no fixed amount" (which is what salt and pepper are), every ingredient
has to be used by some step once the recipe has a method at all, and a branch
that is wired to nothing is refused — that last one being the case where two
doughs get made and the step that kneads them together was never joined to
either.

The recipe page splits into **Preparing** and **Cooking** rather than showing
the ingredients twice, once as a list and again inside the diagram beside it.
Preparing is what to buy and get out; Cooking is the method, as steps or as the
diagram.

A **cooking view** walks that diagram one step at a time, lighting up the
current operation and the lines that go into it, with a stopwatch and a
per-step timer. When you finish, it records how long it really took and how far
the food actually went — "2 portions and one for the lunchbox" — so the recipe
page can eventually say what "serves four" means in *this* house. A **Cooked**
page lists those evenings across every recipe, and any of them can be opened and
corrected: how far a dish went is usually clearer the next day, and before that
page the only way to fix it was to delete the entry and lose the date with it.

Everything that is about the *app* rather than about the food — the household's
accounts, the sign-in configuration, the Django admin — lives in a **Settings**
group at the foot of the sidebar. The **People** page manages both local
accounts and the ones that arrive through SSO, without sending anybody to the
admin. The **Sign-in** page beside it configures the OIDC connection itself —
endpoints, client ID and secret, the group rules and the on/off switch — so
setting single sign-on up needs no `.env` edit and no container restart. It is
superuser-only, reads the discovery document from inside the container, and will
tell you whether the container can actually reach the provider, which is the
failure that otherwise eats an evening.

## The pantry

What is actually in the house, measured against what the recipes ask for. Two
questions, one answer: *what can be cooked now* and *what would have to be
bought*.

Behind it is an **ingredient catalogue** — one row per substance, with the unit
it is usually measured in, the other names it goes by ("Zwiebeln" for
"Zwiebel"), and the sizes it is sold in (sugar in 1 kg, milk in 1 l). Recipe
lines point at it, which is the only reason a cupboard saying "1 kg Zucker" can
answer a recipe asking for 500 g. It grows by itself: every recipe saved mints
the names it does not already know, and the ingredient field on the form
suggests from it — filling in the usual unit, which is what keeps the collection
comparable at all.

Units are a **closed set** with conversion inside a dimension and none across
one. Grams convert to kilograms, tablespoons to millilitres; a clove of garlic
converts to nothing, because it is not four grams. Where the comparison cannot
be made the answer is "cannot tell" rather than a guess — and "cannot tell"
counts against *can be made now*, because that is a promise somebody acts on by
not going to the shop.

The recipe list can then be filtered to what the house can make tonight, or to
what it is two things short of; a recipe says which of its lines are missing and
what to buy, rounded up to whole packets — "one 500 g pack" rather than "380 g",
which is the useful sentence in a shop.

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

The ingredient catalogue arrives with the migrations — about a hundred things a
German kitchen has in it, each with the unit it is usually measured in and the
sizes it is sold in. That is what makes the autosuggest useful on the first day
rather than after somebody has filled a table in. `manage.py seed_catalogue`
tops it up and, with `--link`, points existing recipe lines at it; run it with
`--dry-run` first, because it prints exactly which new ingredients it would
invent.

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
uv run pytest                               # ~500 tests, ~155 s

# After touching any translatable string:
uv run python manage.py makemessages -l de --no-obsolete --no-wrap
uv run python manage.py makemessages -d djangojs -l de --no-obsolete --no-wrap
uv run python tools/fix_po.py               # not optional on Windows, see below
uv run python manage.py compilemessages -l de --ignore=.venv
```

`compilemessages` needs GNU gettext on PATH. On Windows it ships with Git:
`$env:PATH = "C:\Program Files\Git\usr\bin;$env:PATH"`. Without it the command
fails with *"Can't find msguniq"*, which reads as gettext being missing rather
than merely unreachable.

**`--no-wrap` and `tools/fix_po.py` are both load-bearing here.** Without
`--no-wrap`, gettext breaks a long `msgstr` across continuation lines — which is
correct `.po` and which the completeness check in `config/tests.py` reads as an
*empty* translation, so a perfectly translated catalog fails the suite. And on
Windows `makemessages` wraps a long `#:` reference block onto a second line
beginning with a space instead of a second `#:`; `msgfmt` then refuses the whole
file, writes no `.mo`, and the app carries on serving the *previous* catalog —
so the session's translations look compiled and simply are not there.
`tools/fix_po.py` repairs that, and there is now a test that fails on it rather
than leaving it to be rediscovered.

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
  users.py             The People page — the household's accounts, managed here
                       rather than in the Django admin, which speaks Django's
                       vocabulary and shows a Synology account under an opaque
                       `sub`. Authorisation is per view here (staff only) and
                       checked from the outside by a test that walks the
                       URLconf for `user-*`.
  forms.py             The two kinds of account. `has_usable_password()` is what
                       tells them apart — not a heuristic: the OIDC backend calls
                       `set_unusable_password()` precisely so a DSM-managed
                       identity can never also be reachable through the local
                       form.
  models.py            The SSO connection, stored so it can be edited from a
                       page. The docstring is the one to read: it states what
                       moving the client secret out of the environment costs
                       and what bounds the damage, rather than pretending the
                       trade was free.
  sso.py               Where mozilla-django-oidc gets its settings from. The
                       library reads `getattr(settings, ...)`, which is fixed
                       for the life of the process; this answers for the handful
                       of names that are editable and passes the rest through.
  secrets.py           Encrypting that one secret at rest, and being precise
                       about what it buys: a copy of the database is not enough,
                       a copy taken with the environment is.
  sso_views.py         The settings page, plus "read the endpoints off the
                       server" and "can this container reach it" — the two
                       manual steps DEPLOYMENT.md §3 used to ask for.

apps/recipes/          The collection.
  models.py            Recipe, RecipeStep, RecipeIngredient, Tag, CookLog,
                       CookPortion. Ingredients are rows, not text — see below.
  diagram.py           Laying a recipe out as the Cooking-for-Engineers table.
                       Pure: model instances in, dataclasses out, so a page can
                       hand it prefetched lists. The header comment is the one
                       to read — the column an operation sits in is *not* its
                       depth, and the three ways the obvious version renders
                       something wrong are each spelled out.
  images.py            The one door an uploaded photograph comes through: size
                       cap, Pillow verification, a resize, and a filename *we*
                       generate. The upload's own name is never used; an
                       extension is what a server picks a Content-Type from.
  forms.py             The recipe form and its two formsets. Tags are a
                       comma-separated text field rather than a multi-select,
                       because a multi-select asks somebody to go elsewhere and
                       create three objects first — which is how collections end
                       up untagged. The diagram is wired up by *row index*
                       rather than by primary key, and the order is a field of
                       its own rather than the row's position in the range; the
                       module docstring says why, and it is the only version
                       that works while a recipe is being typed in for the
                       first time.
  views.py             The pages, all pure reads except the two forms. Two rules
                       run through it: a read must not write, and the cost must
                       not grow with the collection. The cooking view is a GET
                       and nothing else — the stopwatch is in the browser.
  management/commands/seed_demo.py
                       A development account and four sample recipes, chosen to
                       cover the cases that differ (a fractional amount, an
                       amount-less line, a four-digit amount, a short recipe) —
                       and three diagram shapes: a branch, a standing
                       instruction with nothing flowing into it, and one recipe
                       with no diagram at all. Refuses to run with DEBUG off.

apps/pantry/           What the house has, and what a thing *is*.
  units.py             The closed set of units and the only place that knows
                       what converts into what. A unit belongs to a dimension
                       and only converts within it; everything countable is a
                       dimension of one, which is a deliberate way of saying
                       *this never converts*. The header says why a tablespoon
                       is volume and a cup is not.
  models.py            Ingredient (the substance), IngredientAlias (every other
                       name for it), PurchaseSize (how it is sold), PantryItem
                       (what is in the cupboard). A null pantry amount means
                       "some, not counted" and is treated as enough — which is
                       the truth about salt.
  catalogue.py         Turning a typed name into a row. Exact after folding,
                       aliases searched, and *nothing else guessed*: a prefix
                       match buys a few correct answers and pays for them with
                       wrong ones, and a wrong one here is the pantry claiming
                       a substance the house does not have.
  matching.py          Measuring a recipe against the cupboard. Four verdicts,
                       and the fourth is the point — "cannot tell" is kept
                       separate from "missing" and counts against "can be made
                       now". Pure, so the list page can run it over every
                       recipe at a flat query count.
  starter.py           About a hundred things a German kitchen has in it, with
                       the unit each is usually measured in. Loaded by a data
                       migration, because a feature that only starts helping
                       after somebody runs a command is one nobody turns on.
  management/commands/seed_catalogue.py
                       Tops the shipped list up and, with --link, points
                       existing recipe lines at it. --dry-run first: it prints
                       exactly which new ingredients it would invent.

templates/, static/    The shell (sidebar, topbar, dialogs) and the pages.
static/icons/          The site icon. A second copy of the pot mark from
                       templates/_brand.html, carrying its own colours: a
                       favicon is fetched on its own, outside any page, so it
                       has no `currentColor` to take and no stylesheet to read.
                       base.html names it, and /favicon.ico redirects to it for
                       anything that asks anyway — without both, every visit
                       logged a 404.
tools/fix_po.py        Repairs the reference lines makemessages mangles on
                       Windows. Run between makemessages and compilemessages.
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
