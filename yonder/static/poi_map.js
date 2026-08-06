/* poi_map.js — curated POI world map, v1
   D3 + TopoJSON world base, ~2400 POI markers, category filter pills.
   No external dependencies beyond D3 + TopoJSON (loaded by the host page).
   Degrades gracefully: if data or map fails to load, the page still works.
*/
(function () {
  "use strict";

  /* Category → emoji */
  var CAT_EMOJIS = {
    activity:    "🎯",
    art:         "🎨",
    bar:         "🍺",
    beach:       "🏖️",
    brewery:     "🍻",
    cafe:        "☕",
    culture:     "🎭",
    food:        "🍽️",
    heritage:    "🏰",
    hike:        "🥾",
    island:      "🏝️",
    landmark:    "🗽",
    market:      "🛒",
    monument:    "🗿",
    mountain:    "⛰️",
    museum:      "🏛️",
    nature:      "🌿",
    neighborhood:"🏘️",
    nightlife:   "🌃",
    park:        "🌳",
    place:       "📍",
    restaurant:  "🍽️",
    shop:        "🛍️",
    ski:         "⛷️",
    stay:        "🏨",
    theme_park:  "🎡",
    transit:     "🚉",
    water:       "💧",
    default:     "📍",
  };

  function catEmoji(cat) {
    if (!cat) return CAT_EMOJIS.default;
    var k = cat.toLowerCase().replace(/[^a-z_]/g, "");
    return CAT_EMOJIS[k] || CAT_EMOJIS.default;
  }

  /* Category → hex colour */
  var CAT_COLORS = {
    art: "#9b59b6",
    bar: "#c0392b",
    beach: "#16a085",
    brewery: "#e67e22",
    cafe: "#a0522d",
    culture: "#e67e22",
    food: "#f39c12",
    heritage: "#d4a017",
    hike: "#27ae60",
    island: "#1abc9c",
    landmark: "#2980b9",
    market: "#f1c40f",
    monument: "#7f8c8d",
    mountain: "#2c3e50",
    museum: "#8e44ad",
    nature: "#27ae60",
    neighborhood: "#95a5a6",
    park: "#58d68d",
    place: "#bdc3c7",
    shop: "#fd79a8",
    ski: "#6c5ce7",
    stay: "#74b9ff",
    transit: "#636e72",
    water: "#0984e3",
    activity: "#00b894",
    restaurant: "#f39c12",
    nightlife: "#c0392b",
    default: "#d4a017",
  };

  function catColor(cat) {
    if (!cat) return CAT_COLORS.default;
    var k = cat.toLowerCase().replace(/[^a-z]/g, "");
    return CAT_COLORS[k] || CAT_COLORS.default;
  }

  var container = document.getElementById("poi-map-container");
  var statusEl = document.getElementById("poi-map-status");
  var pillsEl = document.getElementById("poi-cat-pills");
  var countEl = document.getElementById("poi-count");

  if (!container) return;

  function setStatus(msg) {
    if (statusEl) statusEl.textContent = msg;
  }

  var W = container.clientWidth || 900;
  var H = Math.max(480, Math.round(W * 0.52));

  /* Colour palette — matches the explore map's amber theme */
  var OCEAN  = "#d4c8a8";   /* warm parchment water */
  var LAND   = "#ede3c8";   /* lighter parchment land */
  var GRID   = "rgba(184,134,11,0.10)"; /* faint gold graticule */

  /* SVG scaffold */
  var svg = d3
    .select(container)
    .append("svg")
    .attr("viewBox", "0 0 " + W + " " + H)
    .attr("width", "100%")
    .attr("height", H)
    .style("background", OCEAN);

  var projection = d3
    .geoNaturalEarth1()
    .scale((W / 640) * 100)
    .translate([W / 2, H / 2]);

  var path = d3.geoPath().projection(projection);

  /* Tooltip */
  var tooltip = d3
    .select(container)
    .append("div")
    .attr("class", "poi-tooltip")
    .style("display", "none");

  /* State */
  var allData = [];
  var activeCategories = new Set();
  var dotsGroup = null;

  function positionTooltip(event) {
    var rect = container.getBoundingClientRect();
    var x = (event.clientX || 0) - rect.left + 12;
    var y = (event.clientY || 0) - rect.top - 12;
    if (x + 240 > rect.width) x = x - 252;
    tooltip.style("left", x + "px").style("top", y + "px");
  }

  function renderDots(data) {
    if (dotsGroup) dotsGroup.remove();
    dotsGroup = svg.append("g").attr("class", "poi-dots");

    var filtered = activeCategories.size
      ? data.filter(function (d) {
          return activeCategories.has((d.c || "").toLowerCase());
        })
      : data;

    if (countEl) countEl.textContent = filtered.length.toLocaleString();

    dotsGroup
      .selectAll("circle")
      .data(filtered)
      .enter()
      .append("circle")
      .attr("cx", function (d) {
        var p = projection([d.lon, d.lat]);
        return p ? p[0] : -9999;
      })
      .attr("cy", function (d) {
        var p = projection([d.lon, d.lat]);
        return p ? p[1] : -9999;
      })
      .attr("r", 4.5)
      .attr("fill", function (d) {
        return catColor(d.c);
      })
      .attr("fill-opacity", 0.82)
      .attr("stroke", "var(--paper, #f4efe6)")
      .attr("stroke-width", 0.8)
      .style("cursor", "pointer")
      .on("mouseover", function (event, d) {
        d3.select(this).attr("r", 7).attr("fill-opacity", 1);
        tooltip
          .style("display", "block")
          .html(
            '<span class="pt-emoji">' +
              (d.e || "📍") +
              "</span> " +
              '<strong class="pt-name">' +
              escHtml(d.n || "") +
              "</strong>" +
              (d.c ? ' <span class="pt-cat">' + escHtml(d.c) + "</span>" : "") +
              (d.t ? '<p class="pt-note">' + escHtml(d.t) + "</p>" : "") +
              (d.u
                ? '<a class="pt-link" href="' +
                  escHtml(d.u) +
                  '" target="_blank" rel="noopener">Open in Maps ↗</a>'
                : "")
          );
        positionTooltip(event);
      })
      .on("mousemove", function (event) {
        positionTooltip(event);
      })
      .on("mouseout", function () {
        d3.select(this).attr("r", 4.5).attr("fill-opacity", 0.82);
        tooltip.style("display", "none");
      })
      .on("click", function (event, d) {
        if (d.u) window.open(d.u, "_blank", "noopener");
      });
  }

  function escHtml(s) {
    return String(s || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function buildPills(data) {
    if (!pillsEl) return;
    pillsEl.innerHTML = "";

    /* Count per category */
    var counts = {};
    data.forEach(function (d) {
      var k = (d.c || "unknown").toLowerCase();
      counts[k] = (counts[k] || 0) + 1;
    });

    /* Sort by count desc */
    var cats = Object.keys(counts).sort(function (a, b) {
      return counts[b] - counts[a];
    });

    /* "All" pill */
    var allPill = document.createElement("button");
    allPill.className = "poi-pill active";
    allPill.textContent = "🌍 ALL (" + data.length.toLocaleString() + ")";
    allPill.dataset.cat = "";
    pillsEl.appendChild(allPill);

    cats.forEach(function (cat) {
      var btn = document.createElement("button");
      btn.className = "poi-pill";
      var label = cat.replace(/_/g, " ").toUpperCase();
      btn.textContent = catEmoji(cat) + " " + label + " (" + counts[cat] + ")";
      btn.dataset.cat = cat;
      pillsEl.appendChild(btn);
    });

    pillsEl.addEventListener("click", function (e) {
      var btn = e.target.closest(".poi-pill");
      if (!btn) return;
      var cat = btn.dataset.cat;
      if (cat === "") {
        activeCategories.clear();
        pillsEl.querySelectorAll(".poi-pill").forEach(function (b) {
          b.classList.toggle("active", b.dataset.cat === "");
        });
      } else {
        pillsEl.querySelector('[data-cat=""]').classList.remove("active");
        if (activeCategories.has(cat)) {
          activeCategories.delete(cat);
          btn.classList.remove("active");
        } else {
          activeCategories.add(cat);
          btn.classList.add("active");
        }
        if (activeCategories.size === 0) {
          pillsEl.querySelector('[data-cat=""]').classList.add("active");
        }
      }
      renderDots(allData);
    });
  }

  /* Load world map + POI data in parallel */
  var WORLD_URL =
    "https://cdn.jsdelivr.net/npm/world-atlas@2/countries-110m.json";
  var POIS_URL = "/api/pois.json";

  setStatus("Loading map…");

  Promise.all([d3.json(WORLD_URL), d3.json(POIS_URL)])
    .then(function (results) {
      var world = results[0];
      var pois = results[1];

      /* Draw ocean sphere */
      var baseLayer = svg.insert("g", ":first-child").attr("class", "poi-base");
      baseLayer
        .append("path")
        .datum({ type: "Sphere" })
        .attr("d", path)
        .attr("fill", OCEAN)
        .attr("stroke", "none");

      /* Graticule grid */
      baseLayer
        .append("path")
        .datum(d3.geoGraticule10())
        .attr("d", path)
        .attr("fill", "none")
        .attr("stroke", GRID)
        .attr("stroke-width", 0.45)
        .attr("vector-effect", "non-scaling-stroke");

      /* Draw land — no country borders */
      var land = topojson.feature(world, world.objects.countries);
      baseLayer
        .selectAll("path.poi-country")
        .data(land.features)
        .enter()
        .append("path")
        .attr("class", "poi-country")
        .attr("d", path)
        .attr("fill", LAND)
        .attr("stroke", "none");

      if (statusEl) statusEl.style.display = "none";

      allData = pois || [];
      buildPills(allData);
      renderDots(allData);
    })
    .catch(function (err) {
      setStatus("Map could not load — check connection and try again.");
      console.warn("poi_map: load error", err);
    });
})();
