/* The × that clears the search box.
 *
 * It submits rather than only emptying the field: the list is server-rendered,
 * so a cleared box with the filtered results still on screen would be a page
 * lying about what it is showing.
 */
(function () {
  const input = document.getElementById("recipe-search-input");
  const clear = document.getElementById("recipe-search-clear");
  const form = document.getElementById("recipe-search");
  if (!input || !clear || !form) return;

  const sync = () => { clear.hidden = !input.value; };

  clear.addEventListener("click", () => {
    input.value = "";
    sync();
    // requestSubmit, not submit: submit() skips constraint validation and every
    // submit listener the page may have.
    if (form.requestSubmit) form.requestSubmit();
    else form.submit();
  });
  input.addEventListener("input", sync);
  sync();
})();
