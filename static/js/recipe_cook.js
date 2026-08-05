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
    if (prev) prev.disabled = state.index === 0;
    if (next) next.hidden = last;
    if (done) done.hidden = !last;
  }

  const nextButton = root.querySelector("[data-cook-next]");
  const prevButton = root.querySelector("[data-cook-prev]");
  const doneButton = root.querySelector("[data-cook-done]");

  if (nextButton) {
    nextButton.addEventListener("click", () => {
      start();                       // the automatic half of "measured automatically"
      if (state.index < steps.length - 1) state.index += 1;
      store();
      show();
      scrollToStep();
    });
  }
  if (prevButton) {
    prevButton.addEventListener("click", () => {
      if (state.index > 0) state.index -= 1;
      store();
      show();
      scrollToStep();
    });
  }
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

  /* ---- a step's own timer ---- */

  steps.forEach((section) => {
    const button = section.querySelector("[data-cook-step-start]");
    const readout = section.querySelector("[data-cook-remaining]");
    const minutes = parseInt(section.dataset.cookMinutes || "", 10);
    if (!button || !readout || !minutes) return;

    let endsAt = null;
    let ticker = null;

    function tick() {
      const left = endsAt - Date.now();
      readout.textContent = clock(Math.max(0, left));
      if (left > 0) return;
      window.clearInterval(ticker);
      ticker = null;
      endsAt = null;
      section.classList.add("is-ready");
      button.textContent = gettext("Time is up");
      // A page cannot make a sound without a file to play and cannot ask for
      // one without a gesture; a vibration is the one alert a phone in a
      // kitchen will actually deliver, and it is silently ignored elsewhere.
      if (navigator.vibrate) navigator.vibrate([200, 100, 200]);
    }

    button.addEventListener("click", () => {
      start();
      section.classList.remove("is-ready");
      endsAt = Date.now() + minutes * 60000;
      if (ticker) window.clearInterval(ticker);
      ticker = window.setInterval(tick, 250);
      button.textContent = gettext("Running");
      tick();
    });
  });

  /* ---- go ---- */

  if (navEl) navEl.hidden = false;
  show();
  paint();
  if (state.since) keepAwake();
})();
