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

**This section is done in two browser tabs and no text editor.** One half is a
page in DSM, because Synology's SSO Server has no supported way to create an
OIDC application except its own GUI. The other half is a page in *this* app —
**Anmeldung** in the sidebar, superuser only — which is where the client ID, the
secret, the endpoints and the on/off switch live. Nothing here needs `.env` or a
container restart.

> The `OIDC_*` values in `.env` still work and are what the app reads until that
> page has been saved once. After that the stored configuration is the whole
> truth and the environment is ignored. Opening the page on a system configured
> through `.env` shows those values already filled in, so migrating is: open,
> check, save.

### 3.1 Create the application in DSM

SSO Server → **OIDC** → Applications → Add:

| | |
|---|---|
| Application name | Kitchen |
| Redirect URI | `https://kitchen.haeusslerr.de/oidc/callback/` |

The redirect URI must match **exactly**, trailing slash included. It is fixed by
`config/urls.py`, where `mozilla_django_oidc.urls` is included un-namespaced —
see the comment in `apps/accounts/urls.py` for why it cannot live under
`/accounts/`. The app's own SSO page prints the exact string to paste, built
from the address you reached it on; copy it from there rather than typing it.

DSM then shows a **client ID** and a **client secret**. Those two go into the
app's page, not into a file.

### 3.2 Fill the app's page in, in the order that cannot lock you out

Sidebar → **Anmeldung**. With the switch at the top still **off**:

1. Enter the SSO server's address (`https://sso.haeusslerr.de`).
2. Press **Endpunkte vom Server lesen**. It fetches the discovery document
   *from inside the container* and fills the four endpoints in — the same
   request as the old instruction to run
   `curl -s .../webman/sso/.well-known/openid-configuration` by hand, with the
   same caveat: **do not trust any endpoint URL written down anywhere**,
   including this app's own derived defaults, because Synology has moved them
   between DSM versions. If the fetch fails, the SSO Server package's
   **Service → OIDC** tab shows them and every field can be typed in.
3. Enter the client ID and secret from §3.1.
4. Save, then press **Verbindung prüfen** — §3.3 is the failure that button
   exists for.
5. Only now tick **„Mit Synology anmelden“ anbieten** and save again.

The switch refuses to go on while the configuration could not complete a login
(no secret, no client ID, no resolvable endpoints, or RS256 with no JWKS
address), because a sign-in button leading to the provider's error page reads as
the provider being broken rather than as the app not being set up.

The local password form stays reachable throughout, at
`https://kitchen.haeusslerr.de/accounts/login/?local=1`.

### 3.2.1 Where the secret ends up, and what that costs

In the database, encrypted with a key derived from `DJANGO_SECRET_KEY` — which
stays in the environment. So a copy of `db.sqlite3` on its own does not yield
it, which matters because §6 backs that file up nightly to another share. A
backup taken *together with* the environment does yield it, and anybody who can
`docker exec` into the container has both and always did; encryption at rest is
about the copies, not the original.

Two consequences worth knowing before they surprise you:

- **Change `DJANGO_SECRET_KEY` and the secret becomes unreadable.** The app says
  so on the page and refuses to offer the sign-in button rather than failing
  mid-login. Re-enter the secret; nothing else is lost.
- **The secret is never sent back to a browser.** The field on the page is
  write-only: blank means "keep what is stored", and removing it takes a
  separate checkbox.

### 3.3 The trap that is not obvious: hairpin DNS

The browser reaches `sso.haeusslerr.de` from outside and it works. The
**container** then has to reach the same hostname from *inside* the NAS, for the
back-channel token exchange — and if your router does not do NAT hairpinning,
that request leaves the house, comes back to the WAN address, and is dropped.

The symptom is a login that gets as far as the Synology page, accepts the
password, returns to the app, and then fails with a connection error in the
container log. Nothing about it points at DNS.

**Press „Verbindung prüfen“ on the SSO page.** That is this check, run from the
right machine, and it is why the button exists: it asks the container — not your
laptop — whether it can reach each configured address, and names the three
causes that actually happen (name not resolvable, connection refused, timeout).
Over SSH the same question is:

```
docker exec kitchen curl -sI https://sso.haeusslerr.de/ | head -1
```

If it hangs or fails, add a host alias so the container resolves the name to the
NAS's LAN address — in the compose file:

```yaml
    extra_hosts:
      - "sso.haeusslerr.de:192.168.1.10"     # the NAS's LAN address
```

