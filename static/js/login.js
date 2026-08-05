/* The password field's "show while held" eye.
 *
 * Held rather than toggled, deliberately. A toggle leaves the password on
 * screen until somebody remembers to turn it off, which on a kitchen tablet
 * that gets put down is exactly the wrong default. Held is enough to check a
 * typo and cannot be left on.
 *
 * A separate file for the same reason as everything else here: the app ships a
 * Content-Security-Policy, so nothing on a page may be inline.
 */
(function () {
  const button = document.querySelector("[data-pw-reveal]");
  if (!button) return;
  const field = button.closest(".pw-wrap").querySelector("input");
  if (!field) return;

  const show = () => { field.type = "text"; };
  const hide = () => { field.type = "password"; };

  // pointerup and pointerleave both, so dragging off the button while held
  // does not leave the password showing with nothing to hide it.
  button.addEventListener("pointerdown", show);
  button.addEventListener("pointerup", hide);
  button.addEventListener("pointerleave", hide);
  // The keyboard equivalent: focus the button and hold space or enter.
  button.addEventListener("keydown", (event) => {
    if (event.key === " " || event.key === "Enter") show();
  });
  button.addEventListener("keyup", hide);
  button.addEventListener("blur", hide);
})();
