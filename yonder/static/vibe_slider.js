/**
 * Yonder vibe — mandatory hue rainbow under the prompt (Escape + Detour).
 * Bottom field shows vibe name (not hex). Always on; always POSTed.
 */
(function (global) {
  "use strict";

  var VIBES = [
    { id: "chaos",     label: "Chaos",     color: "#e11d48", emoji: "💥" },
    { id: "wild",      label: "Wild",      color: "#f43f5e", emoji: "🦁" },
    { id: "party",     label: "Party",     color: "#ec4899", emoji: "🎉" },
    { id: "romance",   label: "Romance",   color: "#d946ef", emoji: "💕" },
    { id: "neon",      label: "Neon",      color: "#c026d3", emoji: "⚡" },
    { id: "night",     label: "Night",     color: "#9333ea", emoji: "🌙" },
    { id: "soul",      label: "Soul",      color: "#7c3aed", emoji: "🎵" },
    { id: "art",       label: "Art",       color: "#6366f1", emoji: "🎨" },
    { id: "culture",   label: "Culture",   color: "#4f46e5", emoji: "🏛️" },
    { id: "city",      label: "City",      color: "#2563eb", emoji: "🏙️" },
    { id: "future",    label: "Future",    color: "#0284c7", emoji: "🚀" },
    { id: "ocean",     label: "Ocean",     color: "#0891b2", emoji: "🌊" },
    { id: "islands",   label: "Islands",   color: "#0d9488", emoji: "🏝️" },
    { id: "beach",     label: "Beach",     color: "#14b8a6", emoji: "🏖️" },
    { id: "jungle",    label: "Jungle",    color: "#16a34a", emoji: "🌿" },
    { id: "nature",    label: "Nature",    color: "#22c55e", emoji: "🌲" },
    { id: "mountains", label: "Mountains", color: "#65a30d", emoji: "⛰️" },
    { id: "adventure", label: "Adventure", color: "#84cc16", emoji: "🧗" },
    { id: "trains",    label: "Trains",    color: "#ca8a04", emoji: "🚂" },
    { id: "food",      label: "Food",      color: "#eab308", emoji: "🍜" },
    { id: "street",    label: "Street",    color: "#f59e0b", emoji: "🛵" },
    { id: "desert",    label: "Desert",    color: "#f97316", emoji: "🏜️" },
    { id: "sun",       label: "Sun",       color: "#ea580c", emoji: "☀️" },
    { id: "luxury",    label: "Luxury",    color: "#d97706", emoji: "💎" },
    { id: "spa",       label: "Spa",       color: "#b45309", emoji: "🧖" },
    { id: "cozy",      label: "Cozy",      color: "#a16207", emoji: "🧣" },
    { id: "history",   label: "History",   color: "#92400e", emoji: "🏺" },
    { id: "snow",      label: "Snow",      color: "#64748b", emoji: "❄️" },
    { id: "quiet",     label: "Quiet",     color: "#475569", emoji: "🌾" },
    { id: "cheap",     label: "Cheap",     color: "#0f766e", emoji: "💸" },
    // ── new vibes ──────────────────────────────────────────────────────────
    { id: "fire",      label: "Fire",      color: "#dc2626", emoji: "🔥" },
    { id: "ember",     label: "Ember",     color: "#f87171", emoji: "🌋" },
    { id: "rose",      label: "Rose",      color: "#fb7185", emoji: "🌹" },
    { id: "blush",     label: "Blush",     color: "#fda4af", emoji: "🌸" },
    { id: "petal",     label: "Petal",     color: "#f9a8d4", emoji: "🌷" },
    { id: "carnival",  label: "Carnival",  color: "#f472b6", emoji: "🎡" },
    { id: "festival",  label: "Festival",  color: "#db2777", emoji: "🎪" },
    { id: "dream",     label: "Dream",     color: "#c084fc", emoji: "💭" },
    { id: "magic",     label: "Magic",     color: "#a855f7", emoji: "✨" },
    { id: "gothic",    label: "Gothic",    color: "#581c87", emoji: "🦇" },
    { id: "indie",     label: "Indie",     color: "#7e22ce", emoji: "🎸" },
    { id: "lavender",  label: "Lavender",  color: "#c4b5fd", emoji: "💜" },
    { id: "dusk",      label: "Dusk",      color: "#6d28d9", emoji: "🌆" },
    { id: "twilight",  label: "Twilight",  color: "#4338ca", emoji: "🌃" },
    { id: "cosmic",    label: "Cosmic",    color: "#818cf8", emoji: "🌌" },
    { id: "retro",     label: "Retro",     color: "#60a5fa", emoji: "📼" },
    { id: "navy",      label: "Navy",      color: "#1e3a8a", emoji: "🧭" },
    { id: "lakeside",  label: "Lakeside",  color: "#1d4ed8", emoji: "🚣" },
    { id: "fog",       label: "Fog",       color: "#94a3b8", emoji: "🌫️" },
    { id: "flow",      label: "Flow",      color: "#0ea5e9", emoji: "🌬️" },
    { id: "sail",      label: "Sail",      color: "#38bdf8", emoji: "⛵" },
    { id: "reef",      label: "Reef",      color: "#06b6d4", emoji: "🐡" },
    { id: "dive",      label: "Dive",      color: "#0e7490", emoji: "🤿" },
    { id: "tropical",  label: "Tropical",  color: "#10b981", emoji: "🦜" },
    { id: "botanic",   label: "Botanic",   color: "#34d399", emoji: "🌱" },
    { id: "forest",    label: "Forest",    color: "#15803d", emoji: "🌳" },
    { id: "valley",    label: "Valley",    color: "#4ade80", emoji: "🏞️" },
    { id: "meadow",    label: "Meadow",    color: "#86efac", emoji: "🌾" },
    { id: "canopy",    label: "Canopy",    color: "#166534", emoji: "🎋" },
    { id: "savanna",   label: "Savanna",   color: "#a3e635", emoji: "🦒" },
    { id: "wellbeing", label: "Wellbeing", color: "#bef264", emoji: "🧘" },
    { id: "golf",      label: "Golf",      color: "#4d7c0f", emoji: "⛳" },
    { id: "golden",    label: "Golden",    color: "#fcd34d", emoji: "🏅" },
    { id: "dunes",     label: "Dunes",     color: "#fbbf24", emoji: "🐪" },
    { id: "glow",      label: "Glow",      color: "#fb923c", emoji: "🌅" },
    { id: "spice",     label: "Spice",     color: "#c2410c", emoji: "🌶️" },
    { id: "canyon",    label: "Canyon",    color: "#9a3412", emoji: "🏜️" },
    { id: "road",      label: "Road",      color: "#78350f", emoji: "🛣️" },
    { id: "folklore",  label: "Folklore",  color: "#854d0e", emoji: "🧙" },
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
    // ── new vibe traits ────────────────────────────────────────────────────
    fire: {
      adj: "fiery",
      chip: "Fire",
      getaway: "fiery intense few days — heat, energy, never boring",
      stop: "high-energy city stopover",
      escape: "electric city that burns bright",
    },
    ember: {
      adj: "glowing",
      chip: "Ember",
      getaway: "warm glowing few days — soft light and slow evenings",
      stop: "warm-lit city stopover",
      escape: "city with warmth and character",
    },
    rose: {
      adj: "rosy",
      chip: "Rose",
      getaway: "rosy few days — pretty streets, wine, slow mornings",
      stop: "charming rosy-city stopover",
      escape: "picture-perfect city break with colour and flowers",
    },
    blush: {
      adj: "soft",
      chip: "Blush",
      getaway: "soft gentle few days — light city, gentle pace",
      stop: "soft low-key city stopover",
      escape: "gentle city break, easy and unhurried",
    },
    petal: {
      adj: "delicate",
      chip: "Petal",
      getaway: "delicate few days — gardens, markets, soft colour",
      stop: "pretty garden-city stopover",
      escape: "floral delicate city break",
    },
    carnival: {
      adj: "carnival-bright",
      chip: "Carnival",
      getaway: "carnival-bright few days — colour, noise, good chaos",
      stop: "carnival-city stopover, festive and loud",
      escape: "festival city with colour everywhere",
    },
    festival: {
      adj: "festival-ready",
      chip: "Festival",
      getaway: "festival few days — live music, crowds, big energy",
      stop: "festival-city stopover",
      escape: "city with a major festival scene",
    },
    dream: {
      adj: "dreamy",
      chip: "Dream",
      getaway: "dreamy few days — soft focus, wandering, no fixed plan",
      stop: "dreamy wandery city stopover",
      escape: "dreamy unhurried city break",
    },
    magic: {
      adj: "magical",
      chip: "Magic",
      getaway: "magical few days — unexpected corners, wonder",
      stop: "magical city stopover full of surprises",
      escape: "city that feels like a different world",
    },
    gothic: {
      adj: "gothic",
      chip: "Gothic",
      getaway: "gothic few days — dark spires, old stone, moody atmosphere",
      stop: "gothic-city stopover",
      escape: "moody atmospheric city with history and shadows",
    },
    indie: {
      adj: "indie",
      chip: "Indie",
      getaway: "indie few days — record stores, weird bars, local zines",
      stop: "indie-scene city stopover",
      escape: "city with a thriving underground scene",
    },
    lavender: {
      adj: "lavender-calm",
      chip: "Lavender",
      getaway: "lavender-calm few days — countryside, fields, gentle air",
      stop: "calm rural-edge city stopover",
      escape: "calm pastoral destination, soft and scenic",
    },
    dusk: {
      adj: "dusky",
      chip: "Dusk",
      getaway: "dusky few days — golden hour city, rooftops at sunset",
      stop: "dusky rooftop-city stopover",
      escape: "city best seen from above at golden hour",
    },
    twilight: {
      adj: "twilight",
      chip: "Twilight",
      getaway: "twilight few days — blue-hour streets, atmospheric evenings",
      stop: "twilight atmospheric city stopover",
      escape: "city that glows at twilight",
    },
    cosmic: {
      adj: "cosmic",
      chip: "Cosmic",
      getaway: "cosmic few days — stargazing, remote skies, big universe vibes",
      stop: "cosmic dark-sky stopover",
      escape: "remote destination for stars and silence",
    },
    retro: {
      adj: "retro",
      chip: "Retro",
      getaway: "retro few days — vintage scenes, old cinemas, 70s diners",
      stop: "retro-flavoured city stopover",
      escape: "city dripping with retro nostalgia",
    },
    navy: {
      adj: "nautical",
      chip: "Navy",
      getaway: "nautical few days — harbours, tall ships, sea air",
      stop: "harbour-city nautical stopover",
      escape: "maritime city with working docks and sea history",
    },
    lakeside: {
      adj: "lakeside",
      chip: "Lakeside",
      getaway: "lakeside few days — still water, morning mist, rowing",
      stop: "lakeside city stopover",
      escape: "lakeside destination, calm and scenic",
    },
    fog: {
      adj: "misty",
      chip: "Fog",
      getaway: "misty moody few days — grey skies, atmospheric streets",
      stop: "moody foggy-city stopover",
      escape: "atmospheric grey city with character",
    },
    flow: {
      adj: "free-flowing",
      chip: "Flow",
      getaway: "free-flowing few days — no plan, follow the current",
      stop: "easy free-flowing city stopover",
      escape: "city you just drift through",
    },
    sail: {
      adj: "breezy",
      chip: "Sail",
      getaway: "breezy coastal few days — open water and salt air",
      stop: "sailing-town stopover",
      escape: "coastal sailing town long weekend",
    },
    reef: {
      adj: "reef-bright",
      chip: "Reef",
      getaway: "reef few days — snorkelling, coral, vivid underwater colour",
      stop: "reef-coast stopover",
      escape: "reef destination, clear warm water",
    },
    dive: {
      adj: "deep-dive",
      chip: "Dive",
      getaway: "deep-dive few days — into a culture, not just the surface",
      stop: "deep-cut city stopover, off the tourist trail",
      escape: "city that rewards going deeper",
    },
    tropical: {
      adj: "tropical",
      chip: "Tropical",
      getaway: "tropical few days — lush, warm, colourful markets",
      stop: "tropical city stopover",
      escape: "tropical destination with heat and colour",
    },
    botanic: {
      adj: "botanic",
      chip: "Botanic",
      getaway: "botanic few days — gardens, greenhouses, slow green walks",
      stop: "garden-city botanic stopover",
      escape: "city famous for its botanical gardens and green spaces",
    },
    forest: {
      adj: "forested",
      chip: "Forest",
      getaway: "forest few days — tall trees, cool shade, silence",
      stop: "forest-edge city stopover",
      escape: "forest destination, far from the crowds",
    },
    valley: {
      adj: "valley",
      chip: "Valley",
      getaway: "valley few days — green hills, rivers, small towns",
      stop: "valley-town stopover",
      escape: "scenic valley destination, easy pace",
    },
    meadow: {
      adj: "meadow-fresh",
      chip: "Meadow",
      getaway: "meadow-fresh few days — wildflowers, open fields, slow pace",
      stop: "pastoral town stopover",
      escape: "countryside destination, open and green",
    },
    canopy: {
      adj: "canopy",
      chip: "Canopy",
      getaway: "canopy few days — treetop walks, dense green, birdsong",
      stop: "rainforest-edge city stopover",
      escape: "lush canopy destination, off the beaten path",
    },
    savanna: {
      adj: "savanna",
      chip: "Savanna",
      getaway: "savanna few days — wide open skies, dry grass, wildlife",
      stop: "safari-edge city stopover",
      escape: "savanna or bush destination",
    },
    wellbeing: {
      adj: "wellbeing-focused",
      chip: "Wellbeing",
      getaway: "wellbeing few days — yoga, slow mornings, healthy food",
      stop: "wellness retreat stopover",
      escape: "wellbeing destination, rest and reset",
    },
    golf: {
      adj: "links",
      chip: "Golf",
      getaway: "golf trip few days — fairways, clubhouses, coastal links",
      stop: "golf-city stopover",
      escape: "golf destination with great courses",
    },
    golden: {
      adj: "golden",
      chip: "Golden",
      getaway: "golden few days — sun-soaked plazas, late lunches, good wine",
      stop: "golden-hour city stopover",
      escape: "golden warm city break with long evenings",
    },
    dunes: {
      adj: "dune-swept",
      chip: "Dunes",
      getaway: "dune few days — sand, wind, wide desert skies",
      stop: "desert-dunes-edge stopover",
      escape: "sand dune destination, hot and quiet",
    },
    glow: {
      adj: "glowing-warm",
      chip: "Glow",
      getaway: "glowing warm few days — amber light, outdoor evenings",
      stop: "warm glowing city stopover",
      escape: "city that glows in the evening light",
    },
    spice: {
      adj: "spiced",
      chip: "Spice",
      getaway: "spiced few days — aromatic markets, bold flavours, colour",
      stop: "spice-market city stopover",
      escape: "city of spice markets and aromatic streets",
    },
    canyon: {
      adj: "canyon",
      chip: "Canyon",
      getaway: "canyon few days — red rock, dramatic cliffs, big sky",
      stop: "canyon-country city stopover",
      escape: "canyon or red-rock destination",
    },
    road: {
      adj: "road-trip",
      chip: "Road",
      getaway: "road-trip few days — long drives, roadside stops, open highway",
      stop: "road-trip hub stopover",
      escape: "drive-to destination, road the point not just the end",
    },
    folklore: {
      adj: "folkloric",
      chip: "Folklore",
      getaway: "folkloric few days — old traditions, village festivals, craft",
      stop: "folk-culture city stopover",
      escape: "city steeped in folklore and living tradition",
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
    // Do not seed invent from prior Save destinations (pollutes the board).
    // Ranking still uses Saves for which *pattern* pills float higher.
    var seedCodes = [];

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

    var nameRow = host.querySelector(".vibe-name-row");

    function syncUi() {
      var vibe = currentVibe();
      var show = vibe.color;
      hueCursor.style.left = h * 100 + "%";
      hueCursor.style.backgroundColor = show;
      nameOut.textContent = vibe.label;
      nameOut.style.color = "#ffffff";
      nameSwatch.textContent = vibe.emoji || "";
      if (nameRow) {
        nameRow.style.backgroundColor = show;
        nameRow.style.borderColor = show;
      }
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
