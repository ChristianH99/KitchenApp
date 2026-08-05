/* The app shell — what base.html carries on every page: the modal machinery
 * (focus management, and the app's own confirm/alert), the unsaved-changes
 * guard, data-confirm forms, the page help pop-up, the language selector, the
 * sidebar toggle and the dismissable messages.
 *
 * A file rather than inline <script> blocks, and that is not a style
 * preference. The app ships a Content-Security-Policy with `script-src 'self'`
 * and no nonce, because a CSP has no way to allow *our* inline script without
 * also allowing injected inline script — which is the whole thing it exists to
 * stop. See config/csp.py.
 */

// Page data handed over by a template through `json_script`. Reads
// defensively: a missing element means the page did not render its data, and
// with `getElementById(...).textContent` that is a TypeError thrown *before*
// the caller's own "nothing to do here" guard can run — which turns a page that
// should quietly do nothing into one with a dead script.
window.pageData = function pageData(id) {
  const el = document.getElementById(id);
  if (!el) return null;
  try {
    return JSON.parse(el.textContent);
  } catch (err) {
    return null;
  }
};

// The CSRF token, for pages that POST with fetch().
//
// Read from the cookie rather than from a token rendered into the page: a .js
// file cannot be handed a template variable, and settings.py keeps
// CSRF_COOKIE_HTTPONLY false for exactly this. Called at use time, not at load
// time — the cookie is rotated on login, and a page left open across one would
// otherwise hold a stale token for as long as it stayed open.
window.csrfToken = function csrfToken() {
  const match = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]*)/);
  return match ? decodeURIComponent(match[1]) : "";
};

/* ---- modals -------------------------------------------------------------
 *
 * Every dialog goes through window.modalController. The version where each one
 * hides and shows its own overlay looks right and behaves wrongly:
 * `aria-modal="true"` sits on the markup while nothing moves focus into the
 * dialog, keeps it there, or gives it back afterwards. Tab walks straight
 * through to the page behind the overlay — so a keyboard user can type into a
 * form they cannot see, and the button they pressed loses its place for good.
 */
const FOCUSABLE = [
  "a[href]", "button:not([disabled])", "input:not([disabled])",
  "select:not([disabled])", "textarea:not([disabled])", "[tabindex]",
].join(",");

window.modalController = function modalController(modal, options) {
  const opts = options || {};
  let lastFocused = null;

  const focusable = () =>
    Array.from(modal.querySelectorAll(FOCUSABLE))
      .filter((el) => el.tabIndex !== -1 && el.offsetParent !== null);

  function focusIn() {
    // The first control, or the dialog itself when it holds none (help text).
    const first = focusable()[0];
    if (first) {
      first.focus();
    } else {
      const panel = modal.querySelector(".modal") || modal;
      panel.tabIndex = -1;
      panel.focus();
    }
  }

  function open() {
    if (!modal.hidden) return;
    lastFocused = document.activeElement;
    modal.hidden = false;
    document.body.classList.add("modal-open");
    focusIn();
  }

  function close() {
    if (modal.hidden) return;
    modal.hidden = true;
    document.body.classList.remove("modal-open");
    // Back where they were. A dialog opened from a list row is the case that
    // matters: without this, focus resets to the top of the document and the
    // row they were working on is however many tabs away.
    if (lastFocused && document.contains(lastFocused)) lastFocused.focus();
    lastFocused = null;
    if (opts.onClose) opts.onClose();
  }

  modal.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      event.stopPropagation();
      close();
      return;
    }
    if (event.key !== "Tab") return;
    const items = focusable();
    if (!items.length) { event.preventDefault(); return; }
    const first = items[0];
    const last = items[items.length - 1];
    // Wrap, rather than letting Tab walk out of the dialog and behind it.
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  });

  if (opts.backdropCloses !== false) {
    modal.addEventListener("click", (event) => {
      if (event.target === modal) close();
    });
  }

  // A dialog the *server* rendered already open never went through open(), so
  // it would otherwise be the one kind with no focus in it at all — which is
  // the worst case: a question on screen and the keyboard somewhere behind the
  // overlay.
  if (!modal.hidden) {
    document.body.classList.add("modal-open");
    focusIn();
  }

  return { open, close, element: modal, isOpen: () => !modal.hidden };
};

/* The app's own confirm() and alert(), in the page's styling and language.
 *
 * Both return a promise, so a caller reads the way the built-ins did:
 *
 *     if (!(await window.appConfirm({ body: "…" }))) return;
 */
