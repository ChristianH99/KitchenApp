/* The kitchen timer, on every page.
 *
 * A timer you have to stay on one page to hear is not a kitchen timer. Bread
 * goes in, and then somebody looks up the next recipe, checks the pantry, or
 * simply presses Back — and the countdown that was going to tell them the
 * thirty minutes were up was thrown away with the page. So the deadline lives
 * in localStorage, this file is loaded by base.html on *every* page, and what
 * is running follows you around: a small card at the bottom right that counts
 * down and then rings until it is stopped.
 *
 * ---- one store, read from two places ----
 *
 * `kitchen.cook-timers.<recipe id>` holds `{step id: record}` and is the only
 * copy of what is running. static/js/recipe_cook.js writes it when somebody
 * presses Start and reads it back to restore a countdown the page was closed
 * on; this file reads it on every page. Two stores of one fact is how the card
 * comes to show a timer the cooking view has already stopped, so there is one,
 * and this module owns it.
 *
 * Reading it fresh on every tick is also what makes two tabs agree: stopping an
 * alarm in one clears the card in the other within half a second, with no
 * message passing at all.
 *
 * ---- what it can and cannot do about the noise ----
 *
 * A browser will not let a page make a sound until somebody has interacted with
 * *that page*. On the cooking view the gesture is the press of Start, which is
 * why the sound is armed there. Here there may have been no gesture at all —
 * somebody may have opened this page and put the phone down — and there is
 * nothing this file can do about that; it is the browser's rule, not a choice.
 *
 * So: the first touch or keypress of any kind arms the audio, and the alarm is
 * *also* a visible card and a changed tab title, because those are the two
 * things that always work. This is the reason the card is deliberately not
 * dismissable while it is ringing.
 */
