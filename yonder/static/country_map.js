/**
 * Yonder field atlas — zoomable country map (lounge-integrated UI).
 * Click cycles unmarked → visited → avoid → clear. Autosaves to /api/travel-map.
 *
 * Visited order is stamp order (not alphabetical). First visited country is
 * Home origin when Settings HOME_IATA is blank.
 *
 * Mount via:
 *   <div data-yonder-map data-theme="phosphor|amber"
 *        data-visited="CA,JP" data-avoid="RU"></div>
 *   + d3, topojson-client, then this script.
 */
(function (global) {
  "use strict";

  var AVOID_MAX = 10;
  var W = 960;
  var H = 480;
  var SCALE_MIN = 1;
  var SCALE_MAX = 14;
  var TOPO_URL = "https://cdn.jsdelivr.net/npm/world-atlas@2/countries-110m.json";
  var ISO_URL = "/static/iso_numeric_to_a2.json";
  var SAVE_URL = "/api/travel-map";

  function parseList(s) {
    if (Array.isArray(s)) {
      return s
        .map(function (x) {
          return String(x).trim().toUpperCase();
        })
        .filter(function (x) {
          return x.length === 2;
        });
    }
    return String(s || "")
      .split(/[,;]/)
      .map(function (x) {
        return x.trim().toUpperCase();
      })
      .filter(function (x) {
        return x.length === 2;
      });
  }

  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }

  function YonderMap(root, opts) {
    this.root = root;
    this.theme = opts.theme === "amber" ? "amber" : "phosphor";
    this.compact = !!opts.compact;
    this.names = opts.names || {};
    this.autoSave = opts.autoSave !== false;
    this.visited = new Set(parseList(opts.visited));
    this.avoid = new Set(parseList(opts.avoid));
    // Avoid wins if both somehow set
    this.avoid.forEach(
      function (c) {
        this.visited.delete(c);
      }.bind(this)
    );
    this.pathsByCode = {};
    this.zoomBehavior = null;
    this.mapLayer = null;
    this.svg = null;
    this._saveTimer = null;
    this._vibePreview = null;
    this._uid = "ymap-" + Math.random().toString(36).slice(2, 9);
    this._buildShell();
  }

  YonderMap.prototype._makeZoomTools = function () {
    var tools = el("div", "ymap-tools");
    tools.setAttribute("role", "group");
    tools.setAttribute("aria-label", "Map zoom");
    this.btnIn = el("button", null, "+");
    this.btnIn.type = "button";
    this.btnIn.title = "Zoom in";
    this.btnIn.setAttribute("aria-label", "Zoom in");
    this.btnOut = el("button", null, "−");
    this.btnOut.type = "button";
    this.btnOut.title = "Zoom out";
    this.btnOut.setAttribute("aria-label", "Zoom out");
    this.btnReset = el("button", null, "↺");
    this.btnReset.type = "button";
    this.btnReset.title = "Reset view";
    this.btnReset.setAttribute("aria-label", "Reset view");
    tools.appendChild(this.btnIn);
    tools.appendChild(this.btnOut);
    tools.appendChild(this.btnReset);
    return tools;
  };

  YonderMap.prototype._makeMapFrame = function () {
    var frame = el("div", "ymap-frame");
    var stage = el("div", "ymap-stage");
    stage.tabIndex = 0;
    stage.setAttribute("aria-label", "Map stage — keyboard + − 0 to zoom");
    this.stage = stage;
    this.tip = el("div", "ymap-tip");
    stage.appendChild(this.tip);
    this.svgNode = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    this.svgNode.setAttribute("viewBox", "0 0 " + W + " " + H);
    // Cover the frame (no letterboxing when CSS aspect ≠ viewBox)
    this.svgNode.setAttribute("preserveAspectRatio", "xMidYMid slice");
    this.svgNode.setAttribute("role", "img");
    this.svgNode.setAttribute(
      "aria-label",
      "World map — click countries to mark visited or avoid"
    );
    stage.appendChild(this.svgNode);
    frame.appendChild(stage);
    return frame;
  };

  YonderMap.prototype._buildShell = function () {
    if (this.compact) {
      this._buildCompactShell();
      return;
    }
    this._buildFullShell();
  };

  /** Embedded compose-card atlas: map + one summary line + collapsible stamps */
  YonderMap.prototype._buildCompactShell = function () {
    var root = this.root;
    var isDetour = this.theme === "amber";
    root.classList.add("ymap", "ymap-compact");
    root.classList.add(isDetour ? "theme-amber" : "theme-phosphor");
    root.innerHTML = "";

    var bar = el("div", "ymap-compact-bar");
    var label = el("div", "ymap-compact-label");
    label.appendChild(el("span", "ymap-compact-k", isDetour ? "No-land map" : "Passport map"));
    label.appendChild(
      el("span", "ymap-compact-hint", "Click to set as visited. Double click to avoid.")
    );
    bar.appendChild(label);
    bar.appendChild(this._makeZoomTools());
    root.appendChild(bar);

    root.appendChild(this._makeMapFrame());

    var meta = el("div", "ymap-compact-meta");
    meta.innerHTML =
      '<button type="button" class="ymap-drawer-toggle" aria-expanded="false">' +
      '<span class="ymap-counts">' +
      'Visited <strong class="ymap-visited-count">0</strong>' +
      ' · Avoid <strong class="ymap-avoid-count">0</strong><em>/10</em>' +
      "</span>" +
      '<span class="ymap-drawer-caret" aria-hidden="true">▾</span>' +
      "</button>" +
      '<button type="button" class="ymap-clear-btn" hidden title="Clear all visited and avoid stamps">Clear map</button>' +
      '<span class="v ymap-save">—</span>' +
      '<span class="ymap-warn"></span>';
    root.appendChild(meta);

    this.visitedCountEl = meta.querySelector(".ymap-visited-count");
    this.avoidCountEl = meta.querySelector(".ymap-avoid-count");
    this.saveEl = meta.querySelector(".ymap-save");
    this.warnEl = meta.querySelector(".ymap-warn");
    this.clearBtn = meta.querySelector(".ymap-clear-btn");
    this.chromeStatus = null;
    this.drawerToggle = meta.querySelector(".ymap-drawer-toggle");
    if (this.clearBtn) {
      var selfClear = this;
      this.clearBtn.addEventListener("click", function (e) {
        e.preventDefault();
        e.stopPropagation();
        selfClear.clearMap();
      });
    }

    var drawer = el("div", "ymap-drawer");
    drawer.hidden = true;
    this.drawerEl = drawer;

    var drawers = el("div", "ymap-drawers");
    var visitedBlock = el("div", "ymap-chip-block");
    visitedBlock.appendChild(el("div", "ymap-chip-label", "Visited"));
    this.visitedChips = el("div", "ymap-chips");
    this.visitedChips.setAttribute("aria-label", "Visited countries");
    visitedBlock.appendChild(this.visitedChips);
    drawers.appendChild(visitedBlock);

    var avoidBlock = el("div", "ymap-chip-block");
    avoidBlock.appendChild(el("div", "ymap-chip-label", isDetour ? "Avoid" : "Avoid"));
    this.avoidChips = el("div", "ymap-chips");
    this.avoidChips.setAttribute("aria-label", "Avoid countries");
    avoidBlock.appendChild(this.avoidChips);
    drawers.appendChild(avoidBlock);
    drawer.appendChild(drawers);
    root.appendChild(drawer);

    var self = this;
    this.drawerToggle.addEventListener("click", function () {
      var open = drawer.hidden;
      drawer.hidden = !open;
      self.drawerToggle.setAttribute("aria-expanded", open ? "true" : "false");
      self.drawerToggle.classList.toggle("is-open", open);
    });
  };

  YonderMap.prototype._buildFullShell = function () {
    var root = this.root;
    var isDetour = this.theme === "amber";
    root.classList.add("ymap");
    root.classList.add(isDetour ? "theme-amber" : "theme-phosphor");
    root.innerHTML = "";

    var kicker = el("div", "ymap-kicker");
    kicker.appendChild(el("span", "ymap-kicker-label", "Atlas"));
    kicker.appendChild(el("span", "ymap-badge", isDetour ? "Detour" : "Escape"));
    root.appendChild(kicker);

    var head = el("div", "ymap-head");
    var htext = el("div", "ymap-head-text");
    htext.appendChild(
      el("h2", null, isDetour ? "Where not to land" : "Where you've been")
    );
    htext.appendChild(
      el(
        "p",
        "sub",
        isDetour
          ? "Tap a country to cycle open → visited → avoid. Avoid blocks stopovers (max 10)."
          : "Stamp countries you've done. Red keeps Detour off those places."
      )
    );
    head.appendChild(htext);
    head.appendChild(this._makeZoomTools());
    root.appendChild(head);

    root.appendChild(this._makeMapFrame());

    var footer = el("div", "ymap-footer");
    var stats = el("div", "ymap-stats");
    stats.innerHTML =
      '<div class="ymap-stat">' +
      '<span class="k">Visited</span>' +
      '<span class="v ymap-visited-count">0</span>' +
      "</div>" +
      '<div class="ymap-stat">' +
      '<span class="k">Avoid</span>' +
      '<span class="v"><span class="ymap-avoid-count">0</span><em>/10</em></span>' +
      "</div>" +
      '<div class="ymap-stat ymap-stat-sync">' +
      '<span class="k">Sync</span>' +
      '<span class="v ymap-save">—</span>' +
      '<span class="ymap-warn"></span>' +
      "</div>" +
      '<button type="button" class="ymap-clear-btn" hidden title="Clear all visited and avoid stamps">Clear map</button>';
    footer.appendChild(stats);
    this.visitedCountEl = stats.querySelector(".ymap-visited-count");
    this.avoidCountEl = stats.querySelector(".ymap-avoid-count");
    this.saveEl = stats.querySelector(".ymap-save");
    this.warnEl = stats.querySelector(".ymap-warn");
    this.clearBtn = stats.querySelector(".ymap-clear-btn");
    this.chromeStatus = null;
    if (this.clearBtn) {
      var selfFullClear = this;
      this.clearBtn.addEventListener("click", function (e) {
        e.preventDefault();
        selfFullClear.clearMap();
      });
    }

    var legend = el("div", "ymap-legend");
    legend.innerHTML =
      '<span class="leg-item"><span class="swatch none"></span>Open</span>' +
      '<span class="leg-item"><span class="swatch visited"></span>Visited</span>' +
      '<span class="leg-item"><span class="swatch avoid"></span>Avoid</span>';
    footer.appendChild(legend);

    var drawers = el("div", "ymap-drawers");
    var visitedBlock = el("div", "ymap-chip-block");
    visitedBlock.appendChild(el("div", "ymap-chip-label", "Passport stamps"));
    this.visitedChips = el("div", "ymap-chips");
    this.visitedChips.setAttribute("aria-label", "Visited countries");
    visitedBlock.appendChild(this.visitedChips);
    drawers.appendChild(visitedBlock);

    var avoidBlock = el("div", "ymap-chip-block");
    avoidBlock.appendChild(
      el("div", "ymap-chip-label", isDetour ? "No-land list" : "Avoid list")
    );
    this.avoidChips = el("div", "ymap-chips");
    this.avoidChips.setAttribute("aria-label", "Avoid countries");
    avoidBlock.appendChild(this.avoidChips);
    drawers.appendChild(avoidBlock);
    footer.appendChild(drawers);

    var foot = el("p", "ymap-foot");
    foot.textContent = isDetour
      ? "Avoid blocks detour stopovers. Visited stamps don’t change fares."
      : "Visited is your passport wall. Avoid shapes Detour mode.";
    footer.appendChild(foot);
    root.appendChild(footer);
  };

  YonderMap.prototype.stateOf = function (code) {
    if (this.avoid.has(code)) return "avoid";
    if (this.visited.has(code)) return "visited";
    return "none";
  };

  YonderMap.prototype.paint = function (code) {
    var nodes = this.pathsByCode[code] || [];
    var st = this.stateOf(code);
    var vibe = this._vibePreview || null;
    nodes.forEach(function (node) {
      node.classList.remove("visited", "avoid", "active", "other", "vibe-preview");
      node.style.removeProperty("fill");
      node.style.removeProperty("--vibe-fill");
      node.removeAttribute("fill");
      if (st === "visited") {
        node.classList.add("visited");
      } else if (st === "avoid") {
        node.classList.add("avoid");
      } else if (vibe) {
        node.classList.add("vibe-preview");
        node.style.setProperty("--vibe-fill", vibe);
        node.style.fill = vibe;
        node.setAttribute("fill", vibe);
      }
    });
  };

  YonderMap.prototype.paintAll = function () {
    Object.keys(this.pathsByCode).forEach(this.paint.bind(this));
  };

  /**
   * Tint open (unmarked) countries with a vibe color, or null to clear.
   * Visited / avoid are never overridden.
   */
  YonderMap.prototype.setVibePreview = function (colorHex) {
    this._vibePreview = colorHex || null;
    this.paintAll();
  };

  YonderMap.prototype.chipLabel = function (code, isHome) {
    var base = (this.names[code] || code) + " (" + code + ")";
    return isHome ? "Home · " + base : base;
  };

  /** Visited codes in stamp order (Set insertion order — never alphabetized). */
  YonderMap.prototype.orderedVisited = function () {
    return Array.from(this.visited);
  };

  /** Avoid codes in stamp order. */
  YonderMap.prototype.orderedAvoid = function () {
    return Array.from(this.avoid);
  };

  /**
   * Resolve home IATA: explicit Settings HOME_IATA wins; else first visited
   * country with a known primary airport; else currency/USA fallback.
   */
  YonderMap.prototype.resolveHomeIata = function (visitedOrdered) {
    var t = global.FS_TRAVEL || {};
    var locked = String(t.home_iata_setting || "")
      .trim()
      .toUpperCase();
    if (locked.length === 3 && /^[A-Z]{3}$/.test(locked)) return locked;
    // First stamp = home (selection order)
    var list = visitedOrdered || this.orderedVisited();
    var prim = t.primary_iata || {};
    for (var i = 0; i < list.length; i++) {
      var iata = prim[list[i]];
      if (iata) return String(iata).toUpperCase();
    }
    // Fall back to server-resolved home, then currency/USA
    var fb = String(t.home_iata || t.home_iata_fallback || "JFK")
      .trim()
      .toUpperCase();
    return fb.length === 3 ? fb : "JFK";
  };

  YonderMap.prototype.renderChips = function () {
    var self = this;
    var visitedList = this.orderedVisited();
    var homeCode = visitedList.length ? visitedList[0] : null;
    var homeLocked = !!(
      global.FS_TRAVEL &&
      String(global.FS_TRAVEL.home_iata_setting || "").trim().length === 3
    );

    function fill(container, list, cls, emptyText, markHome) {
      container.innerHTML = "";
      list.forEach(function (code, idx) {
        var isHome = markHome && !homeLocked && idx === 0 && code === homeCode;
        var b = el("button", "chip " + cls + (isHome ? " is-home" : ""), self.chipLabel(code, isHome));
        b.type = "button";
        b.title = isHome
          ? "Home (first stamp) — click to advance state"
          : "Click to advance state";
        b.addEventListener("click", function () {
          self.cycle(code);
        });
        container.appendChild(b);
      });
      if (!list.length) {
        container.appendChild(el("span", "empty", emptyText));
      }
    }

    fill(
      this.visitedChips,
      visitedList,
      "visited",
      "No visited countries yet — first stamp is Home",
      true
    );
    fill(this.avoidChips, this.orderedAvoid(), "avoid", "No avoid countries", false);
  };

  YonderMap.prototype.syncUi = function () {
    // Preserve stamp order — do NOT alphabetize (first visited = Home)
    var v = this.orderedVisited();
    var a = this.orderedAvoid();
    if (this.visitedCountEl) this.visitedCountEl.textContent = String(v.length);
    if (this.avoidCountEl) this.avoidCountEl.textContent = String(a.length);
    if (this.warnEl) {
      this.warnEl.textContent =
        a.length >= AVOID_MAX
          ? "Avoid list full — clear one before adding another"
          : "";
    }
    if (this.clearBtn) {
      var stamped = v.length + a.length;
      this.clearBtn.hidden = stamped <= 1;
      this.clearBtn.disabled = stamped === 0;
      this.clearBtn.setAttribute(
        "aria-label",
        stamped > 1
          ? "Clear all " + stamped + " map stamps"
          : "Clear map (need more than one country)"
      );
    }
    this.renderChips();

    var homeIata = this.resolveHomeIata(v);
    if (global.FS_TRAVEL) {
      global.FS_TRAVEL.visited = v;
      global.FS_TRAVEL.avoid = a;
      global.FS_TRAVEL.visited_names = v.map(
        function (c) {
          return this.names[c] || c;
        }.bind(this)
      );
      global.FS_TRAVEL.avoid_names = a.map(
        function (c) {
          return this.names[c] || c;
        }.bind(this)
      );
      global.FS_TRAVEL.home_iata = homeIata;
      if (v.length) {
        global.FS_TRAVEL.home_country = v[0];
        global.FS_TRAVEL.home_country_name = this.names[v[0]] || v[0];
      } else {
        global.FS_TRAVEL.home_country = "";
        global.FS_TRAVEL.home_country_name = "";
      }
    }
    // Let compose suggestion chips re-roll from visited/avoid stamps + home
    try {
      var detail = {
        visited: v.slice(),
        avoid: a.slice(),
        visited_names: (global.FS_TRAVEL && global.FS_TRAVEL.visited_names) || v.slice(),
        avoid_names: (global.FS_TRAVEL && global.FS_TRAVEL.avoid_names) || a.slice(),
        home_iata: homeIata,
        home_country: v.length ? v[0] : "",
      };
      this.root.dispatchEvent(
        new CustomEvent("yonder:mapchange", { bubbles: true, detail: detail })
      );
      document.dispatchEvent(
        new CustomEvent("yonder:mapchange", { bubbles: true, detail: detail })
      );
    } catch (e) {
      /* ignore */
    }
  };

  /** Wipe visited + avoid when the user wants a clean passport map. */
  YonderMap.prototype.clearMap = function () {
    var total = this.visited.size + this.avoid.size;
    if (total <= 1) return;
    if (
      !global.confirm(
        "Clear all " +
          total +
          " stamps (visited + avoid)? This saves to your passport map."
      )
    ) {
      return;
    }
    this.visited.clear();
    this.avoid.clear();
    this.paintAll();
    this.syncUi();
    this.scheduleSave();
  };

  /** Same cycle as Settings: none → visited → avoid → clear */
  YonderMap.prototype.cycle = function (code) {
    if (!code || code.length !== 2) return;
    var st = this.stateOf(code);

    if (st === "none") {
      this.visited.add(code);
      this.avoid.delete(code);
    } else if (st === "visited") {
      if (this.avoid.size >= AVOID_MAX && !this.avoid.has(code)) {
        if (this.warnEl) {
          this.warnEl.textContent = "Avoid list full (max " + AVOID_MAX + ")";
        }
        return;
      }
      this.visited.delete(code);
      this.avoid.add(code);
    } else {
      this.avoid.delete(code);
      this.visited.delete(code);
    }

    this.paint(code);
    this.syncUi();
    this.scheduleSave();
  };

  YonderMap.prototype.setSaveStatus = function (kind, text) {
    if (!this.saveEl) return;
    this.saveEl.className = "v ymap-save" + (kind ? " " + kind : "");
    this.saveEl.textContent = text || "—";
  };

  YonderMap.prototype.scheduleSave = function () {
    if (!this.autoSave) return;
    var self = this;
    this.setSaveStatus("saving", "Syncing…");
    if (this.chromeStatus) {
      this.chromeStatus.className = "status-ok";
      this.chromeStatus.textContent = "WRITING…";
    }
    clearTimeout(this._saveTimer);
    this._saveTimer = setTimeout(function () {
      self.save();
    }, 280);
  };

  YonderMap.prototype.save = async function () {
    var self = this;
    // Stamp order preserved — first visited is Home on the server
    var body = {
      visited: this.orderedVisited(),
      avoid: this.orderedAvoid(),
    };
    try {
      var r = await fetch(SAVE_URL, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Accept: "application/json",
        },
        body: JSON.stringify(body),
      });
      if (!r.ok) throw new Error("HTTP " + r.status);
      var j = await r.json();
      if (j.visited) this.visited = new Set(parseList(j.visited));
      if (j.avoid) this.avoid = new Set(parseList(j.avoid));
      this.paintAll();
      this.syncUi();
      this.setSaveStatus("saved", "Saved");
      if (this.chromeStatus) {
        this.chromeStatus.className = "status-ok";
        this.chromeStatus.textContent = "SIGNAL OK";
      }
      setTimeout(function () {
        if (self.saveEl && self.saveEl.classList.contains("saved")) {
          self.setSaveStatus("", "—");
        }
      }, 1600);
    } catch (err) {
      console.error(err);
      this.setSaveStatus("err", "Save failed");
      if (this.chromeStatus) {
        this.chromeStatus.className = "status-warn";
        this.chromeStatus.textContent = "SYNC ERR";
      }
    }
  };

  YonderMap.prototype.showTip = function (evt, code) {
    if (!this.tip || !this.stage) return;
    var st = this.stateOf(code);
    var label =
      st === "visited" ? "visited" : st === "avoid" ? "avoid" : "unmarked";
    this.tip.textContent = this.chipLabel(code) + " · " + label;
    this.tip.style.display = "block";
    var rect = this.stage.getBoundingClientRect();
    var x = evt.clientX - rect.left + 12;
    var y = evt.clientY - rect.top + 12;
    this.tip.style.left = Math.min(x, rect.width - 170) + "px";
    this.tip.style.top = Math.min(y, rect.height - 36) + "px";
  };

  YonderMap.prototype.hideTip = function () {
    if (this.tip) this.tip.style.display = "none";
  };

  YonderMap.prototype.zoomBy = function (factor) {
    if (!this.zoomBehavior || !this.svg) return;
    this.svg.transition().duration(180).call(this.zoomBehavior.scaleBy, factor);
  };

  YonderMap.prototype.zoomReset = function () {
    if (!this.zoomBehavior || !this.svg) return;
    this.svg
      .transition()
      .duration(220)
      .call(this.zoomBehavior.transform, d3.zoomIdentity);
  };

  YonderMap.prototype.boot = async function () {
    var self = this;
    if (typeof d3 === "undefined" || typeof topojson === "undefined") {
      this.setSaveStatus("err", "Map libs missing");
      if (this.chromeStatus) {
        this.chromeStatus.className = "status-warn";
        this.chromeStatus.textContent = "NO SIGNAL";
      }
      return;
    }

    try {
      this.setSaveStatus("saving", "Loading…");
      if (this.chromeStatus) this.chromeStatus.textContent = "TUNING…";
      var topo = await d3.json(TOPO_URL);
      var isoMap = await d3.json(ISO_URL);
      var countries = topojson.feature(topo, topo.objects.countries);
      // Fit the globe to the full viewBox (not a padded countries bbox)
      // so land fills the frame; slight overscale trims empty oval corners.
      var projection = d3.geoNaturalEarth1().fitExtent(
        [
          [0, 0],
          [W, H],
        ],
        { type: "Sphere" }
      );
      var s = projection.scale();
      var t = projection.translate();
      projection.scale(s * 1.12);
      projection.translate([t[0], t[1] + H * 0.02]);
      var path = d3.geoPath(projection);

      this.svg = d3.select(this.svgNode);
      this.svg.selectAll("*").remove();
      this.pathsByCode = {};

      var clipId = this._uid + "-clip";
      this.svg
        .append("defs")
        .append("clipPath")
        .attr("id", clipId)
        .append("rect")
        .attr("width", W)
        .attr("height", H);

      var clipHost = this.svg
        .append("g")
        .attr("clip-path", "url(#" + clipId + ")");

      this.mapLayer = clipHost.append("g").attr("class", "map-layer");

      var ocean = this.theme === "amber" ? "#e8e0d0" : "#d9e6f2";
      var grid = this.theme === "amber" ? "rgba(184,134,11,0.13)" : "rgba(30,77,123,0.1)";

      this.mapLayer
        .append("path")
        .datum({ type: "Sphere" })
        .attr("d", path)
        .attr("fill", ocean)
        .attr("stroke", "none");

      this.mapLayer
        .append("path")
        .datum(d3.geoGraticule10())
        .attr("d", path)
        .attr("fill", "none")
        .attr("stroke", grid)
        .attr("stroke-width", 0.45)
        .attr("vector-effect", "non-scaling-stroke");

      function codeFor(d) {
        var id = String(d.id);
        return isoMap[id] || isoMap[id.padStart(3, "0")] || "";
      }

      this.mapLayer
        .selectAll("path.country")
        .data(countries.features)
        .join("path")
        .attr("class", "country")
        .attr("d", path)
        .attr("data-code", function (d) {
          return codeFor(d);
        })
        .each(function (d) {
          var code = codeFor(d);
          if (!code) return;
          if (!self.pathsByCode[code]) self.pathsByCode[code] = [];
          self.pathsByCode[code].push(this);
        })
        .on("click", function (event, d) {
          // d3.zoom sets defaultPrevented after a pan
          if (event.defaultPrevented) return;
          var code = codeFor(d);
          if (!code) return;
          self.cycle(code);
          self.showTip(event, code);
        })
        .on("mousemove", function (event, d) {
          var code = codeFor(d);
          if (code) self.showTip(event, code);
        })
        .on("mouseleave", function () {
          self.hideTip();
        });

      this.zoomBehavior = d3
        .zoom()
        .scaleExtent([SCALE_MIN, SCALE_MAX])
        .extent([
          [0, 0],
          [W, H],
        ])
        .translateExtent([
          [-W * 0.35, -H * 0.35],
          [W * 1.35, H * 1.35],
        ])
        .filter(function (event) {
          if (event.type === "wheel") return true;
          return event.button === 0 || event.touches;
        })
        .on("zoom", function (event) {
          if (self.mapLayer) self.mapLayer.attr("transform", event.transform);
        })
        .on("start", function () {
          self.hideTip();
        });

      this.svg.call(this.zoomBehavior).on("dblclick.zoom", null);

      this.btnIn.addEventListener("click", function (e) {
        e.preventDefault();
        self.zoomBy(1.35);
      });
      this.btnOut.addEventListener("click", function (e) {
        e.preventDefault();
        self.zoomBy(1 / 1.35);
      });
      this.btnReset.addEventListener("click", function (e) {
        e.preventDefault();
        self.zoomReset();
      });

      this.stage.addEventListener("keydown", function (e) {
        if (e.key === "+" || e.key === "=") {
          e.preventDefault();
          self.zoomBy(1.35);
        } else if (e.key === "-" || e.key === "_") {
          e.preventDefault();
          self.zoomBy(1 / 1.35);
        } else if (e.key === "0") {
          e.preventDefault();
          self.zoomReset();
        }
      });

      this.paintAll();
      this.syncUi();
      this.setSaveStatus("", "—");
      if (this.chromeStatus) {
        this.chromeStatus.className = "status-ok";
        this.chromeStatus.textContent = "SIGNAL OK";
      }
    } catch (err) {
      console.error(err);
      this.setSaveStatus("err", "Map failed to load");
      if (this.chromeStatus) {
        this.chromeStatus.className = "status-warn";
        this.chromeStatus.textContent = "NO SIGNAL";
      }
    }
  };

  function namesFromCountries(list) {
    var out = {};
    if (!list) return out;
    if (Array.isArray(list)) {
      list.forEach(function (row) {
        if (Array.isArray(row) && row.length >= 2) out[row[0]] = row[1];
        else if (row && row.code) out[row.code] = row.name || row.code;
      });
      return out;
    }
    if (typeof list === "object") return list;
    return out;
  }

  function mount(root, opts) {
    if (!root) return null;
    opts = opts || {};
    if (!opts.names || !Object.keys(opts.names).length) {
      opts.names = namesFromCountries(
        opts.countries ||
          (global.FS_TRAVEL && global.FS_TRAVEL.country_names) ||
          {}
      );
    }
    if (opts.visited == null && global.FS_TRAVEL) {
      opts.visited = global.FS_TRAVEL.visited;
    }
    if (opts.avoid == null && global.FS_TRAVEL) {
      opts.avoid = global.FS_TRAVEL.avoid;
    }
    var map = new YonderMap(root, opts);
    map.boot();
    return map;
  }

  function mountAll() {
    var nodes = document.querySelectorAll("[data-yonder-map]");
    nodes.forEach(function (node) {
      if (node._yonderMap) return;
      var theme =
        node.getAttribute("data-theme") ||
        (node.closest(".theme-amber") ? "amber" : "phosphor");
      // Support legacy data-mode attrs without changing behavior
      if (!node.getAttribute("data-theme") && node.getAttribute("data-mode") === "avoid") {
        theme = "amber";
      }
      var compactAttr = node.getAttribute("data-compact");
      node._yonderMap = mount(node, {
        theme: theme,
        compact:
          compactAttr === "1" ||
          compactAttr === "true" ||
          node.classList.contains("ymap-compact"),
        visited: node.getAttribute("data-visited"),
        avoid: node.getAttribute("data-avoid"),
        autoSave: node.getAttribute("data-autosave") !== "0",
      });
    });
  }

  global.YonderMap = {
    mount: mount,
    mountAll: mountAll,
    AVOID_MAX: AVOID_MAX,
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", mountAll);
  } else {
    // Scripts are typically at end of body after the mount node
    mountAll();
  }
})(typeof window !== "undefined" ? window : globalThis);