Only then does certificate verification still work, because the certificate is
for that hostname and the hostname is what is being requested. Do not "solve"
this by turning verification off — the tick box on the SSO page exists for a
private CA, not for making an error go away.

---

## 4. The container

### 4.1 Prepare the data folder

File Station → create `docker/kitchen/data`. Everything the app writes lives
here — the database, the photographs, the logs — so that an image update
replaces the code and leaves the collection alone.

The container runs as uid 1000, so that folder has to be writable by uid 1000.
Over SSH:

```
sudo chown -R 1000:1000 /volume1/docker/kitchen/data
```

**On this NAS that is not enough, and the way it fails is the problem.** Check
before believing it:

```
ls -land /volume1/docker/kitchen/data
```

A `+` on the permission string (`drwxrwxrwx+`) means the share has a Synology
ACL, and the ACL — not the POSIX bits — is what is enforced. `chown` then
reports success, `ls` shows the new owner, and the container still cannot write.
`/volume1/docker` is ACL-enabled as DSM creates it:

```
$ sudo synoacltool -get /volume1/docker/kitchen/data
[0] group:administrators:allow:rwxpdDaARWc--:fd--
[1] user:ContainerManager:allow:rwxpdDaARWc--:fd--
[2] everyone::allow:r-x---a-R-c--:fd--
```

There is **no owner entry**, so changing the owner grants nothing at all: uid
1000 has no DSM identity and matches only `everyone`, which is `r-x`. The second
tell is that the host reports mode 777 while the container sees the same
directory as 555 — nothing but the ACL layer produces that discrepancy.

What this looks like if you skip the check: the container comes up, prints
`→ applying migrations`, and dies about three seconds later with
`sqlite3.OperationalError: unable to open database file` — and because
`restart: unless-stopped` backs off between attempts, it presents as a crash
roughly a minute in rather than immediately. §9 has the symptom row.

**The fix used here** — run the container as an identity the ACL already grants,
rather than trying to make the ACL grant uid 1000. The compose file already
carries the line; what it needs is the two numbers, and they go in **`.env`**,
beside it:

```
KITCHEN_UID=1026
KITCHEN_GID=101
```

1026 is the DSM user that owns the folder (`Christian`); **101 is
`administrators`, and that is the half that matters** — gid 100 (`users`)
matches only the `everyone` entry. Confirm both against this machine with
`grep -E '^(administrators|users):' /etc/group`, then put the ownership back to
match:

```
sudo chown -R 1026:100 /volume1/docker/kitchen/data
```

**Check the substitution before starting anything.** This is the one line here
whose failure is silent — unset variables fall back to `1000:1000`, which is the
error above:

```
sudo docker compose config | grep user:
```

The reason they live in `.env` and not in the compose file is that **the compose
file is replaced wholesale by the next release you download and `.env` is not**.
Until v0.2.2 this was a hand-edited `user: "1026:101"` line with an instruction
to carry it across every update — a step that works right up until the once it
is forgotten, and forgetting it is this crash loop on a folder that by then has
the household's recipes in it. Note that Compose substitutes `${…}` from the
`.env` in the same directory as the compose file, which is a *different*
mechanism from the `env_file:` key in that file; both happen to read the same
file here, which is why one entry serves both.

Test any of this without waiting on the restart loop — a throwaway container
with the same mount and the same user answers in a second:

```
sudo docker run --rm -u 1026:101 \
  -v /volume1/docker/kitchen/data:/data \
  --entrypoint sh ghcr.io/christianh99/kitchenapp:<version> \
  -c 'id; ls -land /data; touch /data/.wtest && echo WRITABLE && rm /data/.wtest || echo NOT-WRITABLE'
```

**Three alternatives, and why not.** *Widening the ACL's `everyone` entry to
`rwx`* with `synoacltool -replace` and leaving the container at uid 1000 keeps
`.env` stock, at the cost of giving every DSM account write on the household's
recipes and photographs — which they can already read. *Running as root*, which
is what Immich and most self-hosted apps do (no `user:`, no `USER` in the
Dockerfile), sidesteps the ACL entirely because root bypasses the check — but
this app accepts photograph uploads, and it is close to a one-way door: `/data`
fills with root-owned files and going back means chowning a database you have
come to care about. *Chown-then-drop-privileges* in the entrypoint, the
`gosu` pattern, cannot work here at all — the `chown` is the exact call the ACL
makes a no-op, so dropping to uid 1000 afterwards lands straight back here.

