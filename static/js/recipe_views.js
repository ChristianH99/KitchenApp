/* The two switches on a recipe page: Preparing/Cooking, and Steps/Diagram.
 *
 * Both are the same mechanism, so they are one function called twice. The
 * copied-and-renamed version is where a fix to one of them lands in one copy —
 * and the broken one is whichever switch nobody pressed that week.
 *
 * ---- why both panels are in the markup ----
 *
 * Neither switch is a navigation. Somebody scales a recipe from four servings
 * to six and then wants to see the method; a round trip there would put the
 * servings back to four, because the scaling lives in the page. So both panels
 * are rendered and one is hidden, and static/js/recipe_scale.js rewrites every
 * `.ingredient-amount` on the page — in the hidden one too, which is why
 * switching to it never shows a stale number.
 *
 * ---- and why the choice is not remembered ----
 *
 * Unlike the editor's Steps/Diagram switch, which is a statement about how
 * somebody works. This one is a statement about *what they are doing right
 * now*: opening a recipe is nearly always "what do I need", and coming back to
 * a page that starts on the method because of something done last Tuesday is
 * an answer to a question nobody asked twice.
 *
 * The exception is the method switch, which is remembered — inside the cooking
 * panel the choice between a list and a diagram really is a preference, and it
 * is the same choice the editor offers.
 */
(function () {
  function wire(groupAttr, buttonAttr, panelAttr, storageKey) {
    const group = document.querySelector("[" + groupAttr + "]");
    if (!group) return;

    const buttons = Array.from(group.querySelectorAll("[" + buttonAttr + "]"));
    const panels = Array.from(document.querySelectorAll("[" + panelAttr + "]"));
    if (!buttons.length || !panels.length) return;

    const nameOf = (el, attr) => el.getAttribute(attr);

    function show(which, remember) {
      let matched = false;
      panels.forEach((panel) => {
        const on = nameOf(panel, panelAttr) === which;
        panel.hidden = !on;
        if (on) matched = true;
      });
      // A stored value naming a panel this recipe does not have — a method
      // view remembered from a recipe that had steps, opened on one that does
      // not. Fall back rather than hiding everything.
      if (!matched) {
        show(nameOf(panels[0], panelAttr), false);
        return;
      }
      buttons.forEach((button) => {
        const on = nameOf(button, buttonAttr) === which;
        button.classList.toggle("is-on", on);
        button.setAttribute("aria-pressed", on ? "true" : "false");
      });
      if (remember && storageKey) {
        try {
          window.localStorage.setItem(storageKey, which);
        } catch (err) {
          /* storage off; the choice simply does not outlive the page */
        }
      }
    }

    let start = nameOf(buttons[0], buttonAttr);
    if (storageKey) {
      try {
        const stored = window.localStorage.getItem(storageKey);
        if (stored && buttons.some((b) => nameOf(b, buttonAttr) === stored)) start = stored;
      } catch (err) {
        /* the first button stands */
      }
    }

    buttons.forEach((button) => {
      button.addEventListener("click", () => show(nameOf(button, buttonAttr), true));
    });
    show(start, false);
  }

  wire("data-recipe-views", "data-recipe-view", "data-recipe-panel", null);
  wire("data-method-views", "data-method-view", "data-method-panel",
       "kitchen.method-view");
})();
