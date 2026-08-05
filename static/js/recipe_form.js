/* The two formsets on the recipe form — ingredient lines and diagram steps —
 * and the photograph preview.
 *
 * Adding and removing a row exist because a formset is an **index range**, not
 * a list, and every mistake available here comes from forgetting that:
 *
 * - Adding a row means cloning the last one, renumbering every `name` and `id`
 *   from the old index to the new one, *and* bumping TOTAL_FORMS. Miss the last
 *   step and the new row is simply not read on POST — the page looks right and
 *   the ingredient vanishes on save.
 * - Removing a row means ticking its DELETE box and hiding it. It must never
 *   leave the DOM: a form missing from the POST is a hole in the range, and
 *   Django reads the absent fields against that form's own defaults, concludes
 *   it changed, and validates it. That is how a removed row comes back wearing
 *   "This field is required".
 *
 * One function serves both formsets. The version with the ingredient logic
 * copied and renamed is how the two drift: a fix to one of them (the DELETE
 * handling below took three goes) lands in one copy and not the other, and the
 * broken one is the formset nobody was looking at that week.
 */
(function () {
  window.formsetRows = function formsetRows(config) {
    const container = document.querySelector(config.rows);
    const addButton = document.querySelector(config.add);
    if (!container || !addButton) return null;

    // The prefix comes off a field that is actually in the document, not from a
    // literal here: it is inlineformset_factory's to choose, there are now two
    // of them on this page, and a management form found with a "the first
    // TOTAL_FORMS on the page" selector belongs to whichever formset happens to
    // be rendered first.
    const sample = container.querySelector("input[name], select[name], textarea[name]");
    if (!sample) return null;
    const parts = sample.name.match(/^(.+)-(\d+)-/);
    if (!parts) return null;
    const prefix = parts[1];

    const totalForms = document.querySelector("[name='" + prefix + "-TOTAL_FORMS']");
    if (!totalForms) return null;

    const indexPattern = new RegExp(prefix + "-(\\d+)-");

    function renumber(row, index) {
      row.querySelectorAll("input, select, textarea, label").forEach((el) => {
        ["name", "id", "htmlFor"].forEach((attr) => {
          const value = el[attr];
          if (typeof value === "string" && indexPattern.test(value)) {
            el[attr] = value.replace(indexPattern, prefix + "-" + index + "-");
          }
        });
      });
    }

    addButton.addEventListener("click", () => {
      const rows = container.querySelectorAll(config.row);
      const template = rows[rows.length - 1];
      if (!template) return;

      const clone = template.cloneNode(true);
      const index = parseInt(totalForms.value, 10);

      renumber(clone, index);
      clone.classList.remove("row--deleted");
      // Controls this page built for the row it was copied from — the diagram
      // selects. They are rebuilt from scratch after every change, and a stale
      // clone of one would carry the old row's selected value.
      clone.querySelectorAll("[data-generated]").forEach((el) => el.remove());
      clone.querySelectorAll("input, textarea").forEach((el) => {
        if (el.type === "checkbox") {
          el.checked = false;
        } else {
          // The cloned row must not claim the pk of the row it was copied from,
          // or saving it *moves* that ingredient instead of adding one — and
          // the same goes for the hidden index fields, which would otherwise
          // put the new line into the step the old one was in.
          el.value = "";
        }
      });
      clone.querySelectorAll(".field-error").forEach((el) => { el.textContent = ""; });

      container.appendChild(clone);
      totalForms.value = String(index + 1);
      wireRemove(clone);
      const first = clone.querySelector("input:not([type='hidden'])");
      if (first) first.focus();

      announce();
    });

    function wireRemove(row) {
      const button = row.querySelector(config.remove);
      if (!button) return;
      button.addEventListener("click", () => {
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
          row.querySelectorAll("input, textarea").forEach((el) => {
            if (el.type !== "checkbox" && !el.name.endsWith("-id")) el.value = "";
          });
          if (deleteBox) deleteBox.checked = true;
        }
        row.classList.add("row--deleted");
        announce();
      });
    }

    function announce() {
      // The unsaved-changes guard in shell.js listens for input/change events;
      // adding or removing a row is neither, so it is announced explicitly.
      document.dispatchEvent(new CustomEvent("unsaved-change"));
      // And the diagram editor has to rebuild its selects: the set of rows one
      // of them can point at has just changed.
      document.dispatchEvent(new CustomEvent("formset-rows-changed"));
    }

    container.querySelectorAll(config.row).forEach(wireRemove);

    // A row the server rendered as already deleted (a failed save re-showing
    // the form) stays hidden, so the page comes back looking the way it was
    // left.
    container.querySelectorAll("input[name$='-DELETE']").forEach((box) => {
      if (box.checked) {
        const row = box.closest(config.row);
        if (row) row.classList.add("row--deleted");
      }
    });

    return { container, prefix, totalForms };
  };

  window.formsetRows({
    rows: "[data-ingredient-rows]", add: "[data-ingredient-add]",
    row: "[data-ingredient-row]", remove: "[data-ingredient-remove]",
  });
  window.formsetRows({
    rows: "[data-step-rows]", add: "[data-step-add]",
    row: "[data-step-row]", remove: "[data-step-remove]",
  });
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
