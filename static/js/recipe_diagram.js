/* The recipe canvas: laying a recipe out by dragging it.
 *
 * The page holds two formsets — ingredient lines and diagram steps — and three
 * hidden fields carry the whole structure: a step's `parent_index`, a line's
 * `step_index` and `alt_index`, plus a `position` on both for the order.
 * apps/recipes/forms.py says why those are row indices rather than primary
 * keys: on a recipe being typed in for the first time nothing has a primary key
 * yet, and a diagram that can only be drawn on the second save is a diagram
 * nobody draws.
 *
 * What this file does is put the cards **where they will end up**. The formset
 * rows are not previewed here, they are *moved* into the cells of the layout —
 * so the thing being edited and the thing being looked at are one object. A
 * card in the third column with two lines reaching into it is a step that will
 * come back in the third column with two lines reaching into it.
 *
 * ---- the reversal, and its cost ----
 *
 * This used to be a list of rows with a "feeds into" select on each, and a
 * preview drawn as nested boxes rather than as the table — deliberately,
 * because the geometry lives in apps/recipes/diagram.py where it is tested, and
 * a second implementation of it in another language is the thing that quietly
 * disagrees with the page it is previewing.
 *
 * Dropping a card into a column means knowing which column it is in, so that
 * argument no longer holds: the layout has to exist here, before the save. What
 * bounds the cost is that only the *rule* is repeated, and it is three lines:
 *
 *     column(step) = 1 + max(column(children), 0)
 *
 * Everything else the Python does — how far a cell spans to reach its parent,
 * the filler cells that keep a row rectangular — is the `<table>`'s problem and
 * not this grid's, because an empty grid area is simply empty. If the two ever
 * disagree it will be about that one line, in both files, three lines from the
 * top of each.
 *
 * ---- what the geometry is ----
 *
 * One CSS grid per block (a root step and everything flowing into it). Column 1
 * holds ingredient lines, one grid row each; a step sits in the column its
 * deepest input puts it in and spans every row of its own subtree, which is
 * what draws the merge. A root with nothing at all going into it is a standing
 * instruction — "heat the oven to 180 °C" — and takes the full width wherever
 * its position leaves it, which is how it can come after a few other steps
 * rather than only at the top.
 *
 * ---- and the rules it has to keep ----
 *
 * A card is never taken out of the document. Everything is moved back to its
 * holder before the canvas is rebuilt and then moved into a cell, because a
 * formset is an index range and a form missing from the POST is a hole Django
 * validates against its own defaults. `position` is a field of its own for the
 * same reason: renumbering rows to reorder them would push a new row below
 * INITIAL_FORMS, where a form with no primary key is skipped in silence.
 */
