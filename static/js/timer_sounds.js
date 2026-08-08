/* The kitchen timer's sounds, generated rather than fetched.
 *
 * Loaded by the cooking view (which rings one when a step's timer reaches
 * nought) and by the settings page (which plays them so somebody can choose).
 * One file, because two copies of a tone table is how the sound you picked and
 * the sound you get come to differ.
 *
 * ---- why they are synthesised ----
 *
 * An audio file would be a binary asset in the repository, a request the
 * Content-Security-Policy has to allow, and a decode that a phone with its
 * screen off may not have finished by the time the bread is ready. A handful of
 * oscillator notes have none of those problems and are a few lines each.
 *
 * ---- and why the context is made on a gesture ----
 *
 * A browser will not let a page make a noise until somebody has touched it, and
 * an AudioContext created before that starts "suspended" and plays silence
 * without erroring. So it is created — or resumed — inside the press of Start
 * or Play, which is the last moment a gesture is still in hand.
 *
 * ---- one tone, and a tone that keeps going ----
 *
 * `play` sounds a tone once; `ring` sounds it over and over until `silence` is
 * called. The cooking view rings, because a timer that chimed once was a timer
 * you missed by being in the next room — which is the whole failure a kitchen
 * timer exists to prevent. The settings page plays, because it is a sample.
 *
 * `silence` has to stop what is *already sounding* and not merely stop the next
 * repeat, so every note of one sounding goes through a gain node of its own
 * that can simply be disconnected. Stopping the oscillators is not enough on
 * its own: a note scheduled to start in a fifth of a second has not started,
 * and calling `stop()` on one that has not started throws.
 */
(function () {
  // [frequency in Hz, seconds from the start, seconds long] per note.
  const SOUNDS = {
    chime: [[880, 0, 0.5], [1174.7, 0.18, 0.6]],
    bell: [[660, 0, 1.6]],
    beeps: [[1046.5, 0, 0.14], [1046.5, 0.22, 0.14], [1046.5, 0.44, 0.14]],
    alarm: [[1318.5, 0, 0.18], [988, 0.2, 0.18], [1318.5, 0.4, 0.18],
            [988, 0.6, 0.18], [1318.5, 0.8, 0.3]],
    // "none" is deliberately absent: an unknown name and "no sound" are the
    // same instruction, and one lookup covers both.
  };

  let context = null;

  function wake() {
    if (context) {
      // Suspended again — a tab that was backgrounded comes back this way.
      if (context.state === "suspended" && context.resume) context.resume();
      return context;
    }
    const Ctor = window.AudioContext || window.webkitAudioContext;
    if (!Ctor) return null;
    try {
      context = new Ctor();
    } catch (err) {
      context = null;
    }
    return context;
  }

  // The gap between one sounding and the next when it is ringing rather than
  // playing. Long enough that "beeps" does not turn into a continuous tone,
  // short enough that somebody walking back into the kitchen does not decide
  // they imagined it.
  const GAP = 0.8;

  // Every sounding's own output, so `silence` can cut one that is halfway
  // through — including its notes that have not started yet.
  let sounding = [];
  let repeat = null;

  function lengthOf(name) {
    const notes = SOUNDS[name] || [];
    return notes.reduce((longest, note) => Math.max(longest, note[1] + note[2]), 0);
  }

  function play(name) {
    const notes = SOUNDS[name];
    if (!notes) return false;
    const audio = wake();
    if (!audio) return false;
    const at = audio.currentTime;
    // One bus per sounding. Disconnecting it is what makes silencing immediate:
    // an oscillator due to start later cannot be stopped (calling `stop` before
    // `start` throws), but it can be left connected to nothing.
    const bus = audio.createGain();
    bus.gain.value = 1;
    bus.connect(audio.destination);
    sounding.push(bus);
    notes.forEach((note) => {
      const osc = audio.createOscillator();
      const gain = audio.createGain();
      osc.type = "sine";
      osc.frequency.value = note[0];
      // Ramped rather than switched. A gain that jumps from nothing to full
      // clicks, and the click is the part people find unpleasant — not the
      // tone. exponentialRampToValueAtTime cannot reach zero, hence 0.0001.
      gain.gain.setValueAtTime(0.0001, at + note[1]);
      gain.gain.exponentialRampToValueAtTime(0.28, at + note[1] + 0.02);
      gain.gain.exponentialRampToValueAtTime(0.0001, at + note[1] + note[2]);
      osc.connect(gain);
      gain.connect(bus);
      osc.start(at + note[1]);
      osc.stop(at + note[1] + note[2] + 0.05);
    });
    // Dropped once it can no longer make a noise, so a long cooking session
    // does not accumulate a bus per ring.
    window.setTimeout(() => {
      try {
        bus.disconnect();
      } catch (err) { /* already gone */ }
      sounding = sounding.filter((other) => other !== bus);
    }, (lengthOf(name) + 0.2) * 1000);
    return true;
  }

  function ring(name) {
    /* Sound it, and keep sounding it until somebody stops it. */
    silence();
    if (!play(name)) return false;
    repeat = window.setInterval(() => {
      if (!play(name)) silence();
    }, (lengthOf(name) + GAP) * 1000);
    return true;
  }

  function silence() {
    if (repeat) window.clearInterval(repeat);
    repeat = null;
    sounding.forEach((bus) => {
      try {
        bus.disconnect();
      } catch (err) { /* nothing left to disconnect */ }
    });
    sounding = [];
  }

  function isRinging() {
    return repeat !== null;
  }

  window.kitchenSounds = {
    play: play, ring: ring, silence: silence, isRinging: isRinging,
    wake: wake, names: Object.keys(SOUNDS),
  };
})();
