/* The ingredient formset: adding a line, and removing one.
 *
 * Both operations exist because a formset is an **index range**, not a list,
 * and every mistake available here comes from forgetting that:
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
 */
(function () {
  const container = document.querySelector("[data-ingredient-rows]");
  const addButton = document.querySelector("[data-ingredient-add]");
  if (!container || !addButton) return;

  const totalForms = document.querySelector("[name$='-TOTAL_FORMS']");
  if (!totalForms) return;

  // The formset's prefix, taken from the management form rather than hardcoded:
  // it is inlineformset_factory's to choose, and a rename in forms.py should not
  // silently stop this script working.
  const prefix = totalForms.name.replace(/-TOTAL_FORMS$/, "");
  const indexPattern = new RegExp(`${prefix}-(\\d+)-`);

  function renumber(row, index) {
    row.querySelectorAll("input, select, textarea, label").forEach((el) => {
      ["name", "id", "htmlFor"].forEach((attr) => {
        const value = el[attr];
        if (typeof value === "string" && indexPattern.test(value)) {
          el[attr] = value.replace(indexPattern, `${prefix}-${index}-`);
        }
      });
    });
  }

  addButton.addEventListener("click", () => {
    const rows = container.querySelectorAll("[data-ingredient-row]");
    const template = rows[rows.length - 1];
    if (!template) return;

    const clone = template.cloneNode(true);
    const index = parseInt(totalForms.value, 10);

    renumber(clone, index);
    clone.classList.remove("ingredient-row--deleted");
    clone.querySelectorAll("input, textarea").forEach((el) => {
      if (el.type === "checkbox") {
        el.checked = false;
      } else if (el.name.endsWith("-id")) {
        // The cloned row must not claim the pk of the row it was copied from,
        // or saving it *moves* that ingredient instead of adding one.
        el.value = "";
      } else {
        el.value = "";
      }
    });
    clone.querySelectorAll(".field-error").forEach((el) => { el.textContent = ""; });

    container.appendChild(clone);
    totalForms.value = String(index + 1);
    wireRemove(clone);
    const first = clone.querySelector("input");
    if (first) first.focus();

    // The unsaved-changes guard in shell.js listens for input/change events;
    // adding a row is neither, so it is announced explicitly.
    document.dispatchEvent(new CustomEvent("unsaved-change"));
  });

  function wireRemove(row) {
    const button = row.querySelector("[data-ingredient-remove]");
    if (!button) return;
    button.addEventListener("click", () => {
      const deleteBox = row.querySelector("input[name$='-DELETE']");
      const isSaved = (row.querySelector("input[name$='-id']") || {}).value;
      if (deleteBox && isSaved) {
        // An existing ingredient: tick DELETE and hide. The formset skips a
        // row marked for deletion in both validation and save.
        deleteBox.checked = true;
        row.classList.add("ingredient-row--deleted");
      } else {
        // A row that was never saved has nothing to delete, so clearing it is
        // enough — and leaves the index range intact, which removing the
        // element would not.
        row.querySelectorAll("input, textarea").forEach((el) => {
          if (el.type !== "checkbox" && !el.name.endsWith("-id")) el.value = "";
        });
        row.classList.add("ingredient-row--deleted");
        if (deleteBox) deleteBox.checked = true;
      }
      document.dispatchEvent(new CustomEvent("unsaved-change"));
    });
  }

  container.querySelectorAll("[data-ingredient-row]").forEach(wireRemove);

  // A row the server rendered as already deleted (a failed save re-showing the
  // form) stays hidden, so the page comes back looking the way it was left.
  container.querySelectorAll("input[name$='-DELETE']").forEach((box) => {
    if (box.checked) box.closest("[data-ingredient-row]").classList.add("ingredient-row--deleted");
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
