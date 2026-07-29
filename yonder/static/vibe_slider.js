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
    mount: mount,
    mountAll: mountAll,
    indexOfId: indexOfId,
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function () {
      setTimeout(mountAll, 40);
    });
  } else {
    setTimeout(mountAll, 40);
  }
})(typeof window !== "undefined" ? window : this);
