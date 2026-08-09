# State of the work

Written 2026-08-05 at the end of the session that created the repository,
updated the same day by the session that added the diagram, the cooking view and
the account pages, again by the one that turned the diagram editor into a
drag-and-drop canvas, and on 2026-08-06 by the session that added the pantry —
the ingredient catalogue, the closed unit set, the what-can-I-cook matching, the
Steps/Diagram views, the completeness rules and the drag-to-resize tiles, and on
2026-08-08 by the session that fixed five things the household reported: the
silent Play button on the settings page, a step timer that could only be whole
minutes, an alarm that chimed once and stopped, a step with no ingredients that
could not be pulled down over any, and a deleted step taking everything above it
out of the recipe — and then, the same day, three more: adding a step to the
right of one sending that step to the bottom of the list, and the timer now
following you off the cooking page as a card in the corner that rings wherever
you are. It exists so that somebody — or
some agent — picking this up cold knows three things the code cannot tell them:
**what has actually been run**, what was left out on purpose, and what is worth
doing next.

Keep it current. A status document that is six weeks stale is worse than none,
because it is believed.

---

## 1. What has been verified, and how

Everything below was observed working, not merely written.

| | How it was checked |
|---|---|
| The whole test suite | `uv run pytest` — **499 passed**, ~160 s |
| Every page renders | Driven in Chrome against `runserver`: home, list, detail, form, tags, login, cooking view, People |
| Adding a recipe end to end | Typed into the real form in the browser; ingredients, tags and slug all correct on save |
| Blank formset rows dropped | Same submission — 5 rendered rows, 2 filled, 2 ingredients saved |
| Tags reused, not duplicated | 8 tags before, 9 after a submission naming two existing ones and one new |
| Servings scaling | Zwetschgenkuchen 12 → 18 in the browser: 1,5 kg → 2,25 kg, "umgerechnet" note appears — **and the diagram cells and the substitute follow in step**, read back from the DOM |
| The diagram's geometry | Read the rendered `<table>` back out of the page cell by cell: the full-width "Ofen vorheizen" row, the four ingredients merging into "Hefeteig ansetzen", the rowspans and the empty filler cells all as in the reference picture |
| A branching diagram | Kartoffelsalat: two arms (potatoes, dressing) meeting at "übergießen", the shallow arm spanning the columns between |
| The diagram survives an edit | Opened the real edit form, pressed Save with nothing changed, and the diagram came back identical — the hidden index fields had been primed from the saved relations |
| **The drag-and-drop canvas** | Driven in Chrome on the real edit form. A line dragged from one step into another, and out to the tray; a step dropped onto another step, which put it in a column of its own and stretched its cell across the columns between; the arrow keys doing each of those from the handle, with the live region announcing it in German and the focus staying on the handle it moved |
| The canvas agrees with the rendered page | Read the editor's grid placements out of the DOM and the saved `<table>`'s `rowspan`/`colspan` out of the recipe page: same blocks, same columns, same spans. This is the check that matters, because the layout now exists in two languages |
| The arrangement round-trips | Moved the "Ofen vorheizen" band below the whole main block, took a line out of its step, saved, and read the database back: `position` 4 with no parent, the line unassigned. Re-opening the edit page laid out identically — the ordering is a fixed point, not something that drifts one place per save |
| Adding into a step | "+ Zutat hierher" on a step card mints a row already assigned to it and drops it in the right cell |
| **The Steps view of the editor** | Driven in Chrome on the real new-recipe form: the switch marks itself, the cards move into a numbered list, and each one grows a "fließt ein in" / "verwendet in" select built from the other rows of the page |
| **The Brot case, end to end** | The shape the household could not build. "+ Schritt danach" on step 1 minted "verkneten" and set step 1's `parent_index` to it; a second arm was added and pointed at the same step through the select; the list then numbered them 1, 2, 3 with "Nimmt auf, was herauskommt aus: Vorteig ansetzen, Hefe ansetzen" on the third. The same shape saved through the server is pinned in `apps/recipes/tests.py` and lays out three columns wide |
| **The ingredient autosuggest** | Typed "Milch" into a real line: the popup offered the catalogue row, picking it filled the unit with `ml` and wrote the catalogue id into the hidden field |
| **The completeness rules refuse a real page** | Pressed Save on a form with a named line and no amount: the page came back unsaved with "How much Milch? Give an amount, or tick 'no fixed amount'…" on the card — and the banner above it, which is what the `total_error_count` check in the template is for |
| **The pantry, through its own form** | Five things added through the real "Etwas hinzufügen" control, including one with no amount; grouped by category on reload, Salz showing "etwas" |
| **What-can-I-cook** | `/recipes/?have=nearly` narrowed the list to one recipe, carrying a "2 Dinge fehlen" pill — the matching, the filter and the card badge in one pass |
| **A general step over only part of the width** | The Brot case again. "Step 4" detached with the "geht ein in" select, then its band's left end pulled in twice with the new arrows: the editor drew it over columns 3–4, the save round-tripped `span_from`/`span_to` as 3 and 4, and the recipe page rendered `filler[cs=2]` followed by the band at `cs=2` — sitting over Step 2 and Step 3 and nothing else |
| **A second step in the same column** | The shape the household could not build. The "+" on the bottom edge of "Zerbröseln" made a step with the *same parent*, drawn directly below it in column 2 — read back from the DOM as `@2 / 3 rows 3 / 4`. Every other way of adding a step made a new column or a parentless root, which is why theirs "only stayed above the ingredients" |
| Adding where it is wanted | "+" below *Wasser* minted an ingredient directly beneath it, already in Zerbröseln rather than in the tray; "+" on a tile's right edge inserted a step into the chain. No formset renders a spare row any more, and the tray is empty |
| **The ingredient page** | "+" adds a row and bumps TOTAL_FORMS (2→3), "×" hides it and ticks DELETE while leaving it in the DOM (3 in the DOM, 2 shown). The unit dropdown went from ~790px to 176px |
| Unit type-ahead | Typing "g" selects **grams**, typing "gl" selects Glas — measured, not assumed. The browser's own incremental search took the first label sharing a letter |
| **Search as you type** | The catalogue narrowed 112 rows to 3 on "meh" and to 5 on "zucker", the address bar followed, clearing restored all 112, and a query matching nothing showed the empty message. The Search button is hidden once the script runs and still works without it |
| The variety suggestion | "Roggenmehl - Typ 1150" offered *Mehl*; picking it **kept the typed name** and set the unit to `g` and the catalogue link to Mehl |
| The step timer | Counted 25:00 → 24:59, wrote its deadline to localStorage, Stop reset it. A deadline planted in the past and the page reloaded came back at 0:00, "Zeit ist um", the live region announcing it and the stored deadline cleared — the phone-locked-mid-bake case |
| **Resizing a tile by dragging its edge** | Driven with real pointer events on the Brot recipe: one row down, two rows down in a single drag, all four ingredients into one step, and every one of those dragged back — the assignments and their order returned exactly to where they started. The guide appeared during each drag, the scroll did not move (0px), the console stayed clean, and a drag that was released was still there after a save and a page load |
| The band's ends | Dragged the left end from column 3 to column 2 and pressed ArrowRight to put it back; both the hidden fields and the rendered `grid-column` followed |
| One handle per boundary | Read out of the DOM: Step 1 has a handle at Wasser's lower edge and Step 2 one at Test's, and the blank spare card has none. The previous build put a control on the top *and* bottom of every step, so this boundary had two of them 8px apart. A step that owns *no* lines used to have none either — see the row below; that was the bug, not the design |
| **A step with no ingredients can be pulled down over some** | The household's "Vormischen", which nothing could attach Dinkelmehl, Salz and Zucker to. Driven on the real Brot edit form: the handle now exists on that tile (`anchor 3`), sits exactly on the boundary between it and the Dinkelmehl row (measured: both at y=1709.8), and three ArrowDown presses — then, separately, one real pointer drag past Zucker — moved all three lines into it, leaving the ingredient order untouched |
| **Deleting a step no longer takes the recipe apart** | The reported sequence, driven on the real form: "+ Schritt danach" on "Zerbröseln", then "×" on what it made. The canvas came back to two blocks with Zerbröseln in column 2 beside Vormischen, every `position` identical to before and every ingredient still in the step it started in. Blanking the removed row's `parent_index` by hand — which is what the old code did — reproduces the break on the spot: three blocks, Zerbröseln stretched across columns 2–8 with Hefe and Wasser inside it |
| **The alarm rings until it is stopped** | A deadline planted 1.5 s ahead and the page reloaded: `kitchenSounds.isRinging()` still true seven seconds later (it was one 0.7 s chime before), Start hidden, the remaining button relabelled "Alarm ausschalten" — and pressing it silenced the tone, stopped the buzzing and put the timer back to 60:00 |
| **The Play buttons on the settings page make a noise** | They did nothing at all before: the page loaded `sound_preview.js` without `timer_sounds.js`, so the handler returned on its second line. Counted through a patched `createOscillator`: pressing "Abspielen" beside *Alarm* creates its five oscillators on a `running` context and ticks the radio beside it. `config/tests.py::test_no_page_loads_a_script_without_what_it_reaches_for` now walks every template for the same mistake |
| **The temperature waits for a mode** | Read out of the DOM on the real form **with the focus left on the select, which is what a real click leaves behind**: the slot holds the select alone until a mode is chosen, grows the box + "°C" + an error line the moment one is, and drops all three again when the mode is cleared. The first version of this check dispatched `change` without focusing anything, which is a state nobody reaches — it passed while every real click failed, and the box only appeared after a save. Focus the control, then act |
| **A bad temperature says so as it is typed** | Keyed in character by character: "abc" and "-5" never reach the box at all (digits only, `maxlength=3`), "18.5" becomes "185", and 0 or 501 turn the border red with "Eine Ofentemperatur ist eine ganze Zahl zwischen 1 und 500 °C." under it while `checkValidity()` goes false — so the Save is refused rather than the number vanishing. 1, 180 and 500 write straight through with no complaint |
| **The help pop-up fits the screen** | Measured with the recipe form's help open: 1675px of text inside a 893px body, the panel 970px tall in a 1018px viewport with 24px clear top and bottom, the close button on screen, and `scrollTop` reaching the last paragraph. Before the cap the panel was as tall as its content and `body.modal-open` meant nothing could scroll to the end of it |
| **The oven panel arrives on the keystroke** | Typed " im Ofen backen" into a step one letter at a time on the real form and watched the slot: the panel is absent up to "…im Ofe" and present from "…im Ofen" onwards. It used to need a drag or a save first, which is how it was reported |
| **The temperature is a box, not a list** | `type=number min=0 max=500 step=1 inputmode=numeric` with "°C" beside it. Fed 180 / 500 / 0 (written through), 501 / −5 / 18.5 / "abc" (nothing written, and the browser marks the box invalid). 18.5 was the one worth catching: `parseInt` would have stored 18 |
| **"In keinem Schritt" is gone** | Read out of the DOM: no heading and no help paragraph anywhere in that element, and "Salz" sits directly under the diagram as a plain card. It becomes a dashed drop zone with a one-line hint *only* while a drag is in flight — checked mid-drag with real pointer events, and dropping a line there still takes it out of its step |
| The standing-instruction note is gone | `[data-standing-note]` no longer exists on any card, and `markStanding` with it |
| **Adding a step no longer reorders the recipe** | "+ Schritt danach" on "Zerbröseln", on the real Brot form: it stays in rows 1–3 with Hefe and Wasser, "Vormischen" stays below it with its three lines, the new box lands in the column between, and every `position` is untouched. Doing what the old code did — minting the row and leaving it at the end of `stepOrder` — reproduces the report on the spot: Zerbröseln drops to rows 4–6 and Vormischen rises to the top |
| **"+ Step" on a recipe with no steps yet takes the ingredients** | The reported case, driven on the real new-recipe form: three lines typed in, no steps, "+ Schritt" pressed. The new box lands in column 2 spanning all three rows with every `step_index` pointing at it and the tray empty — it used to be a parentless row with nothing in it, drawn as a band over none of them. Then the mixed case: a fourth line added loose beside the existing arm, and the join "+" gave one step that took *both* — "Vermischen" reparented to it and "Hefe" assigned to it, column 3 over all four rows. No console errors, and `[data-step-row]` still matches `steps-TOTAL_FORMS` |
| **A timer follows you off the cooking page** | Started the 60-minute timer on Zwetschgenkuchen with a real click, then walked to the pantry: a card in the bottom-right corner reading ZWETSCHGENKUCHEN / "Hefeteig ansetzen, gehen lassen" / 59:39, linked back to the cooking view. The record in localStorage carries the step, the recipe, its URL and the chosen sound |
| **…and rings there** | Deadline brought forward on the pantry page: four oscillators in three seconds on a `running` context, the card green with "Zeit ist um" and "Alarm ausschalten", `⏰` on the tab title, and the sr-only status announcing it once. Left alone it was **still ringing twelve seconds later** — sampled once a second, with the store intact throughout |
| Two at once | A ringing one and a running one stack, only the expired one makes a noise, and stopping it leaves the other counting; stopping the second takes the dock away entirely. On the cooking view for Zwetschgenkuchen only the *other* recipe's card appears — its own timer is the one beside the step, restored from the same store on load (19:38, marked running, after a reload) |
| **A timer finer than a minute** | Minutes and seconds are two boxes either side of a colon on the step card, and one attribute — `data-cook-seconds` — carries the total to the cooking view. Read back off the real page: 3600 and 2700 for the two Zwetschgenkuchen timers, and no `data-cook-minutes` anywhere. `seed_demo` now has one 45 s step, for the same reason it has one 1,5 kg ingredient |
| The recipe page shows its ingredients once | Read back from the DOM: one `.ingredient-list` on the page, in the Preparing panel; the Cooking panel carries each line under the step that consumes it |
| The cooking view | Walked Kartoffelsalat step by step in the browser: the current cell and its four ingredient rows light up, earlier steps go green, the stopwatch runs, "Fertig" opens the finish panel with the measured minutes filled in |
| **v0.2.0 running on the DS723+** | The first install on the real NAS, 2026-08-08. The image came down, the container started, `→ applying migrations` completed and the process stayed up — on the hardware, not on Docker Desktop. Everything past the container itself (proxy, certificate, sign-in, SSO) is still §2 |
| **The bind mount, and the ACL that defeats the documented `chown`** | Found by a crash loop: 17 restarts, exit 1, `sqlite3.OperationalError: unable to open database file` about 2.7 s after `→ applying migrations` — presenting as a crash *a minute in* because of the restart backoff. The cause was `/volume1/docker` being an ACL-enabled share: `everyone` is `r-x`, there is no owner entry, so `chown -R 1000:1000` reported success and granted nothing. Two tells, both read off the running system: a `+` on `drwxrwxrwx+`, and the host reporting mode 777 while the container saw the same directory as 555. `user: "1026:101"` in the compose file fixes it — 101 is `administrators`, and gid 100 (`users`) does not work. DEPLOYMENT.md §4.1 was rewritten around this |
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
| **…and again, for `v0.2.0`** | The whole body of work below cut as one release: CI green (499 tests, 1m27s), the image built, smoke-tested and pushed as `ghcr.io/christianh99/kitchenapp:0.2.0`, and the release carrying a 76 MB `docker load` tarball, a compose file pinned to `0.2.0` (not `latest`), `env.example` and `SHA256SUMS`. The compose file's checksum verified against that list after downloading it back. Run 31265013619, 2m08s |
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
- **An *update* of a running container.** v0.2.0 is now installed and running on
  the DS723+ (§1), but it went onto an empty folder — it was the first thing
  ever put there. Replacing a *running* container with a newer image — the path
  in §7, including whether the version in the sidebar actually changes — has
  still not been done.
