/* Suggesting an ingredient, and the unit it is usually measured in.
 *
 * Two jobs, and the second is the one that earns this file. Offering names is
 * convenience; filling in the *unit* is what keeps the collection comparable —
 * water in millilitres and butter in grams, every time, so that a kilo in the
 * cupboard can answer a recipe asking for 500 g. A household typing units by
 * hand produces "g", "gr" and "Gramm" for one substance within a month, and no
 * amount of matching afterwards recovers from that.
 *
 * ---- where the data comes from ----
 *
 * A `json_script` block on the page, not a search endpoint. A household
 * catalogue is a few hundred rows and a few kilobytes; embedding it means the
 * suggestion appears in the same frame as the keystroke, with no request, no
 * debounce, no race between two in-flight responses, and no new URL to keep
 * behind the login. apps/pantry/catalogue.py::suggestions builds it, and the
 * shape it returns is already what an endpoint would return — so the day this
 * is measured in thousands, only the fetch has to be added.
 *
 * ---- what it will not do ----
 *
 * It never overwrites a unit somebody has chosen. The suggestion fires when the
 * unit is still empty and stays out of the way afterwards, because "2 EL Milch"
 * is a real line and a helper that keeps resetting it to millilitres is one
 * people fight rather than use.
 *
 * It never rewrites the typed name either. Picking a suggestion replaces the
 * text — that was the point of picking it — but simply typing something the
 * catalogue half-recognises leaves it exactly as typed. The recipe says
 * "festkochende Kartoffeln"; the catalogue link says which substance that is;
 * apps/pantry/catalogue.py keeps those two separate on purpose.
 *
 * ---- the popup lives on <body> ----
 *
 * Not inside the field's own container. On the recipe form these inputs sit in
 * cells of a grid that scrolls sideways inside `.builder-scroll`, and a popup
 * parented there is clipped by it — the list appears with its bottom half cut
 * off, or not at all. Positioned against the input's bounding box instead, and
 * repositioned on scroll and resize.
 */
