/* Per-leg "Check Fares" — fetches a real fare for one leg on demand.
 * Renders inside the shared boarding-pass card (Explore + Saved; shared
 * trip pages are read-only and never render the button).
 * No popups: busy state on the button, inline error on failure. */
(function () {
  "use strict";

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

  document.addEventListener("click", function (e) {
    var btn = e.target.closest(".btn-check-fares");
    if (!btn || btn.disabled) return;
    var slot = btn.closest(".cf-slot");
    if (!slot) return;
    var card = btn.closest(".boarding-pass");
    var err = slot.querySelector(".cf-error");
    var d = slot.dataset;

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
      .catch(function (ex) {
        btn.disabled = false;
        btn.classList.remove("is-busy");
        btn.textContent = "Check Fares";
        if (err) {
          err.hidden = false;
          err.textContent = (ex && ex.message) || "Fare check failed — try again.";
        }
      });
  });
})();
