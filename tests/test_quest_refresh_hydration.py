"""Eager Quest refresh persistence — Task 567.

Confirms a *finished* eager Quest survives a page refresh without a new AI
call, by executing the template's real JS (extracted verbatim from
yonder/templates/index.html) inside Node with a minimal DOM/sessionStorage
stub:

  1. When the background job poller (bootQuestJobPoll) sees status=done, the
     rendered quest HTML is persisted to sessionStorage keyed by
     prompt + origin — exactly like the manual Plan button path.
  2. On the next page load, hydrateQuestFromStorage() restores that cached
     HTML into #quest-results without any fetch (no /api/quest/plan call).
  3. hydrateQuestFromStorage() is a no-op while a data-quest-job placeholder
     is present — hydration never clobbers a still-pending job panel.

Plus rendered-source assertions that the guards live where they must.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

_TEMPLATE = Path(__file__).parent.parent / "yonder" / "templates" / "index.html"

_STORAGE_MARK = "// ── Quest sessionStorage persistence"
_STORAGE_END = "// Returns the redesigned Quest initial card HTML"
_POLL_MARK = "// ── Eager Quest: poll the background job"
_POLL_END = "// ── Detour on-demand button"


def _extract_js() -> str:
    src = _TEMPLATE.read_text(encoding="utf-8")
    a0 = src.index(_STORAGE_MARK)
    a1 = src.index(_STORAGE_END, a0)
    b0 = src.index(_POLL_MARK)
    b1 = src.index(_POLL_END, b0)
    return src[a0:a1] + "\n" + src[b0:b1]


_HARNESS_PRELUDE = r"""
"use strict";
const assert = require("assert");

// ── sessionStorage stub ────────────────────────────────────────────────────
const _store = {};
global.sessionStorage = {
  getItem: (k) => (k in _store ? _store[k] : null),
  setItem: (k, v) => { _store[k] = String(v); },
  removeItem: (k) => { delete _store[k]; },
};

// ── Minimal DOM stub ───────────────────────────────────────────────────────
// Elements match child selectors by substring against their innerHTML — good
// enough for the class / attribute probes the quest JS performs.
function makeEl(id) {
  return {
    id: id,
    innerHTML: "",
    disabled: false,
    removeAttribute: function () {},
    querySelector: function (sel) {
      const html = this.innerHTML || "";
      for (let part of sel.split(",")) {
        part = part.trim();
        if (part[0] === ".") {
          const cls = (part.match(/^\.([\w-]+)/) || [])[1];
          if (cls && html.indexOf(cls) !== -1) return { _matched: part };
        } else if (part[0] === "[") {
          const attr = part.slice(1, part.indexOf("]"));
          if (html.indexOf(attr) !== -1) {
            const m = html.match(new RegExp(attr + '="([^"]*)"'));
            return { getAttribute: (a) => (a === attr && m ? m[1] : null) };
          }
        }
      }
      return null;
    },
  };
}

const byId = {
  "quest-results": makeEl("quest-results"),
  "escape-results": makeEl("escape-results"),
  "detour-results": makeEl("detour-results"),
  "explore-origin": { value: "yvr " },
};

