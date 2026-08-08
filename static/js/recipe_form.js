/* The two formsets on the recipe form — ingredient lines and diagram steps —
 * and the photograph preview.
 *
 * Adding and removing a row exist because a formset is an **index range**, not
 * a list, and every mistake available here comes from forgetting that:
 *
 * - Adding a row means minting one at the next free index — every `name` and
 *   `id` carrying that number — *and* bumping TOTAL_FORMS. Miss the last step
 *   and the new row is simply not read on POST: the page looks right and the
 *   ingredient vanishes on save.
 * - Removing a row means ticking its DELETE box and hiding it. It must never
 *   leave the DOM: a form missing from the POST is a hole in the range, and
 *   Django reads the absent fields against that form's own defaults, concludes
 *   it changed, and validates it. That is how a removed row comes back wearing
 *   "This field is required".
 *
 * The new row comes from a <template> holding the formset's `empty_form`,
 * rather than from a clone of the last card. A clone starts as whatever that
 * card happened to be — its pk, its place in the diagram, its error messages,
 * the controls the canvas built inside it — and every one of those had to be
 * found and cleared. `__prefix__` replaced by a number has none of that state
 * to begin with.
 *
 * Removal is delegated from the document rather than bound per row, because a
 * card does not stay where it was rendered: static/js/recipe_diagram.js moves
 * it into the cell it belongs in, and moving an element with its own listener
 * still works while binding to "the rows in this container" quietly stops
 * finding any.
 *
 * One function serves both formsets. The version with the ingredient logic
 * copied and renamed is how the two drift: a fix to one of them (the DELETE
 * handling below took three goes) lands in one copy and not the other, and the
 * broken one is the formset nobody was looking at that week.
 */
