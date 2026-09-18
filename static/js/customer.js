/* ==========================================================================
   Customer self-service QR portal — check page frontend
   --------------------------------------------------------------------------
   A trimmed sibling of kiosk.js: no physical scanner (there is none on a
   customer's own phone), no wake word, one lookup on screen at a time. The
   spoken-digit parser is reused near-verbatim from kiosk.js — it is the
   well-tested part worth keeping identical rather than rewriting.

   Sections:
     1. Config, state, element handles
     2. Small helpers
     3. Session countdown (auto-logout)
     4. Manual entry
     5. Spoken-code parser (English/Hindi/Marathi digits)
     6. Voice (single attempt, no wake word)
     7. API + rendering
     8. Boot
   ========================================================================== */

(function () {
  "use strict";

  /* ---------- 1. Config, state, element handles -------------------------- */

  var CONFIG = {
    TOAST_MS: 3200,
    STATUS_POLL_MS: 20000
  };

  var $ = function (id) { return document.getElementById(id); };

  var el = {
    manualForm: $("manual-form"),
    manualInput: $("manual-input"),
    micButton: $("mic-button"),
    micLabel: $("mic-label"),
    voicePanel: $("voice-panel"),
    voiceStatus: $("voice-status"),
    voiceTranscript: $("voice-transcript"),
    views: { idle: $("view-idle"), result: $("view-result"), notfound: $("view-notfound") },
    resultStage: $("result-stage"),
    cardTemplate: $("product-card-template"),
    priceTierTemplate: $("price-tier-template"),
    notfoundCode: $("notfound-code"),
    sessionBadge: $("session-badge"),
    muteToggle: $("mute-toggle"),
    fullscreenToggle: $("fullscreen-toggle"),
    sessionRemaining: $("session-remaining"),
    toast: $("toast")
  };

  var state = {
    display: null,
    view: "idle",
    station: "",
    secondsRemaining: 0,
    countdownTimer: null,
    inFlight: null,
    toastTimer: null
  };

  /* ---------- 2. Small helpers -------------------------------------------- */

  function show(node) { if (node) node.hidden = false; }
  function hide(node) { if (node) node.hidden = true; }
  function setText(node, text) { if (node) node.textContent = text; }

  function readJSON(id) {
    var node = $(id);
    if (!node) return null;
    try { return JSON.parse(node.textContent); } catch (err) { return null; }
  }

  function formatMoney(value, symbol) {
    var amount = Number(value);
    if (!isFinite(amount)) return String(value || "");
    var hasPaise = Math.round(amount * 100) % 100 !== 0;
    var text = amount.toLocaleString("en-IN", {
      minimumFractionDigits: hasPaise ? 2 : 0,
      maximumFractionDigits: 2
    });
    return (symbol || "") + text;
  }

  function toast(message, isError) {
    if (!el.toast) return;
    clearTimeout(state.toastTimer);
    el.toast.textContent = message;
    el.toast.classList.toggle("is-error", !!isError);
    show(el.toast);
    state.toastTimer = setTimeout(function () { hide(el.toast); }, CONFIG.TOAST_MS);
  }

  function flags() { return (state.display && state.display.fields) || {}; }
  function currency() { return (state.display && state.display.currency_symbol) || ""; }

  function switchView(name) {
    state.view = name;
    Object.keys(el.views).forEach(function (key) {
      el.views[key].classList.toggle("is-active", key === name);
    });
    // Drives the kiosk stylesheet's phone behaviour: with a result on screen
    // the console collapses to just its input so the price gets the space.
    document.body.classList.toggle("is-showing-result", name !== "idle");
  }

  /* ---------- 3. Session countdown (auto-logout) -------------------------- */

  function renderRemaining(seconds) {
    var m = Math.floor(seconds / 60);
    var s = seconds % 60;
    setText(el.sessionRemaining, m + ":" + (s < 10 ? "0" : "") + s);
    if (el.sessionBadge) el.sessionBadge.classList.toggle("is-low", seconds <= 60);
  }

  /*
    Bounce off the check page rather than deciding where to go here. The
    server holds the real clock, so it is the thing that should rule on
    whether the visit is over — this timer can drift, or fire while the
    session is in fact still alive. An expired visit is sent on to the
    closing page; a live one simply carries on.

    It also keeps the tokenised entry URL out of the JavaScript entirely, so
    it cannot leak through a cached script or a stray Referer header.
  */
  function goExpired() {
    window.location.href = "/customer/check/";
  }

  function startSessionCountdown() {
    clearInterval(state.countdownTimer);
    renderRemaining(state.secondsRemaining);
    state.countdownTimer = setInterval(function () {
      state.secondsRemaining = Math.max(0, state.secondsRemaining - 1);
      renderRemaining(state.secondsRemaining);
      if (state.secondsRemaining <= 0) {
        clearInterval(state.countdownTimer);
        goExpired();
      }
    }, 1000);
  }

  /** Resync from the server's clock rather than the browser's timer, which
   *  drifts (and pauses) when a phone tab is backgrounded. */
  function resyncRemaining(seconds) {
    if (typeof seconds === "number" && isFinite(seconds)) {
      state.secondsRemaining = seconds;
      renderRemaining(state.secondsRemaining);
    }
  }

  function pollStatus() {
    fetch("/customer/status/", { headers: { Accept: "application/json" } })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (data.expired) { goExpired(); return; }
        resyncRemaining(data.seconds_remaining);
      })
      .catch(function () { /* a dropped poll is not worth surfacing */ });
  }

  /* ---------- 4. Manual entry ---------------------------------------------- */

  /**
   * Put the cursor back in the code box.
   *
   * Called only from the typed path, never after a voice lookup: focusing the
   * box raises the on-screen keyboard, which is what someone typing wants and
   * exactly what someone who just used the microphone does not.
   */
  function focusCode() {
    if (!el.manualInput) return;
    el.manualInput.focus({ preventScroll: true });
  }

  function bindManualEntry() {
    if (!el.manualForm) return;
    el.manualForm.addEventListener("submit", function (event) {
      event.preventDefault();
      var typed = el.manualInput.value.trim();
      if (!typed) {
        toast("Type a barcode or product code first.");
        focusCode();
        return;
      }
      el.manualInput.value = "";
      submitCode(typed, "manual");
      // Tapping the button moves focus to it and drops the keyboard, so the
      // box has to be reclaimed even though the customer was just typing in it.
      focusCode();
    });
  }

  /* ---------- 5. Spoken-code parser ---------------------------------------- */
  /* Identical to kiosk.js's parser — same digit words, same filler words, so
     a customer gets exactly the same voice behaviour as the showroom screen. */

  var DIGIT_WORDS = {
    "zero": "0", "oh": "0", "shunya": "0", "sunya": "0", "sifar": "0",
    "one": "1", "ek": "1", "eak": "1",
    "two": "2", "do": "2", "doh": "2",
    "three": "3", "teen": "3", "tin": "3",
    "four": "4", "char": "4", "chaar": "4",
    "five": "5", "paanch": "5", "panch": "5", "paach": "5",
    "six": "6", "chhe": "6", "chah": "6", "chay": "6",
    "seven": "7", "saat": "7", "sat": "7",
    "eight": "8", "aath": "8", "ath": "8",
    "nine": "9", "nau": "9", "nao": "9",
    "don": "2", "doan": "2",
    "saha": "6", "sahaa": "6",
    "nav": "9",
    "aik": "1", "ik": "1", "ekk": "1",
    "donn": "2", "doe": "2",
    "theen": "3", "teenn": "3",
    "chaaar": "4", "chare": "4",
    "paanj": "5", "punch": "5",
    "chhah": "6", "chhey": "6", "chhe6": "6",
    "saatt": "7", "saath": "7",
    "aatth": "8", "aat": "8",
    "nauu": "9", "nou": "9", "naw": "9",
    "शून्य": "0", "एक": "1", "दो": "2", "दोन": "2", "तीन": "3", "चार": "4",
    "पांच": "5", "पाँच": "5", "पाच": "5", "छह": "6", "छे": "6", "सहा": "6",
    "सात": "7", "आठ": "8", "नौ": "9", "नऊ": "9"
  };

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
    "cha": 1, "chi": 1, "che": 1, "cya": 1,
    "kay": 1, "kiti": 1, "kimat": 1, "kimmat": 1, "bhav": 1,
    "ahe": 1, "sanga": 1, "sangaa": 1, "dakhva": 1, "dakhava": 1,
    "mala": 1, "yacha": 1, "yachi": 1, "kramank": 1,
    "का": 1, "की": 1, "के": 1, "क्या": 1, "कितना": 1, "कितने": 1, "है": 1,
    "बताओ": 1, "बता": 1, "बोलो": 1, "दिखाओ": 1, "दाम": 1, "कीमत": 1,
    "प्राइस": 1, "रेट": 1, "बारकोड": 1, "कोड": 1, "नंबर": 1, "मुझे": 1,
    "इसका": 1, "और": 1, "चा": 1, "ची": 1, "चे": 1, "काय": 1, "किती": 1,
    "किंमत": 1, "भाव": 1, "आहे": 1, "सांगा": 1, "दाखवा": 1, "मला": 1,
    "याचा": 1, "याची": 1, "क्रमांक": 1, "आणि": 1
  };

  function normaliseDigits(text) {
    return text.replace(/[०-९]/g, function (ch) {
      return String(ch.charCodeAt(0) - 0x0966);
    });
  }

  function normaliseTranscript(text) {
    return normaliseDigits(text || "")
      .toLowerCase()
      .replace(/[.,!?;:'"।]/g, " ")
      .replace(/[-_/]+/g, " ")
      .replace(/\s+/g, " ")
      .trim();
  }

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
        kept.push(null);
        continue;
      }
      var alnum = token.replace(/[^a-z0-9]/g, "");
      kept.push(alnum || null);
    }

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

    merged.sort(function (a, b) { return b.length - a.length; });
    return merged;
  }

  function codesFromAlternatives(alternatives) {
    for (var i = 0; i < alternatives.length; i++) {
      var codes = parseSpokenCodes(alternatives[i]);
      if (codes.length) return { codes: codes, heard: alternatives[i] };
    }
    return null;
  }

  /* ---------- 6. Voice (single attempt, no wake word) ---------------------- */

  function voiceLang() {
    var voice = (state.display && state.display.voice) || {};
    var lang = voice.language;
    if (lang && lang !== "auto") return lang;
    return "en-IN";
  }

  function setupVoice() {
    var SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SpeechRecognition || !el.micButton) {
      hide(el.micButton);
      return;
    }

    var recognition = new SpeechRecognition();
    recognition.interimResults = true;
    recognition.maxAlternatives = 5;

    var alternatives = [];
    var resolved = false;

    recognition.onstart = function () {
      resolved = false;
      alternatives = [];
      el.micButton.classList.add("is-listening");
      setText(el.micLabel, "Listening…");
      setText(el.voiceStatus, "Listening…");
      setText(el.voiceTranscript, "");
      show(el.voicePanel);
    };

    recognition.onresult = function (event) {
      if (resolved) return;
      var interim = "";
      var sawFinal = false;

      for (var i = event.resultIndex; i < event.results.length; i++) {
        var result = event.results[i];
        if (result.isFinal) {
          sawFinal = true;
          for (var a = 0; a < result.length; a++) alternatives.push(result[a].transcript);
        } else {
          interim += result[0].transcript;
        }
      }
      setText(el.voiceTranscript, interim);

      if (sawFinal) {
        var hit = codesFromAlternatives(alternatives);
        if (hit) {
          resolved = true;
          setText(el.voiceStatus, "Got it:");
          setText(el.voiceTranscript, hit.heard + "  →  " + hit.codes.join(", "));
          try { recognition.stop(); } catch (err) { /* already stopping */ }
          submitCode(hit.codes[0], "voice");
        }
      }
    };

    recognition.onerror = function (event) {
      el.micButton.classList.remove("is-listening");
      setText(el.micLabel, "Speak the code");
      if (resolved) return;
      if (event.error === "not-allowed" || event.error === "service-not-allowed") {
        setText(el.voiceStatus, "Microphone blocked —");
        setText(el.voiceTranscript, "allow mic access, or type the code below.");
      } else if (event.error !== "aborted") {
        setText(el.voiceStatus, "Didn't catch that —");
        setText(el.voiceTranscript, "try again, or type the code below.");
      }
    };

    recognition.onend = function () {
      el.micButton.classList.remove("is-listening");
      setText(el.micLabel, "Speak the code");
      if (resolved) return;
      var hit = codesFromAlternatives(alternatives);
      if (hit) {
        submitCode(hit.codes[0], "voice");
      } else {
        setText(el.voiceStatus, "Didn't catch a code —");
        setText(el.voiceTranscript, "try again, or type it below.");
      }
    };

    el.micButton.addEventListener("click", function () {
      recognition.lang = voiceLang();
      try { recognition.start(); } catch (err) { /* already running */ }
    });

    show(el.micButton);
  }

  /* ---------- 7. API + rendering -------------------------------------------- */

  function submitCode(code, source) {
    if (!code) return;
    if (state.inFlight) state.inFlight.abort();

    var controller = new AbortController();
    state.inFlight = controller;

    var url = "/api/lookup/?code=" + encodeURIComponent(code) +
              "&source=" + encodeURIComponent(source || "manual") +
              "&station=" + encodeURIComponent(state.station);

    fetch(url, { signal: controller.signal, headers: { Accept: "application/json" } })
      .then(function (response) {
        return response.json().then(function (data) {
          return { ok: response.ok, status: response.status, data: data };
        });
      })
      .then(function (result) {
        if (controller !== state.inFlight) return;
        state.inFlight = null;

        var data = result.data || {};
        if (data.display) state.display = data.display;
        if (data.customer_session) resyncRemaining(data.customer_session.seconds_remaining);

        if (data.tally_product) {
          renderResult(data.tally_product);
        } else {
          renderNotFound((data.not_found && data.not_found[0]) || data.code || code);
        }
      })
      .catch(function (error) {
        if (error.name === "AbortError") return;
        state.inFlight = null;
        toast("Cannot reach the price server. Please try again.", true);
      });
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

  /**
   * Separate the hero price from the secondary rates. Same rule as kiosk.js:
   * a response with no default flag anywhere promotes the first tier, so the
   * price area is never left empty.
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
   * Built with createElement/textContent so a price-list name can never
   * inject HTML.
   */
  function fillWholesaleLine(node, tiers, f, sym) {
    if (!node) return;
    node.textContent = "";

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

  function fillCard(card, product) {
    var f = flags();
    var sym = currency();

    var image = card.querySelector(".product-image");
    var placeholder = card.querySelector(".image-placeholder");
    var name = card.querySelector(".product-name");
    var wholesale = card.querySelector(".wholesale-line");
    var tiersContainer = card.querySelector(".price-tiers");
    var description = card.querySelector(".product-description");
    var availability = card.querySelector(".availability");
    var code = card.querySelector(".result-code");

    // Same media handling as the kiosk: a missing image falls back to the
    // tidy placeholder rather than collapsing the card's layout.
    var wantsImage = f.image !== false;
    if (wantsImage) {
      if (product.image) {
        image.src = product.image;
        image.alt = product.name || "";
        image.onerror = function () { hide(image); show(placeholder); };
        show(image);
      } else {
        show(placeholder);
      }
    }
    card.classList.toggle("no-media", !wantsImage);

    if (f.name !== false && product.name) setText(name, product.name);

    // Retail keeps the price area to itself as the hero figure; Wholesale is
    // drawn as a small line below the description. Same split as the kiosk.
    if (tiersContainer) {
      tiersContainer.textContent = "";
      var tiers = (product.prices && product.prices.length) ? product.prices : legacyTiers(product);
      var split = splitTiers(tiers);

      for (var t = 0; t < split.primary.length; t++) {
        var tierNode = buildPriceTier(split.primary[t], f, sym);
        if (tierNode) tiersContainer.appendChild(tierNode);
      }
      fillWholesaleLine(wholesale, split.secondary, f, sym);
    }

    if (f.description === true && product.description) {
      setText(description, product.description);
      show(description);
    }

    if (f.availability === true) {
      var inStock = Number(product.closing_stock) > 0;
      setText(availability, product.availability || (inStock ? "In Stock" : "Out of Stock"));
      availability.className = "availability " + (inStock ? "in-stock" : "out-of-stock");
      show(availability);
    }

    setText(code, product.barcode ? "Code: " + product.barcode : "");
  }

  function renderResult(product) {
    el.resultStage.textContent = "";
    var card = el.cardTemplate.content.firstElementChild.cloneNode(true);
    fillCard(card, product);
    el.resultStage.appendChild(card);
    // The kiosk stylesheet sizes cards off this, so it has to be set here
    // too — without it the single-card layout rules never match.
    el.resultStage.setAttribute("data-count", "1");
    switchView("result");
    // After the view is visible, so each price has a resolved font to be
    // measured against. See price-fit.js.
    PriceFit.fit(el.resultStage);
    // Same spoken price the kiosk gives, honouring the same station setting.
    // There is no wake word here, so no listening to suspend.
    PriceSpeech.announce([product]);
  }

  function renderNotFound(code) {
    setText(el.notfoundCode, code || "—");
    switchView("notfound");
  }

  /* ---------- 7b. Spoken price and screen controls ------------------------- */

  /*
    The two top-bar controls a customer can use. Both are hidden in the
    markup and revealed only once we know they will work, so a browser
    without speech synthesis or without the Fullscreen API never shows a
    button that does nothing.
  */
  function bindSpeech() {
    PriceSpeech.init({
      muteButton: el.muteToggle,
      // Its own key, so muting on the customer's phone does not depend on,
      // or interfere with, the setting on a shared kiosk machine.
      storageKey: "customer.priceSpeechMuted",
      getSettings: function () { return (state.display && state.display.speech) || {}; },
      getCurrency: currency,
      onToast: toast
    });
  }

  function fullscreenSupported() {
    var d = document.documentElement;
    return !!(d.requestFullscreen || d.webkitRequestFullscreen);
  }

  function bindFullscreen() {
    if (!el.fullscreenToggle) return;
    if (!fullscreenSupported()) return;      // iOS Safari on iPhone: no API
    show(el.fullscreenToggle);

    el.fullscreenToggle.addEventListener("click", function () {
      var d = document.documentElement;
      try {
        if (document.fullscreenElement || document.webkitFullscreenElement) {
          (document.exitFullscreen || document.webkitExitFullscreen).call(document);
        } else {
          (d.requestFullscreen || d.webkitRequestFullscreen).call(d);
        }
      } catch (err) {
        toast("Full screen is not available on this device.");
      }
    });
  }

  /* ---------- 8. Boot -------------------------------------------------------- */

  function init() {
    var initial = readJSON("initial-display");
    if (initial) state.display = initial;

    var sessionData = readJSON("session-data") || {};
    state.secondsRemaining = sessionData.seconds_remaining || 0;
    state.station = sessionData.station || "";

    bindManualEntry();
    setupVoice();
    bindSpeech();
    bindFullscreen();
    switchView("idle");
    PriceFit.refitOnFontLoad(function () {
      return state.view === "result" ? el.resultStage : null;
    });
    startSessionCountdown();
    setInterval(pollStatus, CONFIG.STATUS_POLL_MS);

    // The markup carries `autofocus`, but that only fires on a fresh parse and
    // some mobile browsers ignore it outright. Asking again here covers a
    // plain reload, and `pageshow` covers coming back to the page from the
    // browser's cache, where nothing is parsed and autofocus never runs.
    focusCode();
    window.addEventListener("pageshow", function (event) {
      focusCode();
      // A page handed back from the browser's cache is not re-run: its
      // countdown was frozen while it sat there, so a visit that has since
      // ended still looks alive and still shows the prices. Ask the server,
      // which holds the real clock, before letting the customer carry on.
      if (event.persisted) pollStatus();
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
