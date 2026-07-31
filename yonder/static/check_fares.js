/* Per-leg "Check Fares" — fetches a real fare for one leg on demand.
 * Renders inside the shared boarding-pass card (Explore + Saved; shared
 * trip pages are read-only and never render the button).
 * No popups: busy state on the button, inline error on failure.
 *
 * Promoted state ("Search ↗" + Google Flights URL) is persisted to
 * sessionStorage so it survives card re-renders, sort/filter, and
 * Saved-list refreshes within the same browser session. */
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

  function buildSearchUrl(d) {
    var from   = (d.cfOrigin   || "").toUpperCase();
    var to     = (d.cfDest     || "").toUpperCase();
    var dep    = d.cfDepart    || "";   // already YYYY-MM-DD
    var ret    = d.cfReturn    || "";
    var cur    = (d.cfCurrency || "USD").toUpperCase();
    var adults = parseInt(d.cfAdults || "1", 10) || 1;

    var q = ret
      ? "Flights to " + to + " from " + from + " on " + dep + " through " + ret
      : "Flights to " + to + " from " + from + " on " + dep + " oneway";
    if (adults > 1) { q += " with " + adults + " adults"; }

    return "https://www.google.com/travel/flights?hl=en&curr="
      + encodeURIComponent(cur) + "&q=" + encodeURIComponent(q);
  }

  /** Promote a button to the "Search ↗" fallback state. */
  function promoteButton(btn, url) {
    btn.disabled = false;
    btn.classList.remove("is-busy");
    btn.textContent = "Search \u2197";
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

  document.addEventListener("DOMContentLoaded", function () {
    restorePromotedSlots();
  });

  // Also expose for callers that inject cards dynamically
  window.YonderCheckFares = { restorePromotedSlots: restorePromotedSlots };

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
        var span = document.createElement("span");
        span.className = "cf-fare";
        span.textContent = j.display_price || fmtApprox(j.currency, j.price);
        span.dataset.cfPrice = String(j.price);
        span.dataset.cfCurrency = j.currency || "";
        slot.replaceWith(span);
        updateTotals(card);
      })
      .catch(function () {
        var url = buildSearchUrl(d);
        promoteButton(btn, url);
        // Persist so re-renders within this session keep the promoted state
        storeUrl(d, url);
      });
  });
})();