(function () {
  window.formsetRows = function formsetRows(config) {
    const pool = document.querySelector(config.pool);
    const template = document.querySelector(config.template);
    // `add` is optional. The steps formset does not name one: what "+ Step"
    // has to do depends on the shape of the diagram — it joins whatever is
    // currently unfinished into one new final step — and that lives in
    // recipe_diagram.js, which owns the model. A plain append would put a
    // parentless root at the bottom, which draws as a band across the top.
    const addButton = config.add ? document.querySelector(config.add) : null;
    if (!pool || !template || (config.add && !addButton)) return null;

    // The prefix is read out of the blank form, not written as a literal here:
    // it is inlineformset_factory's to choose, there are two formsets on this
    // page, and a management form found with a "the first TOTAL_FORMS on the
    // page" selector belongs to whichever of them rendered first.
    //
    // Read off the template's parsed content rather than out of its markup: a
    // regex over the HTML would have to contain a quote, and this file is
    // checked for balanced brackets by a test that strips quoted strings first
    // — which turns a pattern like that into a stray closing paren.
    const sample = template.content.querySelector("[name*='__prefix__']");
    const parts = sample && sample.name.match(/^(.+)-__prefix__-/);
    if (!parts) return null;
    const prefix = parts[1];

    const totalForms = document.querySelector("[name='" + prefix + "-TOTAL_FORMS']");
    if (!totalForms) return null;

    function add() {
      const index = parseInt(totalForms.value, 10);
      if (!Number.isFinite(index)) return null;

      const holder = document.createElement("div");
      // split/join rather than replace(): a string argument to replace() swaps
      // the *first* occurrence only, and a row carries __prefix__ a dozen times
      // over — the ones left behind would post under a form that does not exist.
      holder.innerHTML = template.innerHTML.split("__prefix__").join(String(index));
      const row = holder.querySelector(config.row);
      if (!row) return null;

      pool.appendChild(row);
      // Last, and only once the row is really in the document: TOTAL_FORMS is
      // the promise that every index below it is present, and a row that failed
      // to build would make that a lie.
      totalForms.value = String(index + 1);
      announce();
      return row;
    }

    if (addButton) {
      addButton.addEventListener("click", () => {
        const row = add();
        if (!row) return;
        const first = row.querySelector("input:not([type='hidden']), textarea");
        if (first) first.focus();
      });
    }

    document.addEventListener("click", (event) => {
      const button = event.target.closest(config.remove);
      if (!button) return;
      const row = button.closest(config.row);
      if (!row) return;

      const deleteBox = row.querySelector("input[name$='-DELETE']");
      const isSaved = (row.querySelector("input[name$='-id']") || {}).value;
      if (deleteBox && isSaved) {
        // An existing row: tick DELETE and hide. The formset skips a row
        // marked for deletion in both validation and save.
        deleteBox.checked = true;
      } else {
        // A row that was never saved has nothing to delete, so clearing it is
        // enough — and leaves the index range intact, which removing the
        // element would not.
        //
        // Everything *except* where the row sat. A removed row is still read:
        // whatever fed it names it by index, and following that reference on to
        // the step this one fed is what makes deleting a step splice it out of
        // the chain rather than detach everything above it. Blanking these
        // fields threw that away, which is why the bug only showed on a step
        // that had never been saved. They report `has_changed` as False either
        // way (apps/recipes/forms.py::_StructureField), so leaving them cannot
        // make an untouched row look edited.
        const structure = ["-parent_index", "-step_index", "-alt_index",
                           "-position", "-span_from", "-span_to"];
        row.querySelectorAll("input, textarea").forEach((el) => {
          if (el.type === "checkbox" || el.name.endsWith("-id")) return;
          if (structure.some((suffix) => el.name.endsWith(suffix))) return;
          el.value = "";
        });
        if (deleteBox) deleteBox.checked = true;
      }
      row.classList.add("row--deleted");
      announce();
    });

    function announce() {
      // The unsaved-changes guard in shell.js listens for input/change events;
      // adding or removing a row is neither, so it is announced explicitly.
      document.dispatchEvent(new CustomEvent("unsaved-change"));
      // And the canvas has to lay itself out again: it has one more cell to
      // place, or one fewer.
      document.dispatchEvent(new CustomEvent("formset-rows-changed"));
    }

    // A row the server rendered as already deleted (a failed save re-showing
    // the form) stays hidden, so the page comes back looking the way it was
    // left.
    pool.querySelectorAll("input[name$='-DELETE']").forEach((box) => {
      if (box.checked) {
        const row = box.closest(config.row);
        if (row) row.classList.add("row--deleted");
      }
    });

    return { pool, prefix, totalForms, add };
  };

  window.recipeFormsets = {
    lines: window.formsetRows({
      pool: "[data-line-pool]", add: "[data-ingredient-add]",
      template: "[data-ingredient-template]",
      row: "[data-ingredient-row]", remove: "[data-ingredient-remove]",
    }),
    steps: window.formsetRows({
      // No `add`: recipe_diagram.js binds "+ Step", because what it should do
      // is a question about the diagram. See addJoiningStep there.
      pool: "[data-step-pool]",
      template: "[data-step-template]",
      row: "[data-step-row]", remove: "[data-step-remove]",
    }),
  };
})();

/* A preview of the photograph that is about to be uploaded.
 *
 * Purely so somebody can see they picked the right file — the browser otherwise
 * shows a filename, and a phone's camera roll filenames are all IMG_4471.
 * `createObjectURL`, not a FileReader data URL, because the file can be several
 * megabytes and there is no reason to base64 it into memory to look at it.
 */
(function () {
  const input = document.querySelector("input[type='file'][name='image']");
  if (!input) return;

  const preview = document.createElement("div");
  preview.className = "image-preview";
  const img = document.createElement("img");
  preview.appendChild(img);
  preview.hidden = true;
  input.parentNode.appendChild(preview);

  let objectUrl = null;
  input.addEventListener("change", () => {
    if (objectUrl) URL.revokeObjectURL(objectUrl);
    const file = input.files && input.files[0];
    if (!file) { preview.hidden = true; return; }
    objectUrl = URL.createObjectURL(file);
    img.src = objectUrl;
    img.alt = file.name;
    preview.hidden = false;
  });
})();
