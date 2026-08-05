# State of the work

Written 2026-08-05, at the end of the session that created the repository. It
exists so that somebody — or some agent — picking this up cold knows three
things the code cannot tell them: **what has actually been run**, what was left
out on purpose, and what is worth doing next.

Keep it current. A status document that is six weeks stale is worse than none,
because it is believed.

---

## 1. What has been verified, and how

Everything below was observed working, not merely written.

| | How it was checked |
|---|---|
| The whole test suite | `uv run pytest` — **180 passed**, ~45 s |
| Every page renders | Driven in Chrome against `runserver`: home, list, detail, form, tags, login |
| Adding a recipe end to end | Typed into the real form in the browser; ingredients, tags and slug all correct on save |
| Blank formset rows dropped | Same submission — 5 rendered rows, 2 filled, 2 ingredients saved |
| Tags reused, not duplicated | 8 tags before, 9 after a submission naming two existing ones and one new |
| Servings scaling | Zwetschgenkuchen 12 → 18 in the browser: 1,5 kg → 2,25 kg, "umgerechnet" note appears |
| German UI | `lang="de"`, sidebar and headings in German, catalogs compiled and loaded |
| Settings import with `DEBUG=False` | `manage.py check --deploy` — see §4 for the three warnings and why two of them stay |
| `deploy/entrypoint.sh` parses | `sh -n` |

## 2. What has **never** been run

This is the important half. None of it is known to be broken; none of it is
known to work either, and the difference matters when somebody is standing at
the NAS.

- **The Docker image has never been built.** `deploy/Dockerfile` is written and
  reasoned about but `docker build` has not been executed once. Expect to
  iterate on it. Most likely first failures: the `uv` layer, and Pillow needing
  `libjpeg-dev`/`zlib1g-dev` in the runtime stage (the slim image carries the
  runtime libraries but a wheel-less Pillow build would need the headers —
  Pillow ships manylinux wheels, so this probably does not bite, but it is the
  first thing to check if the build stops there).
- **gunicorn has never served this app.** It cannot run on Windows (`fcntl`), so
  every observation above is through `runserver`. The WSGI callable is exercised
  by the test client, so the application object is sound; the *server* is not.
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
  `RecipeIngredient` and needs no schema change to recipes.
- **HTMX.** The briefing asked for it and the app does not use it. Nothing here
  needs partial page updates yet: the servings scaler is pure client-side
  arithmetic and everything else is a form post. Adding HTMX for its own sake
  would be a dependency and a second rendering path for no behaviour. The
  moment there is a live-filtering list or an inline edit, it earns its place.
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

1. **Build the image and run it.** Everything in §2 collapses into this one
   task, and until it is done the deployment half of this repository is
   untested. Do it before adding a single feature.
2. **Complete one OIDC round trip against the real SSO server**, then correct
   the endpoint defaults in `settings.py` and DEPLOYMENT.md §3.1 to what was
   actually found. Note the DSM version in the commit message — the next person
   to hit a moved endpoint will want to know which version this was true for.
3. **Favourites.** Small, obviously wanted, and it exercises the first
   per-user relation in the app.
4. **A shopping list** across selected recipes. This is the feature the
   structured ingredients were for, and the first one that will show whether the
   unit field wants normalising (`EL` vs `Esslöffel` vs `Tbsp`) — which it
   currently is not, on purpose: free text until there is a reason.
5. **Pagination on the recipe list.** It renders every recipe. Fine at a hundred
   with lazy-loaded images; not fine at a thousand. The query-cost tests will
   *not* catch this — they pin the query count, which stays flat while the
   payload grows.
6. **A print stylesheet for the recipe page.** People print recipes.

## 6. Things that will bite

Collected because each one cost time in the session that built this, and none is
visible from the code alone.

- **`collectstatic` is a prerequisite of `pytest`**, not only of a deployment.
  A fresh checkout fails most of the suite with "Missing staticfiles manifest
  entry" until it has run once.
- **GNU gettext is not on PATH** on the development machine. It ships with Git:
  `$env:PATH = "C:\Program Files\Git\usr\bin;$env:PATH"`.
- **`makemessages` on Windows can emit a malformed `#:` reference line** — a
  wrapped reference continues with a leading space instead of a second `#:`,
  and `msgfmt` then refuses the whole file with "keyword unknown". It happened
  once here. Symptom: `compilemessages` appears to succeed but no `.mo` appears.
  Check with `msgfmt --check -o /dev/null locale/de/LC_MESSAGES/django.po`.
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
