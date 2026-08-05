# State of the work

Written 2026-08-05 at the end of the session that created the repository, and
updated the same day by the session that added the diagram, the cooking view and
the account pages. It exists so that somebody — or some agent — picking this up
cold knows three things the code cannot tell them: **what has actually been
run**, what was left out on purpose, and what is worth doing next.

Keep it current. A status document that is six weeks stale is worse than none,
because it is believed.

---

## 1. What has been verified, and how

Everything below was observed working, not merely written.

| | How it was checked |
|---|---|
| The whole test suite | `uv run pytest` — **263 passed**, ~90 s |
| Every page renders | Driven in Chrome against `runserver`: home, list, detail, form, tags, login, cooking view, People |
| Adding a recipe end to end | Typed into the real form in the browser; ingredients, tags and slug all correct on save |
| Blank formset rows dropped | Same submission — 5 rendered rows, 2 filled, 2 ingredients saved |
| Tags reused, not duplicated | 8 tags before, 9 after a submission naming two existing ones and one new |
| Servings scaling | Zwetschgenkuchen 12 → 18 in the browser: 1,5 kg → 2,25 kg, "umgerechnet" note appears — **and the diagram cells and the substitute follow in step**, read back from the DOM |
| The diagram's geometry | Read the rendered `<table>` back out of the page cell by cell: the full-width "Ofen vorheizen" row, the four ingredients merging into "Hefeteig ansetzen", the rowspans and the empty filler cells all as in the reference picture |
| A branching diagram | Kartoffelsalat: two arms (potatoes, dressing) meeting at "übergießen", the shallow arm spanning the columns between |
| The diagram survives an edit | Opened the real edit form, pressed Save with nothing changed, and the diagram came back identical — the selects had been primed from the saved relations |
| The cooking view | Walked Kartoffelsalat step by step in the browser: the current cell and its four ingredient rows light up, earlier steps go green, the stopwatch runs, "Fertig" opens the finish panel with the measured minutes filled in |
| Creating a local account | Typed into the real People form; account created, password usable |
| No sideways scroll at 390px | Measured, not eyeballed: the recipe page was 508px wide inside a 390px column before `min-width: 0` and is exactly 390px after it, with the diagram scrolling inside its own box. The form and the cooking view were measured the same way |
| German UI | `lang="de"`, sidebar and headings in German, catalogs compiled and loaded; every new string translated |
| Settings import with `DEBUG=False` | `manage.py check --deploy` — see §4 for the three warnings and why two of them stay |
| **The Docker image builds** | `docker build -f deploy/Dockerfile` on the development machine — **first time ever**, and it succeeded on the first attempt. 218 MB. Both feared failures (the `uv` layer, Pillow needing headers) did not happen; Pillow's manylinux wheels are enough |
| **gunicorn serves this app** | Also a first. `docker run`, migrations applied on start-up from `entrypoint.sh`, two workers booted, the container reported itself `healthy` after ~60 s |
| What the container serves | `/healthz` → `ok`; `/` → 302 to `/accounts/login/?next=%2F`; the login page → 200 with the full CSP header; the hashed `main.<hash>.css` → 200, so WhiteNoise really is serving from inside the image |
| `migrate --check` and `check --deploy` inside the container | Exit 0, and only the two deliberate warnings plus a `W009` for the throwaway smoke-test key |
| The version reaches the page | `KITCHEN_VERSION` build-arg → env var → rendered in the sidebar of a real signed-in page, checked from inside the running image |
| No development data in the image | `/app` has no `db.sqlite3`, no `media/`, no `.env` — the `.dockerignore` does what its comment claims |
| **The release attachment path, end to end** | `docker save \| gzip` → 73 MB; then the image was **deleted outright** and restored from the tarball alone with `docker load`; `docker compose up -d` found it locally without touching a registry, came up healthy, and wrote its database into the bind-mounted folder |
| The generated compose file and checksums | `sed` substitution produces `image: ghcr.io/christianh99/kitchenapp:<version>`; `sha256sum -c` verifies all three assets; `dist/*` picks up all four files including `env.example` |
| The workflows parse | YAML loads, and all 21 `run:` blocks pass `bash -n` with the GitHub expressions stubbed out |
| **CI runs green on GitHub** | Both jobs, on a runner. The *first* run failed — see the entrypoint note in §6, which is the bug the pipeline was worth building for — and everything since is green with no annotations |
| **The release workflow ran end to end** | `v0.1.0`: verify → build → smoke-test the image about to be published → push to GHCR → assemble → attach. First attempt, no fixes needed |
| **The published release carries all four assets** | `kitchen-0.1.0-linux-amd64.tar.gz` (75.6 MB), `docker-compose.yml`, `env.example`, `SHA256SUMS` — and the notes rendered with the deployment instructions above GitHub's generated changelog |
| **The whole consumer path, as the NAS would do it** | In a clean directory: `gh release download` → `sha256sum -c` all three → `docker load` from the tarball → rename `env.example` to `.env` and fill it in → `docker compose up -d`. Container healthy, `/healthz` → `ok`, `/` → 302 to the login, the database written into the bind mount |
| The registry path | `docker logout ghcr.io`, then an anonymous `docker pull ghcr.io/christianh99/kitchenapp:0.1.0` — **succeeds**. The package took the repository's public visibility, so the NAS needs no credentials. DEPLOYMENT.md §8 had guessed the opposite and has been corrected |
| The version really is end to end | Git tag `v0.1.0` → build arg → env var → OCI label (`0.1.0`, revision `fa59ab0`) → **printed in the sidebar of the released container**, seen in a browser |
| The released image is usable | Signed in to it, added a recipe with a two-step diagram through the real form, and the diagram rendered on the detail page |
| `seed_demo` refuses in a release | `CommandError` inside the released container, as designed — it creates an account with a known password |
| **The SSO settings page** | Driven in the browser: filled in and saved, the “from the environment” notice went away, the sign-in button appeared on the login page **with no restart and no `.env`**, and the client secret was nowhere in the returned HTML. “Check the connection” reported the right failure for an address that does not resolve |
| `deploy/entrypoint.sh` parses | `sh -n`, and now also runs for real |

