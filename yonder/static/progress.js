/**
 * Full-screen wait UX: progress bar + rotating fun messages.
 * Templates can inject {country} from the user's visited / avoid lists.
 */
(function (global) {
  "use strict";

  var ADVENTURE_LINES = [
    "Grok is daydreaming about Zurich…",
    "Checking if a Swiss detour is cheaper than flying straight…",
    "Asking Swiss cows for fare tips…",
    "Pricing leg 1 of your detour…",
    "Pricing leg 2 — the way home (ish)…",
    "Comparing direct vs 'accidentally vacation'…",
    "Sniffing out Reykjavik stopovers…",
    "Istanbul called — wants a cameo…",
    "Lisbon is offering pastel de nata and a layover…",
    "Running the numbers so you don't have to…",
    "Negotiating with the algorithm (it wants snacks)…",
    "Looking for chaos that still makes financial sense…",
    "Folding maps that only exist in JSON…",
    "Almost there — polishing adventure scores…",
    "Double-checking nobody invented a fake fare…",
    "Your future self is already packing a day bag…",
    "Calculating adventure premium vs 'just get there'…",
    "Whispering sweet nothings to the flight APIs…",
    "Still hunting — good adventures are slow-cooked…",
    "If this takes a minute, blame geography, not you…",
  ];

  var SEARCH_LINES = [
    "Grok is parsing your wanderlust…",
    "Translating vibes into IATA codes…",
    "Poking Amadeus / SerpAPI / friends…",
    "Sorting fares from cheapest to 'nah'…",
    "Asking which airline actually shows up…",
    "Almost done — ranking the less terrible options…",
    "Counting stops so you don't have to…",
    "Bribing the cache with cookies (the HTTP kind)…",
    "Holding for live prices…",
    "Grok is writing a spicy take on row #3…",
  ];

  // {country} is swapped for a name from the user's lists (Settings map)
  var AVOID_TEMPLATES = [
    "Hard pass on {country} — you put it on the avoid list…",
    "Steering clear of {country} stopovers (as ordered)…",
    "Not nominating {country} for detour duty…",
    "Filtering out {country} so Adventure stays chill…",
    "Grok crossed {country} off the whiteboard…",
    "Skipping {country} — red on your travel map…",
    "Respecting the ban: no {country} cameos…",
    "Avoid list says no to {country}; APIs heard that…",
  ];

  var VISITED_TEMPLATES = [
    "You've already done {country} — hunting fresher ground…",
    "Been-there energy: {country} is green on your map…",
    "Not replaying {country} unless the fare is ridiculous…",
    "Passport stamp from {country} noted — next adventure…",
    "Warm fuzzies for {country}, but today we roam new places…",
    "Checking if anything beats the time you spent in {country}…",
    "Green-map tip: {country} is visited, not forbidden…",
    "Nostalgia for {country} later — fares first…",
  ];

  var AVOID_SEARCH_TEMPLATES = [
    "Not routing through your avoid list ({country})…",
    "Still scanning — {country} stays off the wishlist…",
  ];

  var VISITED_SEARCH_TEMPLATES = [
    "Maybe a return to {country}? Sorting the options…",
    "Fares to places like {country} (or elsewhere) incoming…",
  ];

  var STAGES_ADV = [
    { at: 0, label: "Warming up engines" },
    { at: 8, label: "Inventing detour cities" },
    { at: 22, label: "Pricing the direct baseline" },
    { at: 38, label: "Pricing stopover legs" },
    { at: 72, label: "Scoring adventures" },
    { at: 88, label: "Writing field notes" },
    { at: 96, label: "Landing…" },
  ];

  var STAGES_SEARCH = [
    { at: 0, label: "Reading your request" },
    { at: 15, label: "Calling flight providers" },
    { at: 55, label: "Merging results" },
    { at: 78, label: "Writing field notes" },
    { at: 94, label: "Landing…" },
  ];

  var STORY_LINES = [
    "Still here? Cooking field notes…",
    "Culture + food + vibe cards loading…",
    "Skip takes fares only — wait for the story…",
    "Writing place notes worth reading…",
  ];

  function travelLists() {
    var t = global.FS_TRAVEL || {};
    return {
      avoid: Array.isArray(t.avoid_names) ? t.avoid_names.filter(Boolean) : [],
      visited: Array.isArray(t.visited_names) ? t.visited_names.filter(Boolean) : [],
    };
  }

  function fillTemplate(tpl, country) {
    return String(tpl).split("{country}").join(country);
  }

  /** Expand templates once per country (capped) for variety. */
  function expandTemplates(templates, names, maxLines) {
    maxLines = maxLines || 12;
    if (!names || !names.length || !templates || !templates.length) return [];
    var out = [];
    var i = 0;
    while (out.length < maxLines && i < names.length * templates.length) {
      var country = names[i % names.length];
      var tpl = templates[i % templates.length];
      out.push(fillTemplate(tpl, country));
      i++;
      // Prefer one template pass per country first, then rotate
      if (i >= names.length && out.length >= Math.min(templates.length, names.length * 2)) {
        break;
      }
    }
    // Dedupe exact strings
    var seen = {};
    return out.filter(function (line) {
      if (seen[line]) return false;
      seen[line] = true;
      return true;
    });
  }

  function shuffle(arr) {
    var a = arr.slice();
    for (var i = a.length - 1; i > 0; i--) {
      var j = Math.floor(Math.random() * (i + 1));
      var tmp = a[i];
      a[i] = a[j];
      a[j] = tmp;
    }
    return a;
  }

  /**
   * Interleave personal (visited/avoid) lines with defaults so the wait feels yours.
   */
  function buildLines(mode) {
    var lists = travelLists();
    var base = mode === "adventure" ? ADVENTURE_LINES.slice() : SEARCH_LINES.slice();
    var personal = [];

    if (mode === "adventure") {
      personal = personal.concat(expandTemplates(AVOID_TEMPLATES, lists.avoid, 10));
      personal = personal.concat(expandTemplates(VISITED_TEMPLATES, lists.visited, 10));
    } else {
      personal = personal.concat(expandTemplates(AVOID_SEARCH_TEMPLATES, lists.avoid, 4));
      personal = personal.concat(expandTemplates(VISITED_SEARCH_TEMPLATES, lists.visited, 4));
    }

    if (!personal.length) return base;

    personal = shuffle(personal);
    base = shuffle(base);

    // ~40% personal messages woven in
    var mixed = [];
    var pi = 0;
    var bi = 0;
    while (mixed.length < base.length + personal.length) {
      var wantPersonal =
        personal.length &&
        pi < personal.length &&
        (bi >= base.length || Math.random() < 0.42);
      if (wantPersonal) {
        mixed.push(personal[pi++]);
      } else if (bi < base.length) {
        mixed.push(base[bi++]);
      } else if (pi < personal.length) {
        mixed.push(personal[pi++]);
      } else {
        break;
      }
    }
    return mixed.length ? mixed : base;
  }

  function ensureOverlay() {
    var el = document.getElementById("fs-progress-overlay");
    if (el) return el;
    el = document.createElement("div");
    el.id = "fs-progress-overlay";
    el.setAttribute("aria-live", "polite");
    el.innerHTML =
      '<div class="fs-progress-card">' +
      '  <div class="fs-progress-emoji" id="fs-progress-emoji">✈️</div>' +
      '  <div class="fs-progress-title" id="fs-progress-title">Working…</div>' +
      '  <div class="fs-progress-stage" id="fs-progress-stage"></div>' +
      '  <div class="fs-progress-track"><div class="fs-progress-fill" id="fs-progress-fill"></div></div>' +
      '  <div class="fs-progress-meta">' +
      '    <span id="fs-progress-pct">0%</span>' +
      '    <span id="fs-progress-elapsed">0s</span>' +
      "  </div>" +
      '  <div class="fs-progress-msg" id="fs-progress-msg"></div>' +
      '  <div class="fs-progress-hint" id="fs-progress-hint">Aiming for a quick search — APIs may take longer.</div>' +
      '  <button type="button" class="fs-progress-skip" id="fs-progress-skip" hidden>Skip</button>' +
      "</div>";
    document.body.appendChild(el);
    return el;
  }

  function easeToward(elapsedMs, expectedMs) {
    // Asymptotic curve: approaches ~92% at expectedMs, creeps toward 97% after
    var t = elapsedMs / Math.max(expectedMs, 1000);
    if (t < 1) {
      var u = 1 - Math.pow(1 - t, 3);
      return Math.min(0.92, u * 0.92);
    }
    var extra = elapsedMs - expectedMs;
    return Math.min(0.97, 0.92 + (1 - Math.exp(-extra / 45000)) * 0.05);
  }

  function stageFor(pct, stages) {
    var label = stages[0].label;
    for (var i = 0; i < stages.length; i++) {
      if (pct * 100 >= stages[i].at) label = stages[i].label;
    }
    return label;
  }

  var EMOJIS = ["✈️", "🌍", "🗺️", "🧳", "🏔️", "🍜", "🚂", "🧭", "🏝️", "🎯"];

  function ProgressController(opts) {
    opts = opts || {};
    this.mode = opts.mode || "adventure"; // adventure | search
    this.expectedMs = opts.expectedMs || 30000;
    // When Skip appears (default 42s). 0 = never auto-show.
    this.skipAfterMs =
      opts.skipAfterMs != null
        ? opts.skipAfterMs
        : opts.maxMs != null
          ? opts.maxMs
          : 42000;
    this.searchId = opts.searchId || "";
    this.onSkip = typeof opts.onSkip === "function" ? opts.onSkip : null;
    this.lines = opts.lines || buildLines(this.mode);
    this.stages = opts.stages || (this.mode === "adventure" ? STAGES_ADV : STAGES_SEARCH);
    this.title = opts.title || (this.mode === "adventure" ? "Plotting adventures…" : "Scanning flights…");
    this._timers = [];
    this._start = 0;
    this._msgIdx = 0;
    this._skipped = false;
    this._skipShown = false;
    this._storyHint = false;
  }

  ProgressController.prototype.start = function () {
    var overlay = ensureOverlay();
    overlay.classList.add("show");
    document.body.classList.add("fs-progress-active");
    document.getElementById("fs-progress-title").textContent = this.title;
    document.getElementById("fs-progress-fill").style.width = "0%";
    document.getElementById("fs-progress-pct").textContent = "0%";
    document.getElementById("fs-progress-elapsed").textContent = "0s";
    document.getElementById("fs-progress-msg").textContent = this.lines[0] || "Working…";
    document.getElementById("fs-progress-stage").textContent = this.stages[0].label;
    document.getElementById("fs-progress-emoji").textContent = EMOJIS[0];
    var hint = document.getElementById("fs-progress-hint");
    if (hint) {
      var aimS = Math.round(this.expectedMs / 1000);
      var skipS = Math.round(this.skipAfterMs / 1000);
      hint.textContent =
        "Aiming for ~" +
        aimS +
        "s. After " +
        skipS +
        "s you can Skip for fares only — wait longer for field notes.";
    }
    var skipBtn = document.getElementById("fs-progress-skip");
    if (skipBtn) {
      skipBtn.hidden = true;
      skipBtn.disabled = false;
      skipBtn.textContent = "Skip";
      skipBtn.onclick = null;
    }

    this._start = Date.now();
    this._msgIdx = 0;
    this._skipped = false;
    this._skipShown = false;
    var self = this;

    this._timers.push(
      setInterval(function () {
        var elapsed = Date.now() - self._start;
        var p = easeToward(elapsed, self.expectedMs);
        var pct = Math.round(p * 100);
        document.getElementById("fs-progress-fill").style.width = pct + "%";
        document.getElementById("fs-progress-pct").textContent = pct + "%";
        document.getElementById("fs-progress-elapsed").textContent =
          Math.floor(elapsed / 1000) + "s";
        document.getElementById("fs-progress-stage").textContent = stageFor(p, self.stages);
        if (
          !self._skipShown &&
          self.skipAfterMs > 0 &&
          elapsed >= self.skipAfterMs
        ) {
          self._showSkip();
        }
        // After soft aim / skip window: nudge that field notes may be cooking
        if (
          !self._skipped &&
          self.skipAfterMs > 0 &&
          elapsed >= self.skipAfterMs &&
          !self._storyHint
        ) {
          self._storyHint = true;
          var hintEl = document.getElementById("fs-progress-hint");
          if (hintEl) {
            hintEl.textContent =
              "Skip = fares now. Stay and we’ll add culture / food / vibe field notes.";
          }
        }
      }, 200)
    );

    this._timers.push(
      setInterval(function () {
        if (!self.lines.length) return;
        self._msgIdx = (self._msgIdx + 1) % self.lines.length;
        var msgEl = document.getElementById("fs-progress-msg");
        msgEl.classList.remove("pop");
        void msgEl.offsetWidth;
        var line = self.lines[self._msgIdx];
        // After skip button is available, interleave story lines
        if (
          self._skipShown &&
          !self._skipped &&
          STORY_LINES.length &&
          self._msgIdx % 2 === 0
        ) {
          line = STORY_LINES[(self._msgIdx / 2) % STORY_LINES.length];
        }
        msgEl.textContent = line;
        msgEl.classList.add("pop");
        document.getElementById("fs-progress-emoji").textContent =
          EMOJIS[self._msgIdx % EMOJIS.length];
      }, 2800)
    );
  };

  ProgressController.prototype._showSkip = function () {
    var btn = document.getElementById("fs-progress-skip");
    if (!btn || this._skipShown) return;
    this._skipShown = true;
    btn.hidden = false;
    var self = this;
    btn.onclick = function () {
      self.skip();
    };
  };

  ProgressController.prototype.skip = function () {
    if (this._skipped) return;
    this._skipped = true;
    var btn = document.getElementById("fs-progress-skip");
    if (btn) {
      btn.disabled = true;
      btn.textContent = "Wrapping up…";
    }
    var msg = document.getElementById("fs-progress-msg");
    if (msg) msg.textContent = "Skip — finishing with what we have…";
    var stage = document.getElementById("fs-progress-stage");
    if (stage) stage.textContent = "Wrapping up";
    if (this.onSkip) {
      try {
        this.onSkip(this.searchId);
      } catch (e) {
        /* ignore */
      }
    }
    if (this.searchId) {
      try {
        fetch("/api/search-cancel", {
          method: "POST",
          headers: { "Content-Type": "application/json", Accept: "application/json" },
          credentials: "same-origin",
          body: JSON.stringify({ search_id: this.searchId }),
        }).catch(function () {});
      } catch (e) {
        /* ignore */
      }
    }
  };

  ProgressController.prototype.finish = function () {
    var fill = document.getElementById("fs-progress-fill");
    if (fill) {
      fill.style.width = "100%";
      document.getElementById("fs-progress-pct").textContent = "100%";
      document.getElementById("fs-progress-msg").textContent = "Done — unpacking results…";
      document.getElementById("fs-progress-stage").textContent = "Complete";
    }
    this._clearTimers();
  };

  ProgressController.prototype.fail = function (errMsg) {
    this._clearTimers();
    var overlay = document.getElementById("fs-progress-overlay");
    if (overlay) overlay.classList.remove("show");
    document.body.classList.remove("fs-progress-active");
    if (errMsg) alert(errMsg);
  };

  ProgressController.prototype._clearTimers = function () {
    this._timers.forEach(function (t) {
      clearInterval(t);
    });
    this._timers = [];
  };

  /**
   * Submit a form via fetch while showing progress; replace page with HTML response.
   */
  function submitWithProgress(form, options) {
    options = options || {};
    // Rebuild lines at submit time so map edits in Settings are fresh
    if (!options.lines) {
      options.lines = buildLines(options.mode || "adventure");
    }

    // Soft aim / Skip-after from Settings (travel_ctx) when not overridden
    var t = global.FS_TRAVEL || {};
    if (options.expectedMs == null && t.search_aim_seconds) {
      options.expectedMs = Math.round(Number(t.search_aim_seconds) * 1000);
    }
    if (options.skipAfterMs == null && options.maxMs == null && t.search_max_seconds) {
      options.skipAfterMs = Math.round(Number(t.search_max_seconds) * 1000);
    }
    if (options.expectedMs == null) options.expectedMs = 30000;
    if (options.skipAfterMs == null) options.skipAfterMs = 42000;

    // Per-run id so Skip can finish the job with partial results
    var searchId =
      options.searchId ||
      "s" + Date.now().toString(36) + Math.random().toString(36).slice(2, 8);
    options.searchId = searchId;

    var progress = new ProgressController(options);
    progress.start();

    var action = form.getAttribute("action") || window.location.pathname;
    var method = (form.getAttribute("method") || "POST").toUpperCase();
    var body = new FormData(form);
    body.set("search_id", searchId);

    // No client hard kill — wait as long as the server needs unless user Skips
    // (Skip signals cancel; we still wait for the HTML response with partials)
    var fetchOpts = {
      method: method,
      body: method === "GET" ? undefined : body,
      credentials: "same-origin",
      headers: { Accept: "text/html" },
    };

    var url = action;
    if (method === "GET") {
      var params = new URLSearchParams(body);
      url = action + (action.indexOf("?") >= 0 ? "&" : "?") + params.toString();
      fetchOpts.body = undefined;
    }

    return fetch(url, fetchOpts)
      .then(function (res) {
        if (!res.ok && res.status >= 500) {
          return res.text().then(function (t) {
            throw new Error("Server error " + res.status + (t ? ": " + t.slice(0, 200) : ""));
          });
        }
        // Prefer real navigation so URL/query (flash) and scripts load cleanly
        if (res.redirected && res.url) {
          progress.finish();
          setTimeout(function () {
            window.location.href = res.url;
          }, 280);
          return;
        }
        return res.text().then(function (html) {
          progress.finish();
          setTimeout(function () {
            document.open();
            document.write(html);
            document.close();
          }, 350);
        });
      })
      .catch(function (err) {
        var msg =
          "Request failed: " + (err && err.message ? err.message : String(err));
        progress.fail(msg);
        var btn = form.querySelector('[type="submit"]');
        if (btn) {
          btn.disabled = false;
        }
      });
  }

  global.YonderProgress = {
    ProgressController: ProgressController,
    submitWithProgress: submitWithProgress,
    buildLines: buildLines,
    ADVENTURE_LINES: ADVENTURE_LINES,
    SEARCH_LINES: SEARCH_LINES,
    AVOID_TEMPLATES: AVOID_TEMPLATES,
    VISITED_TEMPLATES: VISITED_TEMPLATES,
  };
})(window);
