/* ==========================================================================
   Spoken price — shared by the kiosk screen and the customer portal.
   --------------------------------------------------------------------------
   Reads the price aloud through the browser's built-in speech synthesis,
   which draws on the voices installed in the OS — so unlike recognition,
   this needs no internet.

   Two things make this trickier than "call speak()":

     1. The microphone can hear the speaker. If a wake word is armed while
        the machine says "price 499 rupees", recognition picks its own voice
        up and can re-trigger. The caller therefore gets `onSuspend` /
        `onResume` hooks to pause listening around an announcement.
     2. Browsers block audio until the page has been interacted with. A scan
        is not a user gesture, so the very first announcement can be dropped;
        any click or keypress unlocks it from then on.

   Lives in its own file because both pages need it. It used to exist only
   inside kiosk.js, so the customer portal had no spoken price at all — and a
   mute button there would have toggled nothing.

   Usage:
       PriceSpeech.init({
         muteButton: document.getElementById("mute-toggle"),
         storageKey: "kiosk.priceSpeechMuted",
         getSettings: function () { return state.display.speech; },
         getCurrency: function () { return "₹"; },
         onToast: function (msg) { toast(msg); },
         onSuspend: ..., onResume: ...        // optional
       });
       PriceSpeech.announce(products);
       PriceSpeech.refresh();                 // after settings change
   ========================================================================== */

