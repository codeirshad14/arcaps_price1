/* ==========================================================================
   Full screen — shared by the kiosk screen and the customer portal.
   --------------------------------------------------------------------------
   Calling requestFullscreen() is all Windows and Android need. Apple devices
   are where it falls apart, in four separate ways:

     1. iPhone has no Fullscreen API for ordinary page elements — only for
        video. iPhones still expose `webkitRequestFullscreen`, which then does
        nothing at all, so support is judged by `fullscreenEnabled` (the
        browser's own answer), never by whether the method exists. The only
        full screen an iPhone gives a web page is opening it from the Home
        Screen, so callers can show that instruction instead.
     2. Safari before 16.4 has only the webkit-prefixed API, and without the
        ALLOW_KEYBOARD_INPUT flag it treats typing as a reason to leave full
        screen. The barcode scanner is a keyboard, so the very first scan
        threw the screen straight back out.
     3. The prefixed request returns nothing rather than a promise, so a
        refusal is reported only through the `webkitfullscreenerror` event.
     4. macOS animates into its own Space over about a second, and the change
        event can land before that animation has finished. Taking focus in
        that window makes macOS abandon the transition, so callers are given
        SETTLE_MS to hold off anything that moves focus.

   Lives in its own file because both pages need it — two copies are what
   left the customer page with none of the fixes above, the same lesson as
   price-fit.js.

   Usage:
       FullScreen.isAvailable()          // real element full screen works
       FullScreen.isStandalone()         // already opened from Home Screen
       FullScreen.canAddToHomeScreen()   // iPhone: the only route it has
       FullScreen.toggle()               // promise; rejects on refusal
       FullScreen.onChange(fn)           // fn(isActive) on enter and leave
       FullScreen.onError(fn)
   ========================================================================== */

window.FullScreen = (function () {
  "use strict";

  var SETTLE_MS = 700;

  var HOME_SCREEN_HINT =
    "iPhone cannot open web pages full screen in the browser. Tap Share " +
    "(the square with the arrow), choose “Add to Home Screen”, " +
    "then open it from the new icon.";

  var doc = document;
  var root = document.documentElement;

  // Every browser on iPhone and iPad is WebKit underneath and reports Apple
  // as its vendor, Chrome and Firefox included. Chrome on a Mac reports
  // Google, so this is Safari's engine specifically.
  var appleWebKit = /apple/i.test(navigator.vendor || "");

  function activeElement() {
    return doc.fullscreenElement || doc.webkitFullscreenElement ||
           doc.webkitCurrentFullScreenElement || null;
  }

  function isActive() { return !!activeElement(); }

  /** True only where element full screen will actually happen. See (1). */
  function isAvailable() {
    if (typeof doc.fullscreenEnabled === "boolean" ||
        typeof doc.webkitFullscreenEnabled === "boolean") {
      return !!(doc.fullscreenEnabled || doc.webkitFullscreenEnabled);
    }
    // A browser too old to report either flag: the method is all there is.
    return !!(root.requestFullscreen || root.webkitRequestFullscreen);
  }

  /** Opened from the Home Screen — already as full screen as a page gets. */
  function isStandalone() {
    if (navigator.standalone === true) return true;
    try {
      return window.matchMedia("(display-mode: standalone)").matches;
    } catch (err) {
      return false;
    }
  }

  /** An iPhone in the browser, where Add to Home Screen is the only way. */
  function canAddToHomeScreen() {
    return appleWebKit && "ontouchend" in doc && !isAvailable() && !isStandalone();
  }

  function enter() {
    // Prefixed first on Apple: it is the only form that takes the keyboard
    // flag, and every Safari still honours it. See (2).
    if (appleWebKit && root.webkitRequestFullscreen) {
      return root.webkitRequestFullscreen(window.Element && Element.ALLOW_KEYBOARD_INPUT);
    }
    if (root.requestFullscreen) return root.requestFullscreen();
    if (root.webkitRequestFullscreen) return root.webkitRequestFullscreen();
    throw new Error("Full screen is not supported here.");
  }

  function exit() {
    if (doc.exitFullscreen) return doc.exitFullscreen();
    if (doc.webkitExitFullscreen) return doc.webkitExitFullscreen();
    if (doc.webkitCancelFullScreen) return doc.webkitCancelFullScreen();
  }

  /**
   * Enter or leave full screen. Must be called from a click or key handler —
   * browsers refuse a request that is not a direct response to the user.
   *
   * Always returns a promise, whichever API was used underneath. A prefixed
   * request that is refused still resolves; onError is what reports it.
   */
  function toggle() {
    try {
      return Promise.resolve(isActive() ? exit() : enter());
    } catch (err) {
      return Promise.reject(err);
    }
  }

  // Safari 16.4+ fires both the prefixed and the plain event for one change,
  // so a listener can run twice in a row. Both callers are idempotent.
  function onChange(fn) {
    var handler = function () { fn(isActive()); };
    doc.addEventListener("fullscreenchange", handler);
    doc.addEventListener("webkitfullscreenchange", handler);
  }

  function onError(fn) {
    doc.addEventListener("fullscreenerror", fn);
    doc.addEventListener("webkitfullscreenerror", fn);
  }

  return {
    SETTLE_MS: SETTLE_MS,
    HOME_SCREEN_HINT: HOME_SCREEN_HINT,
    isActive: isActive,
    isAvailable: isAvailable,
    isStandalone: isStandalone,
    canAddToHomeScreen: canAddToHomeScreen,
    toggle: toggle,
    onChange: onChange,
    onError: onError
  };
})();
