/* ==========================================================================
   Price fitting — shared by the kiosk screen and the customer portal.
   --------------------------------------------------------------------------
   The stylesheet sizes each price by dividing the width of the tier it sits
   in by the width of the amount itself (see `--price-em` in kiosk.css). That
   second number is the one CSS cannot work out, so it is measured here.

   Measuring beats estimating per character. The display face is a variable
   weight with negative tracking and tabular figures, and a per-character
   guess is off by enough to still clip a four-figure price — which was the
   original bug. One hidden span at a known size gives the em width exactly,
   and the answer is cached, so a price that comes back around is free.

   This lives in its own file because it is needed by two pages. It used to
   exist only inside kiosk.js, which is why the customer portal — same markup,
   same class names, different script — kept clipping prices long after the
   kiosk stopped.

   Usage:
       PriceFit.fit(containerElement);      // after the cards are visible
       PriceFit.refitOnFontLoad(getRoot);   // once, at startup
   ========================================================================== */

window.PriceFit = (function () {
  "use strict";

  var RULER_PX = 100;
  // Breathing room, so the amount never quite touches the tier's padding.
  var SAFETY_EM = 0.3;

  var cache = Object.create(null);
  var ruler = null;

  /** Width of `text` in em, in the exact font `sample` is drawn in. */
  function emWidth(text, sample) {
    if (!text) return null;
    if (cache[text] != null) return cache[text];

    if (!ruler) {
      ruler = document.createElement("span");
      ruler.setAttribute("aria-hidden", "true");
      ruler.style.cssText =
        "position:absolute;left:-9999px;top:0;visibility:hidden;" +
        "white-space:pre;pointer-events:none;font-size:" + RULER_PX + "px;";
      document.body.appendChild(ruler);
    }

    var cs = window.getComputedStyle(sample);
    var size = parseFloat(cs.fontSize);
    if (!(size > 0)) return null;          // inside a display:none subtree

    ruler.style.fontFamily = cs.fontFamily;
    ruler.style.fontWeight = cs.fontWeight;
    ruler.style.fontStyle = cs.fontStyle;
    ruler.style.fontVariantNumeric = cs.fontVariantNumeric;
    ruler.style.fontFeatureSettings = cs.fontFeatureSettings;
    // Tracking is authored in em but computes to px against the element's
    // current size. Rescale it to the ruler's size or it lands wrong by
    // exactly the factor between the two.
    var tracking = parseFloat(cs.letterSpacing);
    ruler.style.letterSpacing =
      (isFinite(tracking) ? (tracking / size) * RULER_PX : 0) + "px";
    ruler.textContent = text;

    var em = ruler.getBoundingClientRect().width / RULER_PX;
    if (!isFinite(em) || em <= 0) return null;

    em += SAFETY_EM;
    cache[text] = em;
    return em;
  }

  /**
   * Hand every price under `root` its own measured width.
   *
   * Must run with the cards visible: a price inside a hidden view has no
   * resolved font size to measure tracking against, so switch the view on
   * first and call this second.
   */
  function fit(root) {
    if (!root) return;
    var prices = root.querySelectorAll(".product-price");
    for (var i = 0; i < prices.length; i++) {
      var em = emWidth(prices[i].textContent, prices[i]);
      if (em) prices[i].style.setProperty("--price-em", em.toFixed(3));
    }
  }

  /**
   * A webfont that arrives after the first measurement would leave the cache
   * describing the fallback face, and every price slightly mis-sized (the
   * kiosk fonts are `font-display: swap`, so this is the normal cold load,
   * not an edge case). Throw the cache away once the real fonts are in and
   * measure again.
   *
   * `getRoot` is a function so the caller can hand back whatever is on
   * screen at that moment, or null if nothing is.
   */
  function refitOnFontLoad(getRoot) {
    if (!document.fonts || !document.fonts.ready) return;
    document.fonts.ready.then(function () {
      cache = Object.create(null);
      fit(typeof getRoot === "function" ? getRoot() : getRoot);
    });
  }

  return { fit: fit, refitOnFontLoad: refitOnFontLoad };
})();