The gid only affects file permissions on this one bind mount. The container has
no DSM API and nothing else mounted, so it is not DSM administrator in any sense
that reaches beyond `/data`.

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

   **Leave the e-mail address blank.** `createsuperuser` accepts an empty one,
   and here that is the safety rather than laziness. A token whose address
   matches exactly one local account is *linked* to it automatically — one
   person, both doors, which is the feature — and the account this step creates
   is the one that can do everything, including turning SSO off again. Giving
   it an address means whoever can set that address on a DSM account can sign
   in as it. An empty address never matches, because the link needs a non-empty
   address on both sides. Use a real one only for an account you would be
   content to reach that way, and see the Security section of CLAUDE.md for the
   other three conditions the link has to satisfy.

2. Open `https://kitchen.haeusslerr.de/`, sign in locally, confirm the app works
   end to end — add a recipe, upload a photograph.
3. Now do §3 — the DSM application, then the app's **Anmeldung** page. No
   restart: the switch on that page is what turns SSO on, and it takes effect
   on the next request.
4. Sign out, then use **Mit Synology anmelden**. If it fails, the local login is
   still there at `https://kitchen.haeusslerr.de/accounts/login/?local=1` — which
   is the whole reason it exists.

Optionally, once the DSM groups are settled, on the same page: fill in
**Erlaubte Gruppen** so only household members may sign in, and
**Administrationsgruppe** for the right to manage people. Check the
**Gruppen-Claim** against a real token first — some DSM builds send no group
claim at all, and the app refuses rather than falling open when a configured
group requirement cannot be checked.

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

**Take a copy of `/data` first when the release carries a migration**, and
assume it does unless you have checked. This is thirty seconds and it is the
only undo there is:

```
cd /volume1/docker/kitchen
sudo docker compose stop                        # so the -wal file is folded in
sudo cp -a data "data.before-<new version>"
```

Two reasons it is not ceremony. A roll-back puts the *code* back and cannot put
the *schema* back (see the end of this section), so an image older than a
migration that has already run leaves the database ahead of the code with
nothing to do about it. And a migration that carries a **data step** — one that
fills a new column from an old one rather than only adding it — is the kind
whose failure is silent: nothing crashes, the app comes up, and something is
quietly wrong on a page nobody looks at that day. `recipes/0007_recipe_owner` is
this app's first of those; it fills `owner` from `created_by`, and without it
every existing recipe becomes editable by staff alone, which presents as "Edit
disappeared from my own recipes" rather than as anything to do with an update.

Then edit the one line in `docker-compose.yml` that names the version — or drop
in the one attached to the new release, which from v0.2.2 is safe to do because
the machine-specific `KITCHEN_UID`/`KITCHEN_GID` live in `.env` and not in that
file (§4.1). If you are coming from an older compose file that had
`user: "1026:101"` written into it by hand, move those two numbers into `.env`
now, and confirm with `sudo docker compose config | grep user:`. Then:

```
sudo docker compose pull        # or: gunzip -c kitchen-<new>-linux-amd64.tar.gz | sudo docker load
sudo docker compose up -d
```

Migrations run from `deploy/entrypoint.sh` on start-up. The image is disposable;
`/data` is not.

Once the app is up and you have looked at a page that exercises whatever
changed, the copy can go:

```
sudo rm -rf "data.before-<new version>"
```

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
| Restart loop, `unable to open database file` after `→ applying migrations` | `/data` is not writable by the container's user. On an ACL-enabled share `chown` succeeds and changes nothing — §4.1, including the `+` tell. First check `docker compose config \| grep user:`: if it reads `1000:1000`, `KITCHEN_UID`/`KITCHEN_GID` are not reaching the compose file from `.env`. |
| No way to sign in on a fresh install | There are no default credentials. `docker exec -it kitchen python manage.py createsuperuser` — §5. |
| Signature error during the token exchange | Try `OIDC_RP_SIGN_ALGO=HS256`; older DSM builds sign with the client secret rather than RS256, and then no JWKS endpoint is needed. |
| Nobody can sign in via SSO after a DSM update | Re-read the discovery document (§3.1) — the endpoints may have moved. The local login (`?local=1`) still works. |

Logs: `sudo docker compose -f deploy/docker-compose.yml logs --tail=200`, and a
rotating copy at `/volume1/docker/kitchen/data/logs/kitchenapp.log` — which
survives the container being recreated, unlike stdout.
