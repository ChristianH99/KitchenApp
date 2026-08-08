/* Search as you type, on any page whose search form asks for it.
 *
 * The recipe list and the ingredient catalogue both had a box and a Search
 * button, and pressing the button reloaded the page. Typing three letters and
 * reaching for a button is two gestures for one thought — so the results follow
 * the typing and the button goes away.
 *
 * ---- why it re-asks the server ----
 *
 * Filtering the rows already on the page would be faster and would be wrong:
 * the recipe search looks *inside ingredient lists* and tags, which are not in
 * the markup, and the catalogue search looks at aliases. Narrowing what is
 * visible would silently answer a different question from the one the button
 * answered. So the same URL is fetched and the results region is swapped.
 *
 * ---- and the three things that makes fiddly ----
 *
 * A **race**: keystrokes outrun responses, and the reply to "meh" can land
 * after the reply to "mehl". Each request carries a serial number and anything
 * but the newest is dropped on arrival.
 *
 * The **address bar**: somebody who searches and then reloads, or bookmarks,
 * or shares, should get what they were looking at. `replaceState` keeps the URL
 * honest without adding a history entry per keystroke — the back button would
 * otherwise have to be pressed once for every letter.
 *
 * **No script**: the form still posts to the same place with a real submit
 * button, which is only hidden once this file has run. With JavaScript off the
 * page behaves exactly as it did.
 */
(function () {
  const form = document.querySelector("[data-live-search]");
  if (!form) return;
  // Looked up per swap, never held. `replaceWith` leaves the old node detached,
  // so a reference taken once here would point at something that is no longer
  // on the page — and the second keystroke would appear to do nothing.
  const findResults = () => document.querySelector("[data-live-results]");
  if (!findResults()) return;

  const WAIT = 180;
  let timer = null;
  let serial = 0;

  // Hidden rather than removed: the markup is what a page without this file
  // needs, and taking the button out of the DOM would also take it out of the
  // tab order in a way that is harder to undo.
  form.querySelectorAll("[data-live-submit]").forEach((el) => { el.hidden = true; });
  form.classList.add("is-live");

  function url() {
    const params = new URLSearchParams(new FormData(form));
    // Blank fields would otherwise pile up as "?q=&tag=&order=" — noise in the
    // address bar and in anything anybody copies out of it.
    Array.from(params.keys()).forEach((key) => {
      if (!params.get(key)) params.delete(key);
    });
    const query = params.toString();
    const base = form.getAttribute("action") || location.pathname;
    return base + (query ? "?" + query : "");
  }

  function run() {
    const mine = (serial += 1);
    const target = url();
    form.classList.add("is-searching");
    fetch(target, { headers: { "X-Requested-With": "fetch" } })
      .then((response) => response.text())
      .then((html) => {
        // A slower earlier request landing late must not overwrite a newer
        // answer — the classic "meh" arriving after "mehl".
        if (mine !== serial) return;
        const parsed = new DOMParser().parseFromString(html, "text/html");
        const fresh = parsed.querySelector("[data-live-results]");
        const here = findResults();
        if (fresh && here) here.replaceWith(fresh);
        window.history.replaceState(null, "", target);
      })
      .catch(() => {
        /* Offline, or the server said no. The page keeps what it has, and the
           form still submits normally. */
      })
      .then(() => {
        if (mine === serial) form.classList.remove("is-searching");
      });
  }

  form.addEventListener("input", () => {
    if (timer) window.clearTimeout(timer);
    timer = window.setTimeout(run, WAIT);
  });

  // Enter would submit and reload the page, which is the one thing this exists
  // to avoid. The results are already there.
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    if (timer) window.clearTimeout(timer);
    run();
  });
})();