**One caveat on the responsive work.** Chrome's `resize_window` had no effect in
this session — the viewport stayed at 2048px however it was called — so the
phone layout was verified by *measurement* (forcing the content column to 390px
and checking what overflows) rather than by a phone-sized screenshot. That
caught a real bug the eye would also have caught, and it is not the same as
having looked at it. Worth ten minutes on an actual phone before trusting it.

## 2. What has **never** been run

This is the important half. None of it is known to be broken; none of it is
known to work either, and the difference matters when somebody is standing at
the NAS.

- **`OIDC_ALLOWED_GROUPS` / `OIDC_STAFF_GROUP` against a real token.** The claim
  handling is unit-tested but no DSM has ever supplied the claims.
- **The SSO settings page against a real SSO server.** The page, the stored
  configuration, the write-only secret and the on/off switch were all driven in
  a browser (§1); what has never happened is *"Read the endpoints from the
  server"* pointed at an actual Synology, or a login completing against a
  configuration that came out of the database rather than out of `.env`. The
  discovery-document parsing in particular is written from what OIDC discovery
  documents contain, not from one Synology returned.
- **A migration on a database that already has recipes in it.** Every migration
  so far has run against an empty or development database. The first update that
  carries a schema change to a `/data` with the household's real collection in
  it is the one to take a copy before — see DEPLOYMENT.md §7.
- **A second release, and therefore an *update*.** v0.1.0 was installed from
  nothing. Replacing a running container with a newer image — the path in §7,
  including whether the version in the sidebar actually changes — has not been
  done.
- **Anything on the DS723+ itself.** Everything below §1's container rows was
  observed on the development machine with Docker Desktop, not on the NAS: the
  bind mount at `/volume1/docker/kitchen/data`, the uid-1000 ownership rule, the
  reverse proxy, the certificate, DSM's Container Manager.
- **The OIDC flow has never touched a real Synology SSO Server.** The claim
  handling is unit-tested by handing the backend dictionaries — which is the
  only way to test the DSM version that omits the group claim — but no browser
  has ever completed a round trip. Nothing about the handshake itself, the
  discovery document, or the token signature algorithm has been observed.
- **The `OIDC_OP_*` default endpoint paths in `settings.py` are guesses.** They
  follow Synology's usual `webman/sso/` shape. DEPLOYMENT.md §3.1 tells you to
  read the real ones off the discovery document before trusting them, and that
  instruction is not politeness.
- **`deploy/entrypoint.sh` has never executed** beyond a syntax check.
- **`docker-compose.yml` paths are assumptions** —
  `/volume1/docker/kitchen/data` is the conventional place, not an observed one.
- **The reverse-proxy configuration in DEPLOYMENT.md §2 is from knowledge of
  DSM, not from this DS723+.** The custom-header requirement is real and is the
  thing that breaks OIDC; the exact DSM menu path may differ by version.

## 3. Deliberately not built

Not gaps. Each was considered and left out, and re-opening one is fine — this
list exists so it is re-opened knowingly.

