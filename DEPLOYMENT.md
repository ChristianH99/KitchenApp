# Deploying on the DS723+

The run-book. Follow it in order; §3 is the part that goes wrong.

> **None of this has been executed yet.** The image has never been built,
> gunicorn has never served this app, and no OIDC round trip has completed
> against a real Synology SSO Server. It is written from how DSM and these
> components work, not from a run on this NAS — so expect to correct it as you
> go, and please correct it *here* rather than only in your terminal history.
> OPEN-ITEMS.md §2 lists exactly what is and is not known to work.

Target shape:

```
Internet ──443──▶ Router ──443──▶ Synology Reverse Proxy ──▶ 127.0.0.1:8000
                                          │                    (this container)
                                          └──▶ Synology SSO Server
```

---

## 1. Before anything else: what to expose

The briefing this was built from puts DSM itself on the internet at
`nas.haeusslerr.de`. **Don't.** DSM is the one host on the domain that, if it
falls, takes the recipes, the photos, Home Assistant and every backup with it,
and it has a long history of remotely exploitable bugs precisely because it is
the thing everybody exposes.

Recommended split:

| Host | Reachable from | How |
|---|---|---|
| `kitchen.haeusslerr.de` | internet | reverse proxy, port 443 |
| `sso.haeusslerr.de` | internet | reverse proxy, port 443 — it has to be, or SSO cannot complete from outside |
| `ha.haeusslerr.de` | internet | reverse proxy, port 443 |
| `nas.haeusslerr.de` | LAN + VPN only | no port forward; reach it over Tailscale or the router's VPN |