- **Most of the DS723+, still.** v0.2.0 now starts on the NAS and its bind mount
  works (§1), so the container half of this is no longer a guess. Everything
  around it is: the reverse proxy rule and its two custom headers, the
  certificate, DSM's Container Manager as opposed to `docker compose` over SSH,
  and the app being reached over `https://kitchen.haeusslerr.de` at all rather
  than on `127.0.0.1:8000`. Nobody has signed in on that machine yet.
- **The uid-1000 ownership rule turned out to be wrong on this NAS, and the
  correction is only tested one way round.** `/volume1/docker` is an
  ACL-enabled share whose ACL has no owner entry, so `chown -R 1000:1000`
  succeeds and grants nothing — see DEPLOYMENT.md §4.1. What works here is
  `user: "1026:101"` in the compose file. The documented alternative in that
  section (widening the `everyone` entry with `synoacltool -replace` and leaving
  the container at uid 1000) is written from the same ACL dump and has **not**
  been run.
- **The OIDC flow has never touched a real Synology SSO Server.** The claim
  handling is unit-tested by handing the backend dictionaries — which is the
  only way to test the DSM version that omits the group claim — but no browser
  has ever completed a round trip. Nothing about the handshake itself, the
  discovery document, or the token signature algorithm has been observed.
- **The `OIDC_OP_*` default endpoint paths in `settings.py` are guesses.** They
  follow Synology's usual `webman/sso/` shape. DEPLOYMENT.md §3.1 tells you to
  read the real ones off the discovery document before trusting them, and that
  instruction is not politeness.