(function () {
  const modal = document.querySelector("[data-app-dialog]");
  if (!modal) return;
  const title = modal.querySelector("[data-dialog-title]");
  const body = modal.querySelector("[data-dialog-body]");
  const accept = modal.querySelector("[data-dialog-accept]");
  const cancel = modal.querySelector("[data-dialog-cancel]");
  let settle = null;

  const finish = (answer) => {
    const resolve = settle;
    settle = null;
    controller.close();
    if (resolve) resolve(answer);
  };

  // Closing by Escape or the backdrop is a "no", the same as Cancel.
  const controller = window.modalController(modal, { onClose: () => {
    if (settle) { const resolve = settle; settle = null; resolve(false); }
  } });

  accept.addEventListener("click", () => finish(true));
  cancel.addEventListener("click", () => finish(false));

  function show(config) {
    title.textContent = config.title || "";
    title.hidden = !config.title;
    // Each paragraph its own <p>. The built-in dialogs took one string with
    // newlines in it, which is why every caller of those ends up writing "\n\n".
    body.textContent = "";
    String(config.body || "").split("\n").forEach((line) => {
      if (!line.trim()) return;
      const p = document.createElement("p");
      p.textContent = line;
      body.appendChild(p);
    });
    accept.textContent = config.accept || gettext("OK");
    accept.className = config.danger ? "button button--danger" : "button";
    cancel.hidden = !config.cancellable;
    return new Promise((resolve) => { settle = resolve; controller.open(); });
  }

  window.appConfirm = (config) =>
    show(Object.assign({ cancellable: true, accept: gettext("Continue") }, config));
  window.appAlert = (config) =>
    show(Object.assign({ cancellable: false, accept: gettext("OK") }, config)).then(() => undefined);
})();

/* Guard against leaving a page with unsaved changes.
 *
 * Opt in with data-unsaved-guard on the form. Pages fire an "unsaved-change"
 * event for mutations that are not a plain input/change (adding a formset row,
 * removing one).
 */
(function () {
  const form = document.querySelector("form[data-unsaved-guard]");
  const modal = document.getElementById("unsaved-modal");
  if (!form || !modal) return;

  let dirty = false;
  let bypass = false;      // set once we intend to navigate (save/discard/submit)
  let pendingUrl = null;

  // Controls inside a data-no-dirty region do not change what gets saved
  // (previews, view-only switches), so they must not count as a change.
  const markDirty = (event) => {
    const target = event && event.target;
    if (target && target.closest && target.closest("[data-no-dirty]")) return;
    dirty = true;
  };
  form.addEventListener("input", markDirty);
  form.addEventListener("change", markDirty);
  document.addEventListener("unsaved-change", markDirty);
  form.addEventListener("submit", () => { bypass = true; });

  const controller = window.modalController(modal, { onClose: () => { pendingUrl = null; } });

  document.addEventListener("click", (event) => {
    const link = event.target.closest("a[href]");
    if (!link || bypass || !dirty) return;
    if (link.target === "_blank" || link.hasAttribute("download")) return;
    const href = link.getAttribute("href");
    if (!href || href.startsWith("#") || href.startsWith("mailto:")) return;
    const dest = new URL(link.href, window.location.href);
    if (dest.origin !== window.location.origin) return;  // only guard in-app nav
    event.preventDefault();
    pendingUrl = link.href;
    controller.open();
  });

  /* Closing the tab, and the Back button.
   *
   * The in-page dialog above can only catch a click on a link we render. A tab
   * being closed is the one case a page cannot draw its own dialog for — the
   * browser's is the only thing that can stop it, and the string is ignored by
   * every browser and has been for years.
   */
  window.addEventListener("beforeunload", (event) => {
    if (!dirty || bypass) return;
    event.preventDefault();
    event.returnValue = "";
  });

  modal.querySelector("[data-unsaved-cancel]").addEventListener("click", () => controller.close());
  modal.querySelector("[data-unsaved-discard]").addEventListener("click", () => {
    bypass = true;
    const url = pendingUrl;
    controller.close();
    if (url) window.location.href = url;
  });
  modal.querySelector("[data-unsaved-save]").addEventListener("click", () => {
    bypass = true;
    controller.close();
    // requestSubmit, not submit: form.submit() skips HTML5 constraint
    // validation *and* every submit listener, so "Save" would post a form the
    // Save button itself would have refused.
    if (form.requestSubmit) form.requestSubmit();
    else form.submit();
  });
})();

