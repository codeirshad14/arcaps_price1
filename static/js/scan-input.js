/* ==========================================================================
   Scanner input — shared by the kiosk screen and the customer portal.
   --------------------------------------------------------------------------
   A barcode scanner is a keyboard: it "types" the code very fast, usually
   followed by Enter. Whoever scans must never have to press Show Price, so
   a scan is looked up by itself in every case:

     * The scanner ends with Enter          -> looked up at once.
     * The scanner sends no Enter           -> looked up after FLUSH_IDLE_MS
                                               of silence, if the burst was
                                               machine-fast (see below).
     * Nothing has focus (a phone or tablet,
       where focusing the box would raise
       the on-screen keyboard)              -> the keys are caught on the page
                                               and fed into the box.

   Telling a scanner from a person is a matter of speed AND count. A scanner
   sends one keystroke per character, far faster than fingers. A person
   typing is slower, and a phone's word suggestion or a paste drops a whole
   word in with one keystroke or none — no gaps to measure, so speed alone
   once mistook those for scans and submitted them half-typed.

   This lives in its own file because both pages need it. The customer page
   used to have no scanner handling at all, so there a scan without Enter
   just sat in the box waiting for the button.

   Usage:
       ScanInput.attach({
         input: inputElement,
         form: formElement,
         onSubmit: function (text, source) { ... },   // source: scanner | manual
         isPaused: function () { return dialogOpen; } // optional
       });
   ========================================================================== */

window.ScanInput = (function () {
  "use strict";

  var CONFIG = {
    // Keystroke silence after which a scan with no Enter is taken as done.
    FLUSH_IDLE_MS: 120,
    // A burst whose largest gap between keys exceeds this came from a person.
    MACHINE_MAX_GAP_MS: 60,
    // A burst with no Enter is only submitted on its own once it is this long.
    // A quick typist can land two keys inside MACHINE_MAX_GAP_MS by accident,
    // and submitting their half-typed code from under them is far worse than
    // waiting for Enter. Every part number in the catalogue is longer.
    MIN_AUTO_SUBMIT_LEN: 4
  };

  /** Elements whose keys belong to them, not to the scanner. */
  function ownsItsKeys(node) {
    if (!node || !node.tagName) return false;
    if (node.isContentEditable) return true;
    var tag = node.tagName.toLowerCase();
    // Buttons and links included: Space and Enter press them, and a person
    // working the page with a keyboard must keep that.
    return tag === "input" || tag === "textarea" || tag === "select" ||
           tag === "button" || tag === "a" || tag === "summary";
  }

  function attach(options) {
    var input = options.input;
    var form = options.form;
    var onSubmit = options.onSubmit;
    var isPaused = options.isPaused || function () { return false; };
    var burst = { lastKeyAt: 0, maxGap: 0, keys: 0, timer: null };

    if (!input || !form) return null;

    function noteKeystroke() {
      var now = Date.now();
      // A long pause is evidence of a person, so it is recorded rather than
      // treated as the start of a fresh burst. Resetting here instead — as an
      // earlier version did — meant someone typing slowly looked machine-fast
      // on every single keystroke, and had their half-finished code submitted.
      if (burst.lastKeyAt) burst.maxGap = Math.max(burst.maxGap, now - burst.lastKeyAt);
      burst.lastKeyAt = now;
      burst.keys++;

      clearTimeout(burst.timer);
      burst.timer = setTimeout(flushIfMachineTyped, CONFIG.FLUSH_IDLE_MS);
    }

    function looksMachineTyped(text) {
      return text.length >= 2 &&
             burst.keys >= text.length &&
             burst.maxGap <= CONFIG.MACHINE_MAX_GAP_MS;
    }

    function reset() {
      clearTimeout(burst.timer);
      burst.maxGap = 0;
      burst.lastKeyAt = 0;
      burst.keys = 0;
    }

    /** Hand over whatever is in the box, saying whether it was scanned. */
    function submitNow() {
      var text = input.value.trim();
      var source = text && looksMachineTyped(text) ? "scanner" : "manual";
      input.value = "";
      reset();
      onSubmit(text, source);
    }

    /**
     * A scan that arrived without Enter: the scanner simply stopped. The
     * silence plus the machine-fast burst before it is the only evidence,
     * and a person pausing mid-code must never trigger it — which is what
     * the length and speed tests protect: their code stays for them to finish.
     */
    function flushIfMachineTyped() {
      var text = input.value.trim();
      if (text.length < CONFIG.MIN_AUTO_SUBMIT_LEN) return;
      if (!looksMachineTyped(text)) return;
      submitNow();
    }

    input.addEventListener("keydown", function (event) {
      // Enter reaches the form's submit below; just stop the idle flush
      // from firing underneath it.
      if (event.key === "Enter") { clearTimeout(burst.timer); return; }
      noteKeystroke();
    });

    form.addEventListener("submit", function (event) {
      event.preventDefault();
      submitNow();
    });

    // Keys that land on the page itself, because nothing is focused: on a
    // phone or tablet nothing is, on purpose. Fed into the box so the scan
    // still happens — and shows in the box as it does.
    document.addEventListener("keydown", function (event) {
      if (event.target === input || ownsItsKeys(event.target)) return;
      if (event.ctrlKey || event.metaKey || event.altKey) return;
      if (isPaused()) return;

      if (event.key === "Enter") {
        if (!input.value.trim()) return;
        event.preventDefault();
        submitNow();
        return;
      }
      if (event.key && event.key.length === 1) {
        event.preventDefault();
        input.value += event.key;
        noteKeystroke();
      }
    });

    return { reset: reset };
  }

  return { attach: attach, CONFIG: CONFIG };
})();