Port forwards on the router: **TCP 443 only**. Port 80 is needed solely for
Let's Encrypt's HTTP-01 challenge — if your DNS provider is one DSM supports for
DNS-01 (Control Panel → Security → Certificate → Add → *Let's Encrypt* → DNS),
use that instead and forward nothing on 80 at all.

---

## 2. The reverse proxy rule

Control Panel → Login Portal → Advanced → **Reverse Proxy** → Create.

| | |
|---|---|
| Source protocol | HTTPS |
| Source hostname | `kitchen.haeusslerr.de` |
| Source port | 443 |
| Enable HSTS | leave **off** — Django sends it (`DJANGO_HSTS_SECONDS`), and two sources for one header is one too many |
| Destination protocol | HTTP |
| Destination hostname | `localhost` |
| Destination port | `8000` |

Then the **Custom Header** tab. Add these two (the "Create → WebSocket" preset
does not include them):

| Header name | Value |
|---|---|
| `X-Forwarded-Proto` | `$scheme` |
| `Host` | `$host` |

**This is the step that breaks the login.** Without `X-Forwarded-Proto`, Django
sees plain HTTP on every request and builds the OIDC `redirect_uri` as
`http://kitchen.haeusslerr.de/oidc/callback/`. The SSO server compares that
against the registered callback, finds it does not match, and refuses. What you
see is a redirect loop between the app and the SSO server with no error message
anywhere — which looks like a wrong client secret and is not.

The matching Django side is `DJANGO_TRUST_PROXY_HEADERS=True`, which is the
default whenever `DEBUG` is off.

Certificate: Control Panel → Security → Certificate → add a Let's Encrypt
certificate covering `kitchen.haeusslerr.de` (and the other hosts), then
**Settings** → assign it to the reverse-proxy service.

---

## 3. The Synology SSO client

### 3.1 Read the discovery document first

Do not trust any endpoint URL written down anywhere, including in this app's
`settings.py`. Synology has moved these between DSM versions. Fetch them:

```
curl -s https://sso.haeusslerr.de/webman/sso/.well-known/openid-configuration | python -m json.tool
```

Write down `authorization_endpoint`, `token_endpoint`, `userinfo_endpoint` and
`jwks_uri`. If that URL 404s, the SSO Server package's own **Service → OIDC**
tab shows them. Put whatever you find into `.env`
(`OIDC_OP_AUTHORIZATION_ENDPOINT` and friends); the defaults derived from
`OIDC_OP_BASE` are a starting guess, not a promise.

### 3.2 Create the application

SSO Server → **OIDC** → Applications → Add:

| | |
|---|---|
| Application name | Kitchen |
| Redirect URI | `https://kitchen.haeusslerr.de/oidc/callback/` |

The redirect URI must match **exactly**, trailing slash included. It is fixed by
`config/urls.py`, where `mozilla_django_oidc.urls` is included un-namespaced —
see the comment in `apps/accounts/urls.py` for why it cannot live under
`/accounts/`.

Copy the client ID and secret into `.env`.

### 3.3 The trap that is not obvious: hairpin DNS

The browser reaches `sso.haeusslerr.de` from outside and it works. The
**container** then has to reach the same hostname from *inside* the NAS, for the
back-channel token exchange — and if your router does not do NAT hairpinning,
that request leaves the house, comes back to the WAN address, and is dropped.

The symptom is a login that gets as far as the Synology page, accepts the
password, returns to the app, and then fails with a connection error in the
container log. Nothing about it points at DNS.

Check it after the container is up:

```
docker exec kitchen curl -sI https://sso.haeusslerr.de/ | head -1
```

If that hangs or fails, add a host alias so the container resolves the name to
the NAS's LAN address — in `deploy/docker-compose.yml`:

```yaml
    extra_hosts:
      - "sso.haeusslerr.de:192.168.1.10"     # the NAS's LAN address
```

Only then does `OIDC_VERIFY_SSL=True` still work, because the certificate is for
that hostname and the hostname is what is being requested. Do not "solve" this
by turning verification off.

---

## 4. The container

### 4.1 Prepare the data folder

File Station → create `docker/kitchen/data`. Then over SSH:

```
sudo chown -R 1000:1000 /volume1/docker/kitchen/data
```

1000:1000 is the container's user. Everything the app writes lives here — the
database, the photographs, the logs — so that an image update replaces the code
and leaves the collection alone.

### 4.2 Configure

Copy `.env.example` to `.env` beside the repository (never inside it; it is
gitignored and `.dockerignore`d for a reason) and fill in at least:

```
DJANGO_DEBUG=False
DJANGO_SECRET_KEY=<generate one>
DJANGO_ALLOWED_HOSTS=kitchen.haeusslerr.de
DJANGO_CSRF_TRUSTED_ORIGINS=https://kitchen.haeusslerr.de
KITCHEN_DATA_DIR=/data
DJANGO_TIME_ZONE=Europe/Berlin
OIDC_ENABLED=False          # switch on after the first sign-in, see §5
```

Generate the key with:

```
python -c "from django.core.management.utils import get_random_secret_key as g; print(g())"
```

### 4.3 Build and start

Container Manager → Project → Create → point it at the checkout and
`deploy/docker-compose.yml`. Or over SSH:

```
cd /volume1/docker/kitchen/KitchenApp
sudo docker compose -f deploy/docker-compose.yml up -d --build
```

Check it came up:

```
sudo docker compose -f deploy/docker-compose.yml logs -f
curl -s http://127.0.0.1:8000/healthz        # → ok
```

`healthz` runs one `SELECT 1`, so "ok" means the process is listening *and* its
database is there — which a bare "ok" from a static view would not.

### 4.4 `check --deploy`, and the two warnings that stay

```
sudo docker exec kitchen python manage.py check --deploy
```

It reports two warnings that are **correct here** and must not be "fixed":

- **`security.W005`** — `SECURE_HSTS_INCLUDE_SUBDOMAINS` is off. It would apply
  to every subdomain of `haeusslerr.de`, including `nas.` and `ha.`, which this
  app has no business pinning to HTTPS on their behalf. Turn it on
  (`DJANGO_HSTS_INCLUDE_SUBDOMAINS=True`) only if you own that decision for the
  whole domain.
- **`security.W008`** — `SECURE_SSL_REDIRECT` is not True. The proxy is the only
  way in and already speaks HTTPS. A redirect issued by this process would fire
  only for the container's own health check on `127.0.0.1`, turning a healthy
  server into a failing probe.

`security.W021` (HSTS preload) is silenced in `settings.py` for the same reason
as W005. Anything else it reports is real.

---

## 5. First sign-in

Deliberately in this order, because it is the order that cannot lock you out.

1. With `OIDC_ENABLED=False`, create the fallback administrator:

   ```
   sudo docker exec -it kitchen python manage.py createsuperuser
   ```

2. Open `https://kitchen.haeusslerr.de/`, sign in locally, confirm the app works
   end to end — add a recipe, upload a photograph.
3. Now set `OIDC_ENABLED=True` (and the client ID/secret) in `.env` and restart:

   ```
   sudo docker compose -f deploy/docker-compose.yml up -d
   ```

4. Sign out, then use **Sign in with Synology**. If it fails, the local login is
   still there at `https://kitchen.haeusslerr.de/accounts/login/?local=1` — which
   is the whole reason it exists.

Optionally, once the DSM groups are settled: set `OIDC_ALLOWED_GROUPS` so only
household members may sign in, and `OIDC_STAFF_GROUP` for Django admin access.
Verify `OIDC_GROUPS_CLAIM` against a real token first — some DSM builds send no
group claim at all, and the app refuses rather than falling open when a
configured group requirement cannot be checked.

---

## 6. Backups

There is nothing to configure in the app. The whole collection is
`/volume1/docker/kitchen/data` — one SQLite file plus `media/`. Point **Hyper
Backup** at that folder.

One caveat worth knowing: SQLite in WAL mode keeps recent writes in a `-wal`
file beside the database, so a copy taken while somebody is saving a recipe can
be short. For a household app the window is milliseconds a day and a nightly
backup will never see it. If you want to be strict about it, stop the container
for the backup window, or take the copy with SQLite's own online-backup API
rather than as a file copy.

---

## 7. Updating

```
cd /volume1/docker/kitchen/KitchenApp
git pull
sudo docker compose -f deploy/docker-compose.yml up -d --build
```

Migrations run from `deploy/entrypoint.sh` on start-up. The image is disposable;
`/data` is not.

---

## 8. When something is wrong

| Symptom | Where to look |
|---|---|
| Redirect loop at sign-in, no error | `X-Forwarded-Proto` missing from the proxy rule (§2). This is the common one. |
| "Sign in with Synology" ends in a connection error | Hairpin DNS — the container cannot reach `sso.haeusslerr.de` (§3.3). |
| CSRF failure on every form | `DJANGO_CSRF_TRUSTED_ORIGINS` missing the `https://` scheme. |
| `DisallowedHost` on every request | `DJANGO_ALLOWED_HOSTS` does not include the hostname the proxy forwards. |
| Every page renders unstyled | `collectstatic` did not run — check the build log, not the run log. |
| Container restarts every ten seconds | Read `docker logs`; a missing `DJANGO_SECRET_KEY` raises at import with a sentence saying so. |
| Signature error during the token exchange | Try `OIDC_RP_SIGN_ALGO=HS256`; older DSM builds sign with the client secret rather than RS256, and then no JWKS endpoint is needed. |
| Nobody can sign in via SSO after a DSM update | Re-read the discovery document (§3.1) — the endpoints may have moved. The local login (`?local=1`) still works. |

Logs: `sudo docker compose -f deploy/docker-compose.yml logs --tail=200`, and a
rotating copy at `/volume1/docker/kitchen/data/logs/kitchenapp.log` — which
survives the container being recreated, unlike stdout.