- **The canvas has never been dragged with a finger.** Pointer events were
  chosen over the HTML5 drag API precisely so touch works, and `touch-action:
  none` on the handle is there so a drag is not a scroll — but every drag in §1
  was a mouse or a synthesised pointer. A tablet is exactly where somebody would
  type a recipe in, and it is ten minutes to check.
- **The canvas has never been seen at phone width.** It deliberately does *not*
  reflow to one column — that would destroy the one thing the layout says — so
  it scrolls sideways inside `.builder-scroll` instead. Whether that is workable
  while also dragging is a question a measurement cannot answer. The **Steps**
  view added since is the answer to this on a phone, and it has not been
  measured there either.
- **The pantry has never been used for a week.** Everything in §1 about it was
  driven in one sitting with five things in the cupboard. The questions that
  only time answers: whether anybody keeps the amounts current, whether the
  catalogue fills with near-duplicates faster than it is worth merging them, and
  whether "cannot tell" turns out to be the common answer rather than the rare
  one — which would make the whole filter useless in a way no test can see.
- **The shipped starter catalogue is one person's guess at a German kitchen.**
  About a hundred rows, with pack sizes from memory rather than from a shop.
  Being slightly wrong is survivable — the sizes only round a shortfall up — but
  nobody has checked them against an actual receipt.
