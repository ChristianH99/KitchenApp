/* Typing into a unit dropdown picks the *closest* option, not the first one
 * that happens to share a letter.
 *
 * A browser's own incremental search on a `<select>` walks the options in
 * document order and takes the first whose label starts with what has been
 * typed — then, on a repeated keystroke, cycles to the next one. With the
 * German labels that means "g" can land on "Glas" rather than on "g", which is
 * what the household hit. It also cannot be relied on: the rule differs between
 * browsers, and none of them prefer an *exact* match.
 *
 * So the matching is done here, over the same options, with a rule that says
 * what somebody means:
 *
 *   1. an exact label — typing "g" gets grams, full stop;
 *   2. otherwise the shortest label that starts with it, so "g" would reach
 *      "g" before "Glas" even without rule 1;
 *   3. otherwise the shortest label that contains it, so "ram" still finds
 *      grams;
 *
 * and the typed string resets after a second of silence, the way every other
 * type-ahead does.
 *
 * Only ever *selects* an option that is already there. It cannot invent a
 * value, so a unit the catalogue does not know — carried under "As typed" —
 * stays reachable and stays put.
 */
(function () {
  const RESET_AFTER = 1000;

  function fold(text) {
    return (text || "").trim().toLowerCase();
  }

  function best(select, typed) {
    const needle = fold(typed);
    if (!needle) return null;
    let exact = null;
    let starts = null;
    let holds = null;
    Array.from(select.options).forEach((option) => {
      // The blank option has no label worth matching; picking it by accident
      // would clear the unit.
      if (!option.value) return;
      const label = fold(option.textContent);
      if (label === needle) {
        if (exact === null) exact = option;
        return;
      }
      // Shortest wins, so "g" reaches "g" rather than "Glas" — the shorter
      // label is the one the typed text is a bigger fraction of, which is a
      // decent stand-in for "closest".
      if (label.startsWith(needle)) {
        if (starts === null || label.length < fold(starts.textContent).length) starts = option;
        return;
      }
      if (label.indexOf(needle) !== -1) {
        if (holds === null || label.length < fold(holds.textContent).length) holds = option;
      }
    });
    return exact || starts || holds;
  }

  const typing = new WeakMap();

  document.addEventListener("keydown", (event) => {
    const select = event.target;
    if (!select.matches || !select.matches("select")) return;
    // Only the printable ones. Arrows, Tab and Enter are the browser's to
    // handle, and swallowing them would break keyboard navigation of the list.
    if (event.key.length !== 1 || event.ctrlKey || event.metaKey || event.altKey) return;
    // Not while the list is open in a native popup — the browser is driving
    // then, and two matchers fighting is worse than either.
    if (!select.closest("[data-unit-select]")) return;

    const now = Date.now();
    const state = typing.get(select);
    const typed = (state && now - state.at < RESET_AFTER ? state.text : "") + event.key;
    typing.set(select, { text: typed, at: now });

    const option = best(select, typed);
    if (!option) return;
    event.preventDefault();
    if (select.value === option.value) return;
    select.value = option.value;
    select.dispatchEvent(new Event("change", { bubbles: true }));
  });
})();