(function () {
  const PREFIX = "kitchen.cook-timers.";
  // A deadline this far in the past was not missed, it was abandoned — the tab
  // somebody left open on Tuesday. Restoring it would put a nine-hour-old alarm
  // on the screen, which is the same reasoning as the cooking view's own
  // STALE_MS and the same number.
  const STALE_MS = 12 * 60 * 60 * 1000;

  function read(key) {
    try {
      return JSON.parse(window.localStorage.getItem(key) || "{}") || {};
    } catch (err) {
      return {};                 // private mode, storage off, or corrupt
    }
  }

  function write(key, rows) {
    try {
      if (Object.keys(rows).length) window.localStorage.setItem(key, JSON.stringify(rows));
      // Removed rather than left as "{}": an empty key is a key the next
      // enumeration has to read and discard for the life of the browser.
      else window.localStorage.removeItem(key);
    } catch (err) {
      /* storage off; a timer simply does not outlive the page */
    }
  }

  function storeKeys() {
    const out = [];
    try {
      for (let at = 0; at < window.localStorage.length; at += 1) {
        const key = window.localStorage.key(at);
        if (key && key.indexOf(PREFIX) === 0) out.push(key);
      }
    } catch (err) {
      /* as above */
    }
    return out;
  }

  function all() {
    /* Every timer that is still worth showing, soonest first.
     *
     * Stale rows are dropped here rather than anywhere else, so there is one
     * place that decides what "still running" means and every reader agrees.
     */
    const now = Date.now();
    const found = [];
    storeKeys().forEach((key) => {
      const recipe = key.slice(PREFIX.length);
      const rows = read(key);
      let dropped = false;
      Object.keys(rows).forEach((stepId) => {
        const raw = rows[stepId];
        // A bare number is the shape this store had before it carried anything
        // besides the deadline. Still read, so a timer running across an update
        // is not silently thrown away — it simply has no name to show.
        const row = typeof raw === "number" ? { ends: raw } : (raw || {});
        if (!row.ends || now - row.ends > STALE_MS) {
          delete rows[stepId];
          dropped = true;
          return;
        }
        found.push({
          key: key, recipe: recipe, stepId: stepId, ends: row.ends,
          step: row.step || "", recipeName: row.recipe || "",
          url: row.url || "", sound: row.sound || "chime",
        });
      });
      if (dropped) write(key, rows);
    });
    return found.sort((a, b) => a.ends - b.ends);
  }

  function set(recipe, stepId, row) {
    const key = PREFIX + recipe;
    const rows = read(key);
    rows[stepId] = row;
    write(key, rows);
  }

  function clear(recipe, stepId) {
    const key = PREFIX + recipe;
    const rows = read(key);
    delete rows[stepId];
    write(key, rows);
  }

  window.kitchenTimers = { all: all, set: set, clear: clear };

  /* ---- the card ------------------------------------------------------- */

  // Which recipe this page is *already* showing a timer for. The cooking view
  // has its own countdown beside the step, so putting a second one in the
  // corner would be two readings of one clock — and the moment they disagree by
  // a tick, the wrong one is the one somebody happens to be looking at.
  const cook = document.querySelector("[data-cook]");
  const here = cook ? String(cook.dataset.recipe || "") : null;

  function elsewhere() {
    return all().filter((timer) => String(timer.recipe) !== here);
  }

  const pageTitle = document.title;
  let holder = null;
  const cards = new Map();
  let ringing = null;
  let buzz = null;

  function idOf(timer) {
    return timer.recipe + "/" + timer.stepId;
  }

  function clock(ms) {
    const total = Math.max(0, Math.round(ms / 1000));
    return Math.floor(total / 60) + ":" + String(total % 60).padStart(2, "0");
  }

  function build() {
    if (holder) return holder;
    holder = document.createElement("div");
    holder.className = "timer-dock";
    // A region rather than an alert: it is on the page for half an hour, and
    // "alert" would have a screen reader interrupt whatever somebody is doing
    // the moment it appears. The one thing worth announcing — that a timer has
    // finished — is announced by the row's own status line.
    holder.setAttribute("role", "region");
    holder.setAttribute("aria-label", gettext("Kitchen timers"));
    document.body.appendChild(holder);
    return holder;
  }

  function cardFor(timer) {
    const key = idOf(timer);
    const found = cards.get(key);
    if (found) return found;

    const card = document.createElement("div");
    card.className = "timer-card";

    const body = document.createElement("div");
    body.className = "timer-card-body";

    // A link to the page the timer belongs to, because "which bread is this?"
    // is the first question the card raises and walking back to the recipe list
    // to answer it is the whole of the annoyance.
    const name = document.createElement(timer.url ? "a" : "span");
    name.className = "timer-card-recipe";
    if (timer.url) name.href = timer.url;
    name.textContent = timer.recipeName || gettext("Timer");
    body.appendChild(name);

    const step = document.createElement("span");
    step.className = "timer-card-step";
    step.textContent = timer.step;
    body.appendChild(step);

    const left = document.createElement("span");
    left.className = "timer-card-clock";
    // Never aria-live: a countdown that announces itself reads every second of
    // half an hour out loud.
    left.setAttribute("role", "timer");
    left.setAttribute("aria-live", "off");
    body.appendChild(left);

    const status = document.createElement("span");
    status.className = "sr-only";
    status.setAttribute("role", "status");
    body.appendChild(status);

    const stop = document.createElement("button");
    stop.type = "button";
    stop.className = "timer-card-stop";
    stop.addEventListener("click", () => {
      silence(key);
      clear(timer.recipe, timer.stepId);
      paint();
    });

    card.appendChild(body);
    card.appendChild(stop);
    build().appendChild(card);

    const made = { el: card, clock: left, stop: stop, status: status, rang: false };
    cards.set(key, made);
    return made;
  }

  /* ---- the noise ------------------------------------------------------ */

  function ring(key, sound) {
    if (ringing === key) return;
    ringing = key;
    if (window.kitchenSounds) window.kitchenSounds.ring(sound);
    if (!navigator.vibrate) return;
    const buzzOnce = () => navigator.vibrate([200, 100, 200]);
    buzzOnce();
    buzz = window.setInterval(buzzOnce, 3000);
  }

  function silence(key) {
    // Only whatever is actually ringing may be silenced, so pressing Stop on a
    // countdown that is still running does not quieten a different one.
    if (key !== undefined && ringing !== key) return;
    ringing = null;
    if (window.kitchenSounds) window.kitchenSounds.silence();
    if (buzz) window.clearInterval(buzz);
    buzz = null;
    if (navigator.vibrate) navigator.vibrate(0);
  }

  /* ---- and the tick --------------------------------------------------- */

  function paint() {
    const live = elsewhere();
    const seen = new Set(live.map(idOf));
    cards.forEach((card, key) => {
      if (seen.has(key)) return;
      card.el.remove();
      cards.delete(key);
      silence(key);
    });

    if (!live.length) {
      if (holder) holder.hidden = true;
      if (document.title !== pageTitle) document.title = pageTitle;
      return;
    }
    build().hidden = false;

    const now = Date.now();
    let anyDone = false;
    live.forEach((timer, at) => {
      const card = cardFor(timer);
      // Soonest at the top — `live` is sorted by deadline, so the one that has
      // gone off rises above one with twenty minutes left. Through CSS `order`
      // rather than by moving the element: re-appending a card every half
      // second would take the focus off its own Stop button twice a second,
      // which is the button somebody is reaching for.
      card.el.style.order = String(at);
      const leftMs = timer.ends - now;
      const done = leftMs <= 0;
      card.clock.textContent = done ? gettext("Time is up") : clock(leftMs);
      card.el.classList.toggle("is-ready", done);
      card.stop.textContent = done ? gettext("Stop the alarm") : gettext("Stop");
      if (done && !card.rang) {
        card.rang = true;
        card.status.textContent = gettext("The timer has finished.");
      }
      if (!done && card.rang) {
        card.rang = false;
        card.status.textContent = "";
      }
      anyDone = anyDone || done;
    });

    // The soonest expired one makes the noise. Stopping it clears its row, and
    // the next tick hands the alarm to whatever is expired after that — which
    // is what somebody with two things in the oven would expect.
    const first = live.filter((timer) => timer.ends <= now)[0] || null;
    if (first) ring(idOf(first), first.sound);
    else silence();

    // The tab title, because a backgrounded tab is exactly the case the card
    // cannot be seen in — and it is the one signal that survives a phone
    // refusing to make a sound.
    const wanted = anyDone ? "⏰ " + pageTitle : pageTitle;
    if (document.title !== wanted) document.title = wanted;
  }

  // A browser will not let a page make a noise before it has been touched, so
  // the first interaction of any kind arms the audio — long before the alarm,
  // with luck. `wake` is cheap and idempotent; `once` means this costs nothing
  // for the rest of the page's life.
  const arm = () => { if (window.kitchenSounds) window.kitchenSounds.wake(); };
  document.addEventListener("pointerdown", arm, { once: true });
  document.addEventListener("keydown", arm, { once: true });

  // Half a second rather than a full one, for the same reason the cooking
  // view's stopwatch uses it: a clock ticked exactly on the second drifts
  // visibly against the phone's own.
  window.setInterval(paint, 500);
  // Throttled tabs stop repainting; catching up on the way back means the
  // reading is right the moment somebody looks at it.
  document.addEventListener("visibilitychange", () => { if (!document.hidden) paint(); });
  paint();
})();
