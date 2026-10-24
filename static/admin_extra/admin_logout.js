/* ==========================================================================
   Admin: a visible "Log out" button in the top bar.
   --------------------------------------------------------------------------
   Jazzmin only offers logout inside the username dropdown, which people do
   not find. This adds a plainly visible button beside it.

   Why JavaScript rather than a template override: Jazzmin renders its navbar
   inline in admin/base.html with no block around it, and a local
   admin/base_site.html cannot extend the file of the same name without
   recursing. Injecting one nav item is far less brittle than vendoring a
   whole upstream template that would then drift on every Jazzmin upgrade.

   Why not a topmenu_links entry: those render as plain <a href> GETs, and
   Django 5 removed GET logout — it answers 405. Logging out must be a POST,
   so this reuses Jazzmin's own #logout-form, CSRF token and all.
   ========================================================================== */

(function () {
  "use strict";

  function addLogoutButton() {
    // Jazzmin's own logout form, already carrying a valid CSRF token.
    var form = document.getElementById("logout-form");
    if (!form) return;                         // logged out, or markup changed

    // The right-hand side of the navbar.
    var navbar = document.querySelector(".navbar-nav.ms-auto");
    if (!navbar) return;

    if (document.getElementById("jazzy-visible-logout")) return;   // idempotent

    var item = document.createElement("li");
    item.className = "nav-item";
    item.id = "jazzy-visible-logout";

    var link = document.createElement("a");
    link.className = "nav-link";
    link.href = "#";
    link.title = "Log out";
    link.setAttribute("role", "button");

    var icon = document.createElement("i");
    icon.className = "fas fa-sign-out-alt";
    icon.style.marginRight = "6px";

    link.appendChild(icon);
    link.appendChild(document.createTextNode("Log out"));

    link.addEventListener("click", function (event) {
      event.preventDefault();
      // requestSubmit() runs validation and submit handlers; submit() does
      // not. Fall back for older engines.
      if (typeof form.requestSubmit === "function") form.requestSubmit();
      else form.submit();
    });

    item.appendChild(link);
    navbar.appendChild(item);
  }

  /* ------------------------------------------------------------------------
     Company logo in the admin brand slot.

     Jazzmin's own `site_logo` takes a *static* path, but the logo here is
     uploaded through the admin and lives in media — so it cannot be wired up
     in settings.py. Fetching it means one upload brands both the kiosk and
     the admin instead of the logo having to be added twice.

     Fails silently: a missing logo, a failed request or changed markup just
     leaves Jazzmin's default branding alone.
     ------------------------------------------------------------------------ */

  function applyBrandLogo() {
    var brand = document.querySelector(".brand-link, .navbar-brand");
    if (!brand) return;

    fetch("/api/settings/", { headers: { Accept: "application/json" } })
      .then(function (response) {
        return response.ok ? response.json() : null;
      })
      .then(function (data) {
        var logo = data && data.display && data.display.logo;
        if (!logo) return;

        var existing = brand.querySelector("img");
        if (existing) {
          existing.src = logo;
          return;
        }

        var img = document.createElement("img");
        img.src = logo;
        img.alt = "";
        img.className = "brand-image img-circle elevation-3";
        img.style.opacity = "0.9";
        img.style.maxHeight = "33px";
        img.style.width = "auto";
        brand.insertBefore(img, brand.firstChild);
      })
      .catch(function () {
        // Branding is cosmetic; never let it break the admin.
      });
  }

  /* ------------------------------------------------------------------------
     Visible username next to the account icon.

     Jazzmin only puts the username in a `title` attribute (a hover-only
     browser tooltip) and inside the dropdown once opened. Same constraint as
     the logout button above: no block to override in admin/base.html, so
     this reads the name Jazzmin already put in `title` and prints it next
     to the icon instead of vendoring the navbar template.
     ------------------------------------------------------------------------ */

  function addVisibleUsername() {
    var toggle = document.querySelector('a.nav-link.btn[data-bs-toggle="dropdown"][title]');
    if (!toggle) return;
    if (toggle.querySelector(".jazzy-username")) return;   // idempotent

    var name = toggle.getAttribute("title");
    if (!name) return;

    var span = document.createElement("span");
    span.className = "jazzy-username";
    span.textContent = name;
    toggle.appendChild(span);
  }

  function init() {
    addLogoutButton();
    addVisibleUsername();
    applyBrandLogo();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
