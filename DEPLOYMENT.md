# Deploying on the DS723+

The run-book. Follow it in order; §3 is the part that goes wrong.

> **None of this has been executed on the real NAS.** No OIDC round trip has
> completed against a real Synology SSO Server, and no part of §1–§5 has been
> done on this hardware. It is written from how DSM and these components work,
> not from a run here — so expect to correct it as you go, and please correct it
> *here* rather than only in your terminal history. OPEN-ITEMS.md §2 lists
> exactly what is and is not known to work.
>
> **The container itself is no longer a guess.** The image has now been built,
> started, and observed serving `/healthz`, the login redirect and its own
> static files, with migrations applied on start-up — and §4.3(b), the
> `docker load` path, was done end to end by deleting the image and restoring it
> from the release tarball. What remains untested is that container running on a
> *Synology*, behind *that* proxy, against *that* SSO server, and the GitHub
> Actions plumbing that produces the release in the first place.

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

Put `.env` beside the compose file — never inside a checkout; it is gitignored
and `.dockerignore`d for a reason. If you are deploying from a release, the
attachment is called `env.example` (a dotfile would be invisible in File Station
and is skipped by shell globs, which is why the release does not ship one);
rename it to `.env`. From a checkout, copy `.env.example`. Fill in at least:

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

### 4.3 Get the image and start

**Do not build on the NAS.** A DS723+ has two cores that are already running
DSM, Home Assistant and a backup; compiling a Python image on them takes minutes
it does not have, and it produces an artifact nobody can reproduce. Every tag
pushed to GitHub builds, tests and publishes exactly one image
(`.github/workflows/release.yml`) — take that one. §9 is how a release is cut.

Pick whichever of these fits; they install the same bytes.

**(a) From the registry.** The ordinary path, and the only one where
`docker compose pull` later does the right thing on its own.

```
cd /volume1/docker/kitchen
# Only if the package is private: a GitHub token with read:packages.
#   echo <token> | sudo docker login ghcr.io -u <github-username> --password-stdin
sudo docker compose pull
sudo docker compose up -d
```

**(b) From the release attachment.** No registry, no credentials — which is the
point: it still works when the package is private and this NAS has never been
given a token, and when GitHub is unreachable but you already downloaded the
file. Take `kitchen-<version>-linux-amd64.tar.gz`, `docker-compose.yml` and
`env.example` from the release page and put the first two in
`/volume1/docker/kitchen`:

```
cd /volume1/docker/kitchen
sha256sum -c SHA256SUMS               # optional, and it takes a second
gunzip -c kitchen-<version>-linux-amd64.tar.gz | sudo docker load
sudo docker compose up -d
```

`docker load` brings the image in under the exact tag the compose file names, so
`up -d` finds it locally and never reaches for the registry.

**(c) From a checkout, building here.** Still supported, still the right thing
on a laptop, and the fallback if the pipeline is broken and you need a fix
tonight. This is the *other* compose file — the one with a `build:` section:

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

## 7. Updating, and rolling back

Edit the one line in `docker-compose.yml` that names the version, then:

```
cd /volume1/docker/kitchen
sudo docker compose pull        # or: gunzip -c kitchen-<new>-linux-amd64.tar.gz | sudo docker load
sudo docker compose up -d
```

Migrations run from `deploy/entrypoint.sh` on start-up. The image is disposable;
`/data` is not.

**Confirm the update actually landed.** Open the app and look at the bottom of
the sidebar — the running version is printed there. This is not ceremony: a
container kept alive by `restart: unless-stopped`, a compose file edited in the
wrong folder and a browser holding a cached page all look exactly like a
successful update from the NAS's side, and the only place the truth shows is a
page served by the new code.

**Rolling back** is putting the previous version back in that same line and
running the same two commands. That is the whole reason the compose file pins a
version instead of `latest`: with `latest` there is nothing to edit, `up -d`
fetches the bad image again, and the way back is to remember which tag was good.