- **Meal planning, shopping lists, a pantry.** The app is called *Kitchen* and
  the project is laid out for them (`apps/recipes/` is one app among future
  siblings, not the whole thing), but only recipes exist. The structured
  ingredient rows are the groundwork; a shopping list is an aggregation over
  `RecipeIngredient` and needs no schema change to recipes. It now has two more
  columns to respect: `optional` (don't buy saffron every week) and
  `alternative_for` (don't buy both the butter and the margarine).
- **HTMX.** The briefing asked for it and the app does not use it. Nothing here
  needs partial page updates yet: the servings scaler is pure client-side
  arithmetic, the cooking view shows and hides steps the server already
  rendered, and everything else is a form post. Adding HTMX for its own sake
  would be a dependency and a second rendering path for no behaviour. The
  moment there is a live-filtering list or an inline edit, it earns its place.
- **A server-rendered preview of the diagram while editing.** The form's preview
  is a *nesting* built in JavaScript, not the real table. Drawing the real one
  would mean either re-implementing the rowspan/colspan arithmetic in a second
  language — the thing most likely to quietly disagree with the page it is
  previewing — or a `fetch` to an endpoint that lays out an unsaved POST. The
  second is the one to build if the preview ever needs to be exact; it is a view
  and a template, not a rewrite.
- **Editing the diagram by dragging.** Two `<select>`s per row is not elegant,
  and it works on a phone, survives without JavaScript for everything except the
  wiring itself, and needs no drag-and-drop library. A canvas editor is a real
  feature, not a polish pass.
- **Nutrition, cost per portion, and a real unit vocabulary.** `unit` is still
  free text ("EL" / "Esslöffel" / "Tbsp" are three units as far as the database
  is concerned). Normalising it is the prerequisite for all three, and there is
  no reason to do it until one of them is wanted.
- **Recipe versioning / history.** Somebody rewriting the family Rouladen is
  handled by *who may edit* (`apps/recipes/views._may_edit`), not by an audit
  trail. A household of four does not need one; if it turns out to, the shape to
  copy is `apps/audit.py` in the SlalomTiming repository.
- **Favourites, ratings, comments.** Named in the briefing as "later".
  Favourites is the cheapest and most likely first: a `M2M` from `User` to
  `Recipe`, a star on the card, a filter on the list.
- **Import from a URL.** Everybody wants it and it is a scraper per site.
  Structured ingredient rows make it *possible* (schema.org/Recipe maps onto
  them almost exactly), which is another thing the text-field version would have
  foreclosed.
- **Self-service password change.** Accounts come from DSM. See CLAUDE.md.
- **In-app export/backup.** `/data` is one SQLite file plus `media/`; Hyper
  Backup already covers that share.
- **Per-user access control beyond edit rights.** Anyone signed in reads
  everything. A household is the trust boundary.

## 4. Known warnings that must not be "fixed"

`manage.py check --deploy` reports three. Two are deliberate:

- **`security.W005` — `SECURE_HSTS_INCLUDE_SUBDOMAINS` is off.** It applies to
  *every* subdomain of `haeusslerr.de`, including `nas.` and `ha.`, which this
  app has no business pinning to HTTPS on their behalf. Turn it on only if you
  own that decision for the whole domain. Configurable via
  `DJANGO_HSTS_INCLUDE_SUBDOMAINS`.
- **`security.W008` — `SECURE_SSL_REDIRECT` is not True.** Correct here. The
  Synology proxy is the only way in and it already speaks HTTPS; a redirect
  issued by this process would only ever fire for the container's own health
  check on `127.0.0.1` and would turn a healthy server into a failing probe.
- `security.W021` (preload) is silenced in `settings.py` with the same argument.
- **`security.W009`** appears only when a short throwaway `DJANGO_SECRET_KEY` is
  used for the check itself. A real key does not trip it.

## 5. Worth doing next, roughly in order

1. **Put v0.1.0 on the actual NAS.** Everything up to the container is now
   known good (§1) and nothing past it is. DEPLOYMENT.md §1–§5 in order: the
   data folder and its uid-1000 ownership, the reverse-proxy rule with its two
   custom headers (§2 — this is the step that breaks the login), the SSO client
   read off the real discovery document rather than trusted from `settings.py`,
   then the local fallback administrator *before* switching OIDC on.

   Correct DEPLOYMENT.md as you go, in the file rather than in your terminal
   history, and move what you observed into §1 here.
2. **Complete one OIDC round trip against the real SSO server**, then correct
   the endpoint defaults in `settings.py` and DEPLOYMENT.md §3.1 to what was
   actually found. Note the DSM version in the commit message — the next person
   to hit a moved endpoint will want to know which version this was true for.
3. **Look at it on a real phone**, and at the cooking view in particular — that
   is the page whose whole point is being used on one. See the caveat under §1:
   the responsive work was measured rather than seen.
4. **Favourites.** Small, obviously wanted, and it exercises the first
   per-user relation in the app.
5. **A shopping list** across selected recipes. This is the feature the
   structured ingredients were for, and the first one that will show whether the
   unit field wants normalising (`EL` vs `Esslöffel` vs `Tbsp`) — which it
   currently is not, on purpose: free text until there is a reason.
6. **Pagination on the recipe list, and on the cooking history.** The list
   renders every recipe; the recipe page renders the last ten cookings and says
   how many more there are. Fine at a hundred with lazy-loaded images; not fine
   at a thousand. The query-cost tests will *not* catch this — they pin the
   query count, which stays flat while the payload grows.
7. **A print stylesheet for the recipe page.** People print recipes, and the
   diagram is the part that will come out wrong: it lives in a horizontally
   scrolling box that a printer cannot scroll.
8. **Somewhere to see the cooking history across recipes** — "what did we eat
   this month", and whether "serves four" is ever true in this house. Every
   number for it is already recorded; nothing reads it yet except the recipe
   page's own median.

## 6. Things that will bite

Collected because each one cost time in the session that built this, and none is
visible from the code alone.

- **`collectstatic` is a prerequisite of `pytest`**, not only of a deployment.
  A fresh checkout fails most of the suite with "Missing staticfiles manifest
  entry" until it has run once.
- **A shell script's executable bit does not survive this machine.** Windows
  sets `core.filemode=false`, so git never records it: `deploy/entrypoint.sh`
  sat in the index as `100644` from the day it was written. Docker Desktop hides
  it completely — it copies from NTFS and hands everything `0755`, so the image
  builds and runs perfectly *here* and the identical commit built on Linux dies
  with `exec: "/app/deploy/entrypoint.sh": permission denied`, exit 126, no
  application log. **This is what the first CI run found**, and it is the exact
  failure the pipeline was worth building for: the image that works locally was
  not the image that ships. Fixed in git (`git update-index --chmod=+x`) and
  again in the Dockerfile, which chmods it regardless of how the source arrived.
  Check `git ls-files -s` before adding another script.
- **GNU gettext is not on PATH** on the development machine. It ships with Git:
  `$env:PATH = "C:\Program Files\Git\usr\bin;$env:PATH"`.
- **`makemessages` on Windows emits a malformed `#:` reference line** — a
  wrapped reference continues with a leading space instead of a second `#:`,
  and `msgfmt` then refuses the whole file with "keyword unknown". It is not
  occasional: it happened on *every* run in the second session, and `--no-wrap`
  does not prevent it. Symptom: `compilemessages` prints an error that is easy
  to scroll past, no `.mo` is written, and the app keeps serving the previous
  catalog — so the translations look compiled and are not there. Run
  `uv run python tools/fix_po.py` between `makemessages` and `compilemessages`;
  `config/tests.py` also fails on it now.
- **Pass `--no-wrap` to `makemessages`.** Otherwise gettext breaks a long
  `msgstr` across continuation lines, which is valid `.po` and which the
  completeness check reads as an *empty* translation. A fully translated catalog
  then fails the suite, and the obvious next move is to "fix" the check.
- **`runserver --noreload` caches templates for the life of the process.**
  Django uses the cached template loader; the autoreloader is what normally
  hides that. With `--noreload` a template edit changes nothing until restart,
  and the symptom — the old markup rendering from a file that is plainly correct
  — reads as a browser cache problem and is not one. This cost time in the
  second session, twice.
- **The static manifest is used in development too, and the URL is unhashed.**
  So a CSS or JS edit needs `collectstatic`, *and* the browser will still serve
  its cached copy. When a style change appears not to apply, rule those two out
  before changing the CSS again.
- **A grid or flex column defaults to `min-width: auto`** — "never shrink below
  my content". Any column that can hold the diagram, a wide table or a text
  input needs `min-width: 0`, or the page scrolls sideways on a phone and takes
  the topbar with it. Caught by measurement here, not by looking.
- **A value read by JavaScript must be `|unlocalize`d.** German renders `1.5` as
  `1,5` and `parseFloat` stops at the comma. There is a test for the one place
  this matters today; there is no test for the next place somebody adds.
- **`django_assert_num_queries` asserts a count, it does not capture one.** Use
  `CaptureQueriesContext` when comparing two runs.
- **The `english` fixture has to set both `LANGUAGE_CODE` and
  `translation.override`.** `LocaleMiddleware` resolves the language again per
  request, so an override alone still renders German pages.
- **Chrome's per-origin zoom persists** across navigations and cannot be reset
  from the browser tooling (`ctrl+0` is refused). If screenshots come back
  magnified, read the page with `javascript_tool` instead of fighting it.
