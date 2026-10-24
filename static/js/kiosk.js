/* ==========================================================================
   Showroom Price Checker — kiosk frontend
   --------------------------------------------------------------------------
   Sections:
     1.  Configuration and state
     2.  Element handles
     3.  Small helpers
     4.  Station selection
     5.  Theme application
     6.  Focus management        (the code box must never lose the cursor)
     7.  Scanner input handling
     8.  Manual entry
     9.  Spoken-code parser
     10. Voice engine + wake word
     11. API calls
     12. Rendering
     13. Countdown and view switching
     14. Station picker + fullscreen
     15. Boot

   The page is three views toggled by JavaScript. There are no page reloads:
   a scan swaps the view and the countdown returns it to idle.
   ========================================================================== */

(function () {
  "use strict";

  /* ---------- 1. Configuration and state -------------------------------- */

  var CONFIG = {
    // A scan ends with Enter. For scanners not configured to send one, flush
    // the buffer after this much keystroke silence.
    FLUSH_IDLE_MS: 120,
    // Scanners type far faster than people. If the largest gap between two
    // keystrokes exceeds this, the input came from a human at the keyboard.
    MACHINE_MAX_GAP_MS: 60,
    // A burst with no terminating Enter is only submitted on its own once it
    // is this long. A quick typist can land two characters inside
    // MACHINE_MAX_GAP_MS by accident, and submitting their half-typed code
    // out from under them is far worse than waiting for Enter; no real
    // product code is shorter than this anyway.
    MIN_AUTO_SUBMIT_LEN: 4,
    // Ignore the same code submitted twice in quick succession — scanners
    // sometimes fire twice off one trigger pull.
    DUPLICATE_WINDOW_MS: 500,
    REFOCUS_INTERVAL_MS: 800,
    TOAST_MS: 3200,
    // Wake-word listening restarts constantly; back off if it keeps failing
    // so a denied microphone cannot spin the CPU.
    WAKE_RESTART_MS: 400,
    WAKE_MAX_FAILURES: 5,

    // --- Voice responsiveness ---------------------------------------------
    // The engine only fires `onend` after its own end-of-speech silence
    // timeout, which is 1.5-3s after the customer stops talking and much
    // longer when it hears nothing. Waiting for that made every spoken
    // lookup feel slow, so a code is acted on the moment it is recognised.

    // An interim guess must repeat unchanged for this long before we trust
    // it. Long enough to avoid acting on a half-heard number, short enough
    // to beat the engine's own endpointing.
    INTERIM_STABLE_MS: 550,
    // Hard cap on a single listen, so a silent or stuck engine cannot leave
    // the customer waiting.
    LISTEN_TIMEOUT_MS: 7000,
    // Gap before re-listening in the other language.
    LANG_RETRY_MS: 250
  };

  var STORAGE = {
    STATION: "kiosk.station",
    // Note: there is deliberately no stored voice language. Remembering it
    // made the on-screen language flip to whatever understood the last
    // customer and stay there. The station setting decides it instead.
    MUTED: "kiosk.priceSpeechMuted"
  };

  var state = {
    display: null,        // the `display` block from the API
    station: null,        // station slug in force
    view: "idle",
    lastCode: null,
    lastCodeAt: 0,
    countdownTimer: null,
    toastTimer: null,
    inFlight: null,       // AbortController for the current lookup
    items: [],            // products currently on screen (multi-scan)
    voice: {
      recognition: null,
      supported: false,
      listening: false,
      mode: "idle",       // idle | code | wake
      wakeArmed: false,
      // Set when a person presses Stop. Without it, the next settings refresh
      // would re-arm the microphone behind their back.
      userStopped: false,
      wakeFailures: 0,
      finalTranscript: ""
    }
  };

  var scanner = { lastKeyAt: 0, maxGap: 0, flushTimer: null };

  /* ---------- 2. Element handles ---------------------------------------- */

  var $ = function (id) { return document.getElementById(id); };

  var el = {
    body: document.body,
    views: { idle: $("view-idle"), result: $("view-result"), notfound: $("view-notfound") },

    manualForm: $("manual-form"),
    manualInput: $("manual-input"),

    speakPanel: $("panel-speak"),
    speakHint: $("speak-hint"),
    micButton: $("mic-button"),
    micLabel: $("mic-label"),
    wakeStop: $("wake-stop"),
    wakeStopLabel: $("wake-stop-label"),
    voicePanel: $("voice-panel"),
    voiceStatus: $("voice-status"),
    voiceTranscript: $("voice-transcript"),
    voiceLang: $("voice-lang"),
    voiceLangBadge: $("voice-lang-badge"),

    resultStage: $("result-stage"),
    cardTemplate: $("product-card-template"),
    priceTierTemplate: $("price-tier-template"),
    multiHint: $("multi-hint"),

    notfoundCode: $("notfound-code"),

    countdownFill: $("countdown-fill"),
    countdownText: $("countdown-text"),
    countdownFillNf: $("countdown-fill-nf"),
    countdownTextNf: $("countdown-text-nf"),
    scanNext: $("scan-next"),
    scanNextNf: $("scan-next-nf"),

    muteToggle: $("mute-toggle"),
    stationButton: $("station-button"),
    stationModal: $("station-modal"),
    stationList: $("station-list"),
    stationCancel: $("station-cancel"),
    stationLabel: $("station-label"),

    fullscreenToggle: $("fullscreen-toggle"),
    toast: $("toast")
  };

  /* ---------- 3. Small helpers ------------------------------------------ */

  function show(node) { if (node) node.hidden = false; }
  function hide(node) { if (node) node.hidden = true; }
  function setText(node, text) { if (node) node.textContent = text; }

  function storageGet(key) {
    try { return window.localStorage.getItem(key); } catch (err) { return null; }
  }

  function storageSet(key, value) {
    try {
      if (value === null) window.localStorage.removeItem(key);
      else window.localStorage.setItem(key, value);
    } catch (err) { /* private mode; not important enough to surface */ }
  }

  /**
   * Format a decimal string for display.
   * Paise are dropped when zero — "₹499" reads better across a showroom than
   * "₹499.00" — but kept when they carry information.
   */
  function formatMoney(value, symbol) {
    var amount = Number(value);
    if (!isFinite(amount)) return String(value || "");
    var hasPaise = Math.round(amount * 100) % 100 !== 0;
    var text = amount.toLocaleString("en-IN", {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2
    });
    return (symbol || "") + text;
  }

  /** "42.000" -> "42",  "5.500" -> "5.5" */
  function trimDecimals(value) {
    var n = Number(value);
    if (!isFinite(n)) return String(value || "");
    return String(parseFloat(n.toFixed(3)));
  }

  function toast(message, isError) {
    if (!el.toast) return;
    clearTimeout(state.toastTimer);
    el.toast.textContent = message;
    el.toast.classList.toggle("is-error", !!isError);
    show(el.toast);
    state.toastTimer = setTimeout(function () { hide(el.toast); }, CONFIG.TOAST_MS);
  }

  /* ---------- 4. Station selection --------------------------------------- */

  /**
   * Work out which station this system is.
   *
   * `?station=` wins and is saved, so a machine is set up once by URL and
   * remembers it from then on. `?station=` with an empty value clears it.
   */
  function resolveStation() {
    var params = new URLSearchParams(window.location.search);

    if (params.has("station")) {
      var requested = (params.get("station") || "").trim();
      storageSet(STORAGE.STATION, requested || null);
      state.station = requested || null;
      return;
    }
    state.station = storageGet(STORAGE.STATION);
  }

  /** Append the station to an API URL when one is set. */
  function withStation(url) {
    if (!state.station) return url;
    return url + (url.indexOf("?") === -1 ? "?" : "&") +
           "station=" + encodeURIComponent(state.station);
  }

  /* ---------- 5. Theme application --------------------------------------- */

  /**
   * Push the station's palette into CSS custom properties.
   *
   * The server already wrote these into the page head for the first paint;
   * this keeps them current when the admin changes colours mid-session.
   */
  function applyTheme(display) {
    var theme = display && display.theme;
    if (!theme) return;

    var root = document.documentElement;
    var map = {
      "--bg-start": theme.bg_start,
      "--bg-end": theme.bg_end,
      "--accent": theme.accent,
      "--price": theme.price,
      "--text": theme.text,
      "--text-dim": theme.text_dim,
      "--ok": theme.in_stock,
      "--bad": theme.out_of_stock
    };
    Object.keys(map).forEach(function (name) {
      if (map[name]) root.style.setProperty(name, map[name]);
    });
    if (theme.card_opacity != null) {
      root.style.setProperty("--card-alpha", theme.card_opacity + "%");
    }

    var background = display.background || {};
    if (background.image) {
      root.style.setProperty("--bg-image", 'url("' + background.image + '")');
      root.style.setProperty("--bg-overlay", (background.overlay || 0) + "%");
      el.body.classList.add("has-bg-image");
    } else {
      el.body.classList.remove("has-bg-image");
    }
    el.body.setAttribute("data-bg-fit", background.fit || "cover");
  }

  /* ---------- 6. Focus management ---------------------------------------- */

  /**
   * Keep the cursor in the code box.
   *
   * That box is the scanner's target as well as the typing box, so a scan is
   * lost whenever anything else holds focus — which is why this is enforced
   * rather than merely set once at startup. The station dialog is the single
   * exception: fighting it for focus would make its buttons unusable.
  
  function keepCodeFocus() {
    if (!el.manualInput) return;
    if (el.stationModal && !el.stationModal.hidden) return;
    if (document.activeElement !== el.manualInput) {
      el.manualInput.focus({ preventScroll: true });
    }
  }
 */
  function keepCodeFocus() {
    if (!el.manualInput) return;
    if (el.stationModal && !el.stationModal.hidden) return;
    // macOS enters fullscreen with a native ~1s window animation. Stealing
    // focus while that is in flight makes the browser abandon it, so the
    // screen flicks in and straight back out. Windows/Android transition
    // instantly and never hit this.
    if (state.fullscreenPending) return;
    if (document.activeElement !== el.manualInput) {
      el.manualInput.focus({ preventScroll: true });
    }
  }





  function bindFocusGuards() {
    // Anything that takes focus — pressing a button, a stray click on the
    // page — hands it straight back.
    el.manualInput.addEventListener("blur", function () {
      setTimeout(keepCodeFocus, 0);
    });

    document.addEventListener("click", function () {
      setTimeout(keepCodeFocus, 0);
    });

    window.addEventListener("focus", keepCodeFocus);

    // Backstop for whatever the events above cannot see: a dialog dismissed
    // by the browser, the window manager handing focus elsewhere, a restored
    // background tab. A showroom screen runs for days, so it only has to go
    // wrong once to leave the scanner typing into nothing.
    setInterval(keepCodeFocus, CONFIG.REFOCUS_INTERVAL_MS);
  }

  /* ---------- 7. Scanner input handling ---------------------------------- */

  /*
    The scanner and the keyboard share one visible box.

    There used to be a second, offscreen input that the scanner typed into, so
    a scan could never be lost to whatever else held focus. The cost was that
    the visible box never showed a cursor and staff had to click it before
    they could type. One permanently focused box gives both.

    Telling the two apart is a matter of speed: a scanner emits its characters
    far faster than fingers can, so the largest gap between keystrokes in a
    burst decides which source the lookup is logged under.
  */
  function bindScanner() {
    el.manualInput.addEventListener("keydown", function (event) {
      var now = Date.now();

      // Enter is the scanner's terminator and the person's "go". Both reach
      // the form's submit handler, so the only job here is to stop the
      // idle flush from firing underneath it.
      if (event.key === "Enter") {
        clearTimeout(scanner.flushTimer);
        return;
      }

      // A long pause is evidence of a person, so it is recorded rather than
      // treated as the start of a fresh burst. Resetting here instead — as an
      // earlier version did — meant someone typing slowly looked machine-fast
      // on every single keystroke, and had their half-finished code submitted.
      if (scanner.lastKeyAt) {
        scanner.maxGap = Math.max(scanner.maxGap, now - scanner.lastKeyAt);
      }
      scanner.lastKeyAt = now;

      clearTimeout(scanner.flushTimer);
      scanner.flushTimer = setTimeout(flushIfMachineTyped, CONFIG.FLUSH_IDLE_MS);
    });
  }

  /** True when this burst arrived faster than a person could have typed it. */
  function looksMachineTyped(code) {
    return code.length >= 2 && scanner.maxGap <= CONFIG.MACHINE_MAX_GAP_MS;
  }

  /** Start measuring again, once a burst has been dealt with. */
  function resetBurst() {
    scanner.maxGap = 0;
    scanner.lastKeyAt = 0;
  }

  /**
   * Submit a scan that arrived without a terminating Enter.
   *
   * Scanners that are not configured to send one simply stop typing, so the
   * only evidence left is the silence afterwards plus the machine-speed burst
   * before it. A person pausing mid-code must never trigger this, which is
   * what the speed and length tests are protecting: their unfinished code
   * stays in the box for them to carry on with.
   */
  function flushIfMachineTyped() {
    var code = el.manualInput.value.trim();
    if (code.length < CONFIG.MIN_AUTO_SUBMIT_LEN) return;
    if (!looksMachineTyped(code)) return;

    el.manualInput.value = "";
    resetBurst();
    submitCodes([code], "scanner");
  }

  /* ---------- 8. Manual entry -------------------------------------------- */

  function bindManualEntry() {
    el.manualForm.addEventListener("submit", function (event) {
      event.preventDefault();
      clearTimeout(scanner.flushTimer);

      var typed = el.manualInput.value.trim();
      if (!typed) {
        toast("Scan a tag, or type a barcode or QR number first.");
        keepCodeFocus();
        return;
      }

      // A scanner's Enter and a person's Enter both arrive here; the burst
      // speed is the only thing separating them for the scan log.
      var source = looksMachineTyped(typed) ? "scanner" : "manual";

      el.manualInput.value = "";
      resetBurst();
      // Allow "1345, 1346" to compare two products in one go.
      submitCodes(splitCodes(typed), source);
      // Never blur: the box has to stay ready for the next scan.
      keepCodeFocus();
    });
  }

  /** Split a typed string on commas/spaces into separate codes. */
  function splitCodes(text) {
    var parts = text.split(/[,\s]+/);
    var out = [];
    for (var i = 0; i < parts.length; i++) {
      var part = parts[i].trim();
      if (part) out.push(part);
    }
    return out;
  }

  /* ---------- 9. Spoken-code parser -------------------------------------- */

  // Spoken digits in English, Hindi and Marathi (romanised + Devanagari).
  //
  // Keys are quoted throughout: several of these words ("do", "this", "for")
  // are reserved words, and quoting sidesteps any engine that treats a bare
  // reserved word as a syntax error rather than a property name.
  var DIGIT_WORDS = {
    "zero": "0", "oh": "0", "shunya": "0", "sunya": "0", "sifar": "0",
    "one": "1", "ek": "1", "eak": "1",
    "two": "2", "do": "2", "doh": "2",
    "three": "3", "teen": "3", "tin": "3",
    "four": "4", "char": "4", "chaar": "4",
    "five": "5", "paanch": "5", "panch": "5", "paach": "5",
    // "che" is deliberately absent: it is Marathi for "of" (चे). Treating it
    // as 6 would turn "1345 che kimat" into "13456".
    "six": "6", "chhe": "6", "chah": "6", "chay": "6",
    "seven": "7", "saat": "7", "sat": "7",
    "eight": "8", "aath": "8", "ath": "8",
    // "no" is deliberately absent too — it collides with English "no. 1345".
    "nine": "9", "nau": "9", "nao": "9",

    // Marathi, romanised. Only the numbers that differ from Hindi.
    "don": "2", "doan": "2",
    "saha": "6", "sahaa": "6",
    "nav": "9",

    // How the English recogniser actually writes down Hindi/Marathi digits.
    //
    // This is what makes "auto" work on the FIRST attempt: en-IN does not
    // return Devanagari, it returns its best English spelling of what it
    // heard. Covering those spellings avoids having to ask the customer to
    // repeat themselves in another language.
    //
    // Deliberately excluded because they are ordinary English words that
    // appear in real price questions: "no"/"now" (9), "for" (4), "to"/"too"
    // (2), "at"/"ate" (8), "sat" is kept only because it is already an
    // established Hindi spelling.
    "aik": "1", "ik": "1", "ekk": "1",
    "donn": "2", "doe": "2",
    "theen": "3", "teenn": "3",
    "chaaar": "4", "chare": "4",
    "paanj": "5", "punch": "5",
    "chhah": "6", "chhey": "6", "chhe6": "6",
    "saatt": "7", "saath": "7",
    "aatth": "8", "aat": "8",
    "nauu": "9", "nou": "9", "naw": "9",

    // Devanagari, shared by Hindi and Marathi. The hi-IN and mr-IN
    // recognisers return script, not romanised text, so these are required
    // for spoken Hindi or Marathi to resolve at all.
    "शून्य": "0",
    "एक": "1",
    "दो": "2",
    "दोन": "2",
    "तीन": "3",
    "चार": "4",
    "पांच": "5",
    "पाँच": "5",
    "पाच": "5",
    "छह": "6",
    "छे": "6",
    "सहा": "6",
    "सात": "7",
    "आठ": "8",
    "नौ": "9",
    "नऊ": "9"
  };

  // Words that carry no code information, in all three languages.
  var FILLER_WORDS = {
    "barcode": 1, "bar": 1, "code": 1, "qr": 1, "cue": 1, "number": 1,
    "num": 1, "nam": 1, "price": 1, "rate": 1, "cost": 1, "mrp": 1,
    "product": 1, "item": 1, "scan": 1, "check": 1,
    "what": 1, "whats": 1, "is": 1, "the": 1, "of": 1, "for": 1, "show": 1,
    "me": 1, "tell": 1, "please": 1, "this": 1, "that": 1, "my": 1,
    "a": 1, "an": 1, "and": 1, "also": 1, "aur": 1, "ani": 1,
    "ka": 1, "ki": 1, "ke": 1, "kya": 1, "kitna": 1, "kitne": 1, "kitni": 1,
    "hai": 1, "he": 1, "batao": 1, "bata": 1, "bolo": 1, "bol": 1,
    "dikhao": 1, "dikha": 1, "kar": 1, "karo": 1,
    "mujhe": 1, "iska": 1, "uska": 1,

    // Marathi, romanised.
    "cha": 1, "chi": 1, "che": 1, "cya": 1,
    "kay": 1, "kiti": 1, "kimat": 1, "kimmat": 1, "bhav": 1,
    "ahe": 1, "sanga": 1, "sangaa": 1, "dakhva": 1, "dakhava": 1,
    "mala": 1, "yacha": 1, "yachi": 1, "kramank": 1,

    // Devanagari fillers, Hindi and Marathi together.
    "का": 1, "की": 1, "के": 1,
    "क्या": 1, "कितना": 1,
    "कितने": 1, "है": 1,
    "बताओ": 1, "बता": 1,
    "बोलो": 1, "दिखाओ": 1,
    "दाम": 1, "कीमत": 1,
    "प्राइस": 1, "रेट": 1,
    "बारकोड": 1, "कोड": 1,
    "नंबर": 1, "मुझे": 1,
    "इसका": 1, "और": 1,
    "चा": 1, "ची": 1, "चे": 1,
    "काय": 1, "किती": 1,
    "किंमत": 1, "भाव": 1,
    "आहे": 1, "सांगा": 1,
    "दाखवा": 1, "मला": 1,
    "याचा": 1, "याची": 1,
    "क्रमांक": 1, "आणि": 1
  };

  /**
   * Convert Devanagari digits (०-९) to ASCII.
   * Speech results in Hindi/Marathi often mix scripts, e.g. "१३४५" or "1345".
   */
  function normaliseDigits(text) {
    return text.replace(/[०-९]/g, function (ch) {
      return String(ch.charCodeAt(0) - 0x0966);
    });
  }

  /** Lowercase, fold digits, and reduce a transcript to comparable words. */
  function normaliseTranscript(text) {
    return normaliseDigits(text || "")
      .toLowerCase()
      .replace(/[.,!?;:'"।]/g, " ")
      .replace(/[-_/]+/g, " ")
      .replace(/\s+/g, " ")
      .trim();
  }

  /**
   * Pull every likely product code out of a spoken sentence.
   *
   * Handles the shapes people actually say:
   *   "barcode number 1 3 4 5 ka kya price hai"  -> ["1345"]
   *   "1345 aur 1346 ka price"                   -> ["1345", "1346"]
   *   "एक तीन चार पाच ची किंमत सांगा"              -> ["1345"]
   *
   * @returns {string[]} candidate codes, longest first
   */
  function parseSpokenCodes(transcript) {
    var cleaned = normaliseTranscript(transcript);
    if (!cleaned) return [];

    var tokens = cleaned.split(" ");
    var kept = [];

    for (var i = 0; i < tokens.length; i++) {
      var token = tokens[i];
      if (!token) continue;

      if (Object.prototype.hasOwnProperty.call(DIGIT_WORDS, token)) {
        kept.push(DIGIT_WORDS[token]);
        continue;
      }
      if (FILLER_WORDS[token] === 1) {
        // A filler breaks a digit run: "1345 aur 1346" must stay two codes.
        kept.push(null);
        continue;
      }

      // Keep anything alphanumeric; strip stray characters. Unrecognised
      // Devanagari reduces to "" here and drops out, which is what we want —
      // a product code is never Hindi script.
      var alnum = token.replace(/[^a-z0-9]/g, "");
      kept.push(alnum || null);
    }

    // Join runs of consecutive numeric tokens: "1 3 4 5" -> "1345".
    var merged = [];
    var run = "";
    for (var j = 0; j < kept.length; j++) {
      var piece = kept[j];
      if (piece && /^\d+$/.test(piece)) {
        run += piece;
      } else {
        if (run) { merged.push(run); run = ""; }
        if (piece) merged.push(piece);
      }
    }
    if (run) merged.push(run);

    // Longest first: the code is normally the longest token left standing.
    merged.sort(function (a, b) { return b.length - a.length; });
    return merged;
  }

  /** Backwards-compatible single-code helper. */
  function parseSpokenCode(transcript) {
    var codes = parseSpokenCodes(transcript);
    return codes.length ? codes[0] : "";
  }

  /**
   * Detect a wake phrase and return whatever was said after it.
   *
   * @returns {{matched: boolean, remainder: string}}
   */
  function matchWakePhrase(transcript, phrases) {
    var cleaned = normaliseTranscript(transcript);
    if (!cleaned || !phrases || !phrases.length) {
      return { matched: false, remainder: "" };
    }

    for (var i = 0; i < phrases.length; i++) {
      var phrase = normaliseTranscript(phrases[i]);
      if (!phrase) continue;

      var at = cleaned.indexOf(phrase);
      if (at !== -1) {
        return { matched: true, remainder: cleaned.slice(at + phrase.length).trim() };
      }

      // Speech engines often drop the space in a name ("mr caps" -> "mrcaps"),
      // so compare with spaces removed as well.
      var squashedPhrase = phrase.replace(/\s+/g, "");
      var squashed = cleaned.replace(/\s+/g, "");
      if (squashedPhrase.length >= 5 && squashed.indexOf(squashedPhrase) !== -1) {
        return { matched: true, remainder: "" };
      }
    }
    return { matched: false, remainder: "" };
  }

  /* ---------- 10. Voice engine + wake word -------------------------------- */

  /*
    Language handling is automatic — there is no language button.

    The Web Speech API cannot detect the language itself: it decodes against
    one model at a time. So we lean on two things instead:

      1. en-IN already handles English *and* Hinglish well ("1345 ka price"),
         because the Indian English model is trained on code-switched speech.
         Asking for several alternatives widens the net further.
      2. If nothing in any alternative parses to a code, we automatically
         re-listen once using another language.

    Whichever language produced a usable code is remembered and tried first
    next time, so a Hindi- or Marathi-speaking showroom settles down quickly.
  */
  var LANG_CHAIN = ["en-IN", "hi-IN", "mr-IN"];
  var LANG_LABEL = { "en-IN": "English", "hi-IN": "हिंदी", "mr-IN": "मराठी" };

  /**
   * Show which language the microphone is set to, on screen, at all times.
   *
   * Updated in three places: on load (so it reads "English" before anyone
   * touches the mic), when a listen starts, and when a language succeeds.
   */
  function showVoiceLanguage(lang) {
    var label = LANG_LABEL[lang] || LANG_LABEL[LANG_CHAIN[0]];
    setText(el.voiceLang, label);
    setText(el.voiceLangBadge, label);
  }

  // At most two tries per mic press. Asking a customer to repeat themselves
  // three times is worse than failing and letting them type.
  var MAX_ATTEMPTS = 2;

  // Which fallback language to pair with the preferred one. Rotated on every
  // press so all languages eventually get a turn — otherwise a third language
  // could never be discovered, and so could never become the remembered one.
  var fallbackRotation = 0;

  /**
   * The language shown, and tried first, when nothing is happening.
   *
   * The station's setting if one is pinned, otherwise English. English is the
   * right default even for Hindi and Marathi speakers: the en-IN model
   * handles Hinglish, and the parser understands the romanised spellings it
   * produces for spoken Hindi and Marathi digits.
   */
  function defaultVoiceLang() {
    var pinned = voiceSettings().language;
    if (pinned && pinned !== "auto" && LANG_CHAIN.indexOf(pinned) !== -1) {
      return pinned;
    }
    return LANG_CHAIN[0];      // en-IN
  }

  /**
   * The languages to try for one mic press.
   *
   * A pinned station language means exactly one attempt: no guessing and no
   * "say it again", which is what a showroom with one main language wants.
   *
   * On "auto" it is always English first, then one rotating fallback. English
   * leads deliberately — starting from whatever succeeded last time made the
   * on-screen language flip to हिंदी and stay there, which looked broken.
   * A browser can only decode a recording against one language, so a second
   * language genuinely needs a second utterance; the fallback exists only for
   * when the first pass cannot be salvaged.
   */
  function preferredLangOrder() {
    var pinned = voiceSettings().language;
    if (pinned && pinned !== "auto" && LANG_CHAIN.indexOf(pinned) !== -1) {
      return [pinned];
    }

    var others = LANG_CHAIN.slice(1);          // everything after en-IN
    var fallback = others[fallbackRotation % others.length];
    fallbackRotation++;
    return [LANG_CHAIN[0], fallback].slice(0, MAX_ATTEMPTS);
  }

  /** Try every alternative the engine offered; return the first parseable code. */
  function codesFromAlternatives(alternatives) {
    for (var i = 0; i < alternatives.length; i++) {
      var codes = parseSpokenCodes(alternatives[i]);
      if (codes.length) return { codes: codes, heard: alternatives[i] };
    }
    return null;
  }

  function voiceSettings() {
    var voice = (state.display && state.display.voice) || {};
    return {
      enabled: voice.enabled !== false,
      wakeEnabled: voice.wake_word_enabled === true,
      phrases: voice.wake_phrases || [],
      language: voice.language || "auto"
    };
  }

  function setupVoice() {
    var SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;

    // No browser support: leave the mic hidden. The manual box is already on
    // screen, so there is nothing to fall back to — it *is* the fallback.
    if (!SpeechRecognition) return;

    state.voice.supported = true;

    var recognition = new SpeechRecognition();
    recognition.interimResults = true;
    // More alternatives means more chances one of them contains the digits.
    recognition.maxAlternatives = 5;
    state.voice.recognition = recognition;

    var order = preferredLangOrder();
    var attempt = 0;
    var alternatives = [];
    var retryTimer = null;

    // Per-listen bookkeeping for the fast path.
    var resolved = false;        // a code was accepted; ignore everything after
    var stableTimer = null;      // debounce on a repeated interim guess
    var listenTimer = null;      // hard cap on this listen
    var lastInterimCode = "";

    function clearTurnTimers() {
      clearTimeout(stableTimer);
      clearTimeout(listenTimer);
      stableTimer = null;
      listenTimer = null;
    }

    /**
     * Accept a code and act on it immediately.
     *
     * Called as soon as one is recognised rather than waiting for `onend`,
     * which is what used to make spoken lookups feel slow.
     */
    function resolveWith(codes, heard) {
      if (resolved || !codes || !codes.length) return;
      resolved = true;
      clearTurnTimers();

      // Show which language actually understood it. This is live feedback
      // only — the badge returns to the default once the screen goes idle,
      // so it never gets stuck on a language nobody chose.
      showVoiceLanguage(recognition.lang);

      setText(el.voiceStatus, "Got it:");
      setText(el.voiceTranscript, (heard || "").trim() + "  →  " + codes.join(", "));
      el.voiceTranscript.classList.remove("is-interim");

      // Release the microphone; onend sees `resolved` and stands down.
      try { recognition.stop(); } catch (err) { /* already stopping */ }

      submitCodes(codes.slice(0, maxItems()), "voice");
      rearmWakeWordSoon();
    }

    function listen(index, mode) {
      state.voice.mode = mode;
      attempt = index;
      alternatives = [];
      state.voice.finalTranscript = "";
      resolved = false;
      lastInterimCode = "";
      clearTurnTimers();

      recognition.lang = order[index] || LANG_CHAIN[0];
      // Wake listening must not stop after one phrase.
      recognition.continuous = mode === "wake";
      showVoiceLanguage(recognition.lang);

      try {
        recognition.start();
      } catch (err) {
        // start() throws if the engine is already running; safe to ignore.
      }

      // Never leave someone staring at "Listening…" indefinitely.
      if (mode === "code") {
        listenTimer = setTimeout(function () {
          if (resolved) return;
          var best = codesFromAlternatives(alternatives);
          if (best) {
            resolveWith(best.codes, best.heard);
            return;
          }
          try { recognition.stop(); } catch (err) { /* ignore */ }
          setText(el.voiceStatus, "Didn't catch a code —");
          setText(el.voiceTranscript, "try again, or type it below.");
        }, CONFIG.LISTEN_TIMEOUT_MS);
      }
    }

    recognition.onstart = function () {
      state.voice.listening = true;
      state.voice.wakeFailures = 0;
      // There must always be a way to switch the microphone off while it is
      // on, not only in wake-word mode.
      updateStopButton();

      if (state.voice.mode === "wake") {
        el.voicePanel.classList.add("is-armed");
        setText(el.voiceStatus, "Say a wake word, e.g.");
        setText(el.voiceTranscript, (voiceSettings().phrases[0] || "hi ahmad"));
        show(el.voicePanel);
      } else {
        el.voicePanel.classList.remove("is-armed");
        el.micButton.classList.add("is-listening");
        setText(el.micLabel, "Listening…");
        setText(el.voiceStatus, "Listening…");
        setText(el.voiceTranscript, "");
        show(el.voicePanel);
      }
    };

    recognition.onresult = function (event) {
      if (resolved) return;

      var interim = "";
      var sawFinal = false;

      for (var i = event.resultIndex; i < event.results.length; i++) {
        var result = event.results[i];
        if (result.isFinal) {
          sawFinal = true;
          state.voice.finalTranscript += result[0].transcript + " ";
          for (var a = 0; a < result.length; a++) {
            alternatives.push(result[a].transcript);
          }
        } else {
          interim += result[0].transcript;
        }
      }

      var heardSoFar = (state.voice.finalTranscript + " " + interim).trim();

      if (state.voice.mode === "wake") {
        handleWakeTranscript(heardSoFar, alternatives);
        return;
      }

      // Show what was heard, live, so the customer can see it working.
      // Interim text is styled differently — it is a guess, not a decision.
      setText(el.voiceTranscript, heardSoFar);
      el.voiceTranscript.classList.toggle("is-interim", !sawFinal && !!interim);

      // --- Fast path 1: a final result the parser understands --------------
      // The engine is confident, so there is nothing to gain by waiting for
      // its end-of-speech timeout.
      if (sawFinal) {
        var hit = codesFromAlternatives(alternatives);
        if (hit) {
          resolveWith(hit.codes, hit.heard);
          return;
        }
      }

      // --- Fast path 2: an interim guess that has stopped changing ---------
      // Acting on the first interim would be reckless — the engine revises
      // numbers as it hears more. Requiring the same code twice in a row,
      // and then a short quiet period, is the compromise.
      if (interim) {
        var interimCodes = parseSpokenCodes(interim);
        var candidate = interimCodes.length ? interimCodes.join(",") : "";

        if (candidate && candidate === lastInterimCode) {
          if (!stableTimer) {
            stableTimer = setTimeout(function () {
              if (resolved) return;
              // Prefer a final result if one landed while we waited.
              var late = codesFromAlternatives(alternatives);
              if (late) resolveWith(late.codes, late.heard);
              else resolveWith(interimCodes, interim);
            }, CONFIG.INTERIM_STABLE_MS);
          }
        } else {
          // The guess changed: start the quiet period again.
          clearTimeout(stableTimer);
          stableTimer = null;
          lastInterimCode = candidate;
        }
      }
    };

    /**
     * While armed, watch for the wake phrase in anything heard.
     * If the same sentence already carries a code ("hi ahmad 1345 ka price"),
     * use it straight away rather than making the customer speak twice.
     */
    function handleWakeTranscript(heard, alts) {
      var phrases = voiceSettings().phrases;
      var candidates = [heard].concat(alts);

      for (var i = 0; i < candidates.length; i++) {
        var hit = matchWakePhrase(candidates[i], phrases);
        if (!hit.matched) continue;

        setText(el.voiceStatus, "Yes?");
        setText(el.voiceTranscript, candidates[i]);

        var codes = parseSpokenCodes(hit.remainder);
        if (codes.length) {
          submitCodes(codes.slice(0, maxItems()), "voice");
          state.voice.finalTranscript = "";
          alternatives = [];
          return;
        }

        // Woken, but no code yet — switch to a focused single-shot listen.
        state.voice.finalTranscript = "";
        alternatives = [];
        try { recognition.stop(); } catch (err) { /* already stopping */ }
        clearTimeout(retryTimer);
        retryTimer = setTimeout(function () {
          order = preferredLangOrder();
          listen(0, "code");
        }, 250);
        return;
      }
    }

    recognition.onerror = function (event) {
      state.voice.listening = false;
      el.micButton.classList.remove("is-listening");
      setText(el.micLabel, "Speak the code");

      // We stopped the engine ourselves after accepting a code — the error
      // that follows is bookkeeping, not a failure worth reporting.
      if (resolved) return;
      clearTurnTimers();

      var fatal = event.error === "not-allowed" || event.error === "service-not-allowed";

      if (fatal) {
        setText(el.voiceStatus, "Microphone blocked —");
        setText(el.voiceTranscript, "allow mic access, or type the code below.");
        // Never loop on a permission denial.
        disarmWakeWord(true);
      } else if (event.error === "no-speech") {
        if (state.voice.mode !== "wake") {
          setText(el.voiceStatus, "Didn't hear anything —");
          setText(el.voiceTranscript, "try again, or type the code below.");
        }
      } else if (event.error === "network") {
        setText(el.voiceStatus, "Voice needs internet —");
        setText(el.voiceTranscript, "please type the code below.");
        state.voice.wakeFailures++;
      } else if (event.error !== "aborted") {
        setText(el.voiceStatus, "Voice unavailable —");
        setText(el.voiceTranscript, "please type the code below.");
        state.voice.wakeFailures++;
      }

      // A hard error should not trigger the other-language retry.
      if (fatal) attempt = order.length;
    };

    recognition.onend = function () {
      state.voice.listening = false;
      el.micButton.classList.remove("is-listening");
      setText(el.micLabel, "Speak the code");
      updateStopButton();

      // A code was already accepted in onresult; this is just the engine
      // shutting down afterwards. Nothing left to decide.
      if (resolved) {
        clearTurnTimers();
        return;
      }
      clearTurnTimers();

      // Wake mode: keep the microphone armed by restarting, with a backoff
      // so a persistently failing engine cannot spin.
      if (state.voice.mode === "wake") {
        if (!state.voice.wakeArmed) return;
        if (state.voice.wakeFailures >= CONFIG.WAKE_MAX_FAILURES) {
          disarmWakeWord(true);
          toast("Voice stopped — the speech service kept failing.", true);
          return;
        }
        clearTimeout(retryTimer);
        retryTimer = setTimeout(function () {
          if (state.voice.wakeArmed) listen(0, "wake");
        }, CONFIG.WAKE_RESTART_MS);
        return;
      }

      // Late arrival: a final result that only landed as the engine closed.
      var hit = codesFromAlternatives(alternatives);
      if (hit) {
        resolveWith(hit.codes, hit.heard);
        return;
      }

      // Nothing usable. Retry in another language only when something was
      // actually said — if the microphone heard silence, a different model
      // will hear the same silence, and asking again just wastes their time.
      var heardAnything = state.voice.finalTranscript.trim().length > 0;

      if (heardAnything && attempt + 1 < order.length) {
        var nextLang = order[attempt + 1];
        setText(el.voiceStatus, "Trying " + (LANG_LABEL[nextLang] || nextLang) + " —");
        setText(el.voiceTranscript, "please say the code again.");
        el.voiceTranscript.classList.remove("is-interim");
        clearTimeout(retryTimer);
        // Small gap so the engine fully releases the microphone first.
        retryTimer = setTimeout(function () {
          listen(attempt + 1, "code");
        }, CONFIG.LANG_RETRY_MS);
        return;
      }

      if (!heardAnything) {
        setText(el.voiceStatus, "Didn't hear anything —");
        setText(el.voiceTranscript, "tap the mic and speak, or type the code.");
        el.voiceTranscript.classList.remove("is-interim");
        rearmWakeWordSoon();
        return;
      }

      setText(el.voiceStatus, "Could not understand —");
      setText(el.voiceTranscript, "please try again or type the code below.");
      rearmWakeWordSoon();
    };

    /** After a one-shot listen, go back to waiting for the wake phrase. */
    function rearmWakeWordSoon() {
      if (!state.voice.wakeArmed) return;
      clearTimeout(retryTimer);
      retryTimer = setTimeout(function () {
        if (state.voice.wakeArmed) listen(0, "wake");
      }, 900);
    }

    // Exposed so the buttons and settings changes can drive the engine.
    state.voice.startCodeListen = function () {
      clearTimeout(retryTimer);
      order = preferredLangOrder();
      listen(0, "code");
    };

    state.voice.startWakeListen = function () {
      clearTimeout(retryTimer);
      order = preferredLangOrder();
      listen(0, "wake");
    };

    state.voice.stopEngine = function () {
      clearTimeout(retryTimer);
      try { recognition.abort(); } catch (err) { /* not running */ }
    };

    el.micButton.addEventListener("click", function () {
      if (state.voice.listening && state.voice.mode === "code") {
        try { recognition.stop(); } catch (err) { /* ignore */ }
        return;
      }
      state.voice.stopEngine();
      setTimeout(state.voice.startCodeListen, 120);
    });

    el.wakeStop.addEventListener("click", function () {
      // Armed for the wake word, or mid-listen: either way, stop the mic.
      if (state.voice.wakeArmed || state.voice.listening) {
        disarmWakeWord(false);
        return;
      }
      armWakeWord();
    });

    applyVoiceSettings();
  }

  /**
   * Decide what the stop/start control should look like right now.
   *
   * Shown as a red "Stop mic" whenever the microphone is actually on — either
   * armed for the wake word or mid-listen — so there is never a live mic
   * without a visible way to switch it off. When the wake word is enabled but
   * currently stopped, it becomes a "Start listening" button instead.
   */
  function updateStopButton() {
    if (!state.voice.supported) { hide(el.wakeStop); return; }

    var settings = voiceSettings();
    if (!settings.enabled) { hide(el.wakeStop); return; }

    var micIsOn = state.voice.wakeArmed || state.voice.listening;

    if (micIsOn) {
      setText(el.wakeStopLabel, "Stop mic");
      el.wakeStop.classList.remove("btn--ghost");
      el.wakeStop.classList.add("btn--danger");
      show(el.wakeStop);
    } else if (settings.wakeEnabled) {
      setText(el.wakeStopLabel, "Start listening");
      el.wakeStop.classList.remove("btn--danger");
      el.wakeStop.classList.add("btn--ghost");
      show(el.wakeStop);
    } else {
      hide(el.wakeStop);
    }
  }

  function armWakeWord() {
    if (!state.voice.supported) return;
    // Arming is always a deliberate act, so it clears the stop latch.
    state.voice.userStopped = false;
    state.voice.wakeArmed = true;
    state.voice.wakeFailures = 0;
    updateStopButton();
    if (state.voice.startWakeListen) state.voice.startWakeListen();
  }

  /**
   * Turn continuous listening off.
   * @param {boolean} silent true when a failure disarmed us, not the user.
   */
  function disarmWakeWord(silent) {
    // A person pressing Stop must stay stopped. Only a failure-driven disarm
    // leaves the door open for the settings to re-arm later.
    if (!silent) state.voice.userStopped = true;
    state.voice.wakeArmed = false;
    state.voice.listening = false;
    if (state.voice.stopEngine) state.voice.stopEngine();
    el.voicePanel.classList.remove("is-armed");
    el.micButton.classList.remove("is-listening");
    setText(el.micLabel, "Speak the code");
    updateStopButton();

    if (!silent) {
      hide(el.voicePanel);
      toast("Microphone off.");
    }
  }

  /** Apply the station's voice settings to the buttons and engine. */
  function applyVoiceSettings() {
    // No speech recognition in this browser: drop the whole Speak panel
    // rather than leave an empty card promising something that cannot work.
    if (!state.voice.supported) {
      hide(el.speakPanel);
      return;
    }

    var settings = voiceSettings();

    if (!settings.enabled) {
      hide(el.speakPanel);
      hide(el.micButton);
      hide(el.wakeStop);
      hide(el.voicePanel);
      if (state.voice.wakeArmed) disarmWakeWord(true);
      return;
    }

    show(el.speakPanel);
    show(el.micButton);

    // Reflect the configured language before anyone presses the mic.
    showVoiceLanguage(defaultVoiceLang());

    // Tell people what to say, using a real barcode when one is on screen.
    if (el.speakHint) {
      setText(
        el.speakHint,
        settings.wakeEnabled
          ? "Say “" + (settings.phrases[0] || "hi ahmad") + "”, then the code"
          : "Say “price of 1345” in English, Hindi or Marathi"
      );
    }

    // This runs on every lookup response, so it must not undo a manual Stop.
    if (settings.wakeEnabled) {
      if (!state.voice.wakeArmed && !state.voice.userStopped) armWakeWord();
    } else if (state.voice.wakeArmed) {
      disarmWakeWord(true);
    }
    updateStopButton();
  }

  /* ---------- 10b. Spoken price (text to speech) -------------------------- */

  /*
    The engine itself lives in price-speech.js, shared with the customer
    portal. Everything kiosk-specific is passed in here: where the settings
    come from, and how to pause the wake-word microphone so it does not hear
    the announcement and re-trigger itself.
  */
  function bindSpeech() {
    PriceSpeech.init({
      muteButton: el.muteToggle,
      storageKey: STORAGE.MUTED,
      getSettings: function () { return (state.display && state.display.speech) || {}; },
      getCurrency: currency,
      onToast: toast,

      // Returning true tells the engine a resume is owed once it finishes.
      onSuspend: function () {
        if (state.voice.wakeArmed && state.voice.stopEngine) {
          state.voice.suspendedForSpeech = true;
          state.voice.stopEngine();
          return true;
        }
        return false;
      },

      onResume: function () {
        state.voice.suspendedForSpeech = false;
        // Small gap so the tail of the announcement is not picked up.
        setTimeout(function () {
          if (state.voice.wakeArmed && state.voice.startWakeListen) {
            state.voice.startWakeListen();
          }
        }, 400);
      }
    });
  }

  function announceProducts(products) { PriceSpeech.announce(products); }
  function updateMuteButton() { PriceSpeech.refresh(); }

  /* ---------- 11. API calls ----------------------------------------------- */

  function maxItems() {
    var multi = (state.display && state.display.multi) || {};
    if (multi.enabled === false) return 1;
    return Math.max(1, Math.min(8, multi.max_items || 4));
  }

  /**
   * Look codes up and show the result.
   *
   * Guards against the two ways this gets called too often: the same code
   * twice within DUPLICATE_WINDOW_MS, and a second scan landing while the
   * first request is still open (the older request is aborted so responses
   * cannot arrive out of order).
   */
  function submitCodes(codes, source) {
    var list = (codes || []).filter(function (c) { return c && c.trim(); });
    if (!list.length) return;

    var key = list.join(",");
    var now = Date.now();
    if (key === state.lastCode && now - state.lastCodeAt < CONFIG.DUPLICATE_WINDOW_MS) {
      return;
    }
    state.lastCode = key;
    state.lastCodeAt = now;

    // A scan arriving while results are showing adds to them, up to the
    // station's limit — that is the "compare several products" behaviour.
    //
    // It only makes sense while a countdown is running, because the countdown
    // is what defines "consecutive". With a duration of 0 the panel never
    // clears itself, so every later scan would pile on forever and the screen
    // would fill up. In hold mode each scan replaces the last.
    var accumulate =
      state.view === "result" && maxItems() > 1 && duration() > 0;
    if (accumulate) {
      var existing = state.items.map(function (item) { return item.barcode; });
      list = existing.concat(list).slice(-maxItems());
    }

    stopCountdown();
    if (state.inFlight) state.inFlight.abort();

    var controller = new AbortController();
    state.inFlight = controller;

    var url = withStation("/api/lookup/?code=" + encodeURIComponent(list.join(",")) +
                          "&source=" + encodeURIComponent(source || "scanner"));

    fetch(url, { signal: controller.signal, headers: { Accept: "application/json" } })
      .then(function (response) {
        return response.json().then(function (data) {
          return { ok: response.ok, status: response.status, data: data };
        });
      })
      .then(function (result) {
        if (controller !== state.inFlight) return;   // superseded by a newer scan
        state.inFlight = null;

        var data = result.data || {};

        // The display block rides along on every response, so admin changes
        // land on the very next scan without a refresh.
        if (data.display) applyDisplay(data.display);

        var products = data.tally_products ||
                       (data.tally_product ? [data.tally_product] : []);

        if (products.length) {
          renderResults(products, data.not_found || []);
        } else if (result.status === 400) {
          goIdle();
        } else {
          renderNotFound((data.not_found && data.not_found[0]) || data.code || list[0]);
        }
      })
      .catch(function (error) {
        if (error.name === "AbortError") return;
        state.inFlight = null;
        toast("Cannot reach the price server. Please tell a staff member.", true);
        goIdle();
      });
  }

  function loadInitialSettings() {
    fetch(withStation("/api/settings/"), { headers: { Accept: "application/json" } })
      .then(function (r) { return r.json(); })
      .then(function (data) { if (data.display) applyDisplay(data.display); })
      .catch(function () { /* the server-rendered values already cover us */ });
  }

  /* ---------- 12. Rendering ----------------------------------------------- */

  function applyDisplay(display) {
    state.display = display;
    applyTheme(display);
    applyVoiceSettings();
    updateMuteButton();

    var station = display.station || {};
    if (station.slug) {
      var label = station.name || station.slug;
      if (station.price_list_name) label += " · " + station.price_list_name;
      setText(el.stationLabel, label);
    } else {
      setText(el.stationLabel, "");
    }
  }

  function flags() { return (state.display && state.display.fields) || {}; }
  function currency() { return (state.display && state.display.currency_symbol) || ""; }

  /**
   * Seconds a product stays on screen.
   *
   * ``0`` is meaningful, not missing: it means hold the product until the
   * next scan. Only a genuinely absent or invalid value falls back to 10,
   * which is why this cannot be a plain truthiness check.
   */
  function duration() {
    var seconds = state.display && state.display.duration_seconds;
    if (typeof seconds === "number" && isFinite(seconds) && seconds >= 0) {
      return seconds;
    }
    return 10;
  }

  /**
   * Fill one cloned card from a product, honouring the admin's field flags.
   *
   * Every element starts hidden and is switched on only when both the flag is
   * set and the data is present. That keeps the card balanced for any
   * combination — name+price only, or everything at once.
   */
  /** True when the screen is not locked to a single company. */
  function showingAllCompanies() {
    var station = (state.display && state.display.station) || {};
    return station.all_companies === true;
  }

  function fillCard(card, product, siblingCount) {
    var f = flags();
    var sym = currency();

    var company = card.querySelector(".product-company");
    var image = card.querySelector(".product-image");
    var placeholder = card.querySelector(".image-placeholder");
    var name = card.querySelector(".product-name");
    var wholesale = card.querySelector(".wholesale-line");
    var tiersContainer = card.querySelector(".price-tiers");
    var description = card.querySelector(".product-description");
    var availability = card.querySelector(".availability");
    var code = card.querySelector(".result-code");

    // --- Company ---------------------------------------------------------
    // Only worth screen space when it disambiguates: an all-companies screen,
    // or several products sharing one scanned code. On a screen locked to one
    // company it is just noise.
    if (product.company && (showingAllCompanies() || siblingCount > 1)) {
      setText(company, product.company_code || product.company);
      show(company);
    }

    // --- Image -----------------------------------------------------------
    var wantsImage = f.image !== false;
    if (wantsImage) {
      if (product.image) {
        image.src = product.image;
        image.alt = product.name || "";
        show(image);
        // If the file 404s after loading starts, fall back to the placeholder
        // so the layout never collapses around a broken image.
        image.onerror = function () { hide(image); show(placeholder); };
      } else {
        // Null image is expected, not an error — show the tidy placeholder.
        show(placeholder);
      }
    }
    card.classList.toggle("no-media", !wantsImage);

    // --- Name ------------------------------------------------------------
    if (f.name !== false && product.name) {
      setText(name, product.name);
    } else {
      hide(name);
    }

    // --- Prices ------------------------------------------------------------
    // The default tier (Retail) is the card's hero and keeps the price area to
    // itself; everything else (Wholesale) is drawn as a small line below the
    // description instead. Falls back to the legacy singular price/mrp/discount
    // fields if a response ever omits `prices` — belt and braces, not the
    // normal path.
    if (tiersContainer) {
      tiersContainer.textContent = "";
      var tiers = (product.prices && product.prices.length) ?
        product.prices : legacyTiers(product);
      var split = splitTiers(tiers);

      for (var t = 0; t < split.primary.length; t++) {
        var tierNode = buildPriceTier(split.primary[t], f, sym);
        if (tierNode) tiersContainer.appendChild(tierNode);
      }
      fillWholesaleLine(wholesale, split.secondary, f, sym);
    }

    // --- Description -----------------------------------------------------
    if (f.description === true && product.description) {
      setText(description, product.description);
      show(description);
    }

    // --- Availability ----------------------------------------------------
    if (f.availability === true) {
      var inStock = Number(product.closing_stock) > 0;
      var text;
      if (f.availability_mode === "quantity" && product.closing_stock != null) {
        text = trimDecimals(product.closing_stock) + " " + (product.unit || "");
      } else {
        text = product.availability || (inStock ? "In Stock" : "Out of Stock");
      }
      setText(availability, text.trim());
      availability.className = "availability " + (inStock ? "in-stock" : "out-of-stock");
      show(availability);
    }

    // --- Code echo -------------------------------------------------------
    setText(code, product.barcode ? "Code: " + product.barcode : "");
  }

  /**
   * Separate the hero price from the secondary rates.
   *
   * A response with no default flag anywhere would otherwise leave the price
   * area empty and push every rate up to the small line, so the first tier is
   * promoted in that case.
   */
  function splitTiers(tiers) {
    var primary = [];
    var secondary = [];
    for (var i = 0; i < tiers.length; i++) {
      if (tiers[i].is_default) primary.push(tiers[i]);
      else secondary.push(tiers[i]);
    }
    if (!primary.length && secondary.length) primary.push(secondary.shift());
    return { primary: primary, secondary: secondary };
  }

  /**
   * The secondary rates (Wholesale), as one small line below the description.
   *
   * Built with createElement/textContent rather than markup so a price-list
   * name can never inject HTML — the same reason the tiers use a <template>.
   */
  function fillWholesaleLine(node, tiers, f, sym) {
    if (!node) return;
    node.textContent = "";

    // "Show price" off in the admin hides every figure, this one included.
    if (f.price === false) { hide(node); return; }

    for (var i = 0; i < tiers.length; i++) {
      if (tiers[i].price == null) continue;

      var label = document.createElement("span");
      label.className = "wholesale-label";
      label.textContent = tiers[i].name || "";

      var amount = document.createElement("span");
      amount.className = "wholesale-amount";
      amount.textContent = formatMoney(tiers[i].price, sym);

      node.appendChild(label);
      node.appendChild(amount);
    }

    if (node.firstChild) show(node); else hide(node);
  }

  /** One price-list tier (Retail, Wholesale, ...), cloned onto a card. */
  function buildPriceTier(tier, f, sym) {
    if (!el.priceTierTemplate) return null;
    var node = el.priceTierTemplate.content.firstElementChild.cloneNode(true);
    var label = node.querySelector(".tier-label");
    var price = node.querySelector(".product-price");
    var mrp = node.querySelector(".product-mrp");
    var discount = node.querySelector(".discount-badge");

    setText(label, tier.name || "");
    if (tier.is_default) node.classList.add("is-default-tier");

    if (f.price !== false && tier.price != null) {
      setText(price, formatMoney(tier.price, sym));
    } else {
      hide(price);
    }

    var mrpValue = Number(tier.mrp);
    var priceValue = Number(tier.price);
    if (f.mrp !== false && tier.mrp != null &&
        isFinite(mrpValue) && isFinite(priceValue) && mrpValue > priceValue) {
      setText(mrp, formatMoney(tier.mrp, sym));
      show(mrp);
    }

    var pct = Number(tier.discount_percent);
    if (f.discount !== false && isFinite(pct) && pct > 0) {
      setText(discount, pct + "% OFF");
      show(discount);
    }

    return node;
  }

  /** A single-tier array from the legacy singular fields, for a response
   *  that predates `prices` — a defensive fallback, not the normal path. */
  function legacyTiers(product) {
    if (product.price == null) return [];
    return [{
      name: product.price_list || "Price",
      price: product.price,
      mrp: product.mrp,
      discount_percent: product.discount_percent,
      is_default: true
    }];
  }

  /* ---------- Fitting the amount to its tier ------------------------------ */

  /*
    The measuring itself lives in price-fit.js, because the customer portal
    needs exactly the same behaviour and a second copy here is what let that
    page keep clipping prices after this one was fixed.
  */

  function renderResults(products, missing) {
    state.items = products;

    // Rebuilding from a <template> rather than assembling HTML strings means
    // a product name can never inject markup.
    el.resultStage.textContent = "";
    for (var i = 0; i < products.length; i++) {
      var card = el.cardTemplate.content.firstElementChild.cloneNode(true);
      fillCard(card, products[i], products.length);
      el.resultStage.appendChild(card);
    }
    el.resultStage.setAttribute("data-count", String(products.length));

    if (maxItems() > 1) show(el.multiHint); else hide(el.multiHint);

    if (missing && missing.length) {
      toast("Not found: " + missing.join(", "));
    }

    switchView("result");
    // After the view is visible, so the prices have a resolved font to be
    // measured against — see PriceFit.fit().
    PriceFit.fit(el.resultStage);
    startCountdown(el.countdownFill, el.countdownText);
    announceProducts(products);
  }

  function renderNotFound(code) {
    state.items = [];
    setText(el.notfoundCode, code || "—");
    switchView("notfound");
    startCountdown(el.countdownFillNf, el.countdownTextNf);
  }

  /* ---------- 13. Countdown and view switching ---------------------------- */

  /**
   * Swap what the right-hand detail panel shows.
   *
   * The left console is untouched — it stays on screen through every state so
   * scanning never stops being available.
   */
  function switchView(name) {
    state.view = name;
    Object.keys(el.views).forEach(function (key) {
      el.views[key].classList.toggle("is-active", key === name);
    });

    // On a phone the console and the product cannot both have the screen.
    // This lets the stylesheet collapse the console to just its input while a
    // result is up, so the price gets the space. Scanning is unaffected: the
    // code box is what the stylesheet keeps, and it still holds the cursor.
    el.body.classList.toggle("is-showing-result", name !== "idle");

    if (name === "idle") {
      if (!state.voice.wakeArmed) hide(el.voicePanel);
      // Back to the configured language, so the badge never sticks on
      // whichever language happened to understand the last customer.
      if (state.voice.supported) showVoiceLanguage(defaultVoiceLang());
    }
    keepCodeFocus();
  }

  function stopCountdown() {
    if (state.countdownTimer) {
      clearInterval(state.countdownTimer);
      state.countdownTimer = null;
    }
  }

  /**
   * Run the auto-return timer, updating the label and the progress bar.
   * A new scan calls stopCountdown() first, so the timer always restarts.
   *
   * With a duration of 0 no timer runs at all: the product stays until the
   * next scan replaces it, or someone presses Clear.
   */
  function startCountdown(fillNode, textNode) {
    stopCountdown();

    var seconds = duration();

    if (seconds <= 0) {
      // Held indefinitely. Show a full, static bar rather than an empty one —
      // an empty bar would read as "expired".
      if (fillNode) {
        fillNode.style.transform = "scaleX(1)";
        fillNode.classList.add("is-held");
      }
      if (textNode) textNode.textContent = "Showing until the next scan";
      return;
    }

    if (fillNode) fillNode.classList.remove("is-held");

    var totalMs = seconds * 1000;
    var startedAt = Date.now();

    var tick = function () {
      var elapsed = Date.now() - startedAt;
      var remaining = Math.max(0, totalMs - elapsed);

      if (fillNode) {
        fillNode.style.transform = "scaleX(" + (remaining / totalMs) + ")";
      }
      if (textNode) {
        var seconds = Math.ceil(remaining / 1000);
        textNode.textContent = remaining > 0 ? "Returning in " + seconds + "…" : "";
      }

      if (remaining <= 0) {
        stopCountdown();
        goIdle();
      }
    };

    tick();
    state.countdownTimer = setInterval(tick, 100);
  }

  function goIdle() {
    stopCountdown();
    state.items = [];
    // Clear the duplicate guard so the same item can be scanned again
    // immediately after returning to idle.
    state.lastCode = null;
    state.lastCodeAt = 0;
    switchView("idle");
  }

  /* ---------- 14. Station picker + fullscreen ----------------------------- */

  function bindStationPicker() {
    el.stationButton.addEventListener("click", openStationPicker);
    el.stationCancel.addEventListener("click", closeStationPicker);
    el.stationModal.addEventListener("click", function (event) {
      if (event.target === el.stationModal) closeStationPicker();
    });
  }

  function openStationPicker() {
    el.stationList.textContent = "Loading…";
    show(el.stationModal);

    fetch("/api/stations/", { headers: { Accept: "application/json" } })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        el.stationList.textContent = "";
        var list = data.stations || [];
        if (!list.length) {
          el.stationList.textContent = "No stations configured yet.";
          return;
        }
        list.forEach(function (station) {
          var button = document.createElement("button");
          button.type = "button";
          button.className = "station-option";
          if (station.slug === currentStationSlug()) {
            button.classList.add("is-current");
          }

          var title = document.createElement("strong");
          title.textContent = station.name + (station.is_default ? "  (default)" : "");
          var meta = document.createElement("span");
          meta.textContent = (station.price_list || "Base prices") + " · /?station=" + station.slug;

          button.appendChild(title);
          button.appendChild(meta);
          button.addEventListener("click", function () { chooseStation(station.slug); });
          el.stationList.appendChild(button);
        });
      })
      .catch(function () {
        el.stationList.textContent = "Could not load the station list.";
      });
  }

  function currentStationSlug() {
    if (state.station) return state.station;
    var station = (state.display && state.display.station) || {};
    return station.slug || null;
  }

  function closeStationPicker() {
    hide(el.stationModal);
    keepCodeFocus();
  }

  function chooseStation(slug) {
    storageSet(STORAGE.STATION, slug);
    state.station = slug;
    closeStationPicker();
    // A full reload is the honest way to re-render server-side branding
    // (logo, welcome text) for the new station.
    window.location.href = "/?station=" + encodeURIComponent(slug);
  }
/*
  function bindFullscreen() {
    el.fullscreenToggle.addEventListener("click", function () {
      if (!document.fullscreenElement) {
        document.documentElement.requestFullscreen().catch(function () {
          toast("Fullscreen was blocked by the browser.");
        });
      } else {
        document.exitFullscreen();
      }
      setTimeout(keepCodeFocus, 50);
    });
  }




   ---------- 15. Boot ---------------------------------------------------- */
  /* iPhone has no Fullscreen API for ordinary elements at all, and Safari
     before 16.4 only had the webkit-prefixed names. Probe rather than assume,
     and hide the button outright when there is nothing behind it. */
  function fullscreenSupported() {
    var d = document.documentElement;
    return !!(d.requestFullscreen || d.webkitRequestFullscreen);
  }

  function bindFullscreen() {
    if (!el.fullscreenToggle) return;
    if (!fullscreenSupported()) { hide(el.fullscreenToggle); return; }

    // Held while the transition is animating, so the focus guard stands down.
    // Cleared by fullscreenchange, with a timer as a backstop in case the
    // request is refused and no event ever arrives.
    function settle() {
      state.fullscreenPending = false;
      setTimeout(keepCodeFocus, 50);
    }
    document.addEventListener("fullscreenchange", settle);
    document.addEventListener("webkitfullscreenchange", settle);

    el.fullscreenToggle.addEventListener("click", function () {
      var d = document.documentElement;
      var active = document.fullscreenElement || document.webkitFullscreenElement;

      state.fullscreenPending = true;
      setTimeout(settle, 1500);

      try {
        if (active) {
          (document.exitFullscreen || document.webkitExitFullscreen).call(document);
        } else {
          // May return a promise or nothing, depending on the browser.
          var result = (d.requestFullscreen || d.webkitRequestFullscreen).call(d);
          if (result && result.catch) {
            result.catch(function () {
              settle();
              toast("Fullscreen was blocked by the browser.");
            });
          }
        }
      } catch (err) {
        settle();
        toast("Full screen is not available on this device.");
      }
    });
  }


  function readInitialDisplay() {
    var node = document.getElementById("initial-display");
    if (!node) return null;
    try {
      return JSON.parse(node.textContent);
    } catch (err) {
      return null;
    }
  }

  function init() {
    resolveStation();

    // Paint with the server-rendered settings first so the screen is correct
    // before any network call completes.
    var initial = readInitialDisplay();
    if (initial) {
      state.display = initial;
      applyTheme(initial);
      var station = initial.station || {};
      if (station.slug) {
        if (!state.station) state.station = station.slug;
        var label = station.name || station.slug;
        if (station.price_list_name) label += " · " + station.price_list_name;
        setText(el.stationLabel, label);
      }
    }

    bindFocusGuards();
    bindScanner();
    bindManualEntry();
    bindStationPicker();
    bindFullscreen();
    bindSpeech();
    setupVoice();

    el.scanNext.addEventListener("click", goIdle);
    el.scanNextNf.addEventListener("click", goIdle);

    switchView("idle");
    PriceFit.refitOnFontLoad(function () {
      return state.view === "result" ? el.resultStage : null;
    });
    loadInitialSettings();

    // FUTURE: offline caching would slot in here — cache the last N lookups
    // in localStorage and serve from them when fetch rejects, so a dropped
    // LAN link degrades to stale prices instead of an error toast.
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