The one thing a roll-back does not undo is a **migration**. Going back to an
image older than a migration that has already run leaves the database ahead of
the code. Nothing here has needed a destructive migration yet; if one ever does,
take a copy of `/volume1/docker/kitchen/data` before updating — which is a
sentence worth reading twice before the first update that follows a schema
change.

---

## 8. Cutting a release

```
git tag v1.2.0
git push origin v1.2.0
```

That is the whole procedure. The tag starts `.github/workflows/release.yml`,
which:

1. runs the full test suite and the container smoke test (`ci.yml`, called as a
   reusable workflow, so a release can never be verified by a stale copy of the
   checks);
2. builds `linux/amd64` with the version baked in as `KITCHEN_VERSION` and as
   OCI labels;
3. **starts the image it just built** and checks it answers `/healthz` and
   reports the version it claims to be — because "the artifact we shipped was
   never started" is the failure the whole pipeline exists to prevent;
4. pushes `ghcr.io/christianh99/kitchenapp:<version>` and `:latest`;
5. attaches the image tarball, a `docker-compose.yml` pinned to that version,
   `env.example` and `SHA256SUMS` to the GitHub release, creating the release if
   the tag was pushed on its own and attaching to it if you drafted one first.

If an upload fails but the tag is fine, re-run it from the Actions tab —
**Release → Run workflow** — and give it the tag. It rebuilds and replaces the
assets rather than refusing because they already exist.

Nothing here needs a secret to be configured: the workflow uses the
`GITHUB_TOKEN` that Actions issues per run, with `packages: write` for the push
and `contents: write` for the release. There is no long-lived credential to
rotate, and nothing to leak.

**The package came out public**, because this repository is public and GitHub
gave the package the repository's visibility. Checked, not assumed: after
`docker logout ghcr.io`, `docker pull ghcr.io/christianh99/kitchenapp:0.1.0`
succeeds. So path (a) in §4.3 needs no credentials on the NAS at all.

If this repository is ever made private, the package follows and the NAS starts
getting a 403 from `docker compose pull` that reads like a wrong image name.
Then either change the package's visibility back (GitHub → your profile →
Packages → *kitchenapp* → Package settings), give the NAS a read-only token, or
use the release attachment — path (b), which needs neither and is why it is
there.

---

## 9. When something is wrong

| Symptom | Where to look |
|---|---|
| Redirect loop at sign-in, no error | `X-Forwarded-Proto` missing from the proxy rule (§2). This is the common one. |
| "Sign in with Synology" ends in a connection error | Hairpin DNS — the container cannot reach `sso.haeusslerr.de` (§3.3). |
| CSRF failure on every form | `DJANGO_CSRF_TRUSTED_ORIGINS` missing the `https://` scheme. |
| `docker compose pull` says denied / 403 | The GHCR package has gone private — it follows the repository's visibility (§8). Change it back, give the NAS a read-only token, or use the release attachment instead. |
| The app still shows the old version after an update | The sidebar is telling the truth and something else is not: check you edited the compose file in the folder you are running `up -d` from, and that `docker compose ps` shows a container created just now. |
| The release ran but the assets are missing | Re-run **Release → Run workflow** with the tag. Uploads use `--clobber`, so a re-run replaces a half-written asset instead of failing on it. |
| `DisallowedHost` on every request | `DJANGO_ALLOWED_HOSTS` does not include the hostname the proxy forwards. |
| Every page renders unstyled | `collectstatic` did not run — check the build log, not the run log. |
| Container restarts every ten seconds | Read `docker logs`; a missing `DJANGO_SECRET_KEY` raises at import with a sentence saying so. |
| Signature error during the token exchange | Try `OIDC_RP_SIGN_ALGO=HS256`; older DSM builds sign with the client secret rather than RS256, and then no JWKS endpoint is needed. |
| Nobody can sign in via SSO after a DSM update | Re-read the discovery document (§3.1) — the endpoints may have moved. The local login (`?local=1`) still works. |

Logs: `sudo docker compose -f deploy/docker-compose.yml logs --tail=200`, and a
rotating copy at `/volume1/docker/kitchen/data/logs/kitchenapp.log` — which
survives the container being recreated, unlike stdout.
