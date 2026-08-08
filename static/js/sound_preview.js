/* "Play" beside each timer sound on the settings page.
 *
 * The whole reason the page has these buttons: a sound is only useful if it
 * cuts through your kitchen, and there is no way to tell that from the words
 * "chime" or "alarm". The tones come from static/js/timer_sounds.js, which is
 * also what the cooking view rings — so what you hear here is what you get.
 *
 * Pressing Play also ticks the radio beside it. Somebody comparing sounds is
 * choosing one; making them press twice for that is a step that exists only
 * because the code was written in two halves.
 */
(function () {
  const list = document.querySelector("[data-sound-choices]");
  if (!list || !window.kitchenSounds) return;

  list.addEventListener("click", (event) => {
    const button = event.target.closest("[data-sound-play]");
    if (!button) return;
    const name = button.dataset.soundPlay;

    const row = button.closest(".sound-choice");
    const radio = row && row.querySelector("input[type='radio']");
    if (radio && !radio.checked) {
      radio.checked = true;
      // The unsaved-changes guard in shell.js listens for real events, and a
      // property set from script fires none.
      radio.dispatchEvent(new Event("change", { bubbles: true }));
    }

    // Silenced first, so comparing four sounds by pressing along the list does
    // not layer them on top of each other. One sounding rather than the ringing
    // the cooking view does: this is a sample of the *tone*, and a settings page
    // that starts an alarm nothing on it can stop is not one.
    window.kitchenSounds.silence();
    window.kitchenSounds.play(name);
  });
})();
