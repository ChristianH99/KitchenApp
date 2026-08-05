# The original briefing, and where this repository departs from it

This app was built from a written briefing (*"Projektbriefing: Rezept-App auf
Synology NAS mit Synology SSO"*, 2026-08-05). The author explicitly asked not to
take it as settled and to be told about improvements.

Nine changes were made. They are recorded here rather than only in the commit
message, because the briefing is the document somebody will re-read in a year to
ask "why doesn't it do X?" — and for four of the nine the answer is "it was
asked for, and here is why not".

---

## 1. What the briefing asked for

Faithfully, in its own terms.

**Goal.** A self-hosted recipe app on a Synology DS723+ (DSM 7.3.2-86009 U4),
reachable internally and externally over HTTPS on its own domain, using Synology
SSO for authentication, frugal with CPU and RAM, and extensible later.

**Domain** `haeusslerr.de`, with subdomains `nas.` (DSM), `sso.` (Synology SSO
Server), `recipes.` (this app) and `ha.` (Home Assistant); later `grafana.`,
`immich.`.

**Network.** WAN → router → Synology Reverse Proxy → service. Port forwards
TCP 443 (and TCP 80 for Let's Encrypt).

**Authentication.** The app must not manage passwords. Synology SSO manages
users; Django authenticates over **OpenID Connect**; passwords stay in the SSO
server. Explicitly *not* preferred: direct DSM login calls, an own password
database, own authentication. Django receives `sub`, name, e-mail, optionally
groups/roles. An OIDC client is registered in the SSO server (name "Recipe App",
client id `recipe-app`) with the Django callback as redirect URI.

**Stack.** Django + Python + PostgreSQL or SQLite "depending on size";
`django-allauth` + an OpenID Connect provider, or `mozilla-django-oidc` as the
alternative.

**Deployment.** Synology Container Manager. Containers: Django + Gunicorn;
PostgreSQL; optionally Redis later.

**Resource optimisation.** No unnecessary background services, no heavy
front-end, server-side rendering preferred: Django templates + HTMX rather than
a separate SPA (React/Vue/Angular) — less RAM, fewer containers, less
maintenance.

**Security.** `DEBUG = False` in production. Secrets (`SECRET_KEY`,
`DATABASE_PASSWORD`, `OIDC_CLIENT_SECRET`) out of the repository, via environment
variables, Docker secrets or a `.env` outside it. `ALLOWED_HOSTS` and
`CSRF_TRUSTED_ORIGINS` set to the live host. Let's Encrypt certificates via
Synology for all four hostnames.

**MVP data model.**

```
Recipe
------
id, title, description, ingredients, instructions,
preparation_time, cooking_time, created_by, created_at, updated_at
```

Later: categories, images, favourites, ratings, shopping lists, family sharing.

**Development priorities.** 1. Django skeleton. 2. Docker deployment.
3. Synology SSO over OIDC. 4. User management via SSO. 5. Recipe data model.
6. UI. 7. Backup/monitoring.

---

## 2. What was changed, and why

| # | Briefing | Built | Why | Status |
|---|---|---|---|---|
| 1 | PostgreSQL container (or SQLite "depending on size") | **SQLite**, WAL mode, one file under `/data` | A household collection is a few thousand rows read by four people. Postgres costs a second container and its RAM on a NAS whose stated brief is frugality. Moving later is a `dumpdata`/`loaddata`, not a rewrite. | Done |
| 2 | Redis "later" | **Not present** | Buys nothing until there are background jobs. Nothing here has any. | Done |
| 3 | `nas.haeusslerr.de` reachable from the internet | **LAN/VPN only**; only `kitchen.`, `sso.` and `ha.` forwarded | DSM is the one host that, if it falls, takes the recipes, the photographs, Home Assistant and every backup with it — and it has a long history of remotely exploitable bugs precisely because it is what everybody exposes. | Documented, DEPLOYMENT.md §1. **The user has not yet acted on this**; it is a recommendation about their router. |
| 4 | (absent) | **`SECURE_PROXY_SSL_HEADER` + `USE_X_FORWARDED_HOST`**, and matching custom headers on the proxy rule | The briefing's proxy design is right and this is the setting it needs. Without it Django sees plain HTTP, builds the OIDC `redirect_uri` as `http://…`, and the SSO server rejects it as an unregistered callback. The symptom is a login loop with no error anywhere — it looks like a wrong client secret. **The single most likely thing to go wrong in this deployment.** | Done in code; DEPLOYMENT.md §2 |
| 5 | Port 80 forwarded for Let's Encrypt | **Optional**: use DNS-01 through DSM if the registrar is supported, and forward nothing on 80 | One fewer open port for no loss. Depends on the DNS provider, so it is offered rather than assumed. | Documented, DEPLOYMENT.md §1 |
| 6 | "The app must not manage passwords" | **One local superuser account**, offered second and folded away behind a disclosure | An OIDC-only app is unreachable — *including its administration* — whenever the SSO server, its certificate or the container's DNS is unhappy, which is exactly when somebody needs to get in and fix it. This is a deliberate departure from an explicit instruction, and it is the one most worth re-reading: if the household would rather accept the lockout risk, delete `apps/accounts/views.login_view`'s local branch and set `OIDC_ENABLED` permanently on. | Done |
| 7 | `django-allauth` + OIDC provider, or `mozilla-django-oidc` | **`mozilla-django-oidc`** | One identity provider, one dependency, no social-account tables. allauth earns its weight only with a second provider. (The briefing offered both; this records which was taken.) | Done |
| 8 | `Recipe.ingredients` as a single field | **`RecipeIngredient` rows** (amount, unit, name, note, position) plus **`Recipe.servings`** | The decision the rest of the app follows from. A text field is a third of the work and forecloses scaling servings, shopping lists, and "what can I make with fennel" — none of which can be retrofitted onto free text without re-typing the collection by hand, which is the migration nobody ever does. `servings` says what the amounts are *for*; without it a number is just a number and scaling has nothing to divide by. | Done |
| 9 | Images "later" | **Built in from the start** (`Recipe.image`, verified/resized/renamed on upload) | Adding media to a running container means a new volume and a migration on live data. A recipe app without pictures also does not get used. | Done |

### Smaller adjustments

- **Name and subdomain.** The user asked for a more general name than "recipe
  app": the project is `kitchen`, `apps/recipes/` is its first module, and the
  suggested subdomain is `kitchen.haeusslerr.de` rather than `recipes.`. A URL
  is hard to change once it is bookmarked and pinned in an OIDC client. The
  briefing's `recipes.` is fine if preferred — change `DJANGO_ALLOWED_HOSTS`,
  `DJANGO_CSRF_TRUSTED_ORIGINS` and the registered redirect URI together.
- **OIDC client id** is `kitchen-app` in `.env.example`, not the briefing's
  `recipe-app`, for the same reason. Either works; it must match what is
  registered in the SSO server.
- **Callback URL** is `/oidc/callback/`, not the briefing's example
  `/accounts/oidc/callback/`. It cannot live under `/accounts/`:
  `mozilla_django_oidc` reverses `oidc_authentication_callback` by that bare
  name from inside its own views, so including it in a namespaced URLconf breaks
  the callback with a `NoReverseMatch` mid-login. See `apps/accounts/urls.py`.
- **Groups are optional.** The briefing lists them as "optional" claims and that
  turned out to be load-bearing: Synology's SSO Server has shipped DSM versions
  that send no group claim at all. Nothing beyond `sub` is required, and the
  group check is opt-in — but it fails *closed*: with `OIDC_ALLOWED_GROUPS` set
  and the claim absent, the login is refused rather than waved through.
- **Backup/monitoring** (priority 7) is answered by "there is nothing to
  configure": `/data` is one SQLite file plus `media/`, and Hyper Backup already
  covers that share. `/healthz` is the monitoring surface.

## 3. Asked for and not built

Only one, and it is not a rejection:

- **HTMX.** The briefing named it as the way to avoid an SPA, and that reasoning
  is accepted in full — this app is server-rendered Django templates with one
  small vanilla script per page. HTMX itself is simply not used yet, because
  nothing needs a partial page update: the servings scaler is client-side
  arithmetic and everything else is a form post. It is a dependency and a second
  rendering path until there is a live-filtering list or an inline edit, at
  which point it earns its place. See OPEN-ITEMS.md §3.
