/* Building the diagram on the recipe form.
 *
 * Three hidden fields hold the whole structure — a step's `parent_index`, an
 * ingredient's `step_index` and `alt_index` — and each of them names *another
 * row of this page by its position in the formset*. apps/recipes/forms.py says
 * why indices rather than primary keys: on a recipe being typed in for the
 * first time, nothing has a primary key yet, and a diagram that can only be
 * drawn on the second save is a diagram nobody draws.
 *
 * So the controls have to be built here. A server-rendered <select> would list
 * the rows that existed when the page was sent, which stops being the truth the
 * first time somebody presses "+ Another step" — and stale options in a control
 * that assigns structure is a recipe silently wired to the wrong box.
 *
 * The preview is a **nesting**, not the table. The real geometry — which column
 * an operation lands in, how far each cell has to span to reach its parent —
 * lives in apps/recipes/diagram.py, where it is tested; a second implementation
 * here in a different language would be the thing that quietly disagrees with
 * the page it is previewing. Nested boxes say the same thing the table says
 * ("these go into this, which goes into that") without re-deriving any of it,
 * and the real diagram is one save away.
 */
(function () {
  const stepContainer = document.querySelector("[data-step-rows]");
  const lineContainer = document.querySelector("[data-ingredient-rows]");
  const preview = document.querySelector("[data-diagram-preview]");
  if (!stepContainer || !lineContainer) return;

  function isRemoved(row) {
    const box = row.querySelector("input[name$='-DELETE']");
    return Boolean(box && box.checked);
  }

  function indexOf(row) {
    const field = row.querySelector("input[name], select[name], textarea[name]");
    if (!field) return null;
    const parts = field.name.match(/-(\d+)-/);
    return parts ? parseInt(parts[1], 10) : null;
  }

  function fieldIn(row, suffix) {
    return row.querySelector("input[name$='-" + suffix + "']");
  }

  function collect(container, rowSelector, labelSelector, fallback) {
    const found = [];
    container.querySelectorAll(rowSelector).forEach((row) => {
      if (isRemoved(row)) return;
      const index = indexOf(row);
      if (index === null) return;
      const source = row.querySelector(labelSelector);
      const typed = source ? source.value.trim() : "";
      found.push({
        row, index,
        label: typed || interpolate(fallback, { n: found.length + 1 }, true),
        named: Boolean(typed),
      });
    });
    return found;
  }

  /* ---- the controls -------------------------------------------------- */

  function select(slot, value, options, emptyLabel, onPick) {
    // Rebuilt rather than updated: working out which options changed is more
    // code than making a new one, and a <select> whose options were patched in
    // place keeps a selected value that is no longer among them.
    slot.textContent = "";

    const label = document.createElement("span");
    label.className = "row-extra-label";
    label.textContent = slot.dataset.label || "";

    const field = document.createElement("select");
    field.className = "row-extra-select";
    field.dataset.generated = "1";
    if (slot.dataset.label) field.setAttribute("aria-label", slot.dataset.label);

    const blank = document.createElement("option");
    blank.value = "";
    blank.textContent = emptyLabel;
    field.appendChild(blank);

    options.forEach((option) => {
      const el = document.createElement("option");
      el.value = String(option.index);
      el.textContent = option.label;
      field.appendChild(el);
    });

    // A value pointing at a row that has since been deleted has no option to
    // select, so the control falls back to blank — which is exactly what the
    // server does with the same index (forms.py drops a reference it cannot
    // resolve rather than refusing the save).
    //
    // Matched against the options we just built rather than with a
    // `[value='…']` selector: the value comes out of a hidden input, and one
    // containing a quote would make that selector a SyntaxError — which takes
    // the whole diagram editor down rather than ignoring one bad index.
    const known = Array.from(field.options).some((option) => option.value === value);
    field.value = value !== "" && known ? value : "";
    field.addEventListener("change", () => onPick(field.value));

    slot.appendChild(label);
    slot.appendChild(field);
  }

  function descendantsOf(index, steps) {
    /* Every step that already flows into this one, directly or not.

       Offering them as "feeds into" targets is offering to make a loop, and a
       loop is a recipe whose diagram never finishes rendering. The server
       breaks one if it somehow arrives (forms.py::_break_cycles); the point of
       leaving them out here is that nobody has to be told off for choosing
       something the page put in front of them.
    */
    const blocked = {};
    let changed = true;
    blocked[index] = true;
    while (changed) {
      changed = false;
      steps.forEach((step) => {
        const parent = fieldIn(step.row, "parent_index");
        const target = parent ? parent.value : "";
        if (target !== "" && blocked[target] && !blocked[step.index]) {
          blocked[step.index] = true;
          changed = true;
        }
      });
    }
    return blocked;
  }

  function build() {
    const steps = collect(stepContainer, "[data-step-row]",
      "input[name$='-text']", gettext("Step %(n)s"));
    const lines = collect(lineContainer, "[data-ingredient-row]",
      "input[name$='-name']", gettext("Line %(n)s"));

    // A substitute cannot itself be substituted (forms.py keeps it to one
    // level), so a row that is already an alternative is not offered as a
    // target for another one.
    const plainLines = lines.filter((line) => {
      const alt = fieldIn(line.row, "alt_index");
      return !alt || alt.value === "";
    });

    steps.forEach((step) => {
      const slot = step.row.querySelector("[data-parent-slot]");
      const field = fieldIn(step.row, "parent_index");
      if (!slot || !field) return;
      const blocked = descendantsOf(String(step.index), steps);
      select(slot, field.value,
        steps.filter((other) => other.index !== step.index && !blocked[String(other.index)]),
        slot.dataset.empty || "", (value) => {
          field.value = value;
          refresh();
        });
    });

    lines.forEach((line) => {
      const altField = fieldIn(line.row, "alt_index");
      const stepField = fieldIn(line.row, "step_index");
      const altSlot = line.row.querySelector("[data-alt-slot]");
      const stepSlot = line.row.querySelector("[data-step-slot]");

      if (altSlot && altField) {
        select(altSlot, altField.value,
          plainLines.filter((other) => other.index !== line.index && other.named),
          altSlot.dataset.empty || "", (value) => {
            altField.value = value;
            // A substitute takes its place from the line it replaces, so the
            // step it was in is cleared rather than left to be ignored on save.
            if (value !== "" && stepField) stepField.value = "";
            refresh();
          });
      }

      if (stepSlot && stepField) {
        const isAlternative = Boolean(altField && altField.value !== "");
        stepSlot.hidden = isAlternative;
        if (!isAlternative) {
          select(stepSlot, stepField.value, steps, stepSlot.dataset.empty || "", (value) => {
            stepField.value = value;
            refresh();
          });
        }
      }
    });

    draw(steps, lines);
  }

  /* ---- the preview --------------------------------------------------- */

  function draw(steps, lines) {
    if (!preview) return;
    const body = preview.querySelector("[data-diagram-preview-body]");
    if (!body) return;

    const named = steps.filter((step) => step.named);
    if (!named.length) {
      preview.hidden = true;
      return;
    }
    preview.hidden = false;
    body.textContent = "";

    const childrenOf = {};
    const roots = [];
    named.forEach((step) => {
      const parent = fieldIn(step.row, "parent_index");
      const target = parent ? parent.value : "";
      const known = named.some((other) => String(other.index) === target);
      if (target !== "" && known) {
        (childrenOf[target] = childrenOf[target] || []).push(step);
      } else {
        roots.push(step);
      }
    });

    const linesOf = {};
    lines.forEach((line) => {
      if (!line.named) return;
      const alt = fieldIn(line.row, "alt_index");
      if (alt && alt.value !== "") return;      // shown under the line it replaces
      const field = fieldIn(line.row, "step_index");
      const target = field ? field.value : "";
      if (target === "") return;
      (linesOf[target] = linesOf[target] || []).push(line);
    });

    const seen = {};

    function box(step) {
      const key = String(step.index);
      const el = document.createElement("div");
      el.className = "preview-box";
      if (seen[key]) return el;                 // belt and braces against a loop
      seen[key] = true;

      const inputs = document.createElement("div");
      inputs.className = "preview-inputs";
      (childrenOf[key] || []).forEach((child) => inputs.appendChild(box(child)));
      (linesOf[key] || []).forEach((line) => {
        const item = document.createElement("div");
        item.className = "preview-line";
        item.textContent = line.label;
        inputs.appendChild(item);
      });
      if (inputs.childNodes.length) el.appendChild(inputs);

      const label = document.createElement("div");
      label.className = "preview-step";
      label.textContent = step.label;
      el.appendChild(label);
      return el;
    }

    roots.forEach((root) => body.appendChild(box(root)));

    const loose = lines.filter((line) => {
      if (!line.named) return false;
      const alt = fieldIn(line.row, "alt_index");
      if (alt && alt.value !== "") return false;
      const field = fieldIn(line.row, "step_index");
      return !field || field.value === "";
    });
    if (loose.length) {
      const note = document.createElement("p");
      note.className = "hint";
      note.textContent = interpolate(
        gettext("Not in any step yet: %(names)s"),
        { names: loose.map((line) => line.label).join(", ") }, true
      );
      body.appendChild(note);
    }
  }

  /* ---- keeping it in step -------------------------------------------- */

  let pending = null;

  function refresh() {
    // Debounced, because this runs on every keystroke in an ingredient name —
    // rebuilding a dozen selects per character is visible as a page that
    // stutters while somebody types.
    if (pending) window.clearTimeout(pending);
    pending = window.setTimeout(() => { pending = null; build(); }, 150);
  }

  document.addEventListener("formset-rows-changed", refresh);
  stepContainer.addEventListener("input", refresh);
  lineContainer.addEventListener("input", refresh);

  build();
})();
