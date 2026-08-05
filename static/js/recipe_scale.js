/* Scaling a recipe to a different number of servings.
 *
 * Client-side, and there are two reasons rather than one. It is instant, which
 * matters because scaling is something people fiddle with — up to six, back to
 * four, up to eight — and a round trip per press turns that into a page that
 * blinks. And it keeps the *stored* recipe as the source of truth: nothing is
 * saved, so a scaled view is a view rather than an edit somebody has to undo.
 *
 * Three details that are easy to get wrong:
 *
 * - The base amount is read from `data-amount` every time, never from the text
 *   this script wrote last time. Re-parsing its own output accumulates rounding
 *   error, so stepping up to 6 and back to 4 leaves "249.99 g flour".
 * - Every `.ingredient-amount` **on the page** is scaled, not only the ones in
 *   the sidebar list. The same amount now appears in up to three places — the
 *   list, the diagram cell, the current step of the cooking view — and a scaler
 *   that moves one of them is worse than one that moves none: two numbers on
 *   screen disagreeing, with nothing to say which is the real one.
 * - The stepper starts hidden and is revealed here. A control that is rendered
 *   by the server and does nothing without JavaScript is worse than no control.
 */
(function () {
  const scaler = document.querySelector("[data-serving-scaler]");
  if (!scaler) return;

  // The scaler carries the base itself. It used to be read off the ingredient
  // list, which the cooking view does not render — the amounts there live in
  // the step cards and the diagram, and the scaler would simply never appear.
  const source = scaler.dataset.baseServings
    ? scaler
    : document.querySelector("[data-base-servings]");
  const base = parseInt((source && source.dataset.baseServings) || "", 10);
  if (!base || base < 1) return;

  const countEl = scaler.querySelector("[data-serving-count]");
  const noteEl = scaler.querySelector("[data-serving-note]");
  const down = scaler.querySelector("[data-serving-down]");
  const up = scaler.querySelector("[data-serving-up]");
  if (!countEl || !down || !up) return;

  // Read once. Steps are hidden and shown by the cooking view rather than
  // created, so the set does not change under this script.
  const cells = Array.from(document.querySelectorAll(".ingredient-amount"))
    .map((el) => ({ el, base: parseFloat(el.dataset.amount) }))
    .filter((cell) => !Number.isNaN(cell.base));

  // Locale-aware, because the page is German by default and "1,5" is how that
  // number is written there — the same reason Django localises its own output.
  // maximumFractionDigits: 2 is the recipe-card answer to 1/3 of 250 g: "83,33"
  // rather than "83.33333333333333".
  const format = new Intl.NumberFormat(document.documentElement.lang || "de", {
    maximumFractionDigits: 2,
  });

  const MAX = 99;
  let servings = base;

  function render() {
    countEl.textContent = format.format(servings);
    const factor = servings / base;
    cells.forEach(({ el, base: amount }) => {
      el.textContent = format.format(amount * factor);
    });
    if (noteEl) noteEl.hidden = servings === base;
    down.disabled = servings <= 1;
    up.disabled = servings >= MAX;

    // The cooking view records what was actually made, so its "servings made"
    // box follows the stepper rather than asking somebody to type the number
    // they have just chosen twice.
    document.querySelectorAll("[data-servings-made]").forEach((input) => {
      input.value = String(servings);
    });
  }

  down.addEventListener("click", () => { if (servings > 1) { servings -= 1; render(); } });
  up.addEventListener("click", () => { if (servings < MAX) { servings += 1; render(); } });

  scaler.hidden = false;
  render();
})();