window.PriceSpeech = (function () {
  "use strict";

  var cfg = {
    muteButton: null,
    storageKey: "priceSpeechMuted",
    getSettings: function () { return {}; },
    getCurrency: function () { return ""; },
    onToast: null,
    onSuspend: null,
    onResume: null
  };

  var speech = {
    supported: typeof window.speechSynthesis !== "undefined",
    muted: false,
    unlocked: false,
    speaking: false
  };

  /* ---------- Storage (never fatal) --------------------------------------- */

  function storageGet(key) {
    try { return window.localStorage.getItem(key); } catch (err) { return null; }
  }

  function storageSet(key, value) {
    try {
      if (value === null) window.localStorage.removeItem(key);
      else window.localStorage.setItem(key, value);
    } catch (err) { /* private mode: muting just will not persist */ }
  }

  /* ---------- Settings ----------------------------------------------------- */

  function settings() {
    var s = cfg.getSettings() || {};
    return {
      enabled: s.enabled === true,
      language: s.language || "en-IN",
      template: s.template || "{name}. Price {price} rupees.",
      rate: typeof s.rate === "number" ? s.rate : 0.95,
      pitch: typeof s.pitch === "number" ? s.pitch : 1.05,
      voice: s.voice || "female"
    };
  }

  /** Build the sentence for one product from the station's template. */
  function textFor(product, template) {
    var sym = cfg.getCurrency();
    var values = {
      "{name}": product.name || "",
      // Speak the plain number: "499", not "₹499" — a synthesiser reads the
      // symbol as "rupee sign" or skips it entirely.
      "{price}": product.price != null
        ? String(Math.round(Number(product.price) * 100) / 100) : "",
      "{mrp}": product.mrp != null ? String(Number(product.mrp)) : "",
      "{discount}": product.discount_percent ? String(product.discount_percent) : "0",
      "{currency}": sym,
      "{availability}": product.availability || ""
    };

    var text = template;
    Object.keys(values).forEach(function (token) {
      text = text.split(token).join(values[token]);
    });
    // Collapse the gaps left by empty placeholders.
    return text.replace(/\s+/g, " ").replace(/\s+([.,])/g, "$1").trim();
  }

  /* ---------- Voice selection ---------------------------------------------- */

  /*
    The Web Speech API exposes no gender field, so it has to be inferred from
    the voice name. These are the ones that actually ship on Windows, Chrome
    and Android in the locales this app uses. Anything unrecognised scores
    neutral rather than being excluded — a working voice in the right language
    beats no voice at all.
  */
  var FEMALE_VOICE_HINTS = [
    "female", "woman",
    // Windows
    "zira", "heera", "kalpana", "hazel", "susan", "linda", "catherine", "eva",
    // Google / Android
    "google uk english female", "google us english", "salli", "joanna",
    "kimberly", "amy", "emma", "nicole", "aditi", "raveena", "neerja",
    // Apple
    "samantha", "victoria", "karen", "moira", "tessa", "fiona", "ava", "allison"
  ];

  var MALE_VOICE_HINTS = [
    "male", "man",
    "ravi", "hemant", "madhur", "prabhat", "david", "mark", "george", "james",
    "richard", "google uk english male", "brian", "matthew", "russell",
    "geraint", "alex", "daniel", "fred", "tom", "oliver"
  ];

  function voiceGender(voice) {
    var name = (voice.name || "").toLowerCase();
    var i;

    // Female MUST be tested first. "female" contains "male", and "woman"
    // contains "man" — checking male first would label every "Google UK
    // English Female" voice as male.
    for (i = 0; i < FEMALE_VOICE_HINTS.length; i++) {
      if (name.indexOf(FEMALE_VOICE_HINTS[i]) !== -1) return "female";
    }
    for (i = 0; i < MALE_VOICE_HINTS.length; i++) {
      if (name.indexOf(MALE_VOICE_HINTS[i]) !== -1) return "male";
    }
    return "unknown";
  }

  /**
   * Best installed voice for a language and gender preference.
   *
   * Scored rather than filtered: language is what matters most — a male voice
   * saying the price in the right language is far better than a female voice
   * in the wrong one, or silence.
   */
  function pickVoice(lang, preference) {
    var voices;
    try {
      voices = window.speechSynthesis.getVoices() || [];
    } catch (err) {
      return null;
    }
    if (!voices.length) return null;

    var wanted = (lang || "en-IN").toLowerCase();
    var base = wanted.split("-")[0];
    var want = preference || "female";

    var best = null;
    var bestScore = -Infinity;

    for (var i = 0; i < voices.length; i++) {
      var v = voices[i];
      var vLang = (v.lang || "").replace("_", "-").toLowerCase();
      var score = 0;

      if (vLang === wanted) score += 100;
      else if (vLang.indexOf(base + "-") === 0 || vLang === base) score += 55;
      else continue;                       // wrong language: not a candidate

      // The gender swing (+20/-15 = 35) is deliberately smaller than the gap
      // between an exact and a near locale match (45). A male voice speaking
      // en-IN beats a female voice speaking en-US: getting the accent and
      // number pronunciation right matters more than the timbre.
      if (want !== "auto") {
        var gender = voiceGender(v);
        if (gender === want) score += 20;
        else if (gender !== "unknown") score -= 15;
      }

      // Local voices need no network and start instantly.
      if (v.localService) score += 8;
      // Bracket access: "default" is a reserved word, and v.default trips up
      // older parsers even though modern browsers accept it.
      if (v["default"]) score += 3;

      if (score > bestScore) { bestScore = score; best = v; }
    }
    return best;
  }

  /* ---------- Announcing ---------------------------------------------------- */

  /**
   * Announce the products now on screen.
   * Cancels anything still being said, so a fresh scan always wins.
   */
  function announce(products) {
    var s = settings();
    if (!speech.supported || !s.enabled || speech.muted) return;
    if (!products || !products.length) return;

    try {
      window.speechSynthesis.cancel();
    } catch (err) {
      return;
    }

    var sentences = [];
    for (var i = 0; i < products.length; i++) {
      var line = textFor(products[i], s.template);
      if (line) sentences.push(line);
    }
    if (!sentences.length) return;

    // Let the caller stop listening so the microphone does not hear us.
    var suspended = false;
    if (typeof cfg.onSuspend === "function") suspended = cfg.onSuspend() === true;

    speech.speaking = true;

    var utterance = new SpeechSynthesisUtterance(sentences.join(" "));
    utterance.lang = s.language;
    utterance.rate = Math.min(1.5, Math.max(0.5, s.rate));
    utterance.pitch = Math.min(1.5, Math.max(0.5, s.pitch));
    utterance.volume = 1;

    var chosen = pickVoice(s.language, s.voice);
    if (chosen) utterance.voice = chosen;

    var finish = function () {
      speech.speaking = false;
      if (suspended && typeof cfg.onResume === "function") {
        suspended = false;
        cfg.onResume();
      }
    };

    utterance.onend = finish;
    utterance.onerror = finish;

    try {
      window.speechSynthesis.speak(utterance);
    } catch (err) {
      finish();
    }
  }

  /* ---------- Mute button --------------------------------------------------- */

  function setMuted(muted) {
    speech.muted = muted;
    storageSet(cfg.storageKey, muted ? "1" : null);
    if (muted) {
      try { window.speechSynthesis.cancel(); } catch (err) { /* ignore */ }
    }
    refresh();
  }

  /** Re-read the settings and show, hide or restyle the button to match. */
  function refresh() {
    if (!cfg.muteButton) return;
    var s = settings();

    if (!speech.supported || !s.enabled) {
      cfg.muteButton.hidden = true;
      return;
    }
    cfg.muteButton.hidden = false;

    var on = cfg.muteButton.querySelector(".icon-unmuted");
    var off = cfg.muteButton.querySelector(".icon-muted");
    if (on) on.hidden = speech.muted;
    if (off) off.hidden = !speech.muted;

    cfg.muteButton.title = speech.muted ? "Unmute spoken price" : "Mute spoken price";
    cfg.muteButton.setAttribute("aria-label", cfg.muteButton.title);
    cfg.muteButton.classList.toggle("is-muted", speech.muted);
  }

  function init(options) {
    Object.keys(options || {}).forEach(function (key) {
      if (key in cfg) cfg[key] = options[key];
    });

    speech.muted = storageGet(cfg.storageKey) === "1";

    if (cfg.muteButton) {
      cfg.muteButton.addEventListener("click", function () {
        setMuted(!speech.muted);
        if (typeof cfg.onToast === "function") {
          cfg.onToast(speech.muted ? "Spoken price muted." : "Spoken price on.");
        }
      });
    }

    if (!speech.supported) return;

    // Chrome populates the voice list asynchronously and returns [] until it
    // is ready. Ask for it now to start that off, and again when the browser
    // says it has changed — otherwise a lookup in the first second after load
    // would fall back to the system default voice, which is often male.
    try {
      window.speechSynthesis.getVoices();
    } catch (err) { /* not fatal; the default voice will be used */ }

    if (typeof window.speechSynthesis.onvoiceschanged !== "undefined") {
      window.speechSynthesis.onvoiceschanged = function () {
        try { window.speechSynthesis.getVoices(); } catch (err) { /* ignore */ }
      };
    }

    // Browsers require a gesture before audio may play. Mark the page unlocked
    // on the first interaction of any kind.
    var unlock = function () {
      speech.unlocked = true;
      document.removeEventListener("pointerdown", unlock);
      document.removeEventListener("keydown", unlock);
    };
    document.addEventListener("pointerdown", unlock);
    document.addEventListener("keydown", unlock);

    refresh();
  }

  return {
    init: init,
    announce: announce,
    refresh: refresh,
    setMuted: setMuted,
    isMuted: function () { return speech.muted; },
    isSupported: function () { return speech.supported; }
  };
})();