(function () {
  const builder = document.querySelector("[data-builder]");
  if (!builder) return;

  const canvas = builder.querySelector("[data-builder-canvas]");
  const tray = builder.querySelector("[data-builder-tray]");
  const traySlots = builder.querySelector("[data-builder-tray-slots]");
  const linePool = builder.querySelector("[data-line-pool]");
  const stepPool = builder.querySelector("[data-step-pool]");
  if (!canvas || !tray || !traySlots || !linePool || !stepPool) return;

  const LINE = "[data-ingredient-row]";
  const STEP = "[data-step-row]";
  const CARD = LINE + "," + STEP;

  /* ---- reading and writing a card ------------------------------------ */

  function indexOf(row) {
    // Off a field's name rather than off a data attribute: the name is what the
    // server will read the row back by, so the two cannot drift.
    const field = row.querySelector("[name]");
    const parts = field && field.name.match(/-(\d+)-/);
    return parts ? parseInt(parts[1], 10) : null;
  }

  function fieldIn(row, suffix) {
    return row.querySelector("[name$='-" + suffix + "']");
  }

  function isRemoved(row) {
    const box = row.querySelector("input[name$='-DELETE']");
    return Boolean(box && box.checked);
  }

  function refOf(row, suffix) {
    const field = fieldIn(row, suffix);
    if (!field || field.value === "") return null;
    const value = parseInt(field.value, 10);
    return Number.isFinite(value) ? value : null;
  }

  function setRef(row, suffix, value) {
    const field = fieldIn(row, suffix);
    if (field) field.value = value === null ? "" : String(value);
  }

  function labelOf(row, fallback, at) {
    const field = row.querySelector(row.matches(LINE) ? "[name$='-name']" : "[name$='-text']");
    const typed = field ? field.value.trim() : "";
    return typed || interpolate(fallback, { n: at + 1 }, true);
  }

  /* ---- the model ------------------------------------------------------
   *
   * `lineOrder` and `stepOrder` are the arrangement, as lists of formset
   * indices. They are kept here rather than read back off the DOM because the
   * DOM is rebuilt from them — and they are reconciled against the page on
   * every pass, so a row added by "+ Ingredient" takes a place at the end and a
   * row that has gone loses its own.
   */

  const lineOrder = [];
  const stepOrder = [];

  function bandSpan(row, columns) {
    /* The 1-based column range a standing instruction covers, always valid.
     *
     * The one rule this file repeats from apps/recipes/diagram.py besides the
     * column rule itself, and for the same reason: the editor has to draw what
     * the page will draw. Kept to four lines, and the Python version is
     * ``_band_span`` — if the two ever disagree it will be about the clamping.
     */
    const clamp = (value, fallback) => {
      const n = value === null ? fallback : value;
      return Math.max(1, Math.min(n, columns));
    };
    let start = clamp(refOf(row, "span_from"), 1);
    let end = clamp(refOf(row, "span_to"), columns);
    if (end < start) { const swap = start; start = end; end = swap; }
    return { start: start, end: end };
  }

  function census(selector, order) {
    const rows = new Map();
    document.querySelectorAll(selector).forEach((row) => {
      const index = indexOf(row);
      if (index !== null && !rows.has(index)) rows.set(index, row);
    });
    const kept = order.filter((index) => rows.has(index));
    rows.forEach((row, index) => { if (kept.indexOf(index) === -1) kept.push(index); });
    order.length = 0;
    kept.forEach((index) => order.push(index));
    return rows;
  }

  function model() {
    const lineRows = census(LINE, lineOrder);
    const stepRows = census(STEP, stepOrder);

    const steps = stepOrder.filter((index) => !isRemoved(stepRows.get(index)));
    const lines = lineOrder.filter((index) => !isRemoved(lineRows.get(index)));
    const liveSteps = new Set(steps);
    const liveLines = new Set(lines);

    /* A parent that has been removed is not "no parent" — it is a step that has
     * been taken *out of a chain*, and what fed it now feeds whatever it fed.
     *
     * Reading a reference to a removed row as null is what made adding a step
     * beside "Zerbröseln" and then deleting it again pull the recipe apart:
     * "+ Step after this" rewires A → new → B, so removing the new one left A
     * pointing at nothing, and A and everything under it broke off as a block
     * of its own. Following the reference past the removed rows puts A back on
     * B, which is where it was — the delete undoes the insert and touches
     * nothing else. apps/recipes/forms.py::_resolve_parent does the same on
     * save; the two have to agree, or the picture and the saved recipe do not.
     */
    const parentOf = new Map();
    steps.forEach((index) => {
      const seen = new Set([index]);
      let target = refOf(stepRows.get(index), "parent_index");
      while (target !== null && !liveSteps.has(target)) {
        // A removed row that is not on the page at all, or a ring of removed
        // rows pointing at each other: there is nothing left to inherit.
        if (seen.has(target) || !stepRows.has(target)) { target = null; break; }
        seen.add(target);
        target = refOf(stepRows.get(target), "parent_index");
      }
      parentOf.set(index, target === index ? null : target);
    });
    // A loop cannot be made from this page — a step is never a drop target for
    // its own descendants — but it can arrive in a recipe edited straight in
    // the database, and here it would be an infinite loop inside the layout
    // rather than a wrong picture. Cut it and carry on, which is what
    // apps/recipes/diagram.py does with the same shape.
    steps.forEach((index) => {
      const seen = new Set([index]);
      let current = parentOf.get(index);
      while (current !== null && current !== undefined) {
        if (seen.has(current)) { parentOf.set(index, null); break; }
        seen.add(current);
        current = parentOf.get(current);
      }
    });

    const childrenOf = new Map();
    steps.forEach((index) => childrenOf.set(index, []));
    const roots = [];
    steps.forEach((index) => {
      const parent = parentOf.get(index);
      if (parent === null) roots.push(index);
      else childrenOf.get(parent).push(index);
    });

    // A substitute is not a row of the diagram: it takes its place from the
    // line it replaces, and is drawn inside that line's cell.
    const hostOf = new Map();
    lines.forEach((index) => {
      const host = refOf(lineRows.get(index), "alt_index");
      const usable = host !== null && host !== index && liveLines.has(host);
      hostOf.set(index, usable ? host : null);
    });

    const inputsOf = new Map();
    steps.forEach((index) => inputsOf.set(index, []));
    const altsOf = new Map();
    const loose = [];
    lines.forEach((index) => {
      let host = hostOf.get(index);
      // One level, the same cut apps/recipes/forms.py makes on save:
      // "margarine instead of butter, and oil instead of the margarine" is a
      // chain nothing draws and nobody means.
      if (host !== null && hostOf.get(host) !== null) host = null;
      if (host !== null) {
        if (!altsOf.has(host)) altsOf.set(host, []);
        altsOf.get(host).push(index);
        return;
      }
      const step = refOf(lineRows.get(index), "step_index");
      if (step !== null && inputsOf.has(step)) inputsOf.get(step).push(index);
      else loose.push(index);
    });

    // The one rule shared with apps/recipes/diagram.py: an operation's column
    // is one more than the deepest operation feeding it, counted from the
    // leaves. Depth from the root looks the same on a straight chain and falls
    // apart the moment a recipe branches — two operations that belong side by
    // side land in different columns and the layout grows a staircase.
    const column = new Map();
    const columnOf = (index) => {
      if (column.has(index)) return column.get(index);
      column.set(index, 1);
      let deepest = 0;
      childrenOf.get(index).forEach((child) => {
        deepest = Math.max(deepest, columnOf(child));
      });
      column.set(index, 1 + deepest);
      return column.get(index);
    };
    steps.forEach(columnOf);
    let widest = 0;
    column.forEach((value) => { widest = Math.max(widest, value); });
    const columns = 1 + widest;

    const blocks = [];
    roots.forEach((root) => {
      const bare = !childrenOf.get(root).length && !inputsOf.get(root).length;
      if (bare) {
        // A box nobody has named yet is not a standing instruction, it is a
        // blank card. Only the one with something written in it is coloured as
        // "heat the oven", which also means the colour arrives as the answer to
        // "why is this one across the whole width?".
        const named = Boolean(fieldIn(stepRows.get(root), "text").value.trim());
        // ...and how far across it reaches. Full width unless it has been
        // given a span, which is what lets "heat the oven" sit over only the
        // steps it actually runs alongside. Clamped here exactly as
        // apps/recipes/diagram.py clamps it, because the column count changes
        // as the recipe is edited and a stored number outlives the geometry
        // that produced it.
        const span = bandSpan(stepRows.get(root), columns);
        blocks.push({
          bare: true, standing: named, steps: [root], rows: 1,
          band: span,
          cells: [{ kind: "step", step: root, rowStart: 1, rowEnd: 2,
                    colStart: span.start, colEnd: span.end + 1 }],
        });
        return;
      }
      const cells = [];
      const order = [];
      let cursor = 1;
      const place = (step, parentColumn) => {
        const own = column.get(step);
        const start = cursor;
        // Child operations first, then the loose lines — the reading order of
        // every diagram of this kind: the pot you already have at the top of
        // the group, the things going into it underneath.
        childrenOf.get(step).forEach((child) => place(child, own));
        inputsOf.get(step).forEach((line) => {
          cells.push({ kind: "line", line: line, step: step, row: cursor });
          cursor += 1;
        });
        // An operation with nothing of its own that nevertheless feeds another
        // — "bring a pan of water to the boil" before "cook the pasta in it".
        // It still needs a row to sit in.
        if (cursor === start) cursor += 1;
        cells.push({ kind: "step", step: step, rowStart: start, rowEnd: cursor,
                     colStart: own + 1, colEnd: parentColumn + 1 });
        order.push(step);
      };
      place(root, columns);
      blocks.push({ bare: false, standing: false, steps: order,
                    rows: cursor - 1, cells: cells });
    });

    return {
      lineRows, stepRows, lines, steps, blocks, columns, loose, altsOf, inputsOf,
      childrenOf, parentOf, hostOf,
    };
  }

  /* ---- the arrangement, written back --------------------------------- */

  function sequence(state) {
    /* Re-derive both orders from the layout, then stamp them into the hidden
       `position` fields.

       The point is that the number saved is the order somebody is *looking at*,
       not the order they happened to type things in — so the ingredient list on
       the recipe page reads top to bottom like the diagram does. Steps are
       stamped in the order the layout visits them, children before parents,
       which is also the order the cooking view walks. Both are fixed points:
       laying the result out again produces the same sequence. */
    const seenLines = [];
    const seenSteps = [];
    state.blocks.forEach((block) => {
      block.cells.forEach((cell) => { if (cell.kind === "line") seenLines.push(cell.line); });
      block.steps.forEach((step) => seenSteps.push(step));
    });
    state.loose.forEach((line) => seenLines.push(line));
    // Substitutes ride with the line they replace, which has already been
    // counted; anything still missing (a removed row, a card mid-move) keeps
    // the place it had rather than being dropped from the order entirely.
    state.lines.forEach((line) => { if (seenLines.indexOf(line) === -1) seenLines.push(line); });
    state.steps.forEach((step) => { if (seenSteps.indexOf(step) === -1) seenSteps.push(step); });

    reorder(lineOrder, seenLines);
    reorder(stepOrder, seenSteps);
    stamp(lineOrder, state.lineRows);
    stamp(stepOrder, state.stepRows);
  }

  function reorder(order, live) {
    // Removed rows are not in `live` and must keep a place: they are still in
    // the POST, and a row with no position would be saved wherever the form
    // index happened to put it.
    const rest = order.filter((index) => live.indexOf(index) === -1);
    order.length = 0;
    live.forEach((index) => order.push(index));
    rest.forEach((index) => order.push(index));
  }

  function stamp(order, rows) {
    order.forEach((index, at) => {
      const row = rows.get(index);
      const field = row && fieldIn(row, "position");
      if (field) field.value = String(at);
    });
  }

  /* ---- drawing --------------------------------------------------------
   *
   * Two layouts, one set of cards. The canvas is the diagram; the list is the
   * same recipe as numbered steps with the relations shown as selects. They are
   * never both on screen, and — this is the part that matters — neither is a
   * *copy*. `render` moves the very form rows the POST is built from into
   * whichever shape is being shown, so there is nothing to keep in step and
   * nothing that can be typed into one view and missing from the other.
   *
   * The list exists because the canvas answers one question badly. Dropping
   * card A onto card B means "A feeds into B", which is how you say a recipe
   * *backwards*; somebody building one forwards — "and then these two get
   * kneaded together" — has to create the later step first and then drag the
   * earlier ones onto it. In the list the same relation is a select on each
   * row, read in the direction people write.
   */

  function render(state) {
    // Home first, whichever layout is coming. Emptying the canvas with cards
    // still in it would take them out of the document, and a form missing from
    // the POST is a hole in the index range that Django validates against its
    // own defaults — which is how a row comes back wearing "This field is
    // required".
    state.lineRows.forEach((row) => linePool.appendChild(row));
    state.stepRows.forEach((row) => stepPool.appendChild(row));
    canvas.textContent = "";
    traySlots.textContent = "";
    canvas.classList.toggle("builder-canvas--list", mode === "steps");

    if (mode === "steps") renderSteps(state);
    else renderDiagram(state);

    looseCards(state);
    // Last, and only on the canvas: it measures the laid-out rows, so it has
    // to run once they are in the document and have a size.
    if (mode === "diagram") positionHandles();
  }

  function looseCards(state) {
    /* The lines that are in no step, directly below the ones that are.
     *
     * They are not a *section*. It used to be a titled box with a paragraph
     * explaining itself, and the household did not want one: a line with no box
     * beside it is still an ordinary line of the recipe, and a heading over it
     * makes it look like something to be fixed. So the cards simply follow the
     * last block, which also means they cannot get out of sight.
     *
     * The element they go into is still there when it is empty, because it is
     * also where a line is *dropped* to take it out of a step and a target that
     * only exists once something is in it cannot be used to put the first thing
     * there. main.css draws it as a target only while a drag is happening.
     */
    state.loose.forEach((line) => {
      const cell = document.createElement("div");
      cell.className = "builder-cell builder-cell--line builder-cell--loose";
      cell.dataset.dropLine = String(line);
      cell.dataset.dropStep = "";
      cell.appendChild(lineCell(line, state));
      traySlots.appendChild(cell);
    });
  }

  /* ---- the list ------------------------------------------------------- */

  function renderSteps(state) {
    const list = document.createElement("ol");
    list.className = "step-list";
    // Attached *before* it is filled, and that is not tidiness. The cards are
    // the form's own rows; moving them into a node that is not in the document
    // and then failing to attach it takes them out of the POST altogether, and
    // Django reads the missing forms against their own defaults. Building into
    // an attached node means the worst a bug in here can do is lay them out
    // wrongly.
    canvas.appendChild(list);

    // The order the layout visits, which is children before parents — the
    // order somebody would actually do them, and the order the cooking view
    // walks. Numbering by anything else would put "bake" before "mix".
    let number = 0;
    state.blocks.forEach((block) => {
      block.steps.forEach((step) => {
        number += 1;
        list.appendChild(stepListItem(step, number, state));
      });
    });

    canvas.hidden = !number;
    parentSelects(state);
    stepSelects(state);
  }

  function stepListItem(step, number, state) {
    const item = document.createElement("li");
    item.className = "step-list-item";
    // Still a drop target: switching to the list should not take the dragging
    // away, and dropping a line onto a step here means what it means anywhere.
    item.dataset.dropInto = String(step);

    const mark = document.createElement("span");
    mark.className = "step-list-number";
    mark.textContent = String(number);
    item.appendChild(mark);

    const body = document.createElement("div");
    body.className = "step-list-body";
    body.appendChild(state.stepRows.get(step));

    const feeds = (state.childrenOf.get(step) || []);
    if (feeds.length) {
      // What arrives here already made. The list cannot show the merge as a
      // shape, so it says it in words — otherwise a step that combines two
      // others reads as a step with no inputs at all.
      const from = document.createElement("p");
      from.className = "step-list-from";
      // Named out of `state`, not out of `latest`. `latest` is assigned *after*
      // render() returns, so on the first pass it is still null and this threw
      // a TypeError — halfway through moving the cards into the list, which
      // left three of them outside the document and therefore outside the POST.
      // The page looked like a recipe that had lost its first three steps.
      from.textContent = interpolate(
        gettext("Takes in what comes out of: %(steps)s"),
        {
          steps: feeds.map((child) => labelOf(
            state.stepRows.get(child), gettext("Step %(n)s"), state.steps.indexOf(child),
          )).join(", "),
        }, true
      );
      body.appendChild(from);
    }

    const lines = state.inputsOf.get(step) || [];
    if (lines.length) {
      const holder = document.createElement("ul");
      holder.className = "step-list-lines";
      lines.forEach((line) => {
        const slot = document.createElement("li");
        slot.className = "builder-cell builder-cell--line";
        slot.dataset.dropLine = String(line);
        slot.dataset.dropStep = String(step);
        slot.appendChild(lineCell(line, state));
        holder.appendChild(slot);
      });
      body.appendChild(holder);
    }

    item.appendChild(body);
    return item;
  }

  /* ---- the canvas ----------------------------------------------------- */

  function renderDiagram(state) {
    // The list's two selects are emptied on the way in. Left behind they would
    // be a second, stale answer to a question the cell position is already
    // giving — and the one somebody could still change without the layout
    // moving to match.
    state.stepRows.forEach((row) => clearSlot(row, "parent-slot"));
    state.lineRows.forEach((row) => clearSlot(row, "step-slot"));

    state.blocks.forEach((block, at) => {
      canvas.appendChild(gap(at));
      const grid = document.createElement("div");
      grid.className = "builder-block" + (block.standing ? " builder-block--standing" : "");
      // Marked so the first letter typed into a blank full-width box can colour
      // it without waiting for the next layout — see the input handler below.
      grid.dataset.bare = block.bare ? "1" : "0";
      // Wide enough that a step's name is readable without clicking into it:
      // a box you cannot read is not a diagram. Past four or five columns the
      // block is simply wider than the page and .builder-scroll takes over,
      // which is what the recipe page does with the same drawing.
      const rest = state.columns - 1;
      grid.style.gridTemplateColumns = "minmax(13rem, 1.4fr)"
        + (rest > 0 ? " repeat(" + rest + ", minmax(11rem, 1fr))" : "");
      block.cells.forEach((cell) => grid.appendChild(cellFor(cell, state)));
      canvas.appendChild(grid);
    });
    canvas.appendChild(gap(state.blocks.length));

    canvas.hidden = !state.blocks.length;
  }

  function gap(at) {
    const el = document.createElement("div");
    el.className = "builder-gap";
    el.dataset.dropGap = String(at);
    return el;
  }

  function cellFor(cell, state) {
    const el = document.createElement("div");
    if (cell.kind === "line") {
      el.className = "builder-cell builder-cell--line";
      // Read back by positionHandles() to put a resize handle exactly on the
      // boundary it moves.
      el.dataset.row = String(cell.row);
      el.style.gridRow = String(cell.row);
      el.style.gridColumn = "1";
      el.dataset.dropLine = String(cell.line);
      el.dataset.dropStep = String(cell.step);
      el.appendChild(lineCell(cell.line, state));
      // A "+" on the lower edge of every line, and on the upper edge of the
      // first one, so there is an insertion point above and below each of them.
      if (cell.row === 1) el.appendChild(linePlus(cell, state, "before"));
      el.appendChild(linePlus(cell, state, "after"));
      return el;
    }
    el.className = "builder-cell builder-cell--step";
    el.style.gridRow = cell.rowStart + " / " + cell.rowEnd;
    el.style.gridColumn = cell.colStart + " / " + cell.colEnd;
    el.dataset.dropInto = String(cell.step);
    el.appendChild(state.stepRows.get(cell.step));
    // The draggable edges, appended after the card so they sit above it. Only
    // where there is something on the other side to move — see rowHandleFor()
    // and bandHandles().
    const rows = rowHandleFor(cell, state);
    if (rows) el.appendChild(rows);
    bandHandles(cell, state).forEach((handle) => el.appendChild(handle));
    // ...and a "+" on its right edge, which is where a step that comes *after*
    // this one belongs — the new box then covers the same ingredient rows this
    // one does, because it is inserted into the chain rather than beside it.
    el.appendChild(stepPlus(cell));
    el.appendChild(siblingPlus(cell));
    return el;
  }

  function lineCell(index, state) {
    const row = state.lineRows.get(index);
    const alts = state.altsOf.get(index);
    if (!alts || !alts.length) return row;
    // A substitute is drawn under the line it replaces and inside its cell,
    // which is where the recipe page draws it too. It is still a card of its
    // own, so dragging it out of here is how it stops being a substitute.
    const group = document.createElement("div");
    group.className = "line-group";
    group.appendChild(row);
    alts.forEach((alt) => {
      const nested = document.createElement("div");
      nested.className = "line-alt";
      nested.appendChild(state.lineRows.get(alt));
      group.appendChild(nested);
    });
    return group;
  }

  /* ---- the "instead of" select ----------------------------------------
   *
   * The one relation that is not a drag. Dropping one line onto another
   * already means "put it in the same step"; the same gesture cannot also mean
   * "replace it", and making the common case ambiguous for the sake of a
   * relation used once in twenty recipes is a bad trade.
   */

  function clearSlot(row, slotName) {
    const slot = row.querySelector("[data-" + slotName + "]");
    if (slot && !slot.contains(document.activeElement)) slot.textContent = "";
  }

  function relationSelect(row, slotName, options, current, onChange) {
    /* Build one "this row points at that row" control into a slot on a card.
       Three of them exist — "instead of", "feeds into", "used in" — and they
       differ only in which rows they offer and what is written back, so they
       are one function. The copied-and-renamed version is how the fix to one
       of them (the value-matching below took two goes) lands in one copy. */
    const slot = row.querySelector("[data-" + slotName + "]");
    if (!slot) return;
    // Rebuilt rather than patched: working out which options changed is more
    // code than making a new one, and a <select> whose options were edited in
    // place keeps a selected value that is no longer among them. But never
    // while somebody is in it.
    if (slot.contains(document.activeElement)) return;
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
    blank.textContent = slot.dataset.empty || "";
    field.appendChild(blank);

    options.forEach((option) => {
      const el = document.createElement("option");
      el.value = String(option.value);
      el.textContent = option.text;
      field.appendChild(el);
    });

    // Matched against the options just built rather than with a `[value='…']`
    // selector: the value comes out of a hidden input, and one holding a quote
    // would make that selector a SyntaxError — which takes the whole canvas
    // down rather than ignoring one bad index.
    const value = current === null || current === undefined ? "" : String(current);
    const known = Array.from(field.options).some((option) => option.value === value);
    field.value = known ? value : "";
    field.addEventListener("change", () => {
      onChange(field.value === "" ? null : parseInt(field.value, 10));
      refresh();
    });

    slot.appendChild(label);
    slot.appendChild(field);
  }

  function substituteSelects(state) {
    // A substitute cannot itself be substituted — apps/recipes/forms.py keeps
    // it to one level — so a row that already replaces something is not
    // offered as a target for another.
    const candidates = state.lines.filter((index) => state.hostOf.get(index) === null);
    state.lines.forEach((index) => {
      const row = state.lineRows.get(index);
      const options = candidates
        .filter((other) => other !== index)
        .map((other, position) => ({
          value: other,
          text: labelOf(state.lineRows.get(other), gettext("Line %(n)s"), position),
        }));
      relationSelect(row, "alt-slot", options, state.hostOf.get(index), (to) => {
        setRef(row, "alt_index", to);
        // A substitute takes its place from the line it replaces, so the step
        // it was in is cleared rather than left to be ignored on save.
        if (to !== null) setRef(row, "step_index", null);
      });
    });
  }

  function parentSelects(state) {
    /* "Feeds into", on every step card, in the list view.
     *
     * This is the control the canvas does not have, and the reason the list
     * exists at all: on the canvas the relation is expressed by dragging the
     * *earlier* step onto the later one, which is the sentence backwards. Here
     * each step says where its output goes, which is how a recipe is written.
     *
     * A step's own descendants are left out. Offering them would be offering
     * to make a loop, and nobody should be told off for picking something the
     * page put in front of them. */
    state.steps.forEach((index) => {
      const row = state.stepRows.get(index);
      const banned = descendantsIn(state, index);
      const options = state.steps
        .filter((other) => other !== index && !banned.has(other))
        .map((other, position) => ({
          value: other,
          text: labelOf(state.stepRows.get(other), gettext("Step %(n)s"), position),
        }));
      relationSelect(row, "parent-slot", options, state.parentOf.get(index), (to) => {
        setRef(row, "parent_index", to);
      });
    });
  }

  function stepSelects(state) {
    /* "Used in", on every ingredient card, in the list view. The list's
       equivalent of dropping a line into a cell. A substitute has no step of
       its own, so its control says so rather than offering a choice that would
       be discarded on save. */
    state.lines.forEach((index) => {
      const row = state.lineRows.get(index);
      if (state.hostOf.get(index) !== null) {
        const slot = row.querySelector("[data-step-slot]");
        if (slot && !slot.contains(document.activeElement)) slot.textContent = "";
        return;
      }
      const options = state.steps.map((step, position) => ({
        value: step,
        text: labelOf(state.stepRows.get(step), gettext("Step %(n)s"), position),
      }));
      const current = refOf(row, "step_index");
      relationSelect(row, "step-slot", options,
                     state.steps.indexOf(current) === -1 ? null : current, (to) => {
        setRef(row, "step_index", to);
      });
    });
  }

  function descendantsIn(state, index) {
    const found = new Set();
    const walk = (step) => {
      (state.childrenOf.get(step) || []).forEach((child) => {
        if (found.has(child)) return;
        found.add(child);
        walk(child);
      });
    };
    walk(index);
    return found;
  }

  /* ---- keeping the caret where somebody left it ----------------------- */

  function withFocusKept(work) {
    const active = document.activeElement;
    // Where the page is scrolled to, kept across the redraw. Pressing a
    // boundary arrow rebuilds every cell, and a rebuilt cell is a new element:
    // the browser's scroll anchoring loses the thing it was holding on to and
    // the page lurches — several hundred pixels, on the press of a button whose
    // whole job is a one-column adjustment.
    const scrollX = window.scrollX;
    const scrollY = window.scrollY;
    // An edge button is not inside a card, so the caret-keeping below never
    // covered it; it is rebuilt by name instead. Without this, adjusting a
    // band twice means finding the arrow again after the first press.
    const edgeKey = active && active.dataset ? active.dataset.edgeKey : null;

    const held = active && active.closest && active.closest(CARD) ? active : null;
    let from = null;
    let to = null;
    try {
      from = held ? held.selectionStart : null;
      to = held ? held.selectionEnd : null;
    } catch (err) {
      // Reading a selection off a number or checkbox input throws. Nothing to
      // restore, and the focus itself still is.
      from = null;
    }
    work();

    if (edgeKey) {
      // `preventScroll`, or focusing the rebuilt arrow undoes the scroll
      // restore two lines below by bringing it into view again.
      const again = builder.querySelector('[data-edge-key="' + edgeKey + '"]');
      if (again && !again.disabled) {
        try {
          again.focus({ preventScroll: true });
        } catch (err) {
          again.focus();
        }
      }
    }
    window.scrollTo(scrollX, scrollY);

    if (!held || !document.contains(held)) return;
    held.focus();
    if (from === null || !held.setSelectionRange) return;
    try {
      held.setSelectionRange(from, to);
    } catch (err) {
      /* not a field with a caret */
    }
  }

  function fitStepText(row) {
    /* Grow a step's box to the text in it.

       The field is a textarea so a name longer than its column wraps instead
       of scrolling — but a fixed number of rows then either clips
       "ausrollen, dachziegelartig belegen" or leaves "backen" sitting in three
       empty lines. Sized to its content, the box is as tall as what it says,
       which is what the rendered cell does with the same words. */
    const area = row.querySelector("textarea[name$='-text']");
    // `scrollHeight` on something not being displayed is 0, which would
    // collapse the box to nothing the moment it came back.
    if (!area || !area.offsetParent) return;
    area.style.height = "auto";
    // Plus the borders. Everything on this page is `box-sizing: border-box`,
    // so `height` is measured to the outside of the border while
    // `scrollHeight` stops at the padding — assigning one to the other leaves
    // the box exactly two border-widths too short, which is not enough to see
    // and is enough to clip the last line.
    const style = window.getComputedStyle(area);
    const borders = parseFloat(style.borderTopWidth) + parseFloat(style.borderBottomWidth);
    area.style.height = (area.scrollHeight + borders) + "px";
  }

  function fitEveryStep() {
    if (latest) latest.stepRows.forEach(fitStepText);
  }

  function refresh() {
    withFocusKept(() => {
      const state = model();
      sequence(state);
      render(state);
      substituteSelects(state);
      ovenPanels(state);
      // Set before fitting: fitEveryStep() measures the boxes of whatever the
      // current layout is, and that is this one.
      latest = state;
      fitEveryStep();
    });
  }

  let latest = null;

  /* ---- which of the two layouts is showing ----------------------------
   *
   * Remembered across pages and sessions, because it is a statement about how
   * somebody thinks rather than about this recipe — and being put back into a
   * view you did not choose, once per recipe, is worse than not offering the
   * choice.
   *
   * The default is the list. The canvas is the more interesting of the two and
   * it is what the recipe *page* shows, but it is not where a recipe gets
   * typed: an empty canvas asks somebody to arrange things before there is
   * anything to arrange. One press is enough to disagree, and then it is
   * remembered.
   */

  const MODE_KEY = "kitchen.builder-mode";
  let mode = "steps";
  try {
    const stored = window.localStorage.getItem(MODE_KEY);
    if (stored === "steps" || stored === "diagram") mode = stored;
  } catch (err) {
    /* private mode, or storage turned off. The default stands. */
  }

  const modeButtons = Array.from(builder.querySelectorAll("[data-builder-mode]"));

  function showMode(next, remember) {
    mode = next === "diagram" ? "diagram" : "steps";
    modeButtons.forEach((button) => {
      const on = button.dataset.builderMode === mode;
      button.classList.toggle("is-on", on);
      button.setAttribute("aria-pressed", on ? "true" : "false");
    });
    // The keyboard hint is about dragging on the canvas. In the list the same
    // moves are the selects on each card, and a line telling somebody to press
    // the arrow keys is one more thing to ignore.
    const keys = builder.querySelector("[data-builder-keys]");
    if (keys) keys.hidden = mode !== "diagram";
    if (remember) {
      try {
        window.localStorage.setItem(MODE_KEY, mode);
      } catch (err) {
        /* nothing to do; the choice simply does not outlive the page */
      }
    }
  }

  modeButtons.forEach((button) => {
    button.addEventListener("click", () => {
      showMode(button.dataset.builderMode, true);
      refresh();
    });
  });

  /* ---- moving things -------------------------------------------------- */

  function moveBeside(order, index, neighbour, before) {
    const from = order.indexOf(index);
    if (from !== -1) order.splice(from, 1);
    let at = order.indexOf(neighbour);
    if (at === -1) at = order.length;
    else if (!before) at += 1;
    order.splice(at, 0, index);
  }

  function moveLast(order, index) {
    const from = order.indexOf(index);
    if (from !== -1) order.splice(from, 1);
    order.push(index);
  }

  function putLine(index, step, neighbour, before) {
    const row = latest.lineRows.get(index);
    if (!row) return;
    // Dragging a substitute anywhere is how it stops being one: it has just
    // been given a place of its own, and a row cannot both replace a line and
    // sit beside it.
    setRef(row, "alt_index", null);
    setRef(row, "step_index", step);
    if (neighbour === index) {
      /* dropped back onto itself — it has not moved past anything */
    } else if (neighbour === null) {
      moveLast(lineOrder, index);
    } else {
      moveBeside(lineOrder, index, neighbour, before);
    }
    refresh();
  }

  function putStepInto(index, parent) {
    const row = latest.stepRows.get(index);
    if (!row || parent === index || descendants(index).has(parent)) return;
    setRef(row, "parent_index", parent);
    refresh();
  }

  function putStepAtGap(index, at) {
    const row = latest.stepRows.get(index);
    if (!row) return;
    setRef(row, "parent_index", null);
    const from = stepOrder.indexOf(index);
    if (from !== -1) stepOrder.splice(from, 1);
    // Where the block that will follow it starts. Its own subtree is skipped —
    // a step dropped in front of the block it is already the root of has not
    // moved, and anchoring to itself would put it after its own children.
    let anchor = null;
    const mine = descendants(index);
    for (let k = at; k < latest.blocks.length && anchor === null; k += 1) {
      latest.blocks[k].steps.forEach((step) => {
        if (anchor === null && step !== index && !mine.has(step)) anchor = step;
      });
    }
    const to = anchor === null ? -1 : stepOrder.indexOf(anchor);
    stepOrder.splice(to === -1 ? stepOrder.length : to, 0, index);
    refresh();
  }

  function descendants(index) {
    const found = new Set();
    const walk = (step) => {
      (latest.childrenOf.get(step) || []).forEach((child) => {
        if (found.has(child)) return;
        found.add(child);
        walk(child);
      });
    };
    walk(index);
    return found;
  }

  /* ---- dragging -------------------------------------------------------
   *
   * Pointer events rather than the HTML5 drag-and-drop API, which does not fire
   * for touch at all — and this recipe gets written on a tablet propped against
   * the tiles as often as at a desk. One code path for mouse, pen and finger.
   */

  let drag = null;

  builder.addEventListener("pointerdown", (event) => {
    if (event.button !== 0) return;
    const grip = event.target.closest("[data-grip]");
    if (!grip) return;
    const row = grip.closest(CARD);
    const index = row === null ? null : indexOf(row);
    if (index === null) return;
    drag = {
      kind: row.matches(LINE) ? "line" : "step",
      index: index, row: row, grip: grip, pointer: event.pointerId,
      fromX: event.clientX, fromY: event.clientY,
      started: false, ghost: null, target: null,
    };
    // Capture keeps the moves coming to this handle even when the pointer
    // outruns it — which it does, because the card moves out from under the
    // finger the moment the layout is redrawn. It is an optimisation and not a
    // requirement: the listeners are on the whole builder and the target is
    // found by hit-testing, so a browser that refuses the capture still drags.
    // Guarded because refusing it throws, and an exception here would leave a
    // drag that has begun and can never be finished.
    try {
      grip.setPointerCapture(event.pointerId);
    } catch (err) {
      /* no capture; the events still arrive by bubbling */
    }
  });

  builder.addEventListener("pointermove", (event) => {
    if (!drag || event.pointerId !== drag.pointer) return;
    if (!drag.started) {
      // A press is not a drag until it travels. Without the threshold, reaching
      // the handle with a click — which is how the keyboard gets there too —
      // starts and ends a drag in the same place and repaints for nothing.
      if (Math.abs(event.clientX - drag.fromX) < 4
          && Math.abs(event.clientY - drag.fromY) < 4) return;
      begin(event);
    }
    event.preventDefault();
    drag.ghost.style.left = (event.clientX - drag.offsetX) + "px";
    drag.ghost.style.top = (event.clientY - drag.offsetY) + "px";
    aim(event);
  });

  builder.addEventListener("pointerup", (event) => {
    if (!drag || event.pointerId !== drag.pointer) return;
    // Read before finishing: `finish` clears the drag, and the move below is
    // about the card that *was* being dragged.
    const target = drag.started ? drag.target : null;
    const index = drag.index;
    finish();
    if (target) drop(index, target);
  });

  builder.addEventListener("pointercancel", (event) => {
    if (drag && event.pointerId === drag.pointer) finish();
  });

  function begin(event) {
    drag.started = true;
    const box = drag.row.getBoundingClientRect();
    const ghost = drag.row.cloneNode(true);
    ghost.classList.add("drag-ghost");
    // A clone of a form row would post a second copy of every field in it, and
    // duplicate the ids its labels point at. It is a picture, not a form.
    ghost.querySelectorAll("input, select, textarea").forEach((el) => {
      el.removeAttribute("name");
      el.removeAttribute("id");
    });
    ghost.style.width = box.width + "px";
    document.body.appendChild(ghost);
    drag.ghost = ghost;
    drag.offsetX = event.clientX - box.left;
    drag.offsetY = event.clientY - box.top;
    drag.row.classList.add("is-dragging");
    builder.classList.add("is-dragging");
  }

  function finish() {
    if (drag.ghost) drag.ghost.remove();
    drag.row.classList.remove("is-dragging");
    builder.classList.remove("is-dragging");
    clearAim();
    drag = null;
  }

  function aim(event) {
    clearAim();
    // The ghost is `pointer-events: none`, so what is under the pointer is the
    // page rather than the thing being dragged.
    const under = document.elementFromPoint(event.clientX, event.clientY);
    drag.target = under ? resolve(under, event) : null;
    if (!drag.target) return;
    const el = drag.target.el;
    if (drag.target.kind === "line-beside") {
      el.classList.add(drag.target.before ? "is-drop-before" : "is-drop-after");
    } else {
      el.classList.add("is-drop");
    }
  }

  function clearAim() {
    builder.querySelectorAll(".is-drop, .is-drop-before, .is-drop-after")
      .forEach((el) => el.classList.remove("is-drop", "is-drop-before", "is-drop-after"));
  }

  function resolve(element, event) {
    if (drag.kind === "line") {
      const beside = element.closest("[data-drop-line]");
      if (beside) {
        const box = beside.getBoundingClientRect();
        return {
          kind: "line-beside", el: beside,
          line: parseInt(beside.dataset.dropLine, 10),
          step: beside.dataset.dropStep === "" ? null : parseInt(beside.dataset.dropStep, 10),
          before: event.clientY < box.top + box.height / 2,
        };
      }
      const into = element.closest("[data-drop-into]");
      if (into) {
        return { kind: "line-into", el: into, step: parseInt(into.dataset.dropInto, 10) };
      }
      if (element.closest("[data-builder-tray]")) return { kind: "line-out", el: tray };
      return null;
    }

    const into = element.closest("[data-drop-into]");
    if (into) {
      const step = parseInt(into.dataset.dropInto, 10);
      // Its own descendants are not offered: choosing one would be choosing to
      // make a loop, and nobody should be told off for picking something the
      // page put in front of them.
      if (step === drag.index || descendants(drag.index).has(step)) return null;
      return { kind: "step-into", el: into, step: step };
    }
    const at = element.closest("[data-drop-gap]");
    if (at) return { kind: "step-gap", el: at, at: parseInt(at.dataset.dropGap, 10) };
    if (element.closest("[data-builder-tray]")) {
      return { kind: "step-gap", el: tray, at: latest.blocks.length };
    }
    return null;
  }

  function drop(index, target) {
    if (target.kind === "line-beside") {
      putLine(index, target.step, target.line, target.before);
    } else if (target.kind === "line-into") {
      putLine(index, target.step, null, false);
    } else if (target.kind === "line-out") {
      putLine(index, null, null, false);
    } else if (target.kind === "step-into") {
      putStepInto(index, target.step);
    } else if (target.kind === "step-gap") {
      putStepAtGap(index, target.at);
    }
  }

  /* ---- the same moves, from the keyboard ------------------------------
   *
   * Not a "pick up, move, put down" mode. Each press is a move that has already
   * happened, so there is nothing to remember being in the middle of and
   * nothing that can be left half-done by walking away — which is the failure
   * of every grab-and-drop keyboard implementation.
   */

  builder.addEventListener("keydown", (event) => {
    const grip = event.target.closest && event.target.closest("[data-grip]");
    if (!grip) return;
    const keys = ["ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight"];
    if (keys.indexOf(event.key) === -1) return;
    const row = grip.closest(CARD);
    const index = row === null ? null : indexOf(row);
    if (index === null) return;
    event.preventDefault();
    // The card is *moved* into its new cell rather than rebuilt, so this
    // handle is still the same element afterwards and `withFocusKept` around
    // the redraw puts the focus back on it. Nothing to restore here — and the
    // version that looked the row up again by index took it off the two maps
    // in turn, which for a step numbered 0 focused the *ingredient* numbered 0.
    if (row.matches(LINE)) nudgeLine(index, event.key);
    else nudgeStep(index, event.key);
  });

  function lineSlots() {
    /* Every place a line can be, top to bottom: the cells of the canvas in
       order, then the tray. `step` is null for the tray, which is what makes
       walking off the bottom of the last block take a line out of its step. */
    const slots = [];
    latest.blocks.forEach((block) => {
      block.cells.forEach((cell) => {
        if (cell.kind === "line") slots.push({ line: cell.line, step: cell.step });
      });
    });
    latest.loose.forEach((line) => slots.push({ line: line, step: null }));
    return slots;
  }

  function nudgeLine(index, key) {
    if (key === "ArrowLeft") {
      putLine(index, null, null, false);
      sayLoose(index);
      return;
    }
    const slots = lineSlots();
    const at = slots.findIndex((slot) => slot.line === index);

    if (key === "ArrowRight") {
      // The next step along, wrapping — so holding the key walks the line
      // through every step rather than stopping at the last one.
      const here = at === -1 ? null : slots[at].step;
      if (!latest.steps.length) return;
      const from = here === null ? -1 : latest.steps.indexOf(here);
      const next = latest.steps[(from + 1) % latest.steps.length];
      if (next === undefined || next === here) return;
      putLine(index, next, null, false);
      sayIn(index, next);
      return;
    }

    const to = at + (key === "ArrowDown" ? 1 : -1);
    if (at === -1 || to < 0) return;
    if (to >= slots.length) {
      // Off the bottom of the last block is out of every step, which is the
      // tray — the same thing the pointer does by dropping a line down there.
      if (slots[at].step === null) return;
      putLine(index, null, null, false);
      sayLoose(index);
      return;
    }
    // Before the slot going up, after it going down: the line walks the list a
    // place at a time and changes step as it crosses a boundary.
    putLine(index, slots[to].step, slots[to].line, key === "ArrowUp");
    if (slots[to].step === null) sayLoose(index);
    else sayIn(index, slots[to].step);
  }

  function nudgeStep(index, key) {
    const parent = latest.parentOf.get(index);
    // A root's siblings are the other roots — one per block, and it is the last
    // step the block's layout visits, because children are placed first.
    const siblings = parent === null
      ? latest.blocks.map((block) => block.steps[block.steps.length - 1])
      : latest.childrenOf.get(parent).slice();
    const at = siblings.indexOf(index);

    if (key === "ArrowLeft") {
      if (parent === null) return;
      putStepAtGap(index, blockOf(parent));
      sayOnItsOwn(index);
      return;
    }
    if (key === "ArrowRight") {
      // Into the next sibling, or the previous one when it is already last:
      // "feeds into the next step along" has to mean something for the last
      // step too, and the only other step at its level is the one before it.
      const target = siblings[at + 1] !== undefined ? siblings[at + 1] : siblings[at - 1];
      if (at === -1 || target === undefined) return;
      putStepInto(index, target);
      sayFeeds(index, target);
      return;
    }

    const step = key === "ArrowDown" ? 1 : -1;
    if (parent === null) {
      // Gap `k` is the space in front of block `k`, so moving down one block
      // means landing in front of the block after next.
      const here = blockOf(index);
      const to = here + (step > 0 ? 2 : -1);
      if (here === -1 || to < 0 || to > latest.blocks.length) return;
      putStepAtGap(index, to);
    } else {
      const target = siblings[at + step];
      if (at === -1 || target === undefined) return;
      moveBeside(stepOrder, index, target, step < 0);
      refresh();
    }
    sayPlace(index);
  }

  function blockOf(step) {
    return latest.blocks.findIndex((block) => block.steps.indexOf(step) !== -1);
  }

  /* ---- saying what happened -------------------------------------------
   *
   * Every arrow press moves something that may now be off the screen, and the
   * page gives no other sign that anything happened. The live region is the
   * whole feedback for somebody not using a pointer.
   */

  function say(text) {
    const status = builder.querySelector("[data-builder-status]");
    if (status) status.textContent = text;
  }

  function nameOf(index, kind) {
    const rows = kind === "line" ? latest.lineRows : latest.stepRows;
    const order = kind === "line" ? lineOrder : stepOrder;
    const row = rows.get(index);
    if (!row) return "";
    const fallback = kind === "line" ? gettext("Line %(n)s") : gettext("Step %(n)s");
    return labelOf(row, fallback, order.indexOf(index));
  }

  function sayIn(line, step) {
    say(interpolate(gettext("%(line)s is now in “%(step)s”"),
                    { line: nameOf(line, "line"), step: nameOf(step, "step") }, true));
  }

  function sayLoose(line) {
    say(interpolate(gettext("%(line)s is not in any step"),
                    { line: nameOf(line, "line") }, true));
  }

  function sayFeeds(step, parent) {
    say(interpolate(gettext("%(step)s now feeds into “%(parent)s”"),
                    { step: nameOf(step, "step"), parent: nameOf(parent, "step") }, true));
  }

  function sayOnItsOwn(step) {
    say(interpolate(gettext("%(step)s no longer feeds into anything"),
                    { step: nameOf(step, "step") }, true));
  }

  function sayPlace(step) {
    const at = blockOf(step);
    if (at === -1) return;
    say(interpolate(gettext("%(step)s is now %(n)s of %(total)s"),
                    { step: nameOf(step, "step"), n: at + 1,
                      total: latest.blocks.length }, true));
  }

  /* ---- adding into, and after, a step ---------------------------------- */

  builder.addEventListener("click", (event) => {
    const button = event.target.closest("[data-step-add-line]");
    if (!button) return;
    const card = button.closest(STEP);
    const step = card === null ? null : indexOf(card);
    const formsets = window.recipeFormsets;
    if (step === null || !formsets || !formsets.lines) return;
    const row = formsets.lines.add();
    if (!row) return;
    setRef(row, "step_index", step);
    refresh();
    focusFirst(row);
  });

  builder.addEventListener("click", (event) => {
    const button = event.target.closest("[data-step-add-beside]");
    if (!button) return;
    const card = button.closest(STEP);
    const step = card === null ? null : indexOf(card);
    if (step === null || !latest) return;
    addSiblingStep(step);
  });

  builder.addEventListener("click", (event) => {
    /* "+ Step after this": mint a step that this one feeds into.
     *
     * The move the drag gesture makes hard to find. Dropping A onto B means "A
     * feeds into B", so building a recipe in the order it is written — this
     * happens, *and then* this — meant creating the later step first and
     * dragging the earlier one backwards onto it. Here the sentence runs
     * forwards, and the new box lands in the column to the right because that
     * is what having something feed into it means.
     *
     * The new step is inserted *between* this one and whatever this one used
     * to feed into, rather than appended at the end. Adding a step in the
     * middle of a chain must not detach the rest of it.
     *
     * It always creates a step, and never guesses that an existing one was
     * meant. The version that did — pressing it on the second of two parallel
     * arms and having it join the step the first arm already feeds — reads
     * well on the recipe it was written for and is unpredictable on every
     * other: whether a press adds a box or silently rewires something
     * depended on the shape of the rest of the page. Joining two arms is a
     * different sentence and it has its own control, the "feeds into" select
     * on each card in the list view.
     *
     * The button on the card and the "+" on the tile's right edge are the same
     * move, so they are the same function. They were two copies of it, and the
     * copies had already drifted: only one of them placed the new row in the
     * order.
     */
    const button = event.target.closest("[data-step-add-after]");
    if (!button) return;
    const card = button.closest(STEP);
    const step = card === null ? null : indexOf(card);
    if (step === null || !latest) return;
    addStepAfter(step);
  });

  function focusFirst(row) {
    const first = row.querySelector(
      "input:not([type='hidden']):not([type='checkbox']), textarea"
    );
    if (first) first.focus();
  }

  /* ---- resizing a tile by dragging its edge ---------------------------
   *
   * Two things can be resized, and both are "how far does this tile reach":
   *
   *   * down the ingredients — which lines go into this step, moved by dragging
   *     the **bottom edge** of the step's tile up or down;
   *   * across the columns — how far a standing instruction ("heat the oven")
   *     reaches, moved by dragging the **left or right end** of its band.
   *
   * ---- one handle per boundary ----
   *
   * The version this replaced put a control on the top *and* the bottom of every
   * step, which meant every boundary had two of them: the bottom of "Step 1" and
   * the top of "Step 2" are the same line, eight pixels apart, doing the same
   * thing from opposite sides. So the rule now is one handle per *boundary*,
   * owned by the tile above it — the tile whose own lines end there.
   *
   * ---- and why the drag does not commit as it goes ----
   *
   * Moving a line re-lays the whole canvas out, which destroys the element being
   * dragged and with it the pointer capture. So a drag changes nothing until it
   * is released: while it moves it only draws a guide at the boundary it would
   * land on, and the release applies the difference in one go. That also means a
   * drag that wanders and comes back is a drag that did nothing, which is what
   * anybody would expect.
   *
   * Every handle is focusable and answers the arrow keys, because a drag with no
   * keyboard equivalent is a control half this household cannot use — and the
   * pointer version is the one that cannot be offered to a screen reader at all.
   */

  function neighboursOfStep(step, state) {
    /* The boundary either side of a step's **own lines**.
     *
     * Its own lines, and not the rows its cell spans — those are different, and
     * the difference is a bug this has had once already. A step's cell covers
     * its whole subtree: in a chain the second step's cell starts at row 1
     * because the first step's ingredients sit inside it.
     */
    const block = state.blocks.find((b) => b.steps.indexOf(step) !== -1);
    if (!block || block.bare) return null;
    const lines = block.cells.filter((c) => c.kind === "line");
    const byRow = new Map(lines.map((c) => [c.row, c]));
    const own = lines.filter((c) => c.step === step).sort((a, b) => a.row - b.row);
    const first = own[0] || null;
    const last = own[own.length - 1] || null;

    /* Where the step's bottom edge *is*, as a row number.
     *
     * Its own last line when it has one — and when it has none, the last row of
     * its own cell, which for a step with nothing going into it is the single
     * empty row the layout gave it. That second case is the one that was
     * missing: a step with no ingredients had no boundary, so it got no handle,
     * so the one gesture that would give it its first ingredient was the one
     * gesture not on offer. It is how "Vormischen" could not be pulled down
     * over Dinkelmehl, Salz and Zucker — the shape it exists for.
     */
    const cell = block.cells.find((c) => c.kind === "step" && c.step === step);
    const anchorRow = last ? last.row : (cell ? cell.rowEnd - 1 : null);
    const below = anchorRow === null ? null : (byRow.get(anchorRow + 1) || null);

    const parent = state.parentOf.get(step);
    const inBlock = parent !== null && parent !== undefined
      && block.steps.indexOf(parent) !== -1;

    return {
      block: block,
      rows: lines.sort((a, b) => a.row - b.row),
      first: first,
      last: last,
      anchorRow: anchorRow,
      below: below,
      // Where this step's last line goes when the boundary moves up. Whoever
      // owns the row beneath it, or — for a step that owns every line in the
      // block — the step this one feeds into.
      belowStep: below ? below.step : (inBlock ? parent : null),
    };
  }

  function growDown(step) {
    /* Take the line directly below this step's own lines into it. */
    const near = neighboursOfStep(step, latest);
    if (!near || !near.below) return false;
    // Beside its own last line — or, for a step taking its *first* ingredient,
    // beside itself, which `putLine` reads as "it has not moved past anything"
    // and leaves the order alone. Passing null there would send the line to the
    // end of the whole list on the way to being put back where it was.
    putLine(near.below.line, step, near.last ? near.last.line : near.below.line, false);
    return true;
  }

  function shrinkUp(step) {
    /* Hand this step's last line to whatever is beneath it. */
    const near = neighboursOfStep(step, latest);
    if (!near || !near.last || near.belowStep === null) return false;
    putLine(near.last.line, near.belowStep, near.below ? near.below.line : null, true);
    return true;
  }

  function moveRowBoundary(step, delta) {
    /* Apply `delta` whole ingredients to the boundary under `step`.
     *
     * One at a time, re-reading the layout between each: every move changes
     * which line is next, so a batch computed up front would be right for the
     * first and wrong for the rest.
     */
    let done = 0;
    for (let n = 0; n < Math.abs(delta); n += 1) {
      const moved = delta > 0 ? growDown(step) : shrinkUp(step);
      if (!moved) break;
      done += 1;
    }
    return done;
  }

  function setBand(step, start, end, columns) {
    const row = latest.stepRows.get(step);
    if (!row) return false;
    if (start < 1 || end > columns || end < start) return false;
    // Written as null when it is the whole width again, so "no span" and "a
    // span that happens to cover everything" are one state rather than two
    // that render identically and compare differently.
    const whole = start === 1 && end === columns;
    setRef(row, "span_from", whole ? null : start);
    setRef(row, "span_to", whole ? null : end);
    refresh();
    return true;
  }

  /* ---- where the handles go ------------------------------------------- */

  function rowHandleFor(cell, state) {
    /* The bottom edge of a step's own lines, when there is something on the
       other side of it — a next line to take, or a step to hand to.

       A step with no ingredients at all still gets one, as long as there is a
       line beneath it to take: that is the only way it will ever have a first
       one without dragging the ingredients to it one at a time. */
    const near = neighboursOfStep(cell.step, state);
    if (!near || near.anchorRow === null) return null;
    const canGrow = Boolean(near.below);
    const canShrink = Boolean(near.last) && near.belowStep !== null;
    if (!canGrow && !canShrink) return null;

    const handle = document.createElement("div");
    handle.className = "cell-resize cell-resize--rows";
    handle.tabIndex = 0;
    handle.setAttribute("role", "separator");
    handle.setAttribute("aria-orientation", "horizontal");
    handle.setAttribute("aria-label", gettext("Drag to change which ingredients go into this step"));
    handle.title = gettext("Drag to change which ingredients go into this step");
    handle.dataset.resize = "rows";
    handle.dataset.step = String(cell.step);
    handle.dataset.anchorRow = String(near.anchorRow);
    handle.dataset.handleKey = cell.step + "|rows";
    return handle;
  }

  function bandHandles(cell, state) {
    /* The two ends of a standing instruction's band.
     *
     * Only on a *named* one: a blank card is drawn full width too, and giving
     * it ends to drag is offering to lay out something nobody has written yet.
     */
    const block = state.blocks.find((b) => b.cells.indexOf(cell) !== -1);
    if (!block || !block.bare || !block.band || !block.standing) return [];
    if (state.columns < 2) return [];

    return ["start", "end"].map((which) => {
      const handle = document.createElement("div");
      handle.className = "cell-resize cell-resize--cols cell-resize--" + which;
      handle.tabIndex = 0;
      handle.setAttribute("role", "separator");
      handle.setAttribute("aria-orientation", "vertical");
      const label = which === "start"
        ? gettext("Drag to change where this step starts")
        : gettext("Drag to change how far this step reaches");
      handle.setAttribute("aria-label", label);
      handle.title = label;
      handle.dataset.resize = which === "start" ? "band-start" : "band-end";
      handle.dataset.step = String(cell.step);
      handle.dataset.handleKey = cell.step + "|" + handle.dataset.resize;
      return handle;
    });
  }

  function positionHandles() {
    /* Put each row handle on the boundary it moves.
     *
     * Measured rather than computed: the rows are not a uniform height — a line
     * whose name wrapped is taller than one that did not — and the only thing
     * that knows is the layout itself. The column handles need none of this;
     * they sit on the ends of their own cell, which CSS can say.
     */
    canvas.querySelectorAll(".builder-cell--step").forEach((stepCell) => {
      const block = stepCell.parentElement;
      if (!block) return;
      const stepBox = stepCell.getBoundingClientRect();
      stepCell.querySelectorAll("[data-anchor-row]").forEach((handle) => {
        const lineCell = block.querySelector(
          '.builder-cell--line[data-row="' + handle.dataset.anchorRow + '"]'
        );
        // No line cell on that row means a step with no ingredients of its own,
        // whose anchor is the empty row the layout gave it — and its own cell
        // *is* that row, so the bottom of the cell is the same boundary.
        const box = lineCell ? lineCell.getBoundingClientRect() : stepBox;
        handle.style.top = (box.bottom - stepBox.top) + "px";
      });
    });
  }

  /* ---- the drag itself ------------------------------------------------- */

  let resize = null;

  function rowEdges(near) {
    /* The y of every boundary between the block's line rows, plus the two ends,
       in order — so a pointer position can be turned into "which boundary". */
    return near.rows.map((cell) => {
      const el = canvas.querySelector('.builder-cell--line[data-row="' + cell.row + '"]');
      const box = el ? el.getBoundingClientRect() : null;
      return box ? { row: cell.row, top: box.top, bottom: box.bottom } : null;
    }).filter(Boolean);
  }

  function columnEdges(block) {
    /* The x of every column boundary of a block's grid, read off the computed
       template — the band's own cell only covers some of them. */
    const style = window.getComputedStyle(block);
    const widths = style.gridTemplateColumns.split(" ").map(parseFloat).filter((n) => !isNaN(n));
    const gap = parseFloat(style.columnGap) || 0;
    const left = block.getBoundingClientRect().left;
    const edges = [];
    let x = left;
    widths.forEach((width, at) => {
      edges.push({ column: at + 1, left: x, right: x + width });
      x += width + gap;
    });
    return edges;
  }

  builder.addEventListener("pointerdown", (event) => {
    const handle = event.target.closest("[data-resize]");
    if (!handle || event.button !== 0 || !latest) return;
    event.preventDefault();
    event.stopPropagation();

    const step = parseInt(handle.dataset.step, 10);
    const kind = handle.dataset.resize;
    const block = handle.closest(".builder-block");

    if (kind === "rows") {
      const near = neighboursOfStep(step, latest);
      if (!near || near.anchorRow === null) return;
      const edges = rowEdges(near);
      // Which line row the boundary sits under, as an index. **-1 is a real
      // answer**: a step with no ingredients of its own sits above every line
      // in the block, and there is no row beneath the boundary to name.
      let at = -1;
      edges.forEach((edge, k) => { if (edge.row <= near.anchorRow) at = k; });
      resize = { kind: kind, step: step, edges: edges, from: at, to: at, handle: handle };
    } else {
      const edges = columnEdges(block);
      const span = latest.blocks.find((b) => b.steps.indexOf(step) !== -1).band;
      const at = kind === "band-start" ? span.start - 1 : span.end - 1;
      resize = {
        kind: kind, step: step, edges: edges, from: at, to: at,
        span: span, handle: handle,
      };
    }

    try {
      handle.setPointerCapture(event.pointerId);
    } catch (err) {
      /* the listeners are on the builder; hit-testing still works */
    }
    resize.pointer = event.pointerId;
    builder.classList.add("is-resizing");
    showGuide();
  });

  builder.addEventListener("pointermove", (event) => {
    if (!resize || event.pointerId !== resize.pointer) return;
    event.preventDefault();

    if (resize.kind === "rows") {
      // The last boundary the pointer has passed, measured against the middle
      // of each row: a boundary moves when the pointer is more than half way
      // over the row it would swallow.
      let to = -1;
      resize.edges.forEach((edge, at) => {
        if (event.clientY > (edge.top + edge.bottom) / 2) to = at;
      });
      // The floor is where the drag started when that was above every row, so
      // a drag that wanders upwards and comes back is still a drag that did
      // nothing. Otherwise the boundary cannot go above the first line.
      resize.to = Math.max(resize.from < 0 ? -1 : 0, to);
    } else {
      let to = 0;
      resize.edges.forEach((edge, at) => {
        if (event.clientX > (edge.left + edge.right) / 2) to = at + 1;
      });
      // An end may not cross the other one: a band is at least one column wide.
      resize.to = resize.kind === "band-start"
        ? Math.max(0, Math.min(to, resize.span.end - 1))
        : Math.max(resize.span.start - 1, Math.min(to, resize.edges.length - 1));
    }
    showGuide();
  });

  builder.addEventListener("pointerup", (event) => {
    if (!resize || event.pointerId !== resize.pointer) return;
    const done = resize;
    endResize();
    apply(done);
  });

  builder.addEventListener("pointercancel", (event) => {
    if (resize && event.pointerId === resize.pointer) endResize();
  });

  function apply(done) {
    const delta = done.to - done.from;
    if (!delta) return;
    if (done.kind === "rows") {
      moveRowBoundary(done.step, delta);
    } else if (done.kind === "band-start") {
      setBand(done.step, done.to + 1, done.span.end, latest.columns);
    } else {
      setBand(done.step, done.span.start, done.to + 1, latest.columns);
    }
  }

  function endResize() {
    hideGuide();
    builder.classList.remove("is-resizing");
    resize = null;
  }

  /* A line showing where the edge would land. Drawn rather than moving the
     cards, because moving them means re-laying the canvas out, which destroys
     the handle being dragged. */
  let guide = null;

  function showGuide() {
    if (!resize) return;
    if (!guide) {
      guide = document.createElement("div");
      guide.className = "resize-guide";
      document.body.appendChild(guide);
    }
    const edge = resize.edges[resize.to];
    const cell = resize.handle.closest(".builder-cell");
    const box = cell ? cell.getBoundingClientRect() : null;
    if (!box) return;
    if (resize.kind === "rows") {
      // A `to` of -1 is the boundary above the block's first line row — where
      // a step with no ingredients of its own starts out — so there is no edge
      // to read the bottom of and the top of the first row is the same line.
      const y = edge ? edge.bottom : (resize.edges[0] ? resize.edges[0].top : null);
      if (y === null) { hideGuide(); return; }
      guide.hidden = false;
      guide.className = "resize-guide resize-guide--rows";
      guide.style.left = (box.left + window.scrollX) + "px";
      guide.style.width = box.width + "px";
      guide.style.top = (y + window.scrollY) + "px";
      guide.style.height = "";
    } else if (!edge) {
      hideGuide();
    } else {
      guide.hidden = false;
      const at = resize.kind === "band-start" ? edge.left : edge.right;
      guide.className = "resize-guide resize-guide--cols";
      guide.style.left = (at + window.scrollX) + "px";
      guide.style.top = (box.top + window.scrollY) + "px";
      guide.style.height = box.height + "px";
      guide.style.width = "";
    }
  }

  function hideGuide() {
    if (guide) guide.hidden = true;
  }

  /* ---- the same edges, from the keyboard ------------------------------- */

  builder.addEventListener("keydown", (event) => {
    const handle = event.target.closest && event.target.closest("[data-resize]");
    if (!handle || !latest) return;
    const step = parseInt(handle.dataset.step, 10);
    const kind = handle.dataset.resize;

    if (kind === "rows") {
      if (event.key !== "ArrowUp" && event.key !== "ArrowDown") return;
      event.preventDefault();
      moveRowBoundary(step, event.key === "ArrowDown" ? 1 : -1);
      return;
    }
    if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
    event.preventDefault();
    const block = latest.blocks.find((b) => b.steps.indexOf(step) !== -1);
    if (!block || !block.band) return;
    const step1 = event.key === "ArrowRight" ? 1 : -1;
    if (kind === "band-start") {
      setBand(step, block.band.start + step1, block.band.end, latest.columns);
    } else {
      setBand(step, block.band.start, block.band.end + step1, latest.columns);
    }
  });

  /* ---- adding where it is wanted ----------------------------------------
   *
   * A "+" appears between the tiles on hover and mints a row *there*.
   *
   * What it replaces: "+ Zutat" and "+ Schritt" at the top of the page, which
   * added at the end of the formset — so a new ingredient landed in the "not in
   * any step" tray at the bottom and had to be dragged back up to where it was
   * meant to go. Adding something and then moving it is two gestures for one
   * thought, and the household said so.
   *
   * Two kinds:
   *
   *   * between (or below) the ingredient cells in column 1 — a new line, in
   *     the step whose cell covers that row;
   *   * on the right edge of a step tile — a new step *after* it in the chain,
   *     which is what makes the new box cover the same ingredients the previous
   *     one did.
   */

  function plusButton(label, run) {
    const holder = document.createElement("div");
    holder.className = "cell-plus";
    const button = document.createElement("button");
    button.type = "button";
    button.className = "cell-plus-button";
    button.setAttribute("aria-label", label);
    button.title = label;
    button.textContent = "+";
    button.addEventListener("click", (event) => {
      event.preventDefault();
      event.stopPropagation();
      run();
    });
    holder.appendChild(button);
    return holder;
  }

  function addLineAt(step, neighbour, before) {
    /* Mint an ingredient in `step`, beside `neighbour`. */
    const formsets = window.recipeFormsets;
    if (!formsets || !formsets.lines) return;
    const row = formsets.lines.add();
    if (!row) return;
    const index = indexOf(row);
    if (index === null) return;
    setRef(row, "step_index", step === null ? null : step);
    if (neighbour !== null && neighbour !== undefined) {
      moveBeside(lineOrder, index, neighbour, before);
    }
    refresh();
    focusFirst(latest.lineRows.get(index) || row);
  }

  function addSiblingStep(step) {
    /* A second step *below* this one in the same column, feeding the same
     * thing it does — the shape the household could not build.
     *
     * "Zerbröseln und im Wasser auflösen" takes the yeast and the water;
     * "Vormischen" takes the flour, the salt and the sugar; both then go into
     * "Mit Holzlöffel vermischen". Two steps side by side in one column, each
     * with its own ingredients. Every way of adding a step made either a new
     * *column* ("+ Step after this", which inserts into the chain) or a root
     * with no parent — which draws as a band above everything, which is
     * exactly what they saw: "it only stayed above the ingredients".
     */
    const card = latest.stepRows.get(step);
    const formsets = window.recipeFormsets;
    if (!card || !formsets || !formsets.steps) return;
    const row = formsets.steps.add();
    if (!row) return;
    const created = indexOf(row);
    if (created === null) return;
    // The same parent, so it lands beside rather than after — and directly
    // below this one in the order, so it draws where the "+" was pressed.
    setRef(row, "parent_index", refOf(card, "parent_index"));
    moveBeside(stepOrder, created, step, false);
    refresh();
    focusFirst(latest.stepRows.get(created) || row);
  }

  function addStepAfter(step) {
    /* "+ Step after this": a new step that `step` feeds into, inserted between
       it and whatever it used to feed.

       **The new row takes the place in the order that `step` had**, and that
       line is the whole of a bug the household reported: adding a step to the
       right of one moved that step, and its ingredients, to the bottom of the
       list. A new row is minted at the end of the formset, so without this it
       sorted last among its new siblings — and `step`, now a *grand*child
       reached through it, went to the bottom with everything feeding it, while
       the steps beside it rose above. Nothing about the recipe had changed; only
       where a brand-new empty box happened to sit in a list.

       Immediately *before* `step`, with nothing between them, so the new box
       occupies exactly the slot `step` is vacating among its siblings. The
       order somebody is looking at is only ever changed by somebody. */
    const card = latest.stepRows.get(step);
    const formsets = window.recipeFormsets;
    if (!card || !formsets || !formsets.steps) return;
    const row = formsets.steps.add();
    if (!row) return;
    const created = indexOf(row);
    if (created === null) return;
    setRef(row, "parent_index", refOf(card, "parent_index"));
    setRef(card, "parent_index", created);
    moveBeside(stepOrder, created, step, true);
    refresh();
    focusFirst(latest.stepRows.get(created) || row);
  }

  function linePlus(cell, state, where) {
    // `where` is "before" or "after" the line in this cell.
    const label = gettext("Add an ingredient here");
    const plus = plusButton(label, () => addLineAt(cell.step, cell.line, where === "before"));
    plus.classList.add("cell-plus--row", "cell-plus--" + where);
    return plus;
  }

  function stepPlus(cell) {
    const plus = plusButton(gettext("Add a step after this one"),
                            () => addStepAfter(cell.step));
    plus.classList.add("cell-plus--col");
    return plus;
  }

  function siblingPlus(cell) {
    // On the bottom edge: a step *beside* this one, in the same column. The
    // gap between two tiles in a column is exactly where somebody points when
    // they mean "and another one of these".
    const plus = plusButton(gettext("Add a step below, in the same column"),
                            () => addSiblingStep(cell.step));
    plus.classList.add("cell-plus--sibling");
    return plus;
  }

  /* ---- the oven ---------------------------------------------------------
   *
   * A step that says "vorheizen" is almost always a step with a temperature and
   * a mode, and both of those are things a page can put an icon beside and a
   * cooking view can shout across a kitchen. Written into `text` they are
   * neither: "180 °C" inside a sentence is a string, and "Ober-/Unterhitze"
   * typed by hand comes out four ways.
   *
   * So the words are watched for, and when one appears the card grows a panel.
   * Watched rather than required: nobody is made to fill it in, and a step that
   * mentions an oven without one is simply a step.
   *
   * **Watched as it is typed**, which it was not: `ovenPanels` only ran inside
   * `refresh()`, and typing deliberately does not lay the canvas out again — so
   * the panel arrived on the next drag, or on the next page load after a save.
   * The word is the trigger, so the keystroke that completes it is the moment
   * the panel belongs on the card. The input handler at the foot of this file
   * calls `ovenPanel` for the one card being typed into.
   */

  const OVEN_WORDS = [
    "vorheiz", "preheat", "backofen", "ofen", "oven", "backen", "bake",
  ];

  const OVEN_MODES = [
    ["top_bottom", gettext("top and bottom heat")],
    ["fan", gettext("fan")],
    ["top", gettext("top heat")],
    ["bottom", gettext("bottom heat")],
    ["grill", gettext("grill")],
    ["fan_grill", gettext("fan grill")],
  ];

  // What the box will take. The temperature used to be a dropdown of the
  // settings a domestic oven has detents for, on the reasoning that 183 is a
  // slip rather than an intention — but a closed list is wrong the first time
  // somebody's oven, or somebody's recipe, says a number that is not on it, and
  // then there is nowhere to put it at all. A box with a range is the same
  // protection without the wall: 1 to 500, whole degrees.
  //
  // The floor is 1 rather than 0, because 0 is the one number that would be
  // accepted here and dropped later: `RecipeStep.heats_the_oven` reads the
  // column as a boolean and `_oven.html` renders it as one, so a step stored at
  // 0 °C keeps the number and shows nothing. Refusing it in front of somebody
  // is the whole job of this control.
  const OVEN_MIN = 1;
  const OVEN_MAX = 500;

  function mentionsAnOven(row) {
    const field = fieldIn(row, "text");
    const said = (field ? field.value : "").toLowerCase();
    return OVEN_WORDS.some((word) => said.indexOf(word) !== -1);
  }

  function ovenTrouble(raw) {
    /* What is wrong with what is in the box, or "" when nothing is.
     *
     * Empty is not wrong — it is a step whose mode is set and whose temperature
     * has not been typed yet. Everything reaching here is digits, so the only
     * complaint left is the range.
     */
    const said = raw.trim();
    if (said === "") return "";
    const typed = Number(said);
    if (Number.isInteger(typed) && typed >= OVEN_MIN && typed <= OVEN_MAX) return "";
    return interpolate(
      gettext("An oven temperature is a whole number between %(min)s and %(max)s °C."),
      { min: OVEN_MIN, max: OVEN_MAX }, true
    );
  }

  function clearOvenPanel(row) {
    /* Take the whole panel away, and its validity state with it.
     *
     * The second half is not tidiness. `setCustomValidity` on the temperature
     * box makes the browser refuse the submission, which is the point — but a
     * removed step card is `display: none`, and an invalid control that cannot
     * be focused makes Save do *nothing at all*, with a console warning as the
     * only explanation. So a card that has gone loses its complaint.
     */
    const slot = row && row.querySelector("[data-oven-slot]");
    if (!slot || slot.contains(document.activeElement)) return;
    const box = slot.querySelector(".oven-temp-box");
    if (box) box.setCustomValidity("");
    slot.textContent = "";
  }

  function ovenTempBox(row, slot, celsius) {
    /* The temperature, which exists only once there is a mode to go with it. */
    const temp = document.createElement("input");
    // `type="text"`, deliberately, with the numeric keypad asked for
    // separately. A number input *looks* like the stricter choice and is the
    // opposite: it takes "e", "+", "-" and "." as you type and then reports
    // `value` as an empty string, so the page cannot even tell you what you
    // typed. That is exactly where a temperature went missing on save.
    temp.type = "text";
    temp.inputMode = "numeric";
    temp.autocomplete = "off";
    // 500 is three digits, so a fourth cannot be typed at all.
    temp.maxLength = 3;
    // Not `oven-temp` — that class is the *rendered* pill on the recipe page,
    // and one name for two different things is how a style meant for one of
    // them lands on the other.
    temp.className = "row-extra-input oven-temp-box";
    temp.setAttribute("aria-label", slot.dataset.tempLabel || "");
    temp.value = celsius === null ? "" : String(celsius);

    const unit = document.createElement("span");
    unit.className = "oven-temp-unit";
    unit.setAttribute("aria-hidden", "true");
    unit.textContent = "°C";

    const error = document.createElement("span");
    error.className = "field-error oven-error";

    function check() {
      const trouble = ovenTrouble(temp.value);
      error.textContent = trouble;
      temp.classList.toggle("is-invalid", Boolean(trouble));
      temp.setAttribute("aria-invalid", trouble ? "true" : "false");
      // Said by the browser as well as on the page, which is what stops a Save
      // going through with the value quietly absent — the whole of the
      // complaint that a bad number "just goes missing".
      temp.setCustomValidity(trouble);
      // Only a usable value is written through. `input` rather than `change`,
      // because the hidden field is what the POST carries and a value still
      // being typed when somebody presses Save has to be there already.
      const usable = !trouble && temp.value.trim() !== "";
      setRef(row, "oven_celsius", usable ? Number(temp.value.trim()) : null);
    }

    temp.addEventListener("input", () => {
      // Digits and nothing else — no letters, no signs, no separators. Stripped
      // rather than reported: a character that never appears needs no
      // explanation, and it leaves the message below to be only about range.
      const digits = temp.value.replace(/[^0-9]/g, "");
      if (digits !== temp.value) {
        // Otherwise the caret jumps to the end of the box every time a stray
        // key is swallowed, which turns a typo in the middle of "180" into a
        // fight.
        const at = temp.selectionStart === null ? digits.length : temp.selectionStart;
        const lost = temp.value.length - digits.length;
        const back = Math.max(0, at - lost);
        temp.value = digits;
        try {
          temp.setSelectionRange(back, back);
        } catch (err) {
          /* not a field with a caret; nothing to restore */
        }
      }
      check();
    });

    check();
    slot.appendChild(temp);
    slot.appendChild(unit);
    slot.appendChild(error);
  }

  function ovenPanel(row) {
    /* The oven controls on one card, built only when they belong there.
     *
     * The **mode is the anchor**: it is what says a step sets the oven at all,
     * and the temperature is a detail of it. So the select is the panel, and
     * the box appears only once a mode has been chosen — an empty temperature
     * box beside an empty dropdown was two questions where there is one.
     *
     * The exception is a step that already *has* a temperature and no mode.
     * Hiding the box there would leave a saved value on the recipe page with no
     * way to see or change it on the form, which is the trap this control keeps
     * walking into. It stays until somebody empties it.
     */
    const slot = row && row.querySelector("[data-oven-slot]");
    if (!slot) return;

    /* What must not be pulled out from under somebody's fingers — and *only*
     * that.
     *
     * This used to be "do nothing at all while the focus is anywhere in here",
     * which is too broad by exactly the case the panel exists for: choosing a
     * mode leaves the focus **on the select**, so the temperature box it was
     * meant to reveal did not appear until the next page load. Clearing the
     * mode had the mirror problem — the box stayed until a save. The guard is
     * about not destroying a *focused element*, so it now protects that one
     * element rather than the whole slot.
     */
    const busy = slot.contains(document.activeElement) ? document.activeElement : null;

    const celsius = refOf(row, "oven_celsius");
    const modeField = fieldIn(row, "oven_mode");
    const mode = modeField ? modeField.value : "";

    if (!mode && celsius === null && !mentionsAnOven(row)) {
      // Left standing while somebody is in it; the next layout takes it away.
      if (!busy) slot.textContent = "";
      return;
    }

    let modes = slot.querySelector(".oven-select");
    if (!modes) {
      // Building the select means emptying the slot first, so it waits for the
      // focus to leave.
      if (busy) return;
      slot.textContent = "";
      modes = document.createElement("select");
      modes.className = "row-extra-select oven-select";
      modes.setAttribute("aria-label", slot.dataset.modeLabel || "");
      const noMode = document.createElement("option");
      noMode.value = "";
      noMode.textContent = slot.dataset.modeLabel || "";
      modes.appendChild(noMode);
      OVEN_MODES.forEach((pair) => {
        const option = document.createElement("option");
        option.value = pair[0];
        option.textContent = pair[1];
        modes.appendChild(option);
      });
      modes.addEventListener("change", () => {
        if (modeField) modeField.value = modes.value;
        // No mode, no temperature. The box is about to go, and a number left
        // behind in the hidden field would be saved with nothing on the page
        // showing it — the same invisible value, from the other side.
        if (!modes.value) setRef(row, "oven_celsius", null);
        ovenPanel(row);
      });
      slot.appendChild(modes);
    }
    if (modes.value !== mode) modes.value = mode;

    const box = slot.querySelector(".oven-temp-box");
    if (mode || celsius !== null) {
      if (!box) ovenTempBox(row, slot, celsius);
    } else if (box && box !== busy) {
      box.setCustomValidity("");
      box.remove();
      const unit = slot.querySelector(".oven-temp-unit");
      if (unit) unit.remove();
      const said = slot.querySelector(".oven-error");
      if (said) said.remove();
    }
  }

  function ovenPanels(state) {
    // Every step row, not only the live ones: a card somebody has removed is
    // `display: none` and must not keep a validity complaint — `clearOvenPanel`
    // says what that costs.
    state.stepRows.forEach((row) => {
      if (isRemoved(row)) clearOvenPanel(row);
      else ovenPanel(row);
    });
  }

  /* ---- keeping it in step --------------------------------------------- */

  document.addEventListener("formset-rows-changed", refresh);

  /* How tall a step's box has to be depends on how many lines its name wraps
     to, and that depends on two things this file does not control. The columns
     are fluid, so a narrower window is more lines; and the first layout runs
     before the web fonts have arrived, so every box is measured in the
     fallback face — which is narrower here, and leaves the real text one line
     short of the room it was given. Both are measured again rather than
     guessed at. */
  window.addEventListener("resize", fitEveryStep);
  if (document.fonts && document.fonts.ready) document.fonts.ready.then(fitEveryStep);

  let pending = null;
  builder.addEventListener("input", (event) => {
    const field = event.target;
    if (field && typeof field.name === "string" && field.name.endsWith("-text")) {
      const card = field.closest(STEP);
      if (card) fitStepText(card);
      // The oven, on the keystroke that finishes the word rather than on the
      // next layout. `ovenPanel` is cheap when the answer has not changed and
      // never touches a slot somebody's caret is in.
      if (card) ovenPanel(card);
      // The one keystroke that changes how something is drawn. A full-width box
      // with nothing written in it is a blank card; the moment it has a name it
      // is a standing instruction, and the colour is the answer to "why is this
      // one across the whole width?" — so it arrives with the first letter
      // rather than at the next layout.
      const block = field.closest("[data-bare='1']");
      if (block) {
        block.classList.toggle("builder-block--standing", Boolean(field.value.trim()));
      }
    }
    // Typing changes no geometry, so the layout is left alone — moving a card
    // out from under somebody's caret to redraw a name they are halfway
    // through is worse than a stale option list. Only the substitute select,
    // whose options *are* those names, is rebuilt, and only once they stop.
    if (pending) window.clearTimeout(pending);
    pending = window.setTimeout(() => {
      pending = null;
      if (latest) substituteSelects(latest);
    }, 300);
  });

  // Mark the switch before the first layout. `mode` already holds the
  // remembered choice and `render` reads it — but nothing had told the buttons,
  // so the page opened with the list showing and neither option looking
  // selected.
  showMode(mode, false);
  refresh();
})();
