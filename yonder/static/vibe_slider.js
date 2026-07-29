/**
 * Yonder vibe — mandatory hue rainbow under the prompt (Escape + Detour).
 * Bottom field shows vibe name (not hex). Always on; always POSTed.
 */
(function (global) {
  "use strict";

  var VIBES = [
    { id: "chaos", label: "Chaos", color: "#e11d48" },
    { id: "wild", label: "Wild", color: "#f43f5e" },
    { id: "party", label: "Party", color: "#ec4899" },
    { id: "romance", label: "Romance", color: "#d946ef" },
    { id: "neon", label: "Neon", color: "#c026d3" },
    { id: "night", label: "Night", color: "#9333ea" },
    { id: "soul", label: "Soul", color: "#7c3aed" },
    { id: "art", label: "Art", color: "#6366f1" },
    { id: "culture", label: "Culture", color: "#4f46e5" },
    { id: "city", label: "City", color: "#2563eb" },
    { id: "future", label: "Future", color: "#0284c7" },
    { id: "ocean", label: "Ocean", color: "#0891b2" },
    { id: "islands", label: "Islands", color: "#0d9488" },
    { id: "beach", label: "Beach", color: "#14b8a6" },
    { id: "jungle", label: "Jungle", color: "#16a34a" },
    { id: "nature", label: "Nature", color: "#22c55e" },
    { id: "mountains", label: "Mountains", color: "#65a30d" },
    { id: "adventure", label: "Adventure", color: "#84cc16" },
    { id: "trains", label: "Trains", color: "#ca8a04" },
    { id: "food", label: "Food", color: "#eab308" },
    { id: "street", label: "Street", color: "#f59e0b" },
    { id: "desert", label: "Desert", color: "#f97316" },
    { id: "sun", label: "Sun", color: "#ea580c" },
    { id: "luxury", label: "Luxury", color: "#d97706" },
    { id: "spa", label: "Spa", color: "#b45309" },
    { id: "cozy", label: "Cozy", color: "#a16207" },
    { id: "history", label: "History", color: "#92400e" },
    { id: "snow", label: "Snow", color: "#64748b" },
    { id: "quiet", label: "Quiet", color: "#475569" },
    { id: "cheap", label: "Cheap", color: "#0f766e" },
  ];

  /**
   * Adjective / mood traits for prompt chips under the vibe slider.
   * Chips re-roll from current vibe + passport map (visited / avoid).
   */
  var VIBE_TRAITS = {
    chaos: {
      adj: "chaotic",
      chip: "Chaos",
      getaway: "chaotic few days out of town, zero plan, somewhere loud and different",
      stop: "messy party-city stopover, 2-4 days",
      escape: "last-minute chaotic hop somewhere neon and unpredictable",
    },
    wild: {
      adj: "wild",
      chip: "Wild",
      getaway: "wild outdoor few days, rugged and unplanned",
      stop: "wild nature stopover, 2-4 days",
      escape: "wild landscape trip — trails, not museums",
    },
    party: {
      adj: "party-forward",
      chip: "Party",
      getaway: "party weekend somewhere new — nightlife first",
      stop: "nightlife city stopover, 2-3 nights",
      escape: "party city for a long weekend, clubs and late dinners",
    },
    romance: {
      adj: "romantic",
      chip: "Romance",
      getaway: "romantic few days — slow dinners, pretty streets",
      stop: "romantic city stopover, 2-4 days",
      escape: "romantic city break, walkable and intimate",
    },
    neon: {
      adj: "neon",
      chip: "Neon",
      getaway: "neon nights somewhere electric and new",
      stop: "neon megacity stopover, 2-4 days",
      escape: "neon city lights trip — night markets and skyline",
    },
    night: {
      adj: "nocturnal",
      chip: "Night",
      getaway: "night-owl city for a few days — bars, late food",
      stop: "after-dark city stopover",
      escape: "city that comes alive at night",
    },
    soul: {
      adj: "soulful",
      chip: "Soul",
      getaway: "soulful slow trip — music, people, no rush",
      stop: "soulful music-city stopover",
      escape: "soulful city with live music and character",
    },
    art: {
      adj: "artsy",
      chip: "Art",
      getaway: "artsy few days — galleries, design, creatives",
      stop: "art-city stopover, museums and neighborhoods",
      escape: "art capital long weekend",
    },
    culture: {
      adj: "cultural",
      chip: "Culture",
      getaway: "culture-heavy few days — museums, temples, local life",
      stop: "deep culture stopover, 3-5 days",
      escape: "cultural capital with history and food",
    },
    city: {
      adj: "urban",
      chip: "City",
      getaway: "big-city few days — walkable core, transit, skyline",
      stop: "major city stopover, 2-4 days",
      escape: "iconic city long weekend",
    },
    future: {
      adj: "futuristic",
      chip: "Future",
      getaway: "futuristic tech-city few days",
      stop: "ultra-modern city stopover",
      escape: "high-tech skyline and transit trip",
    },
    ocean: {
      adj: "oceanic",
      chip: "Ocean",
      getaway: "on the water for a few days — ocean air, not inland",
      stop: "coastal ocean stopover",
      escape: "oceanfront city or harbor town",
    },
    islands: {
      adj: "island",
      chip: "Islands",
      getaway: "island few days — ferries, salt air, slow pace",
      stop: "island stopover if the routing works",
      escape: "island hop or single-island break",
    },
    beach: {
      adj: "beachy",
      chip: "Beach",
      getaway: "beach few days — sand, warm, low hassle",
      stop: "beach-town stopover",
      escape: "beach destination long weekend",
    },
    jungle: {
      adj: "jungly",
      chip: "Jungle",
      getaway: "jungle / rainforest few days, green and humid",
      stop: "jungle-edge city stopover then out",
      escape: "tropical green destination",
    },
    nature: {
      adj: "nature-first",
      chip: "Nature",
      getaway: "nature-first few days — parks, trails, fresh air",
      stop: "nature stopover outside the tourist core",
      escape: "outdoors trip, not a mega-city",
    },
    mountains: {
      adj: "mountain",
      chip: "Mountains",
      getaway: "mountain few days — altitude, views, cool air",
      stop: "mountain-town stopover",
      escape: "mountain destination, hike-friendly",
    },
    adventure: {
      adj: "adventurous",
      chip: "Adventure",
      getaway: "adventurous few days — active, outdoors, new",
      stop: "adventure-city stopover with day trips",
      escape: "adventure trip, not just cafes",
    },
    trains: {
      adj: "rail-forward",
      chip: "Trains",
      getaway: "train-forward few days — stations, rail day trips",
      stop: "great rail hub stopover",
      escape: "trip built around scenic trains",
    },
    food: {
      adj: "food-obsessed",
      chip: "Food",
      getaway: "food-obsessed few days — markets, street eats, one splurge meal",
      stop: "food-city stopover, 2-4 days",
      escape: "destination famous for food",
    },
    street: {
      adj: "street-level",
      chip: "Street",
      getaway: "street-level few days — markets, alleys, local snacks",
      stop: "street-food city stopover",
      escape: "walkable street-culture city",
    },
    desert: {
      adj: "desert",
      chip: "Desert",
      getaway: "desert few days — dry heat, big sky",
      stop: "desert-edge city stopover",
      escape: "desert or oasis destination",
    },
    sun: {
      adj: "sunny",
      chip: "Sun",
      getaway: "sunny warm few days — heat and light",
      stop: "sunny stopover, not grey weather",
      escape: "guaranteed sun destination",
    },
    luxury: {
      adj: "luxe",
      chip: "Luxury",
      getaway: "luxe few days — nice hotel, great food, no roughing it",
      stop: "polished luxury-city stopover",
      escape: "upscale city break",
    },
    spa: {
      adj: "spa-slow",
      chip: "Spa",
      getaway: "spa-slow few days — rest, baths, quiet",
      stop: "wellness stopover",
      escape: "spa or hot-spring destination",
    },
    cozy: {
      adj: "cozy",
      chip: "Cozy",
      getaway: "cozy few days — cafes, rain-friendly, soft pace",
      stop: "cozy small-city stopover",
      escape: "cozy walkable town, not a mega-metropolis",
    },
    history: {
      adj: "historic",
      chip: "History",
      getaway: "history-heavy few days — old towns, ruins, museums",
      stop: "historic city stopover",
      escape: "history capital long weekend",
    },
    snow: {
      adj: "snowy",
      chip: "Snow",
      getaway: "snowy few days — cold air, winter light",
      stop: "winter-city stopover",
      escape: "snow destination, cold weather ok",
    },
    quiet: {
      adj: "quiet",
      chip: "Quiet",
      getaway: "quiet few days — low noise, not party",
      stop: "calm low-key stopover",
      escape: "quiet town or calm city break",
    },
    cheap: {
      adj: "budget",
      chip: "Cheap",
      getaway: "cheap few days out of town — low fares, low daily spend",
      stop: "cheap stopover city, good value",
      escape: "cheapest solid city break I can find",
    },
  };

  function clamp(n, a, b) {
    return Math.max(a, Math.min(b, n));
  }

  function hexToRgb(hex) {
    var h = String(hex || "").replace("#", "");
    if (h.length === 3) h = h[0] + h[0] + h[1] + h[1] + h[2] + h[2];
    var n = parseInt(h, 16);
    if (isNaN(n)) return { r: 124, g: 58, b: 237 };
    return { r: (n >> 16) & 255, g: (n >> 8) & 255, b: n & 255 };
  }

  function rgbToHsv(r, g, b) {
    r /= 255;
    g /= 255;
    b /= 255;
    var max = Math.max(r, g, b);
    var min = Math.min(r, g, b);
    var d = max - min;
    var h = 0;
    var s = max === 0 ? 0 : d / max;
    var v = max;
    if (d !== 0) {
      if (max === r) h = ((g - b) / d + (g < b ? 6 : 0)) / 6;
      else if (max === g) h = ((b - r) / d + 2) / 6;
      else h = ((r - g) / d + 4) / 6;
    }
    return { h: h, s: s, v: v };
  }

  VIBES.forEach(function (v) {
    v._rgb = hexToRgb(v.color);
    v._hsv = rgbToHsv(v._rgb.r, v._rgb.g, v._rgb.b);
  });

  function nearestByHue(h) {
    var best = VIBES[0];
    var bestD = Infinity;
    for (var i = 0; i < VIBES.length; i++) {
      var vh = VIBES[i]._hsv.h;
      var d = Math.abs(h - vh);
      if (d > 0.5) d = 1 - d;
      d += (1 - VIBES[i]._hsv.s) * 0.02;
      if (d < bestD) {
        bestD = d;
        best = VIBES[i];
      }
    }
    return best;
  }

  function indexOfId(id) {
    if (!id) return -1;
    var needle = String(id).toLowerCase().trim();
    for (var i = 0; i < VIBES.length; i++) {
      if (VIBES[i].id === needle || VIBES[i].label.toLowerCase() === needle) {
        return i;
      }
    }
    return -1;
  }

  function _listJoin(names, max) {
    max = max == null ? 3 : max;
    var arr = (names || []).filter(Boolean).slice(0, max);
    if (!arr.length) return "";
    if (arr.length === 1) return arr[0];
    if (arr.length === 2) return arr[0] + " or " + arr[1];
    return arr.slice(0, -1).join(", ") + ", or " + arr[arr.length - 1];
  }

  function _resolveHomeFromTravel(t, visited, extraHome) {
    // 1) Explicit override from caller
    if (extraHome && String(extraHome).length === 3) {
      return String(extraHome).toUpperCase();
    }
    // 2) Settings HOME_IATA lock
    var locked = String((t && t.home_iata_setting) || "")
      .trim()
      .toUpperCase();
    if (locked.length === 3 && /^[A-Z]{3}$/.test(locked)) return locked;
    // 3) Server-resolved home (resolve_home_iata) — single source of truth for pills
    //    Prefer this over re-deriving from map so chips never say From JFK when
    //    the server knows YVR (first stamp CA). Map sync updates home_iata live.
    var serverHome = String((t && t.home_iata) || "")
      .trim()
      .toUpperCase();
    if (serverHome.length === 3 && /^[A-Z]{3}$/.test(serverHome)) return serverHome;
    // 4) Derive from stamp order (first visited with primary airport)
    var prim = (t && t.primary_iata) || {};
    var list = visited || [];
    for (var i = 0; i < list.length; i++) {
      if (prim[list[i]]) return String(prim[list[i]]).toUpperCase();
    }
    // 5) Currency / USA fallback
    var fb = String((t && t.home_iata_fallback) || "")
      .trim()
      .toUpperCase();
    return fb.length === 3 ? fb : "";
  }

  function _mapCtx(extra) {
    var t = (typeof global !== "undefined" && global.FS_TRAVEL) || {};
    var visited_names =
      (extra && extra.visited_names) || t.visited_names || [];
    var avoid_names = (extra && extra.avoid_names) || t.avoid_names || [];
    var visited = (extra && extra.visited) || t.visited || [];
    var avoid = (extra && extra.avoid) || t.avoid || [];
    var home = _resolveHomeFromTravel(
      t,
      visited,
      extra && extra.home_iata
    );
    return {
      visited: visited,
      avoid: avoid,
      visited_names: visited_names,
      avoid_names: avoid_names,
      home_iata: home || "home",
      home_country: (extra && extra.home_country) || (visited && visited[0]) || "",
    };
  }

  function _notBeenPhrase(ctx) {
    var names = ctx.visited_names || [];
    if (!names.length) return "somewhere I haven't been";
    if (names.length <= 3) {
      return "not " + _listJoin(names) + " — somewhere new";
    }
    return "not somewhere I've already stamped on the map";
  }

  function _avoidPhrase(ctx) {
    var names = ctx.avoid_names || [];
    if (!names.length) return "";
    if (names.length <= 2) return " skip " + _listJoin(names);
    return " skip my avoid list";
  }

  function _monthWindow() {
    var d = new Date();
    d.setDate(d.getDate() + 45);
    var months = [
      "January",
      "February",
      "March",
      "April",
      "May",
      "June",
      "July",
      "August",
      "September",
      "October",
      "November",
      "December",
    ];
    return months[d.getMonth()] + " " + d.getFullYear();
  }

  /**
   * Dataset-completion pills: each chip fills missing prompt slots so Grok/APIs
   * get a fuller, more accurate request (vibe + map + shape + timing + constraints).
   *
   * ranking (optional, from ★ Saves) only re-orders which completions to show —
   * it does not replace chips with "from your Saves" destinations.
   *
   * Returns [{ id, label, q, vibe, source, pattern, slots, seed_iatas, score }].
   */
  function buildSuggestions(vibeId, mapExtra, ranking) {
    var id = String(vibeId || "").toLowerCase().trim() || "adventure";
    var trait = VIBE_TRAITS[id] || VIBE_TRAITS.adventure;
    var vibe = null;
    for (var i = 0; i < VIBES.length; i++) {
      if (VIBES[i].id === id) {
        vibe = VIBES[i];
        break;
      }
    }
    var label = (vibe && vibe.label) || trait.chip || id;
    var ctx = _mapCtx(mapExtra);
    var origin =
      ctx.home_iata && ctx.home_iata.length === 3 ? ctx.home_iata : "home";
    var notBeen = _notBeenPhrase(ctx);
    var avoidBit = _avoidPhrase(ctx);
    var adj = trait.adj || label.toLowerCase();
    var when = _monthWindow();
    var hasMap =
      (ctx.visited_names && ctx.visited_names.length) ||
      (ctx.avoid_names && ctx.avoid_names.length);
    var rank = ranking || {};
    var pw = rank.pattern_weights || {};
    var seeds = Array.isArray(rank.dest_seeds) ? rank.dest_seeds : [];
    // Soft invent seeds only (not shown as "Save · city")
    var seedCodes = seeds
      .slice(0, 3)
      .map(function (s) {
        return s && s.iata;
      })
      .filter(Boolean);

    function baseScore(pattern, slotsFilled) {
      var s = 1.0 + (slotsFilled || 0) * 0.15;
      if (pw[pattern] != null) s += Number(pw[pattern]) || 0;
      return s;
    }

    // Each pill = one complete-ish dataset for the engine
    var candidates = [
      {
        id: "ds:getaway_new",
        pattern: "getaway_new",
        label: "New " + label.toLowerCase() + " getaway",
        slots: ["origin", "vibe", "shape:getaway", "new_places", "map"],
        q:
          "Open getaway from " +
          origin +
          ": " +
          trait.getaway +
          " — keep it " +
          adj +
          ", " +
          notBeen +
          (avoidBit ? "," + avoidBit : "") +
          ". No named second city; surprise me with a solid hub.",
        seed_iatas: seedCodes.slice(0, 2),
      },
      {
        id: "ds:stopover",
        pattern: "stopover",
        label: adj.charAt(0).toUpperCase() + adj.slice(1) + " stopover",
        slots: ["origin", "vibe", "shape:stopover", "new_places"],
        q:
          "From " +
          origin +
          " long-haul with a " +
          trait.stop +
          " — " +
          notBeen +
          (avoidBit ? "," + avoidBit : "") +
          ". Price the detour package.",
        seed_iatas: seedCodes.slice(0, 2),
      },
      {
        id: "ds:escape_city",
        pattern: "escape_city",
        label: label + " city break",
        slots: ["origin", "vibe", "shape:escape", "destination_intent"],
        q:
          "From " +
          origin +
          ": " +
          trait.escape +
          " — " +
          adj +
          " vibe" +
          (avoidBit ? "," + avoidBit : "") +
          ". One clear destination city, economy, one traveler.",
        seed_iatas: seedCodes.slice(0, 1),
      },
      {
        id: "ds:timed",
        pattern: "timed",
        label: when.split(" ")[0] + " · " + label,
        slots: ["origin", "vibe", "timing", "shape:getaway", "new_places"],
        q:
          "Around " +
          when +
          " from " +
          origin +
          ": " +
          adj +
          " trip — " +
          trait.getaway +
          ", " +
          notBeen +
          (avoidBit ? "," + avoidBit : "") +
          ". Use that depart window.",
        seed_iatas: [],
      },
      {
        id: "ds:budget_col",
        pattern: "budget_col",
        label: "Budget + " + label.toLowerCase(),
        slots: ["origin", "vibe", "budget", "new_places", "shape:getaway"],
        q:
          "Cheap " +
          adj +
          " few days out of " +
          origin +
          " — low fares, reasonable daily spend, " +
          notBeen +
          (avoidBit ? "," + avoidBit : "") +
          ". Favor value hubs.",
        seed_iatas: seedCodes.slice(0, 2),
      },
    ];

    if (hasMap) {
      var mapBits = [];
      if (ctx.visited_names && ctx.visited_names.length) {
        mapBits.push(
          ctx.visited_names.length <= 3
            ? "I've already been to " + _listJoin(ctx.visited_names)
            : "I've stamped " + ctx.visited_names.length + " countries on my map"
        );
      }
      if (ctx.avoid_names && ctx.avoid_names.length) {
        mapBits.push("hard avoid " + _listJoin(ctx.avoid_names, 3));
      }
      candidates.push({
        id: "ds:map_aware",
        pattern: "map_aware",
        label: "Map-aware " + label.toLowerCase(),
        slots: ["origin", "vibe", "map", "new_places", "shape:getaway"],
        q:
          "From " +
          origin +
          ", " +
          adj +
          " trip. " +
          mapBits.join("; ") +
          ". " +
          notBeen +
          ". " +
          trait.getaway +
          ". Honor the passport map strictly.",
        seed_iatas: seedCodes.slice(0, 2),
      });
    } else {
      // Map is empty — pill that completes the map-context slot by asking for new stamps later
      candidates.push({
        id: "ds:map_bootstrap",
        pattern: "map_aware",
        label: "Stamp home first",
        slots: ["map_hint", "origin", "vibe"],
        q:
          "From " +
          origin +
          " (treat as home): " +
          adj +
          " getaway, somewhere new. " +
          trait.getaway +
          ". I'll stamp visited countries on the map as I go.",
        seed_iatas: [],
      });
    }

    var chips = candidates.map(function (c) {
      return {
        id: c.id,
        label: c.label,
        q: c.q,
        vibe: id,
        source: "dataset",
        pattern: c.pattern,
        slots: c.slots,
        seed_iatas: c.seed_iatas || [],
        score: baseScore(c.pattern, (c.slots && c.slots.length) || 0),
        tooltip:
          "Fills search details: " +
          (c.slots || []).join(", ") +
          (rank.save_count
            ? " · ranked with " + rank.save_count + " Save signal(s)"
            : ""),
      };
    });

    chips.sort(function (a, b) {
      return (b.score || 0) - (a.score || 0);
    });
    return chips.slice(0, 4);
  }

  function findMap() {
    var node = document.querySelector("[data-yonder-map]");
    return node && node._yonderMap ? node._yonderMap : null;
  }

  function bindDrag(el, onMove) {
    function pos(e) {
      var rect = el.getBoundingClientRect();
      var cx = e.clientX;
      if (e.touches && e.touches[0]) cx = e.touches[0].clientX;
      return clamp((cx - rect.left) / Math.max(rect.width, 1), 0, 1);
    }
    function move(e) {
      e.preventDefault();
      onMove(pos(e));
    }
    function up() {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      window.removeEventListener("touchmove", move);
      window.removeEventListener("touchend", up);
    }
    el.addEventListener("pointerdown", function (e) {
      e.preventDefault();
      if (el.setPointerCapture && e.pointerId != null) {
        try {
          el.setPointerCapture(e.pointerId);
        } catch (err) {}
      }
      onMove(pos(e));
      window.addEventListener("pointermove", move);
      window.addEventListener("pointerup", up);
    });
    el.addEventListener(
      "touchstart",
      function (e) {
        e.preventDefault();
        onMove(pos(e));
        window.addEventListener("touchmove", move, { passive: false });
        window.addEventListener("touchend", up);
      },
      { passive: false }
    );
  }

  function mount(host, opts) {
    if (!host || host._vibeSlider) return host && host._vibeSlider;
    opts = opts || {};
    var inputId = opts.inputId || host.getAttribute("data-input-id") || "vibe-input";
    var initialId = opts.initial || host.getAttribute("data-initial") || "";

    var hidden = document.getElementById(inputId);
    if (!hidden) {
      hidden = document.createElement("input");
      hidden.type = "hidden";
      hidden.id = inputId;
      hidden.name = "vibe";
      host.parentNode.insertBefore(hidden, host);
    }
    hidden.disabled = false;
    hidden.removeAttribute("disabled");
    if (!hidden.name) hidden.name = "vibe";

    // Prefer locked server/form initial (post-search); otherwise a fresh random vibe each load
    var lockVibe = host.getAttribute("data-lock-vibe") === "1";
    var idx = lockVibe && initialId ? indexOfId(initialId) : -1;
    if (idx < 0) {
      idx = Math.floor(Math.random() * VIBES.length);
    }
    var start = VIBES[idx];

    host.classList.add("vibe-slider-host", "vibe-inline-host", "is-open", "is-required");
    host.innerHTML =
      '<div class="vibe-panel">' +
      '  <div class="vibe-hue" role="slider" aria-label="Trip vibe" tabindex="0" aria-valuemin="0" aria-valuemax="100">' +
      '    <div class="vibe-hue-cursor"></div>' +
      "  </div>" +
      '  <div class="vibe-name-row">' +
      '    <span class="vibe-name-swatch" aria-hidden="true"></span>' +
      '    <output class="vibe-name" aria-live="polite"></output>' +
      "  </div>" +
      "</div>";

    var hueEl = host.querySelector(".vibe-hue");
    var hueCursor = host.querySelector(".vibe-hue-cursor");
    var nameOut = host.querySelector(".vibe-name");
    var nameSwatch = host.querySelector(".vibe-name-swatch");

    var h = start._hsv.h;
    var shell = host.closest(".compose-field");
    if (shell) shell.classList.add("has-vibe");

    function currentVibe() {
      return nearestByHue(h);
    }

    function applyMap(color) {
      var map = findMap();
      if (map) map.setVibePreview(color || null);
    }

    function contrastInk(hex) {
      var rgb = hexToRgb(hex);
      // relative luminance — light vibes get dark label text
      var y = (0.2126 * rgb.r + 0.7152 * rgb.g + 0.0722 * rgb.b) / 255;
      return y > 0.62 ? "#1a1200" : "#ffffff";
    }

    function mixToward(hex, toward, t) {
      var a = hexToRgb(hex);
      var b = hexToRgb(toward);
      function ch(x, y) {
        return Math.round(x + (y - x) * t);
      }
      function p(n) {
        var s = Math.max(0, Math.min(255, n)).toString(16);
        return s.length === 1 ? "0" + s : s;
      }
      return "#" + p(ch(a.r, b.r)) + p(ch(a.g, b.g)) + p(ch(a.b, b.b));
    }

    /** Paint lounge chrome from vibe color (sky/brass/accent aliases). */
    function applyPageTheme(color) {
      var root = document.documentElement;
      var deep = mixToward(color, "#000000", 0.32);
      var mist = mixToward(color, "#ffffff", 0.88);
      var pairs = [
        ["--vibe-now", color],
        ["--sky-bright", color],
        ["--sky", deep],
        ["--sky-mist", mist],
        ["--brass", color],
        ["--brass-deep", deep],
        ["--brass-mist", mist],
        ["--accent", color],
        ["--accent-soft", mist],
        ["--gold", color],
        ["--gold-soft", mist],
      ];
      pairs.forEach(function (kv) {
        root.style.setProperty(kv[0], kv[1]);
      });
      if (document.body) {
        document.body.classList.add("has-vibe-theme");
        document.body.style.setProperty("--vibe-now", color);
      }
      if (shell) shell.style.setProperty("--vibe-now", color);
      host.style.setProperty("--vibe-now", color);
    }

    function paintGoButtons(color) {
      var form = host.closest("form");
      var root = form || document;
      root.querySelectorAll(".btn-vibe-go").forEach(function (btn) {
        btn.style.background = color;
        btn.style.borderColor = color;
        btn.style.color = contrastInk(color);
        btn.style.boxShadow = "0 2px 12px " + color + "55";
      });
    }

    function syncUi() {
      var vibe = currentVibe();
      var show = vibe.color;
      hueCursor.style.left = h * 100 + "%";
      hueCursor.style.backgroundColor = show;
      nameOut.textContent = vibe.label;
      nameOut.style.color = show;
      nameSwatch.style.backgroundColor = show;
      hueEl.setAttribute("aria-valuenow", String(Math.round(h * 100)));
      hueEl.setAttribute("aria-valuetext", vibe.label);
      hidden.value = vibe.id;
      hidden.disabled = false;
      applyPageTheme(show);
      paintGoButtons(show);
      applyMap(show);
      try {
        var detail = { id: vibe.id, label: vibe.label, color: vibe.color };
        host.dispatchEvent(
          new CustomEvent("yonder:vibechange", { bubbles: true, detail: detail })
        );
        document.dispatchEvent(
          new CustomEvent("yonder:vibechange", { bubbles: true, detail: detail })
        );
      } catch (e) {
        /* ignore */
      }
    }

    function setById(id) {
      var i = indexOfId(id);
      if (i < 0) return false;
      h = VIBES[i]._hsv.h;
      syncUi();
      return true;
    }

    bindDrag(hueEl, function (x) {
      h = x;
      syncUi();
    });

    hueEl.addEventListener("keydown", function (e) {
      var step = e.shiftKey ? 0.06 : 0.025;
      if (e.key === "ArrowRight" || e.key === "ArrowUp") {
        h = clamp(h + step, 0, 0.999);
        e.preventDefault();
      } else if (e.key === "ArrowLeft" || e.key === "ArrowDown") {
        h = clamp(h - step, 0, 0.999);
        e.preventDefault();
      } else return;
      syncUi();
    });

    // Map may boot later — re-apply tint once paths exist
    var tries = 0;
    var mapWait = setInterval(function () {
      tries += 1;
      var map = findMap();
      if (map && map.pathsByCode && Object.keys(map.pathsByCode).length) {
        applyMap(currentVibe().color);
        clearInterval(mapWait);
      } else if (tries > 40) {
        clearInterval(mapWait);
      }
    }, 150);

    syncUi();

    var api = {
      open: function () {
        syncUi();
      },
      close: function () {
        /* required — no-op */
      },
      isOpen: function () {
        return true;
      },
      setById: setById,
      getVibe: function () {
        return currentVibe();
      },
      vibes: VIBES,
    };
    host._vibeSlider = api;
    return api;
  }

  function mountAll() {
    document.querySelectorAll("[data-vibe-slider]").forEach(function (node) {
      if (!node._vibeSlider) mount(node);
    });
  }

  global.YonderVibe = {
    VIBES: VIBES,
    VIBE_TRAITS: VIBE_TRAITS,
    mount: mount,
    mountAll: mountAll,
    indexOfId: indexOfId,
    buildSuggestions: buildSuggestions,
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function () {
      setTimeout(mountAll, 40);
    });
  } else {
    setTimeout(mountAll, 40);
  }
})(typeof window !== "undefined" ? window : this);
