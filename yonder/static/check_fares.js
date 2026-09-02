/* Per-leg "Check Fares" — fetches a real fare for one leg on demand.
 * Renders inside the shared boarding-pass card (Explore + Saved; shared
 * trip pages are read-only and never render the button).
 * No popups: busy state on the button, inline error on failure.
 *
 * Promoted state ("Aviasales ↗" + affiliate URL) is persisted to
 * sessionStorage so it survives card re-renders, sort/filter, and
 * Saved-list refreshes within the same browser session.
 *
 * Estimate pill: on load, fetches /api/fare-estimate and injects a
 * ✈ ~$680–$1,240 pill into .pb-fast-facts when cached data exists. */
(function () {
  "use strict";

  var SS_PREFIX = "cf:";

  function legKey(d) {
    var k = (d.cfOrigin || "") + "-" + (d.cfDest || "") + "-" + (d.cfDepart || "").replace(/-/g, "");
    if (d.cfReturn) k += "-" + (d.cfReturn || "").replace(/-/g, "");
    return SS_PREFIX + k;
  }

  function storeUrl(d, url) {
    try { sessionStorage.setItem(legKey(d), url); } catch (e) { /* quota / private mode */ }
  }

  function loadUrl(d) {
    try { return sessionStorage.getItem(legKey(d)) || ""; } catch (e) { return ""; }
  }

  function fmtApprox(cur, amt) {
    return "~" + (cur ? cur + " " : "") + Math.round(amt);
  }

  function updateTotals(card) {
    if (!card) return;
    // Totals stay hidden while any leg still lacks a real fare
    if (card.querySelector(".cf-slot")) return;
    var tot = card.querySelector(".cf-total");
    if (!tot) return;
    var total = parseFloat(tot.dataset.cfBase || "0") || 0;
    var cur = tot.dataset.cfCurrency || "";
    var ok = true;
    card.querySelectorAll(".cf-fare").forEach(function (f) {
      var p = parseFloat(f.dataset.cfPrice);
      if (isNaN(p)) { ok = false; return; }
      total += p;
      cur = f.dataset.cfCurrency || cur;
    });
    if (!ok || total <= 0) return;
    tot.textContent = fmtApprox(cur, total);
    tot.style.fontSize = "";
  }

  /** Build an Aviasales search URL from cf-slot dataset fields.
   *  Uses window.AVIASALES_MARKER when available (set by Aviasales task).
   *  Falls back to sub_id=YonderFlights only when marker is missing. */
  function buildSearchUrl(d) {
    var from   = (d.cfOrigin   || "").toUpperCase();
    var to     = (d.cfDest     || "").toUpperCase();
    var dep    = d.cfDepart    || "";   // YYYY-MM-DD
    var ret    = d.cfReturn    || "";
    var adults = parseInt(d.cfAdults || "1", 10) || 1;
    var cur    = (d.cfCurrency || "USD").toUpperCase();

    // Parse depart DDMM
    var depParts = dep.split("-");
    if (depParts.length < 3 || !from || !to) {
      // Fallback: Aviasales homepage
      var qs = _aviasalesQs(cur);
      return "https://www.aviasales.com/?" + qs + (from ? "&params=" + encodeURIComponent(from) + "1" : "");
    }
    var day   = depParts[2];
    var month = depParts[1];
    var ddmm  = day + month;

    var path;
    if (ret) {
      var retParts = ret.split("-");
      var rday  = retParts[2] || "";
      var rmonth = retParts[1] || "";
      var rddmm = rday + rmonth;
      path = from + ddmm + to + rddmm + adults;
    } else {
      path = from + ddmm + to + adults;
    }

    return "https://www.aviasales.com/search/" + path + "?" + _aviasalesQs(cur);
  }

  // Compile-time constant — matches AVIASALES_MARKER in yonder/links.py.
  // window.AVIASALES_MARKER can override this at runtime (e.g. for testing).
  var _MARKER = "756039.Zza75700ced74b488c8090948-756039";

  function _aviasalesQs(currency) {
    var marker = (typeof window !== "undefined" && window.AVIASALES_MARKER) || _MARKER;
    var qs = marker ? "marker=" + encodeURIComponent(marker) + "&" : "";
    qs += "sub_id=YonderFlights";
    if (currency) {
      qs += "&currency=" + currency.toLowerCase();
    }
    return qs;
  }

  /** Promote a button to the "ORG → DST ↗" affiliate state. */
  function promoteButton(btn, url) {
    var slot = btn.closest(".cf-slot");
    var origin = slot && slot.dataset.cfOrigin;
    var dest = slot && slot.dataset.cfDest;
    btn.disabled = false;
    btn.classList.remove("is-busy");
    btn.textContent = (origin && dest ? "Book " + origin + " \u2192 " + dest : "Aviasales Flights") + " \u2197";
    btn.dataset.cfSearchUrl = url;
  }

  /**
   * Scan all .cf-slot elements and restore promoted state for any leg
   * whose key is already recorded in sessionStorage.  Call on page load
   * and after any code that injects new boarding-pass cards into the DOM.
   */
  function restorePromotedSlots(root) {
    root = root || document;
    root.querySelectorAll(".cf-slot").forEach(function (slot) {
      var btn = slot.querySelector(".btn-check-fares");
      if (!btn || btn.dataset.cfSearchUrl) return;   // already promoted
      var url = loadUrl(slot.dataset);
      if (url) promoteButton(btn, url);
    });
  }

  /** Inject a ✈ ~$680–$1,240 estimate pill into .pb-fast-facts for a slot. */
  function injectEstimatePill(slot, label, stale) {
    // Walk up to the boarding-pass card and find the fast-facts bar nearest this slot
    var card = slot.closest(".boarding-pass");
    if (!card) return;
    var factsEl = card.querySelector(".pb-fast-facts");
    if (!factsEl) return;
    // Avoid duplicate pills
    if (factsEl.querySelector(".pb-estimate")) return;
    var span = document.createElement("span");
    span.className = "pb-fact pb-estimate";
    span.textContent = "\u2708 " + label + (stale ? " (avg)" : "");
    if (stale) { span.style.opacity = "0.6"; }
    // Prepend so it appears before activity links
    factsEl.insertBefore(span, factsEl.firstChild);
  }

  /** Fetch /api/fare-estimate and inject pill when data exists. */
  function fetchAndShowEstimate(slot) {
    var d = slot.dataset;
    var origin = d.cfOrigin || "";
    var dest   = d.cfDest   || "";
    var cur    = d.cfCurrency || "USD";
    if (!origin || !dest) return;
    var url = "/api/fare-estimate?origin=" + encodeURIComponent(origin)
            + "&destination=" + encodeURIComponent(dest)
            + "&currency=" + encodeURIComponent(cur);
    fetch(url)
      .then(function (r) { return r.json(); })
      .then(function (j) {
        if (j && j.ok && j.label) {
          injectEstimatePill(slot, j.label, !!j.stale);
        }
      })
      .catch(function () { /* no data — pill simply absent */ });
  }

  document.addEventListener("DOMContentLoaded", function () {
    restorePromotedSlots();
    // Fetch estimates for all visible cf-slots
    document.querySelectorAll(".cf-slot").forEach(function (slot) {
      fetchAndShowEstimate(slot);
    });
  });

  // Also expose for callers that inject cards dynamically
  window.YonderCheckFares = {
    restorePromotedSlots: restorePromotedSlots,
    fetchAndShowEstimate: fetchAndShowEstimate,
  };

  document.addEventListener("click", function (e) {
    var btn = e.target.closest(".btn-check-fares");
    if (!btn || btn.disabled) return;
    var slot = btn.closest(".cf-slot");
    if (!slot) return;
    var card = btn.closest(".boarding-pass");
    var err = slot.querySelector(".cf-error");
    var d = slot.dataset;

    if (btn.dataset.cfSearchUrl) {
      window.open(btn.dataset.cfSearchUrl, "_blank", "noopener");
      return;
    }

    btn.disabled = true;
    btn.classList.add("is-busy");
    btn.textContent = "Checking\u2026";
    if (err) { err.hidden = true; err.textContent = ""; }

    fetch("/api/leg-fare", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        origin: d.cfOrigin,
        destination: d.cfDest,
        depart: d.cfDepart,
        return_date: d.cfReturn || null,
        adults: parseInt(d.cfAdults || "1", 10) || 1,
        currency: d.cfCurrency || "",
      }),
    })
      .then(function (r) {
        return r.json().then(function (j) { return { ok: r.ok, j: j }; });
      })
      .then(function (res) {
        if (!res.ok || !res.j || !res.j.ok) {
          throw new Error((res.j && res.j.error) || "No fares found — try again later.");
        }
        var j = res.j;
        var priceLabel = j.display_price || fmtApprox(j.currency, j.price);

        // Update (or create) the estimate pill with the fresh price
        var card2 = slot.closest(".boarding-pass");
        if (card2) {
          var factsEl = card2.querySelector(".pb-fast-facts");
          if (factsEl) {
            var existing = factsEl.querySelector(".pb-estimate");
            if (existing) {
              existing.textContent = "\u2708 " + priceLabel;
              existing.style.opacity = "";
            } else {
              var span2 = document.createElement("span");
              span2.className = "pb-fact pb-estimate";
              span2.textContent = "\u2708 " + priceLabel;
              factsEl.insertBefore(span2, factsEl.firstChild);
            }
          }
        }

        var fareSpan = document.createElement("span");
        fareSpan.className = "cf-fare";
        fareSpan.textContent = priceLabel;
        fareSpan.dataset.cfPrice = String(j.price);
        fareSpan.dataset.cfCurrency = j.currency || "";
        slot.replaceWith(fareSpan);
        updateTotals(card);

        // Change the action button(s) in bp-actions to Aviasales
        _upgradeActionsToAviasales(card, d);
      })
      .catch(function () {
        var url = buildSearchUrl(d);
        promoteButton(btn, url);
        // Persist so re-renders within this session keep the promoted state
        storeUrl(d, url);
      });
  });

  /** After a successful live fare fetch, replace Google Flights links in
   *  .bp-actions with a single Aviasales button for this leg. */
  function _upgradeActionsToAviasales(card, d) {
    if (!card) return;
    var actions = card.querySelector(".bp-actions");
    if (!actions) return;
    var url = buildSearchUrl(d);
    // Check if an Aviasales button already present
    if (actions.querySelector(".btn-aviasales-result")) return;
    var a = document.createElement("a");
    a.className = "btn secondary btn-aviasales-result";
    a.href = url;
    a.target = "_blank";
    a.rel = "noopener";
    a.textContent = (d.cfOrigin && d.cfDest ? "Book " + d.cfOrigin + " \u2192 " + d.cfDest : "Aviasales Flights") + " \u2197";
    actions.appendChild(a);
  }
})();