global.document = {
  getElementById: (id) => byId[id] || null,
  querySelector: function (sel) {
    sel = sel.trim();
    if (sel[0] === "[") {
      // document-level attribute probe: search quest-results
      const hit = byId["quest-results"].querySelector(sel);
      if (hit) return hit;
      if (sel === "[data-search-prompt]" && global.__searchPromptEl) {
        return global.__searchPromptEl;
      }
      return null;
    }
    if (sel[0] === "#") {
      const m = sel.match(/^#([\w-]+)\s*(.*)$/);
      const el = byId[m[1]];
      if (!el) return null;
      if (!m[2]) return el;
      return el.querySelector(m[2]);
    }
    return null;
  },
};

// Textarea prompt + hooks referenced by the extracted code.
var ta = { value: "temples and street food" };
function bootPlanQuestButton() { global.__bootPlanCalls = (global.__bootPlanCalls || 0) + 1; }

// fetch recorder — every network call is logged; tests assert on the list.
global.__fetchCalls = [];
global.__fetchResponse = null;
global.fetch = function (url) {
  global.__fetchCalls.push(String(url));
  return Promise.resolve({ json: () => Promise.resolve(global.__fetchResponse) });
};

// Synchronous timers so the poller's tick runs without waiting.
global.setTimeout = function (fn) { fn(); };

async function drain() { for (let i = 0; i < 20; i++) await Promise.resolve(); }
"""

_SCENARIOS = {
    # 1) Poller done-path persists HTML keyed by prompt+origin, using only the
    #    status endpoint — never /api/quest/plan.
    "poller_persists": r"""
(async () => {
  const QUEST_HTML = '<div class="card" id="quest-results-card"><div class="boarding-pass">HAN → BKK</div></div>';
  byId["quest-results"].innerHTML = '<p data-quest-job="job-abc123">Planning…</p>';
  global.__fetchResponse = { status: "done", ok: true, html: QUEST_HTML };

  bootQuestJobPoll();
  await drain();

  assert.deepStrictEqual(__fetchCalls, ["/api/quest/status/job-abc123"],
    "poller must hit only the status endpoint, got: " + JSON.stringify(__fetchCalls));
  const key = _questStorageKey(ta.value, "yvr ");
  assert.ok(key.indexOf("temples and street food") !== -1, "key carries the prompt");
  assert.ok(key.indexOf("YVR") !== -1, "key carries the normalized origin");
  assert.strictEqual(sessionStorage.getItem(key), QUEST_HTML,
    "finished eager quest HTML must be saved under the prompt+origin key");
  assert.strictEqual(byId["quest-results"].innerHTML, QUEST_HTML);
  console.log("PASS poller_persists");
})().catch((e) => { console.error(e); process.exit(1); });
""",
    # 2) Refresh: hydrateQuestFromStorage restores the cached card with zero
    #    network calls.
    "hydrate_restores": r"""
(async () => {
  const QUEST_HTML = '<div class="card" id="quest-results-card"><div class="boarding-pass">HAN → BKK</div></div>';
  _store[_questStorageKey("temples and street food", "YVR")] = QUEST_HTML;
  // Simulated post-refresh page: escape results rendered, quest panel empty.
  byId["escape-results"].innerHTML = '<div class="boarding-pass">YVR → NRT</div>';
  byId["quest-results"].innerHTML = "";

  hydrateQuestFromStorage();

  assert.strictEqual(byId["quest-results"].innerHTML, QUEST_HTML,
    "hydration must restore the cached quest HTML after refresh");
  assert.deepStrictEqual(__fetchCalls, [],
    "hydration must not make any network call (no new AI plan)");
  assert.ok((global.__bootPlanCalls || 0) >= 1, "post-inject hooks must run");
  console.log("PASS hydrate_restores");
})().catch((e) => { console.error(e); process.exit(1); });
""",
    # 2b) Falls back to the [data-search-prompt] element when the textarea is
    #     empty (server-rendered results after refresh).
    "hydrate_prompt_fallback": r"""
(async () => {
  const QUEST_HTML = '<div class="card" id="quest-results-card"><div class="quest-idea-card">idea</div></div>';
  _store[_questStorageKey("beach reset", "YVR")] = QUEST_HTML;
  ta.value = "";
  global.__searchPromptEl = { dataset: { searchPrompt: "beach reset" } };
  byId["escape-results"].innerHTML = '<div class="boarding-pass">YVR → NRT</div>';
  byId["quest-results"].innerHTML = "";

  hydrateQuestFromStorage();

  assert.strictEqual(byId["quest-results"].innerHTML, QUEST_HTML,
    "hydration must fall back to data-search-prompt when the textarea is empty");
  console.log("PASS hydrate_prompt_fallback");
})().catch((e) => { console.error(e); process.exit(1); });
""",
    # 3) A pending eager job placeholder blocks hydration entirely.
    "hydrate_skips_pending_job": r"""
(async () => {
  const PLACEHOLDER = '<p data-quest-job="job-live">Planning your Quest…</p>';
  _store[_questStorageKey("temples and street food", "YVR")] = '<div class="boarding-pass">stale</div>';
  byId["escape-results"].innerHTML = '<div class="boarding-pass">YVR → NRT</div>';
  byId["quest-results"].innerHTML = PLACEHOLDER;

  hydrateQuestFromStorage();

  assert.strictEqual(byId["quest-results"].innerHTML, PLACEHOLDER,
    "hydration must NOT clobber a still-pending data-quest-job placeholder");
  assert.deepStrictEqual(__fetchCalls, []);
  console.log("PASS hydrate_skips_pending_job");
})().catch((e) => { console.error(e); process.exit(1); });
""",
    # Already-injected quest (e.g. live Plan finished) is never overwritten.
    "hydrate_skips_injected": r"""
(async () => {
  const LIVE = '<div class="card"><div class="boarding-pass">live result</div></div>';
  _store[_questStorageKey("temples and street food", "YVR")] = '<div class="boarding-pass">stale</div>';
  byId["escape-results"].innerHTML = '<div class="boarding-pass">YVR → NRT</div>';
  byId["quest-results"].innerHTML = LIVE;

  hydrateQuestFromStorage();

  assert.strictEqual(byId["quest-results"].innerHTML, LIVE,
    "hydration must not overwrite an already-injected quest card");
  console.log("PASS hydrate_skips_injected");
})().catch((e) => { console.error(e); process.exit(1); });
""",
}


@pytest.fixture(scope="module")
def extracted_js() -> str:
    js = _extract_js()
    # Sanity: the blocks we run must contain the functions under test.
    for fn in ("_questStorageKey", "_saveQuestToStorage", "hydrateQuestFromStorage", "bootQuestJobPoll"):
        assert f"function {fn}" in js, f"{fn} missing from extracted template JS"
    return js


def _run_node(tmp_path: Path, extracted_js: str, scenario: str) -> str:
    node = shutil.which("node")
    if not node:
        pytest.skip("node not available")
    script = tmp_path / f"{scenario}.js"
    script.write_text(
        _HARNESS_PRELUDE + "\n" + extracted_js + "\n" + _SCENARIOS[scenario],
        encoding="utf-8",
    )
    proc = subprocess.run([node, str(script)], capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, f"node scenario {scenario} failed:\n{proc.stderr}\n{proc.stdout}"
    assert f"PASS {scenario}" in proc.stdout
    return proc.stdout


class TestEagerQuestRefreshPersistence:
    def test_poller_done_persists_keyed_by_prompt_origin(self, tmp_path, extracted_js):
        _run_node(tmp_path, extracted_js, "poller_persists")

    def test_hydrate_restores_after_refresh_without_ai_call(self, tmp_path, extracted_js):
        _run_node(tmp_path, extracted_js, "hydrate_restores")

    def test_hydrate_falls_back_to_search_prompt_dataset(self, tmp_path, extracted_js):
        _run_node(tmp_path, extracted_js, "hydrate_prompt_fallback")

    def test_hydrate_skipped_while_job_placeholder_present(self, tmp_path, extracted_js):
        _run_node(tmp_path, extracted_js, "hydrate_skips_pending_job")

    def test_hydrate_never_overwrites_injected_quest(self, tmp_path, extracted_js):
        _run_node(tmp_path, extracted_js, "hydrate_skips_injected")


class TestTemplateSourceGuards:
    """Rendered-source guards: the persistence hooks live where they must."""

    def test_poller_done_branch_saves_to_storage(self, extracted_js):
        b0 = extracted_js.index("function bootQuestJobPoll")
        poll_body = extracted_js[b0:]
        done_idx = poll_body.index('json.status === "done"')
        save_idx = poll_body.index("_saveQuestToStorage", done_idx)
        assert save_idx > done_idx, (
            "bootQuestJobPoll must persist the quest HTML in its done/ok branch"
        )

    def test_hydrate_guards_precede_storage_read(self, extracted_js):
        h0 = extracted_js.index("function hydrateQuestFromStorage")
        end = extracted_js.index("function bootQuestJobPoll")
        body = extracted_js[h0:end]
        job_guard = body.index("[data-quest-job]")
        injected_guard = body.index(".boarding-pass, .quest-idea-card")
        load = body.index("_loadQuestFromStorage")
        assert job_guard < load and injected_guard < load, (
            "hydrateQuestFromStorage must check the pending-job placeholder and "
            "already-injected card BEFORE reading sessionStorage"
        )

    def test_storage_key_combines_prompt_and_origin(self, extracted_js):
        m = re.search(r"function _questStorageKey\(prompt, origin\)\s*{([^}]*)}", extracted_js)
        assert m, "_questStorageKey missing"
        body = m.group(1)
        assert "prompt" in body and "origin" in body, (
            "quest storage key must be derived from BOTH prompt and origin"
        )