- **No recipe with a real, deep diagram has been typed in through the Steps
  view.** The Brot case in §1 is three steps. Whether a numbered list is still
  the easier of the two at a dozen is unknown.
- **`deploy/entrypoint.sh` has never executed** beyond a syntax check.
- **`docker-compose.yml` paths are assumptions** —
  `/volume1/docker/kitchen/data` is the conventional place, not an observed one.
- **The reverse-proxy configuration in DEPLOYMENT.md §2 is from knowledge of
  DSM, not from this DS723+.** The custom-header requirement is real and is the
  thing that breaks OIDC; the exact DSM menu path may differ by version.

## 3. Deliberately not built

Not gaps. Each was considered and left out, and re-opening one is fine — this
list exists so it is re-opened knowingly.

- ~~**A pantry.**~~ **Built**, as `apps/pantry/` — the sibling app this entry
  anticipated. It carries the ingredient catalogue (one row per substance, with
  its usual unit, its other names and the sizes it is sold in), the closed unit
  set, the cupboard itself, and the matching that answers *what can be cooked
  now* and *what would have to be bought*. `RecipeIngredient` gained a nullable
  FK to it and nothing else about recipes changed.
- **Meal planning, and a shopping list across several recipes.** Still absent,
  and now most of the way there rather than at the beginning:
  `matching.shopping_list` already totals one substance across several recipes
  and rounds to whole packets. What is missing is a page to choose the recipes
  on. It respects the two columns this entry has always named — `optional`
  (don't buy saffron every week) and `alternative_for` (don't buy both the
  butter and the margarine).