(function () {
  const source = document.getElementById("ingredient-catalogue");
  if (!source) return;

  let CATALOGUE = [];
  try {
    CATALOGUE = JSON.parse(source.textContent) || [];
  } catch (err) {
    // A malformed catalogue must not take the form down with it. Typing still
    // works; only the suggestions are gone.
    return;
  }
  if (!CATALOGUE.length) return;

  const MAX_SHOWN = 8;

  function fold(text) {
    return (text || "").trim().toLowerCase();
  }

  /* Every name a row answers to, so an alias matches without the row having to
     be searched twice. Built once — this runs on every keystroke otherwise. */
  const ENTRIES = [];
  CATALOGUE.forEach((row) => {
    const names = [row.name].concat(row.alt || []);
    ENTRIES.push({ row: row, keys: names.map(fold), names: names });
  });

  // Where a variety is separated from the substance: "Dinkelmehl - Typ 630".
  // A list of separators rather than a regular expression, because
  // config/tests.py checks every script for balanced brackets by stripping
  // quoted strings first — and a regex literal holding "(" is not a quoted
  // string, so it reads as an unclosed paren and fails the whole file.
  const SEPARATORS = [" - ", " – ", " — ", ", ", " (", "/", ","];

  function head(text) {
    // "Dinkelmehl - Typ 630" -> "dinkelmehl". The part before the first dash,
    // comma or bracket is the substance; what follows is the variety.
    const whole = fold(text);
    let cut = -1;
    SEPARATORS.forEach((mark) => {
      const at = whole.indexOf(mark);
      if (at > 0 && (cut === -1 || at < cut)) cut = at;
    });
    return cut === -1 ? "" : whole.slice(0, cut).trim();
  }

  function baseOf(text) {
    /* The catalogue entry a typed name is a *variety of*, or null.
     *
     * Two rules, both narrow on purpose. First the part before a dash or a
     * comma — "Dinkelmehl - Typ 630" is a Dinkelmehl. Then the tail of a German
     * compound: "Dinkelmehl" ends with "mehl", "Buttermilch" ends with
     * "milch", and in German the last word of a compound is what the thing *is*.
     *
     * Four characters minimum, so "Ei" does not claim every name ending in a
     * vowel. And this only ever produces a *suggestion*: picking it links the
     * line to flour and leaves the text saying "Dinkelmehl - Typ 630", because
     * that is what the recipe says and what somebody has to buy. Nothing here
     * matches silently — apps/pantry/catalogue.py stays exact for that reason.
     */
    const needle = fold(text);
    if (!needle) return null;
    const front = head(text);
    for (let i = 0; i < ENTRIES.length; i += 1) {
      const entry = ENTRIES[i];
      for (let k = 0; k < entry.keys.length; k += 1) {
        const key = entry.keys[k];
        if (key.length >= 3 && front && front === key) return entry;
      }
    }
    for (let i = 0; i < ENTRIES.length; i += 1) {
      const entry = ENTRIES[i];
      for (let k = 0; k < entry.keys.length; k += 1) {
        const key = entry.keys[k];
        const stem = front || needle;
        if (key.length >= 4 && stem.length > key.length && stem.endsWith(key)) {
          return entry;
        }
      }
    }
    return null;
  }

  function search(text) {
    const needle = fold(text);
    if (needle.length < 1) return [];
    const starts = [];
    const contains = [];
    for (let i = 0; i < ENTRIES.length; i += 1) {
      const entry = ENTRIES[i];
      let best = -1;
      entry.matched = undefined;
      for (let k = 0; k < entry.keys.length; k += 1) {
        const at = entry.keys[k].indexOf(needle);
        if (at === -1) continue;
        // Which of its names matched is worth keeping: a row found through
        // "Zwiebeln" should say so rather than silently offering "Zwiebel".
        if (best === -1 || at < best) best = at;
        if (at === 0) { entry.matched = entry.names[k]; break; }
        if (entry.matched === undefined) entry.matched = entry.names[k];
      }
      if (best === -1) continue;
      (best === 0 ? starts : contains).push({ row: entry.row, matched: entry.matched });
      if (starts.length >= MAX_SHOWN) break;
    }

    // What somebody typed the beginning of first — that is nearly always what
    // they meant, and burying it under a substring match is how a suggestion
    // list becomes something people press Escape on.
    const found = starts.concat(contains).slice(0, MAX_SHOWN);

    // Nothing matched the name itself, but it may be a variety of something the
    // catalogue knows. Offered last and marked, because taking it keeps the
    // text and changes only what the line points at.
    if (!found.length) {
      const base = baseOf(text);
      if (base) found.push({ row: base.row, matched: base.names[0], variant: true });
    }
    return found;
  }

  /* ---- the popup ------------------------------------------------------- */

  let popup = null;
  let owner = null;      // the input the popup currently belongs to
  let options = [];
  let active = -1;
  let counter = 0;

  function ensurePopup() {
    if (popup) return popup;
    popup = document.createElement("ul");
    popup.className = "suggest-popup";
    popup.setAttribute("role", "listbox");
    popup.hidden = true;
    document.body.appendChild(popup);
    // Pointerdown rather than click: clicking moves focus, and the blur
    // handler below closes the popup before a click event would ever land.
    popup.addEventListener("pointerdown", (event) => {
      const item = event.target.closest("[data-at]");
      if (!item) return;
      event.preventDefault();
      choose(parseInt(item.dataset.at, 10));
    });
    return popup;
  }

  function place() {
    if (!popup || popup.hidden || !owner) return;
    const box = owner.getBoundingClientRect();
    popup.style.left = (box.left + window.scrollX) + "px";
    popup.style.top = (box.bottom + window.scrollY) + "px";
    popup.style.width = box.width + "px";
  }

  function open(input, entries) {
    ensurePopup();
    owner = input;
    options = entries;
    active = -1;
    popup.textContent = "";
    entries.forEach((entry, at) => {
      const item = document.createElement("li");
      item.className = "suggest-option";
      item.id = "suggest-option-" + (counter += 1);
      item.setAttribute("role", "option");
      item.dataset.at = String(at);

      const name = document.createElement("span");
      name.className = "suggest-name";
      name.textContent = entry.row.name;
      item.appendChild(name);

      // Said only when it is not the name itself, so the common row is one
      // word rather than the same word twice.
      if (entry.variant) {
        const via = document.createElement("span");
        via.className = "suggest-via";
        via.textContent = gettext("keep your wording, count it as this");
        item.appendChild(via);
      } else if (entry.matched && fold(entry.matched) !== fold(entry.row.name)) {
        const via = document.createElement("span");
        via.className = "suggest-via";
        via.textContent = entry.matched;
        item.appendChild(via);
      }
      if (entry.row.unit) {
        const unit = document.createElement("span");
        unit.className = "suggest-unit";
        unit.textContent = unitName(entry.row.unit);
        item.appendChild(unit);
      }
      popup.appendChild(item);
    });
    popup.hidden = false;
    input.setAttribute("aria-expanded", "true");
    place();
  }

  function close() {
    if (!popup || popup.hidden) return;
    popup.hidden = true;
    if (owner) {
      owner.setAttribute("aria-expanded", "false");
      owner.removeAttribute("aria-activedescendant");
    }
    owner = null;
    options = [];
    active = -1;
  }

  function highlight(to) {
    const items = popup.querySelectorAll("[data-at]");
    items.forEach((item) => item.classList.remove("is-active"));
    if (to < 0 || to >= items.length) { active = -1; return; }
    active = to;
    items[to].classList.add("is-active");
    // scrollIntoView with block:"nearest" so walking the list with the
    // keyboard does not jump the page under it.
    items[to].scrollIntoView({ block: "nearest" });
    if (owner) owner.setAttribute("aria-activedescendant", items[to].id);
  }

  // Set while a chosen value is being written back, and cleared by the first
  // event that is not ours. Without it the list re-opens the instant it is
  // used: choose() dispatches an `input` event so the unsaved-changes guard
  // and the canvas hear about the new name, the document-level handler below
  // answers that event by searching — and what it searches for is now the
  // exact catalogue name, which of course matches. The popup shut and
  // immediately came back, sitting over whatever was underneath it.
  let justChosen = false;

  function choose(at) {
    const entry = options[at];
    const input = owner;
    if (!entry || !input) return;
    // Picking replaces the typed text — that is what was asked for. Except for
    // a *variety*: "Dinkelmehl - Typ 630" is flour, and the useful outcome is
    // the line pointing at flour while still saying which flour. Overwriting it
    // with "Mehl" would throw away the only part somebody has to read in a
    // shop.
    if (!entry.variant) input.value = entry.row.name;
    applyUnit(input, entry.row);
    setLink(input, entry.row.id);
    close();
    input.focus();
    // The unsaved-changes guard and the canvas both listen for real events, and
    // a value set from script fires neither.
    justChosen = true;
    input.dispatchEvent(new Event("input", { bubbles: true }));
    input.dispatchEvent(new Event("change", { bubbles: true }));
    justChosen = false;
  }

  /* ---- the fields a suggestion writes into ----------------------------- */

  function scopeOf(input) {
    return input.closest("[data-suggest-scope]") || document;
  }

  function unitFieldFor(input) {
    const selector = input.dataset.unitTarget;
    if (selector) return document.querySelector(selector);
    const scope = scopeOf(input);
    return scope.querySelector("select[name$='-unit'], select[name='unit']");
  }

  function linkFieldFor(input) {
    const selector = input.dataset.idTarget;
    if (selector) return document.querySelector(selector);
    return scopeOf(input).querySelector("input[name$='-ingredient']");
  }

  function applyUnit(input, row) {
    const field = unitFieldFor(input);
    if (!field || !row.unit) return;
    // Never over a choice somebody has already made. "2 EL Milch" is a real
    // line and a helper that keeps putting it back to millilitres is one
    // people work around rather than with.
    if (field.value) return;
    const known = Array.from(field.options).some((option) => option.value === row.unit);
    if (!known) return;
    field.value = row.unit;
    field.dispatchEvent(new Event("change", { bubbles: true }));
  }

  function setLink(input, id) {
    const field = linkFieldFor(input);
    if (field) field.value = id === null || id === undefined ? "" : String(id);
  }

  function unitName(code) {
    // Read off a real dropdown rather than duplicated here, so the wording is
    // the one the page uses and is translated by the same catalogue.
    const field = document.querySelector("select[name$='-unit'], select[name='unit']");
    if (!field) return code;
    const option = Array.from(field.options).find((each) => each.value === code);
    return option ? option.textContent.trim() : code;
  }

  /* ---- wiring ----------------------------------------------------------
   *
   * Delegated from the document rather than bound per input. The recipe form
   * mints rows as somebody types and static/js/recipe_diagram.js moves the
   * cards into other cells afterwards; anything bound to "the inputs present
   * at load" quietly stops finding the ones that matter.
   */

  document.addEventListener("input", (event) => {
    const input = event.target;
    if (!input.matches || !input.matches("[data-ingredient-input]")) return;
    // Our own event, fired by choose(). The link is already correct and the
    // list has just been dismissed on purpose.
    if (justChosen) return;
    // The typed name no longer matches the row it was linked to. Clearing the
    // link is what stops "Butter" edited to "Buttermilch" from still counting
    // as butter in the cupboard.
    const link = linkFieldFor(input);
    if (link && link.value) {
      const linked = CATALOGUE.find((row) => String(row.id) === link.value);
      if (!linked || fold(linked.name) !== fold(input.value)) link.value = "";
    }
    const found = search(input.value);
    if (!found.length) { close(); return; }
    open(input, found);
  });

  document.addEventListener("keydown", (event) => {
    const input = event.target;
    if (!input.matches || !input.matches("[data-ingredient-input]")) return;
    if (!popup || popup.hidden || owner !== input) {
      // ArrowDown on a closed field opens it, which is how somebody who has
      // not typed anything sees what is on offer.
      if (event.key === "ArrowDown") {
        const found = search(input.value);
        if (found.length) { open(input, found); highlight(0); event.preventDefault(); }
      }
      return;
    }
    if (event.key === "ArrowDown") {
      event.preventDefault();
      highlight(active + 1 >= options.length ? 0 : active + 1);
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      highlight(active - 1 < 0 ? options.length - 1 : active - 1);
    } else if (event.key === "Enter") {
      // Only when something is actually selected. Otherwise Enter is the
      // submit it has always been — swallowing it would make the form
      // unsubmittable from the keyboard whenever the popup happened to be up.
      if (active !== -1) { event.preventDefault(); choose(active); }
    } else if (event.key === "Escape") {
      event.preventDefault();
      close();
    } else if (event.key === "Tab") {
      close();
    }
  });

  document.addEventListener("focusout", (event) => {
    if (event.target === owner) {
      // Deferred: the pointerdown on an option lands before this would close
      // the list, but a focus moving elsewhere has to close it.
      window.setTimeout(() => {
        if (owner && document.activeElement !== owner) close();
      }, 0);
    }
  });

  window.addEventListener("resize", place);
  window.addEventListener("scroll", place, true);

  /* On load, link every name that is already an exact catalogue match. An edit
     page rendered from rows saved before the catalogue existed then posts back
     with the links filled in, and the recipe starts taking part in the pantry
     matching without anybody re-typing it. */
  function linkExisting() {
    const byName = new Map();
    CATALOGUE.forEach((row) => {
      byName.set(fold(row.name), row);
      (row.alt || []).forEach((name) => {
        if (!byName.has(fold(name))) byName.set(fold(name), row);
      });
    });
    document.querySelectorAll("[data-ingredient-input]").forEach((input) => {
      const link = linkFieldFor(input);
      if (!link || link.value || !input.value) return;
      const row = byName.get(fold(input.value));
      if (row) link.value = String(row.id);
    });
  }

  linkExisting();
  document.addEventListener("formset-rows-changed", linkExisting);
})();
