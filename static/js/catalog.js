/* ==========================================================================
   Internal catalogue — frontend
   --------------------------------------------------------------------------
   Browse every product: search by name or part number, filter by category,
   company, stock, price and discount, sort, and open any product for its
   full details. Data comes from /api/catalog/ (staff only).

   Sections:
     1. Config, state, element handles
     2. Small helpers
     3. Reading and writing the filters (kept in the address bar)
     4. Loading products
     5. Rendering cards, filters and chips
     6. Product detail
     7. Filters drawer (phones)
     8. Boot
   ========================================================================== */

(function () {
  "use strict";

  /* ---------- 1. Config, state, element handles -------------------------- */

  var SEARCH_DEBOUNCE_MS = 260;
  var PRICE_DEBOUNCE_MS = 500;

  var config = readJSON("catalog-config") || {};

  var state = {
    filters: { q: "", category: "", company: "", stock: "", discount: false,
               min_price: "", max_price: "", sort: "name" },
    page: 0,
    hasMore: true,
    loading: false,
    controller: null,     // AbortController of the request in flight
    items: [],            // every product loaded so far, by position
    facets: { categories: [], companies: [] },
    toastTimer: null
  };

  var $ = function (id) { return document.getElementById(id); };

  var el = {
    search: $("search"),
    searchForm: $("search-form"),
    searchClear: $("search-clear"),
    sort: $("sort"),
    grid: $("grid"),
    scroll: $("scroll"),
    more: $("more"),
    empty: $("empty"),
    emptyClear: $("empty-clear"),
    count: $("count"),
    chips: $("active-chips"),
    cardTemplate: $("card-template"),

    filters: $("filters"),
    filtersOpen: $("filters-open"),
    filtersClose: $("filters-close"),
    filtersBackdrop: $("filters-backdrop"),
    filtersBadge: $("filters-badge"),
    filtersClear: $("filters-clear"),
    segments: document.querySelectorAll(".segment[data-stock]"),
    minPrice: $("min-price"),
    maxPrice: $("max-price"),
    discountOnly: $("discount-only"),
    categoryList: $("category-list"),
    companyGroup: $("company-group"),
    companyList: $("company-list"),

    backToScan: $("back-to-scan"),
    detail: $("detail"),
    detailClose: $("detail-close"),
    toast: $("toast")
  };

  // Phones and tablets: nothing focuses the search box but a tap on it, so
  // the on-screen keyboard never covers the list uninvited.
  var touchScreen = !!(window.matchMedia && window.matchMedia("(pointer: coarse)").matches);

  /* ---------- 2. Small helpers ------------------------------------------- */

  function readJSON(id) {
    var node = document.getElementById(id);
    if (!node) return null;
    try { return JSON.parse(node.textContent); } catch (err) { return null; }
  }

  function show(node) { if (node) node.hidden = false; }
  function hide(node) { if (node) node.hidden = true; }
  function setText(node, text) { if (node) node.textContent = text; }

  function debounce(fn, ms) {
    var timer = null;
    return function () {
      clearTimeout(timer);
      timer = setTimeout(fn, ms);
    };
  }

  /** "₹1,499.00" — the same money format as the counter screen. */
  function money(value) {
    var amount = Number(value);
    if (value == null || !isFinite(amount)) return "—";
    return (config.currency || "") + amount.toLocaleString("en-IN", {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2
    });
  }

  /** "42.000" -> "42",  "5.500" -> "5.5" */
  function quantity(value) {
    var n = Number(value);
    return isFinite(n) ? String(parseFloat(n.toFixed(3))) : String(value || "");
  }

  function toast(message) {
    if (!el.toast) return;
    clearTimeout(state.toastTimer);
    el.toast.textContent = message;
    el.toast.classList.add("is-error");
    show(el.toast);
    state.toastTimer = setTimeout(function () { hide(el.toast); }, 3200);
  }

  function retailTier(product) {
    var prices = product.prices || [];
    for (var i = 0; i < prices.length; i++) if (prices[i].slug === "retail") return prices[i];
    return { price: product.price, mrp: product.mrp, discount_percent: product.discount_percent };
  }

  function wholesaleTier(product) {
    var prices = product.prices || [];
    for (var i = 0; i < prices.length; i++) if (prices[i].slug === "wholesale") return prices[i];
    return null;
  }

  function inStock(product) { return Number(product.closing_stock) > 0; }

  /* ---------- 3. Reading and writing the filters ------------------------- */

  /*
    The filters live in the address bar, so a reload, the Back button, or a
    link sent to a colleague all land on the same list.
  */
  function readFiltersFromUrl() {
    var params = new URLSearchParams(window.location.search);
    var f = state.filters;
    f.q = params.get("q") || "";
    f.category = params.get("category") || "";
    f.company = params.get("company") || "";
    f.stock = params.get("stock") || "";
    f.discount = params.get("discount") === "1";
    f.min_price = params.get("min_price") || "";
    f.max_price = params.get("max_price") || "";
    f.sort = params.get("sort") || "name";
  }

  function filterParams() {
    var f = state.filters;
    var params = new URLSearchParams();
    if (f.q) params.set("q", f.q);
    if (f.category) params.set("category", f.category);
    if (f.company) params.set("company", f.company);
    if (f.stock) params.set("stock", f.stock);
    if (f.discount) params.set("discount", "1");
    if (f.min_price) params.set("min_price", f.min_price);
    if (f.max_price) params.set("max_price", f.max_price);
    if (f.sort && f.sort !== "name") params.set("sort", f.sort);
    return params;
  }

  function writeFiltersToUrl() {
    var params = filterParams();
    if (config.station) params.set("station", config.station);
    var query = params.toString();
    try {
      history.replaceState(null, "", window.location.pathname + (query ? "?" + query : ""));
    } catch (err) { /* a sandboxed frame: the list still works */ }
  }

  /** Put the filter values back into the controls (after a URL load or a reset). */
  function syncControls() {
    var f = state.filters;
    el.search.value = f.q;
    el.searchClear.hidden = !f.q;
    el.sort.value = f.sort;
    el.minPrice.value = f.min_price;
    el.maxPrice.value = f.max_price;
    el.discountOnly.checked = f.discount;
    Array.prototype.forEach.call(el.segments, function (segment) {
      segment.classList.toggle("is-on", segment.getAttribute("data-stock") === f.stock);
      segment.setAttribute("aria-checked", segment.getAttribute("data-stock") === f.stock);
    });
  }

  function activeFilterCount() {
    var f = state.filters;
    return [f.category, f.company, f.stock, f.discount, f.min_price, f.max_price]
      .filter(Boolean).length;
  }

  /* ---------- 4. Loading products ---------------------------------------- */

  /** Start the list again from page 1 — any filter or search change. */
  function reload() {
    state.page = 0;
    state.hasMore = true;
    state.items = [];
    writeFiltersToUrl();
    loadNextPage(true);
  }

  function loadNextPage(fresh) {
    if (!fresh && (state.loading || !state.hasMore)) return;
    if (state.controller) state.controller.abort();

    var controller = new AbortController();
    state.controller = controller;
    state.loading = true;

    var page = state.page + 1;
    var params = filterParams();
    params.set("page", String(page));
    if (config.station) params.set("station", config.station);

    if (fresh) el.grid.classList.add("is-loading");
    el.more.classList.remove("is-done");

    fetch("/api/catalog/?" + params.toString(), {
      signal: controller.signal,
      headers: { Accept: "application/json" },
      credentials: "same-origin"
    })
      .then(function (response) {
        // Signed out in another tab, or the session ran out: back to sign in.
        if (response.status === 401 || response.status === 403) {
          window.location.href = "/login/?next=" + encodeURIComponent(location.pathname + location.search);
          throw new Error("signed-out");
        }
        if (!response.ok) throw new Error("HTTP " + response.status);
        return response.json();
      })
      .then(function (data) {
        if (controller !== state.controller) return;   // superseded
        state.controller = null;
        state.loading = false;
        state.page = page;
        state.hasMore = !!data.has_more;
        if (data.currency_symbol) config.currency = data.currency_symbol;

        if (fresh) {
          el.grid.textContent = "";
          el.grid.classList.remove("is-loading");
          el.scroll.scrollTop = 0;
        }
        if (data.facets) {
          state.facets = data.facets;
          renderFacets();
        }
        appendCards(data.results || []);
        renderSummary(data.count || 0);

        el.empty.hidden = (data.count || 0) > 0;
        el.more.classList.toggle("is-done", !state.hasMore);
        // A short first page on a tall screen leaves nothing to scroll to,
        // so the "near the bottom" trigger never fires: keep filling.
        if (state.hasMore && el.scroll.scrollHeight <= el.scroll.clientHeight + 40) {
          loadNextPage(false);
        }
      })
      .catch(function (error) {
        if (error.name === "AbortError" || error.message === "signed-out") return;
        state.loading = false;
        el.grid.classList.remove("is-loading");
        el.more.classList.add("is-done");
        toast("Could not load the catalogue. Check the connection and try again.");
      });
  }

  /* ---------- 5. Rendering cards, filters and chips ---------------------- */

  function appendCards(products) {
    var fragment = document.createDocumentFragment();
    for (var i = 0; i < products.length; i++) {
      var index = state.items.length;
      state.items.push(products[i]);
      var card = buildCard(products[i], index);
      // A small stagger, so a new page arrives as a wave, not a block.
      card.style.animationDelay = Math.min(i, 12) * 22 + "ms";
      fragment.appendChild(card);
    }
    el.grid.appendChild(fragment);
  }

  function buildCard(product, index) {
    var card = el.cardTemplate.content.firstElementChild.cloneNode(true);
    card.setAttribute("data-index", String(index));
    card.setAttribute("aria-label", (product.name || "") + " — open details");

    var image = card.querySelector(".cat-image");
    var placeholder = card.querySelector(".cat-placeholder");
    if (product.image) {
      image.src = product.image;
      image.alt = product.name || "";
      image.hidden = false;
      placeholder.hidden = true;
      image.onerror = function () { image.hidden = true; placeholder.hidden = false; };
    }

    setText(card.querySelector(".cat-code"), product.barcode || "");
    var company = card.querySelector(".cat-company");
    if (config.all_companies && product.company) {
      setText(company, product.company_code || product.company);
      company.hidden = false;
    }
    setText(card.querySelector(".cat-name"), product.name || "");
    setText(card.querySelector(".cat-category"), product.category || "");

    var retail = retailTier(product);
    setText(card.querySelector(".cat-price"), money(retail.price));
    var mrp = card.querySelector(".cat-mrp");
    if (retail.mrp != null && Number(retail.mrp) > Number(retail.price)) {
      setText(mrp, money(retail.mrp));
      mrp.hidden = false;
    }
    var discount = card.querySelector(".cat-discount");
    if (Number(retail.discount_percent) > 0) {
      setText(discount, Number(retail.discount_percent) + "% OFF");
      discount.hidden = false;
    }

    var wholesale = wholesaleTier(product);
    var wholesaleNode = card.querySelector(".cat-wholesale");
    if (wholesale && wholesale.price != null) {
      wholesaleNode.append("Wholesale ");
      var strong = document.createElement("strong");
      strong.textContent = money(wholesale.price);
      wholesaleNode.appendChild(strong);
    }

    var stockNode = card.querySelector(".cat-stock");
    var stockValue = document.createElement("strong");
    stockValue.textContent = quantity(product.closing_stock) + " " + (product.unit || "");
    stockNode.append("Stock ");
    stockNode.appendChild(stockValue);
    stockNode.classList.add(inStock(product) ? "is-in" : "is-out");
    card.classList.toggle("is-out", !inStock(product));

    return card;
  }

  function renderSummary(total) {
    el.count.textContent = "";
    var strong = document.createElement("strong");
    strong.textContent = total.toLocaleString("en-IN");
    el.count.appendChild(strong);
    el.count.append(total === 1 ? " product" : " products");

    renderChips();
    var active = activeFilterCount();
    el.filtersBadge.textContent = String(active);
    el.filtersBadge.hidden = active === 0;
  }

  function facetButton(label, count, isOn, onPick) {
    var button = document.createElement("button");
    button.type = "button";
    button.className = "facet" + (isOn ? " is-on" : "");
    button.setAttribute("aria-pressed", isOn ? "true" : "false");
    var name = document.createElement("span");
    name.className = "facet-name";
    name.textContent = label;
    button.appendChild(name);
    if (count != null) {
      var badge = document.createElement("span");
      badge.className = "facet-count";
      badge.textContent = count.toLocaleString("en-IN");
      button.appendChild(badge);
    }
    button.addEventListener("click", onPick);
    return button;
  }

  function renderFacets() {
    var f = state.filters;
    var categories = state.facets.categories || [];
    var total = categories.reduce(function (sum, c) { return sum + c.count; }, 0);

    el.categoryList.textContent = "";
    el.categoryList.appendChild(facetButton("All categories", total, !f.category, function () {
      pick("category", "");
    }));
    categories.forEach(function (category) {
      var id = String(category.id);
      el.categoryList.appendChild(facetButton(category.name, category.count, f.category === id, function () {
        pick("category", f.category === id ? "" : id);
      }));
    });

    var companies = state.facets.companies || [];
    if (config.all_companies && companies.length > 1) {
      el.companyList.textContent = "";
      el.companyList.appendChild(facetButton("All companies", null, !f.company, function () {
        pick("company", "");
      }));
      companies.forEach(function (company) {
        el.companyList.appendChild(facetButton(company.name, company.count, f.company === company.slug,
          function () { pick("company", f.company === company.slug ? "" : company.slug); }));
      });
      show(el.companyGroup);
    } else {
      hide(el.companyGroup);
    }
  }

  /** The filters in force, each a chip that removes itself when tapped. */
  function renderChips() {
    var f = state.filters;
    var chips = [];

    if (f.category) {
      var category = (state.facets.categories || []).filter(function (c) {
        return String(c.id) === f.category;
      })[0];
      chips.push([category ? category.name : "Category", function () { pick("category", ""); }]);
    }
    if (f.company) {
      var company = (state.facets.companies || []).filter(function (c) { return c.slug === f.company; })[0];
      chips.push([company ? company.name : f.company, function () { pick("company", ""); }]);
    }
    if (f.stock) chips.push([f.stock === "in" ? "In stock" : "Out of stock", function () { pick("stock", ""); }]);
    if (f.discount) chips.push(["Discounted", function () { pick("discount", false); }]);
    if (f.min_price || f.max_price) {
      var label = f.min_price && f.max_price ? money(f.min_price) + " – " + money(f.max_price)
        : f.min_price ? "From " + money(f.min_price) : "Up to " + money(f.max_price);
      chips.push([label, function () {
        state.filters.min_price = "";
        state.filters.max_price = "";
        syncControls();
        reload();
      }]);
    }

    el.chips.textContent = "";
    chips.forEach(function (chip) {
      var button = document.createElement("button");
      button.type = "button";
      button.className = "active-chip";
      button.setAttribute("aria-label", "Remove filter: " + chip[0]);
      button.append(chip[0]);
      button.insertAdjacentHTML("beforeend",
        '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" ' +
        'stroke-linecap="round" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18"/></svg>');
      button.addEventListener("click", chip[1]);
      el.chips.appendChild(button);
    });
  }

  /** Change one filter and start the list again. */
  function pick(name, value) {
    state.filters[name] = value;
    syncControls();
    reload();
  }

  function clearAll() {
    var sort = state.filters.sort;
    state.filters = { q: "", category: "", company: "", stock: "", discount: false,
                      min_price: "", max_price: "", sort: sort };
    syncControls();
    reload();
  }

  /* ---------- 6. Product detail ------------------------------------------ */

  function openDetail(product) {
    var image = $("detail-image");
    var placeholder = $("detail-placeholder");
    image.hidden = true;
    placeholder.hidden = false;
    var src = product.image_large || product.image;
    if (src) {
      image.onload = function () { image.hidden = false; placeholder.hidden = true; };
      image.onerror = function () { image.hidden = true; placeholder.hidden = false; };
      image.alt = product.name || "";
      image.src = src;
    } else {
      image.removeAttribute("src");
    }

    setText($("detail-code"), product.barcode || "");
    var company = $("detail-company");
    company.hidden = !(config.all_companies && product.company);
    setText(company, product.company || "");
    var category = $("detail-category");
    category.hidden = !product.category;
    setText(category, product.category || "");
    setText($("detail-name"), product.name || "");

    var retail = retailTier(product);
    setText($("detail-price"), money(retail.price));
    var mrp = $("detail-mrp");
    mrp.hidden = !(retail.mrp != null && Number(retail.mrp) > Number(retail.price));
    setText(mrp, money(retail.mrp));
    var discount = $("detail-discount");
    discount.hidden = !(Number(retail.discount_percent) > 0);
    setText(discount, Number(retail.discount_percent) + "% OFF");

    var wholesale = wholesaleTier(product);
    var facts = [
      ["Retail (incl. GST)", money(retail.price)],
      ["Wholesale (incl. GST)", wholesale ? money(wholesale.price) : "—"],
      ["MRP", retail.mrp != null ? money(retail.mrp) : "—"],
      ["Discount", Number(retail.discount_percent) > 0 ? Number(retail.discount_percent) + "%" : "None"],
      ["Stock", quantity(product.closing_stock) + " " + (product.unit || ""), inStock(product) ? "is-in" : "is-out"],
      ["Availability", product.availability || (inStock(product) ? "In Stock" : "Out of Stock"),
        inStock(product) ? "is-in" : "is-out"],
      ["GST", product.gst != null ? quantity(product.gst) + "%" : "—"],
      ["HSN / SAC", product.hsn || "—"],
      ["Part number", product.barcode || "—"],
      ["Alias", product.alias || "—"]
    ];
    var list = $("detail-facts");
    list.textContent = "";
    facts.forEach(function (fact) {
      var row = document.createElement("div");
      row.className = "cat-fact";
      var dt = document.createElement("dt");
      dt.textContent = fact[0];
      var dd = document.createElement("dd");
      dd.textContent = fact[1];
      if (fact[2]) dd.className = fact[2];
      row.appendChild(dt);
      row.appendChild(dd);
      list.appendChild(row);
    });

    var desc = $("detail-desc");
    desc.hidden = !product.description;
    setText(desc, product.description || "");

    show(el.detail);
    el.detailClose.focus({ preventScroll: true });
  }

  function closeDetail() { hide(el.detail); }

  /* ---------- 7. Filters drawer (phones) --------------------------------- */

  function openFilters() {
    el.filters.classList.add("is-open");
    show(el.filtersBackdrop);
  }

  function closeFilters() {
    el.filters.classList.remove("is-open");
    hide(el.filtersBackdrop);
  }

  /* ---------- 8. Boot ----------------------------------------------------- */

  function bind() {
    var searchSoon = debounce(function () {
      state.filters.q = el.search.value.trim();
      reload();
    }, SEARCH_DEBOUNCE_MS);

    el.search.addEventListener("input", function () {
      el.searchClear.hidden = !el.search.value;
      searchSoon();
    });

    el.searchForm.addEventListener("submit", function (event) {
      event.preventDefault();
      state.filters.q = el.search.value.trim();
      reload();
      // On a phone the keyboard has done its job; let the list be seen.
      if (touchScreen) el.search.blur();
    });

    el.searchClear.addEventListener("click", function () {
      el.search.value = "";
      el.searchClear.hidden = true;
      state.filters.q = "";
      reload();
      if (!touchScreen) el.search.focus();
    });

    el.sort.addEventListener("change", function () { pick("sort", el.sort.value); });

    Array.prototype.forEach.call(el.segments, function (segment) {
      segment.addEventListener("click", function () {
        pick("stock", segment.getAttribute("data-stock"));
      });
    });

    var priceSoon = debounce(function () {
      state.filters.min_price = el.minPrice.value.trim();
      state.filters.max_price = el.maxPrice.value.trim();
      reload();
    }, PRICE_DEBOUNCE_MS);
    el.minPrice.addEventListener("input", priceSoon);
    el.maxPrice.addEventListener("input", priceSoon);

    el.discountOnly.addEventListener("change", function () {
      pick("discount", el.discountOnly.checked);
    });

    el.filtersClear.addEventListener("click", clearAll);
    el.emptyClear.addEventListener("click", clearAll);
    el.filtersOpen.addEventListener("click", openFilters);
    el.filtersClose.addEventListener("click", closeFilters);
    el.filtersBackdrop.addEventListener("click", closeFilters);

    // One listener for every card, present and future.
    el.grid.addEventListener("click", function (event) {
      var card = event.target.closest(".cat-card");
      if (card) openDetail(state.items[Number(card.getAttribute("data-index"))]);
    });
    el.grid.addEventListener("keydown", function (event) {
      if (event.key !== "Enter" && event.key !== " ") return;
      var card = event.target.closest(".cat-card");
      if (!card) return;
      event.preventDefault();
      openDetail(state.items[Number(card.getAttribute("data-index"))]);
    });

    el.detailClose.addEventListener("click", closeDetail);
    el.detail.addEventListener("click", function (event) {
      if (event.target === el.detail) closeDetail();
    });

    document.addEventListener("keydown", function (event) {
      if (event.key !== "Escape") return;
      if (!el.detail.hidden) closeDetail();
      else if (el.filters.classList.contains("is-open")) closeFilters();
    });

    // Load the next page as the list nears its end.
    if ("IntersectionObserver" in window) {
      new IntersectionObserver(function (entries) {
        if (entries[0].isIntersecting) loadNextPage(false);
      }, { root: el.scroll, rootMargin: "600px 0px" }).observe(el.more);
    } else {
      el.scroll.addEventListener("scroll", function () {
        if (el.scroll.scrollTop + el.scroll.clientHeight > el.scroll.scrollHeight - 600) {
          loadNextPage(false);
        }
      });
    }

    // Back to the price checker on the same station.
    if (el.backToScan && config.station) {
      el.backToScan.href = "/?station=" + encodeURIComponent(config.station);
    }
  }

  function init() {
    readFiltersFromUrl();
    syncControls();
    bind();
    reload();
    // Ready to type on a computer; on a phone, only when tapped.
    if (!touchScreen) el.search.focus({ preventScroll: true });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