- **HTMX.** The briefing asked for it and the app does not use it. Nothing here
  needs partial page updates yet: the servings scaler is pure client-side
  arithmetic, the cooking view shows and hides steps the server already
  rendered, and everything else is a form post. Adding HTMX for its own sake
  would be a dependency and a second rendering path for no behaviour. The
  moment there is a live-filtering list or an inline edit, it earns its place.
- ~~**Editing the diagram by dragging.**~~ **Built.** This entry used to say a
  canvas editor was a real feature rather than a polish pass, which was true —
  it is now `static/js/recipe_diagram.js`, and the form's "Ingredients" and
  "Method as a diagram" sections have become one canvas that the formset rows
  live inside. The two `<select>`s per row are gone; the one that remains is
  "Statt" (a substitute), because dropping one line onto another already means
  "put it in the same step" and the same gesture cannot also mean "replace it".
  What it cost, so the next pass knows: the layout rule now exists in JavaScript
  as well as in `apps/recipes/diagram.py` (CLAUDE.md's standing decisions say
  what bounds that), and the *arranging* no longer survives without JavaScript —
  the form still renders every field and still saves, but as a flat list.
- ~~**A server-rendered preview of the diagram while editing.**~~ **Moot.**
  There is no preview any more: the editor *is* the layout, and the cards are
  the formset rows themselves rather than a drawing of them. The `fetch`-an
  endpoint idea is no longer worth keeping — what would be previewed is already
  on the screen.