// Page help: any [data-help-open] button opens the page's [data-help-modal].
(function () {
  const openers = document.querySelectorAll("[data-help-open]");
  const modal = document.querySelector("[data-help-modal]");
  if (!openers.length || !modal) return;
  const controller = window.modalController(modal);
  openers.forEach((btn) => btn.addEventListener("click", controller.open));
  modal.addEventListener("click", (event) => {
    if (event.target.closest("[data-help-close]")) controller.close();
  });
})();

/* Any form carrying data-confirm asks before it submits — through the app's own
 * dialog, not the browser's. Replaces the inline onsubmit="return confirm(…)"
 * attribute, which a Content-Security-Policy has no way to allow.
 *
 * The answer arrives a turn later than window.confirm's did, so the submit is
 * always stopped and re-issued once somebody has said yes. `agreed` is what
 * stops the second pass asking again.
 */
(function () {
  document.querySelectorAll("form[data-confirm]").forEach((form) => {
    let agreed = false;
    form.addEventListener("submit", async (event) => {
      if (agreed) return;
      event.preventDefault();
      const ok = await window.appConfirm({
        title: form.dataset.confirmTitle || "",
        body: form.dataset.confirm,
        accept: form.dataset.confirmAccept || gettext("Continue"),
        danger: form.matches("[data-confirm-danger]"),
      });
      if (!ok) return;
      agreed = true;
      if (form.requestSubmit) form.requestSubmit();
      else form.submit();
    });
  });
})();

// Topbar language selector: toggle the menu; close on outside click or Escape.
(function () {
  const root = document.querySelector("[data-lang-switcher]");
  if (!root) return;
  const toggle = root.querySelector("[data-lang-toggle]");
  const menu = root.querySelector("[data-lang-menu]");
  if (!toggle || !menu) return;

  const open = () => { menu.hidden = false; toggle.setAttribute("aria-expanded", "true"); };
  const close = () => { menu.hidden = true; toggle.setAttribute("aria-expanded", "false"); };

  toggle.addEventListener("click", (event) => {
    event.stopPropagation();
    if (menu.hidden) open(); else close();
  });
  document.addEventListener("click", (event) => {
    if (!menu.hidden && !root.contains(event.target)) close();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !menu.hidden) close();
  });
})();

// The sidebar toggle.
(function () {
  const shell = document.querySelector(".shell");
  const overlay = document.getElementById("shell-overlay");
  const toggle = document.getElementById("shell-hamburger");
  if (!shell || !overlay || !toggle) return;

  const isMobile = () => window.matchMedia("(max-width: 900px)").matches;
  const STORE = "kitchen.sidebar-collapsed";

  // Collapsing is a preference about the whole app, not about one page. Left
  // unstored it resets on every navigation, so somebody who wanted the width
  // has to collapse it again on each page they open. Desktop only: on a phone
  // the sidebar is off-canvas anyway and opening it is a momentary thing.
  let stored = null;
  try { stored = window.localStorage.getItem(STORE); } catch (err) { stored = null; }
  if (stored === "1") shell.classList.add("sidebar-collapsed");

  // "Expanded" is derived differently per viewport, which is also why the
  // attribute is not rendered by the template: the server cannot know which
  // one it is about to be read on.
  function syncAria() {
    const shown = isMobile()
      ? shell.classList.contains("sidebar-open")
      : !shell.classList.contains("sidebar-collapsed");
    toggle.setAttribute("aria-expanded", String(shown));
  }

  toggle.addEventListener("click", () => {
    if (isMobile()) {
      shell.classList.toggle("sidebar-open");
    } else {
      const collapsed = shell.classList.toggle("sidebar-collapsed");
      try { window.localStorage.setItem(STORE, collapsed ? "1" : "0"); } catch (err) { /* private mode */ }
    }
    syncAria();
  });
  overlay.addEventListener("click", () => {
    shell.classList.remove("sidebar-open");
    syncAria();
  });
  window.addEventListener("resize", syncAria);
  syncAria();
})();

/* Django's messages — "“Kartoffelsalat” was saved".
 *
 * As a plain <ul> a screen reader is never told anything happened: the page
 * simply has one more list on it than before. role="status" (in base.html)
 * announces politely, and each message gets a dismiss button — they otherwise
 * sit there until the next navigation, above the very form somebody is using.
 */
(function () {
  document.querySelectorAll(".messages .notice").forEach((notice) => {
    const close = document.createElement("button");
    close.type = "button";
    close.className = "notice-dismiss";
    close.setAttribute("aria-label", gettext("Dismiss"));
    close.textContent = "×";
    close.addEventListener("click", () => notice.remove());
    notice.appendChild(close);
  });
})();
