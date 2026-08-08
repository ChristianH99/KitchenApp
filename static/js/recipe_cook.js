/* The guided cooking view: one step at a time, a stopwatch, and the diagram
 * lighting up the part of itself you are currently standing in.
 *
 * **The stopwatch is in the browser, not on the server.** Two reasons, and both
 * of them are about this app's hardware rather than about taste. A "cooking
 * session" row would mean the *opening* of a page taking SQLite's single write
 * lock — the one every other request in the house queues behind — and this app
 * has a rule that a read does not write (apps/recipes/tests.py pins it). And a
 * server-side session ends when a phone on a worktop goes to sleep and the tab
 * is evicted, which is exactly the ninety minutes it was supposed to measure.
 * localStorage survives that. The elapsed time crosses to the server once, in
 * the POST that records the cooking.
 *
 * **The steps are all rendered, and hidden.** Building them in the browser
 * would mean a page that is empty when the script fails; hiding server-rendered
 * ones means the fallback is the whole recipe in order, which is a usable page.
 *
 * **The clock starts by itself.** "How long did that take" is a question nobody
 * remembers to ask beforehand, so the first press of Next starts it — there is
 * a Start button as well, for somebody who wants it running while they get the
 * pans out.
 */
(function () {
  const root = document.querySelector("[data-cook]");
  if (!root) return;

  const steps = Array.from(root.querySelectorAll("[data-cook-step]"));
  const clockEl = root.querySelector("[data-cook-clock]");
  const toggleEl = root.querySelector("[data-cook-toggle]");
  const resetEl = root.querySelector("[data-cook-reset]");
  const progressEl = root.querySelector("[data-cook-progress]");
  const navEl = root.querySelector("[data-cook-nav]");
  const finishRowEl = root.querySelector("[data-cook-finish-row]");
  const finishEl = root.querySelector("[data-cook-finish]");

  const STORE = "kitchen.cook." + (root.dataset.recipe || "0");
  // Older than this and it is not the same cooking — it is yesterday's tab
  // still open. Restoring it would put a nine-hour stopwatch in the box.
  const STALE_MS = 12 * 60 * 60 * 1000;

  const state = load() || { index: 0, elapsed: 0, since: null, saved: Date.now() };
  if (state.index >= steps.length) state.index = Math.max(0, steps.length - 1);

  function load() {
    let raw = null;
    try {
      raw = window.localStorage.getItem(STORE);
    } catch (err) {
      return null;   // private mode, or storage disabled
    }
    if (!raw) return null;
    let parsed = null;
    try {
      parsed = JSON.parse(raw);
    } catch (err) {
      return null;
    }
    if (!parsed || Date.now() - (parsed.saved || 0) > STALE_MS) return null;
    return parsed;
  }

  function store() {
    state.saved = Date.now();
    try {
      window.localStorage.setItem(STORE, JSON.stringify(state));
    } catch (err) { /* nothing to do; the page still works */ }
  }

  function forget() {
    try {
      window.localStorage.removeItem(STORE);
    } catch (err) { /* as above */ }
  }

  /* ---- the stopwatch ---- */

  function elapsed() {
    return state.elapsed + (state.since ? Date.now() - state.since : 0);
  }

  function clock(ms) {
    const total = Math.max(0, Math.round(ms / 1000));
    const minutes = Math.floor(total / 60);
    const seconds = total % 60;
    return minutes + ":" + String(seconds).padStart(2, "0");
  }

  function paint() {
    if (clockEl) clockEl.textContent = clock(elapsed());
    if (toggleEl) toggleEl.textContent = state.since ? gettext("Pause") : gettext("Start");
  }

  function start() {
    if (state.since) return;
    state.since = Date.now();
    store();
    paint();
    keepAwake();
  }

  function pause() {
    if (!state.since) return;
    state.elapsed += Date.now() - state.since;
    state.since = null;
    store();
    paint();
    releaseAwake();
  }

  if (toggleEl) {
    toggleEl.addEventListener("click", () => { if (state.since) pause(); else start(); });
  }

  if (resetEl) {
    resetEl.addEventListener("click", async () => {
      const ok = await window.appConfirm({
        title: gettext("Start again?"),
        body: gettext("The stopwatch goes back to zero and you return to the first step."),
        accept: gettext("Start again"),
      });
      if (!ok) return;
      state.elapsed = 0;
      state.since = null;
      state.index = 0;
      store();
      show();
      paint();
      releaseAwake();
    });
  }

  // Half a second rather than a full one: a clock ticked exactly on the second
  // drifts visibly against the phone's own, because the interval never fires at
  // the instant it was asked to.
  window.setInterval(paint, 500);

  /* ---- the screen, while somebody has their hands in a bowl ----
   *
   * Best effort. The Wake Lock API needs a secure context and is not
   * everywhere; a kitchen tablet that dims after two minutes is an annoyance
   * and not a failure, so every branch here is silent.
   */
  let wakeLock = null;

  function keepAwake() {
    if (!navigator.wakeLock || wakeLock) return;
    navigator.wakeLock.request("screen").then((lock) => {
      wakeLock = lock;
      lock.addEventListener("release", () => { wakeLock = null; });
    }).catch(() => { wakeLock = null; });
  }

  function releaseAwake() {
    if (wakeLock) { wakeLock.release().catch(() => {}); wakeLock = null; }
  }

  // A lock is dropped when the tab goes to the background and is not given back.
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible" && state.since) keepAwake();
  });

  /* ---- which step is showing ---- */

  const diagramCells = Array.from(document.querySelectorAll(".diagram-cell[data-step]"));

  function show() {
    steps.forEach((section, index) => {
      section.hidden = index !== state.index;
      section.classList.toggle("is-current", index === state.index);
    });

    const current = steps[state.index];
    const currentId = current ? current.dataset.cookStep : "";
    const doneIds = steps.slice(0, state.index).map((s) => s.dataset.cookStep);

    // The diagram is the map: the cell you are in is lit, the ones behind you
    // are ticked off, and an ingredient row carries the id of the box it feeds
    // so "what goes in now" lights up with the same selector.
    diagramCells.forEach((cell) => {
      const id = cell.dataset.step;
      cell.classList.toggle("is-current", Boolean(id) && id === currentId);
      cell.classList.toggle("is-done", Boolean(id) && id !== currentId && doneIds.indexOf(id) !== -1);
    });

    if (progressEl) {
      progressEl.textContent = interpolate(
        gettext("Step %(n)s of %(total)s"),
        { n: state.index + 1, total: steps.length }, true
      );
    }

    const last = state.index >= steps.length - 1;
    const prev = root.querySelector("[data-cook-prev]");
    const next = root.querySelector("[data-cook-next]");
    const done = root.querySelector("[data-cook-done]");
    // Disabled rather than hidden at either end: these are pinned to the sides
    // of the page, and one of them vanishing leaves the other looking like it
    // moved.
    if (prev) prev.disabled = state.index === 0;
    if (next) next.disabled = last;
    if (done) done.hidden = !last;
    if (finishRowEl) finishRowEl.hidden = !last;
  }

  const nextButton = root.querySelector("[data-cook-next]");
  const prevButton = root.querySelector("[data-cook-prev]");
  const doneButton = root.querySelector("[data-cook-done]");

  // One implementation per move, so a button and a swipe cannot come to mean
  // two slightly different things.
  function goNext() {
    if (state.index >= steps.length - 1) return;
    start();                         // the automatic half of "measured automatically"
    state.index += 1;
    store();
    show();
    scrollToStep();
  }

  function goPrev() {
    if (state.index <= 0) return;
    state.index -= 1;
    store();
    show();
    scrollToStep();
  }

  if (nextButton) nextButton.addEventListener("click", goNext);
  if (prevButton) prevButton.addEventListener("click", goPrev);
  if (doneButton) {
    doneButton.addEventListener("click", () => {
      pause();
      finish();
    });
  }

  function scrollToStep() {
    const current = steps[state.index];
    if (current && current.scrollIntoView) {
      current.scrollIntoView({ block: "nearest", behavior: "smooth" });
    }
  }

  /* ---- finishing ---- */

  function finish() {
    if (!finishEl) return;
    finishEl.hidden = false;
    const minutes = finishEl.querySelector("input[name='minutes']");
    // Never zero: somebody who cooked for forty seconds did not, and a 0 in
    // the box reads as "the stopwatch was not running" — which is the one
    // thing this is here to stop happening.
    if (minutes && !minutes.value) {
      minutes.value = String(Math.max(1, Math.round(elapsed() / 60000)));
    }
    finishEl.scrollIntoView({ block: "start", behavior: "smooth" });
    const first = finishEl.querySelector("input, textarea");
    if (first) first.focus();
  }

  // Saved: the cooking is recorded server-side, so the half-finished state in
  // this browser is not only useless but actively wrong — the next visit would
  // resume a walk through a recipe that is already eaten.
  const finishForm = finishEl && finishEl.querySelector("form");
  if (finishForm) finishForm.addEventListener("submit", forget);

  /* ---- a step's own timer ----------------------------------------------
   *
   * A countdown off a **wall-clock deadline**, not a tick count. A phone in a
   * kitchen locks its screen, and a backgrounded tab has its timers throttled
   * to once a minute or stopped altogether — so anything that counted its own
   * ticks would come back from a locked screen claiming the bread had four
   * minutes left when it had been out for ten. The deadline is a timestamp; the
   * interval only repaints it, and being throttled costs a stale reading and
   * nothing else.
   *
   * It is written to localStorage for the same reason the walk-through's own
   * position is: the page gets reloaded, the browser gets closed, and a timer
   * that forgot is worse than no timer.
   *
   * The store itself belongs to static/js/timer_watch.js, which base.html
   * loads on every page — because a timer you have to stay on this page to hear
   * is not a kitchen timer. This page writes the deadline and the few words
   * needed to say *which* timer it is; every other page reads the same rows and
   * puts a card in the corner. One store, so the card cannot show a countdown
   * this page has already stopped.
   */

  const RECIPE = String(root.dataset.recipe || "");

  function deadlineFor(id) {
    if (!window.kitchenTimers) return null;
    const found = window.kitchenTimers.all().find(
      (timer) => timer.recipe === RECIPE && String(timer.stepId) === String(id)
    );
    return found ? found.ends : null;
  }

  function rememberDeadline(id, endsAt, label) {
    if (!window.kitchenTimers) return;
    if (!endsAt) {
      window.kitchenTimers.clear(RECIPE, id);
      return;
    }
    // The sound is recorded with the timer rather than looked up when it rings:
    // the page that ends up ringing it may be the pantry, which has no reason
    // to have asked the server what this person chose.
    window.kitchenTimers.set(RECIPE, id, {
      ends: endsAt,
      step: label || "",
      recipe: root.dataset.recipeName || "",
      url: root.dataset.cookUrl || window.location.pathname,
      sound: root.dataset.timerSound || "chime",
    });
  }

  /* ---- the sound ----
   *
   * From static/js/timer_sounds.js, which the settings page uses too — so the
   * tone somebody picked while comparing them is the tone that rings here. The
   * context is woken inside the press of Start, which is the last moment a
   * gesture is still in hand and a browser will still let a page make a noise.
   *
   * **It rings until it is stopped.** A single chime is a chime you miss by
   * being in the next room or by running a tap, which is the one thing a
   * kitchen timer exists to prevent — and the bread carries on baking. So the
   * tone repeats, the phone keeps buzzing, and both stop on the one press that
   * also puts the timer back to where it started.
   *
   * `ringingId` is which step's timer is making the noise, so that stopping a
   * *different* step's countdown does not silence it. Only one at a time: a
   * second timer reaching nought takes the alarm over, which is the same sound
   * for the same reason and nothing anybody could tell apart anyway.
   */

  let ringingId = null;
  let buzz = null;

  function ring(id) {
    const named = root.dataset.timerSound || "chime";
    ringingId = id;
    if (window.kitchenSounds) window.kitchenSounds.ring(named);
    // A vibration as well as the tone: a phone face-down on a worktop in a
    // noisy kitchen is the case the sound alone does not cover. Repeated on the
    // same reasoning, and ignored silently where it is not supported.
    if (!navigator.vibrate) return;
    const buzzOnce = () => navigator.vibrate([200, 100, 200]);
    buzzOnce();
    buzz = window.setInterval(buzzOnce, 3000);
  }

  function silence(id) {
    // Only the step that is actually ringing may stop the noise. Pressing Stop
    // on a countdown still running elsewhere is not an answer to the alarm.
    if (id !== undefined && ringingId !== id) return;
    ringingId = null;
    if (window.kitchenSounds) window.kitchenSounds.silence();
    if (buzz) window.clearInterval(buzz);
    buzz = null;
    if (navigator.vibrate) navigator.vibrate(0);
  }

  function wakeAudio() {
    if (window.kitchenSounds) window.kitchenSounds.wake();
  }

  steps.forEach((section) => {
    const holder = section.querySelector("[data-cook-step-timer]");
    const button = section.querySelector("[data-cook-step-start]");
    const stopButton = section.querySelector("[data-cook-step-stop]");
    const readout = section.querySelector("[data-cook-remaining]");
    const status = section.querySelector("[data-cook-timer-status]");
    // Seconds, not minutes: a step may be "1:30" — see RecipeStep.timer_seconds,
    // which is what puts the whole duration in one attribute so the browser
    // never has to add two of them up.
    const seconds = parseInt(section.dataset.cookSeconds || "", 10);
    if (!holder || !button || !readout || !seconds) return;

    const id = holder.dataset.cookStepId || section.dataset.cookStep;
    const label = holder.dataset.cookStepLabel || "";
    const startLabel = button.textContent;
    const stopLabel = stopButton ? stopButton.textContent : "";
    let endsAt = null;
    let ticker = null;
    let rang = false;

    function stopTicking() {
      if (ticker) window.clearInterval(ticker);
      ticker = null;
    }

    function reset() {
      stopTicking();
      silence(id);
      endsAt = null;
      rang = false;
      rememberDeadline(id, null);
      section.classList.remove("is-ready", "is-running");
      readout.textContent = clock(seconds * 1000);
      button.textContent = startLabel;
      button.hidden = false;
      if (stopButton) {
        stopButton.textContent = stopLabel;
        stopButton.hidden = true;
      }
    }

    function tick() {
      const left = endsAt - Date.now();
      readout.textContent = clock(Math.max(0, left));
      if (left > 0) return;
      stopTicking();
      section.classList.remove("is-running");
      section.classList.add("is-ready");
      // The row is deliberately **left in the store**. It used to be cleared
      // the instant the countdown reached nought, which meant walking away from
      // a ringing alarm silenced it — the deadline was gone, so no other page
      // knew there was anything to ring. An expired timer is an alarm nobody
      // has answered yet, and it stays one until Stop is pressed (or twelve
      // hours pass, which timer_watch.js calls abandoned rather than missed).
      if (rang) return;
      rang = true;
      if (status) status.textContent = gettext("The timer has finished.");
      // The alarm keeps going, so the one control on screen is the one that
      // stops it. Start is hidden meanwhile: a button reading "Time is up" that
      // silently restarts the countdown when pressed — which is what was here —
      // is the wrong answer to the only question being asked.
      button.hidden = true;
      if (stopButton) {
        stopButton.textContent = gettext("Stop the alarm");
        stopButton.hidden = false;
      }
      ring(id);
    }

    function run(deadline) {
      endsAt = deadline;
      rang = false;
      rememberDeadline(id, endsAt, label);
      section.classList.remove("is-ready");
      section.classList.add("is-running");
      button.hidden = true;
      if (stopButton) {
        stopButton.textContent = stopLabel;
        stopButton.hidden = false;
      }
      stopTicking();
      ticker = window.setInterval(tick, 250);
      tick();
    }

    button.addEventListener("click", () => {
      start();                       // the walk-through's own stopwatch
      wakeAudio();                   // while we still have the gesture
      run(Date.now() + seconds * 1000);
    });
    if (stopButton) stopButton.addEventListener("click", reset);

    // Left running when the page was closed. A deadline already past shows
    // "time is up" rather than a negative number — and rings, because nobody
    // saw it the first time.
    const saved = deadlineFor(id);
    if (saved) run(saved);
    else readout.textContent = clock(seconds * 1000);

    // Throttled tabs stop repainting. Catching up on the way back means the
    // reading is right the moment somebody looks at it.
    document.addEventListener("visibilitychange", () => {
      if (!document.hidden && endsAt) tick();
    });
  });

  /* ---- swiping, on a touch screen -------------------------------------
   *
   * The same two moves as the arrows, because on a phone the arrows are hidden:
   * a 44px target pinned to the edge of a phone screen is a mis-tap, and the
   * thumb is already there.
   *
   * Only for `touch`. A mouse drag across the page is a text selection and a
   * trackpad's inertia would fire this on an ordinary scroll — so the gesture
   * is read from touch pointers alone, and only when it is decisively sideways:
   * more than 60px across and more than twice as far across as down, or every
   * attempt to scroll a long step turns the page.
   */

  let swipe = null;

  root.addEventListener("pointerdown", (event) => {
    if (event.pointerType !== "touch") return;
    // Not from inside something the finger is meant to be using.
    if (event.target.closest("input, textarea, select, button, a, .diagram-scroll")) {
      swipe = null;
      return;
    }
    swipe = { x: event.clientX, y: event.clientY, id: event.pointerId };
  });

  root.addEventListener("pointerup", (event) => {
    if (!swipe || event.pointerId !== swipe.id) return;
    const dx = event.clientX - swipe.x;
    const dy = event.clientY - swipe.y;
    swipe = null;
    if (Math.abs(dx) < 60 || Math.abs(dx) < Math.abs(dy) * 2) return;
    if (dx < 0) goNext();
    else goPrev();
  });

  root.addEventListener("pointercancel", () => { swipe = null; });

  /* ---- go ---- */

  if (navEl) navEl.hidden = false;
  show();
  paint();
  if (state.since) keepAwake();
})();