- ~~**A real unit vocabulary.**~~ **Built.** `unit` is a code from a closed set
  (`apps/pantry/units.py`), a dropdown rather than free text, and
  `recipes/0003` translated the values that predate it. What made it worth doing
  was the pantry: "1 kg Zucker" in the cupboard answers "500 g Zucker" in a
  recipe only if something can convert one into the other.
- **Nutrition and cost per portion.** Both now have their prerequisite — a
  normalised unit and a catalogue row to hang a number on. Neither is wanted
  yet; a per-100 g figure on `Ingredient` is where they would start, and the
  honest warning is that keeping such figures current is a chore nobody in a
  household volunteers for.
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
- **Self-service password change.** Accounts come from the identity provider.
  See CLAUDE.md.
- **Anything that guesses which substance a name means.** `catalogue.lookup` is
  exact after case-folding, with aliases searched and nothing else. Stemming,
  plural-stripping and prefix matching were all considered and left out: they
  buy a handful of correct matches and pay for them with wrong ones, and a wrong
  one here is the pantry claiming a substance the house does not have.
- **A pantry per person, or a history of what was in the cupboard.** One row per
  ingredient, overwritten. "How much sugar is there" has one answer and the
  house is the trust boundary; a log of it would be a table nobody reads.
- **Automatic depletion when a recipe is cooked.** Tempting, and wrong without a
  weekly shop being recorded too — the cupboard would drift to empty and stay
  there, which is worse than a number somebody updates when they notice.
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

1. **Put v0.2.0 on the actual NAS.** Everything up to the container is now
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
5. **A shopping list across *several* recipes.** Half of this exists:
   `apps/pantry/matching.shopping_list` already takes a list of
   `(recipe, verdict)` pairs, adds the same substance up across them and rounds
   the total to whole packets. What is missing is the page — somewhere to choose
   four recipes for the week and get one list out. The unit question that used
   to be listed here is settled: units are a closed set now
   (`apps/pantry/units.py`).
6. **Pagination on the recipe list, and on the cooking history.** The list
   renders every recipe; the recipe page renders the last ten cookings and says
   how many more there are. Fine at a hundred with lazy-loaded images; not fine
   at a thousand. The query-cost tests will *not* catch this — they pin the
   query count, which stays flat while the payload grows.
7. **A print stylesheet for the recipe page.** People print recipes, and the
   diagram is the part that will come out wrong: it lives in a horizontally
   scrolling box that a printer cannot scroll.
8. **Turn the cooking history into an answer rather than a list.** The page
   exists (`/cooked/`) and entries can be corrected after the fact, which was
   the thing that was actually missing. What it still does not do is *add up*:
   "what did we eat this month", and whether "serves four" is ever true in this
   house. Every number for it is recorded; only the recipe page's own median
   reads any of it.
9. **Tidy the catalogue once it has been used for a while.** It grows by itself
   from every recipe saved, which is what makes it useful and what will leave
   "Kartoffeln" beside "festkochende Kartoffeln". Merging is a manual job in the
   admin today; if it turns out to be a monthly chore, it wants a button on the
   catalogue page rather than a cleverer matcher — see CLAUDE.md for why the
   matcher stays exact.

## 6. Things that will bite

Collected because each one cost time in the session that built this, and none is
visible from the code alone.

- **An exception inside `render()` in `static/js/recipe_diagram.js` deletes form
  rows.** Not a hypothetical: the Steps view shipped with `nameOf()` reading
  `latest`, which `refresh()` only assigns *after* `render()` returns — so on
  the first pass it was null, the call threw halfway through moving the cards
  into a detached `<ol>`, and three step cards ended up outside the document
  altogether. `TOTAL_FORMS` still said six. The page looked like a recipe that
  had silently lost its first three steps, and saving it would have.

  Two things now bound it: the list is attached to the canvas *before* it is
  filled, so the worst a throw can do is lay the cards out wrongly rather than
  remove them; and anything called during a render takes `state` as an argument
  instead of reaching for `latest`. **Nothing in the test suite can catch this**
  — no test here runs JavaScript. The check that finds it is counting
  `[data-step-row]` in the DOM against `steps-TOTAL_FORMS` in the browser, and
  it is worth doing by hand after any change to that file.
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
