/* Add and remove rows of a short formset, with a "+" and an "×".
 *
 * For the lists on the ingredient page — the other names a substance goes by,
 * and the sizes it is sold in. Neither is a canvas and neither has geometry;
 * they are a handful of like things, and the number of them is whatever the
 * household needs rather than whatever the server decided to render. That is
 * the whole reason this exists: the page used to ship two blank rows of each,
 * on every ingredient, most of which have one or none.
 *
 * ---- the formset trap, again ----
 *
 * A formset is an **index range**, not a list, and everything that can go wrong
 * here comes from forgetting it:
 *
 *   * adding means minting a row at the next free index — every `name` and `id`
 *     carrying that number — *and* bumping TOTAL_FORMS. Miss the last step and
 *     the row is simply not read on POST: the page looks right and the value
 *     vanishes on save.
 *   * removing an existing row means ticking its DELETE box and hiding it. It
 *     must never leave the DOM: a form missing from the POST is a hole in the
 *     range, and Django reads the absent fields against that form's own
 *     defaults, concludes it changed, and validates it. That is how a removed
 *     row comes back wearing "This field is required".
 *
 * A row that was never saved has no pk to delete, so clearing it and taking it
 * out of TOTAL_FORMS *is* safe — but only when it is the last one, or the
 * range gets a hole. Simpler to treat both the same way, which is what this
 * does.
 *
 * static/js/recipe_form.js does the same job for the recipe's two formsets. It
 * is not shared because that one also has to tell the canvas to lay itself out
 * again, and the shared version was mostly a parameter saying which half to
 * skip.
 */
(function () {
  document.querySelectorAll("[data-rows-add]").forEach((addButton) => {
    const name = addButton.dataset.rowsAdd;
    const list = document.querySelector('[data-rows="' + name + '"]');
    const template = document.querySelector('[data-rows-template="' + name + '"]');
    if (!list || !template) return;

    // The prefix is read out of the blank form rather than written as a literal
    // here: it is inlineformset_factory's to choose, there are two formsets on
    // this page, and a management form found with a "the first TOTAL_FORMS on
    // the page" selector belongs to whichever rendered first.
    const sample = template.content.querySelector("[name*='__prefix__']");
    const parts = sample && sample.name.match(/^(.+)-__prefix__-/);
    if (!parts) return;
    const totalForms = document.querySelector("[name='" + parts[1] + "-TOTAL_FORMS']");
    if (!totalForms) return;

    addButton.addEventListener("click", () => {
      const index = parseInt(totalForms.value, 10);
      if (!Number.isFinite(index)) return;

      const holder = document.createElement("tbody");
      // split/join rather than replace(): a string argument to replace() swaps
      // the *first* occurrence only, and a row carries __prefix__ a dozen times
      // over — the ones left behind would post under a form that does not exist.
      holder.innerHTML = template.innerHTML.split("__prefix__").join(String(index));
      const row = holder.querySelector("[data-row]");
      if (!row) return;

      list.appendChild(row);
      // Last, and only once the row is really in the document: TOTAL_FORMS is
      // the promise that every index below it is present, and a row that failed
      // to build would make that a lie.
      totalForms.value = String(index + 1);
      document.dispatchEvent(new CustomEvent("unsaved-change"));

      const first = row.querySelector("input:not([type='hidden']):not([type='checkbox']), select");
      if (first) first.focus();
    });
  });

  // Delegated from the document rather than bound per row, so a row added a
  // moment ago answers the same way as one the server rendered.
  document.addEventListener("click", (event) => {
    const button = event.target.closest("[data-row-remove]");
    if (!button) return;
    const row = button.closest("[data-row]");
    if (!row) return;

    const deleteBox = row.querySelector("input[name$='-DELETE']");
    if (deleteBox) deleteBox.checked = true;
    // Cleared as well as ticked. A blank row that is *also* marked for deletion
    // cannot fail validation on the way out whichever branch Django takes.
    row.querySelectorAll("input, select, textarea").forEach((field) => {
      if (field.type !== "checkbox" && !field.name.endsWith("-id")) field.value = "";
    });
    row.hidden = true;
    document.dispatchEvent(new CustomEvent("unsaved-change"));
  });

  // A row the server rendered as already deleted — a failed save re-showing the
  // form — stays hidden, so the page comes back looking the way it was left.
  document.querySelectorAll("[data-row] input[name$='-DELETE']").forEach((box) => {
    if (box.checked) {
      const row = box.closest("[data-row]");
      if (row) row.hidden = true;
    }
  });
})();
