"""Tests for yonder/rate_limit.py — per-session rate limiting and daily budget guard.

These tests run without a real server or database and are unaffected by MOCK mode.
"""
from __future__ import annotations

import asyncio
import os
import pytest

# Ensure rate limiting is enabled for tests
os.environ.setdefault("RATE_LIMIT_ENABLED", "true")


def _make_request(sess: str = "test-sess-abc", ip: str = "127.0.0.1", ua: str = "pytest"):
    """Minimal fake Request-like object sufficient for _client_key."""
    class _Headers(dict):
        def get(self, key, default=None):
            return super().get(key.lower(), default)

    class _Client:
        host = ip

    class _FakeReq:
        cookies = {"yv_sess": sess}
        headers = _Headers({"x-forwarded-for": "", "user-agent": ua})
        client = _Client()

    return _FakeReq()


# ── _client_key ───────────────────────────────────────────────────────────────

def test_client_key_always_ip_based_ignores_session():
    """Session cookie must NOT change the key — rotating it cannot bypass limits."""
    from yonder.rate_limit import _client_key
    req = _make_request(sess="mysession123", ip="10.0.0.1", ua="agent/1.0")
    key_with_sess = _client_key(req, "mysession123")
    key_no_sess = _client_key(req, "")
    # Both calls from the same IP+UA produce the same key regardless of session
    assert key_with_sess == key_no_sess
    assert key_with_sess.startswith("ip:")


def test_client_key_different_ip_gives_different_key():
    from yonder.rate_limit import _client_key
    req1 = _make_request(sess="same-sess", ip="10.0.0.1", ua="bot/1.0")
    req2 = _make_request(sess="same-sess", ip="10.0.0.2", ua="bot/1.0")
    # Different IP → different key even with the same session cookie
    assert _client_key(req1, "same-sess") != _client_key(req2, "same-sess")


def test_client_key_stable_across_calls():
    from yonder.rate_limit import _client_key
    req = _make_request(sess="", ip="192.168.1.1", ua="browser/2.0")
    k1 = _client_key(req, "")
    k2 = _client_key(req, "")
    assert k1 == k2
    assert k1.startswith("ip:")


def test_client_key_ua_rotation_gives_same_key():
    """Rotating User-Agent must NOT change the key — UA is excluded from the key."""
    from yonder.rate_limit import _client_key

    class _FakeReq:
        class client:
            host = "10.1.2.3"
        cookies = {}

        def __init__(self, ua: str):
            self._ua = ua

        @property
        def headers(self):
            class _H:
                def get(inner, k, default=None):
                    if k.lower() == "user-agent":
                        return self._ua
                    return default
            return _H()

    req_a = _FakeReq("Mozilla/5.0 (Windows NT 10.0)")
    req_b = _FakeReq("curl/8.1.0")
    assert _client_key(req_a, "") == _client_key(req_b, ""), (
        "UA rotation changed the rate-limit key — it must be excluded"
    )


def test_client_key_xff_ignored_without_trusted_proxy(monkeypatch):
    """Without TRUSTED_PROXY, XFF must be ignored entirely (prevents spoofing)."""
    import yonder.rate_limit as _rl
    monkeypatch.setattr(_rl, "_TRUSTED_PROXY", "")  # no trusted proxy configured

    class _FakeHeaders:
        def get(self, k, default=None):
            if k.lower() == "x-forwarded-for":
                return "1.2.3.4"  # attacker-supplied
            return default

    class _FakeReq:
        client_host = "5.6.7.8"  # real direct connection IP
        headers = _FakeHeaders()

        class client:
            host = "5.6.7.8"

    req = _FakeReq()
    key = _rl._client_key(req, "")
    # Key must be based on the direct IP "5.6.7.8", NOT on the spoofed "1.2.3.4"
    import hashlib
    expected = "ip:" + hashlib.sha256("5.6.7.8".encode()).hexdigest()[:24]
    assert key == expected, f"XFF spoof was honoured without TRUSTED_PROXY: key={key!r}"


def test_client_key_xff_honoured_from_trusted_proxy(monkeypatch):
    """When the direct connection comes from TRUSTED_PROXY, XFF is trusted."""
    import yonder.rate_limit as _rl
    monkeypatch.setattr(_rl, "_TRUSTED_PROXY", "10.0.0.1")

    class _FakeHeaders:
        def get(self, k, default=None):
            if k.lower() == "x-forwarded-for":
                return "203.0.113.5"  # real client IP forwarded by proxy
            return default

    class _FakeReq:
        headers = _FakeHeaders()

        class client:
            host = "10.0.0.1"  # direct connection IS the trusted proxy

    req = _FakeReq()
    key = _rl._client_key(req, "")
    import hashlib
    expected = "ip:" + hashlib.sha256("203.0.113.5".encode()).hexdigest()[:24]
    assert key == expected, f"XFF not used from trusted proxy: key={key!r}"


def _xff_req(direct_ip: str, xff: str):
    class _FakeHeaders:
        def get(self, k, default=None):
            if k.lower() == "x-forwarded-for":
                return xff
            return default

    class _FakeReq:
        headers = _FakeHeaders()

        class client:
            host = direct_ip

    return _FakeReq()


def test_client_key_replit_deployment_separates_visitors(monkeypatch):
    """On Replit deployments, two visitors behind the same proxy get distinct buckets."""
    import yonder.rate_limit as _rl
    monkeypatch.setattr(_rl, "_TRUSTED_PROXY", "")
    monkeypatch.setattr(_rl, "_ON_REPLIT_DEPLOYMENT", True)

    key_a = _rl._client_key(_xff_req("172.16.0.9", "203.0.113.5"), "")
    key_b = _rl._client_key(_xff_req("172.16.0.9", "198.51.100.7"), "")
    assert key_a != key_b, "visitors behind the Replit proxy shared one bucket"


def test_client_key_replit_deployment_uses_rightmost_xff(monkeypatch):
    """Spoofed client-prepended XFF entries must not change the key."""
    import yonder.rate_limit as _rl
    import hashlib
    monkeypatch.setattr(_rl, "_TRUSTED_PROXY", "")
    monkeypatch.setattr(_rl, "_ON_REPLIT_DEPLOYMENT", True)

    # Real client 203.0.113.5; attacker prepends fake IPs before sending.
    honest = _rl._client_key(_xff_req("172.16.0.9", "203.0.113.5"), "")
    spoofed = _rl._client_key(_xff_req("172.16.0.9", "1.2.3.4, 9.9.9.9, 203.0.113.5"), "")
    assert honest == spoofed, "prepending fake XFF entries minted a fresh quota bucket"
    expected = "ip:" + hashlib.sha256("203.0.113.5".encode()).hexdigest()[:24]
    assert honest == expected


def test_client_key_replit_deployment_empty_xff_falls_back(monkeypatch):
    import yonder.rate_limit as _rl
    import hashlib
    monkeypatch.setattr(_rl, "_TRUSTED_PROXY", "")
    monkeypatch.setattr(_rl, "_ON_REPLIT_DEPLOYMENT", True)
    key = _rl._client_key(_xff_req("172.16.0.9", ""), "")
    assert key == "ip:" + hashlib.sha256("172.16.0.9".encode()).hexdigest()[:24]


@pytest.mark.asyncio
async def test_per_visitor_buckets_behind_proxy(monkeypatch):
    """Regression: exhausting one visitor's plan quota must not block another visitor."""
    import yonder.rate_limit as _rl
    monkeypatch.setattr(_rl, "_TRUSTED_PROXY", "")
    monkeypatch.setattr(_rl, "_ON_REPLIT_DEPLOYMENT", True)
    monkeypatch.setattr(_rl, "RATE_LIMIT_ENABLED", True)
    _rl._windows.clear()

    visitor_a = _xff_req("172.16.0.9", "203.0.113.5")
    visitor_b = _xff_req("172.16.0.9", "198.51.100.7")

    for _ in range(_rl.PLAN_LIMIT):
        assert (await _rl.check_plan(visitor_a, "")).allowed
    assert not (await _rl.check_plan(visitor_a, "")).allowed  # A is exhausted
    assert (await _rl.check_plan(visitor_b, "")).allowed, (
        "visitor B was locked out by visitor A's quota — shared bucket regression"
    )
    _rl._windows.clear()


# ── Sliding window ────────────────────────────────────────────────────────────

def test_sliding_window_allows_within_limit():
    from yonder.rate_limit import _windows, _sliding_check
    import time
    _windows.clear()
    now = time.monotonic()
    for _ in range(3):
        result = _sliding_check("testkey", limit=5, window=60, now=now)
        assert result.allowed
    assert result.retry_after == 0


def test_sliding_window_blocks_when_limit_exceeded():
    from yonder.rate_limit import _windows, _sliding_check
    import time
    _windows.clear()
    now = time.monotonic()
    for _ in range(3):
        _sliding_check("bkey", limit=3, window=60, now=now)
    result = _sliding_check("bkey", limit=3, window=60, now=now)
    assert not result.allowed
    assert result.retry_after > 0


def test_sliding_window_evicts_expired_entries():
    from yonder.rate_limit import _windows, _sliding_check
    import time
    _windows.clear()
    now = time.monotonic()
    # Fill up
    for _ in range(3):
        _sliding_check("ekey", limit=3, window=5, now=now)
    # Should be blocked
    assert not _sliding_check("ekey", limit=3, window=5, now=now).allowed
    # Advance time past the window
    later = now + 6
    result = _sliding_check("ekey", limit=3, window=5, now=later)
    assert result.allowed  # old entries evicted


# ── check_search (async) ──────────────────────────────────────────────────────

def test_check_search_mock_bypasses_limit():
    """MOCK mode must always allow — never block real-or-fake provider-free users."""
    import yonder.rate_limit as rl
    # Patch SEARCH_LIMIT to 0 to guarantee a limit hit in non-mock
    orig = rl.SEARCH_LIMIT
    rl.SEARCH_LIMIT = 0
    try:
        req = _make_request()
        result = asyncio.run(rl.check_search(req, "sess", mock=True))
        assert result.allowed
    finally:
        rl.SEARCH_LIMIT = orig


def test_check_search_blocks_after_limit():
    import yonder.rate_limit as rl
    rl._windows.clear()
    orig_limit = rl.SEARCH_LIMIT
    orig_window = rl.SEARCH_WINDOW
    rl.SEARCH_LIMIT = 2
    rl.SEARCH_WINDOW = 60
    try:
        req = _make_request(sess="sess-rl-test")
        r1 = asyncio.run(rl.check_search(req, "sess-rl-test", mock=False))
        r2 = asyncio.run(rl.check_search(req, "sess-rl-test", mock=False))
        r3 = asyncio.run(rl.check_search(req, "sess-rl-test", mock=False))
        assert r1.allowed
        assert r2.allowed
        assert not r3.allowed
        assert r3.retry_after > 0
    finally:
        rl.SEARCH_LIMIT = orig_limit
        rl.SEARCH_WINDOW = orig_window
        rl._windows.clear()


def test_check_plan_blocks_independently_of_search():
    """Plan and search windows are scoped separately."""
    import yonder.rate_limit as rl
    rl._windows.clear()
    req = _make_request(sess="sess-plan-test")
    orig_limit = rl.PLAN_LIMIT
    rl.PLAN_LIMIT = 1
    try:
        r1 = asyncio.run(rl.check_plan(req, "sess-plan-test", mock=False))
        r2 = asyncio.run(rl.check_plan(req, "sess-plan-test", mock=False))
        # Search window is separate — should still allow
        rs = asyncio.run(rl.check_search(req, "sess-plan-test", mock=False))
        assert r1.allowed
        assert not r2.allowed
        assert rs.allowed  # search is a different counter
    finally:
        rl.PLAN_LIMIT = orig_limit
        rl._windows.clear()


# ── Daily budget guard ────────────────────────────────────────────────────────

def test_daily_budget_allows_within_limit():
    import yonder.rate_limit as rl
    rl._budget_count = 0.0
    rl._budget_date = ""
    orig = rl.DAILY_AI_BUDGET
    rl.DAILY_AI_BUDGET = 5
    try:
        for _ in range(5):
            assert rl.check_daily_budget(mock=False, cost=1.0)
        # 6th call should be denied
        assert not rl.check_daily_budget(mock=False, cost=1.0)
    finally:
        rl.DAILY_AI_BUDGET = orig
        rl._budget_count = 0.0


def test_daily_budget_mock_always_allowed():
    import yonder.rate_limit as rl
    rl._budget_count = 10000.0
    rl._budget_date = ""
    orig = rl.DAILY_AI_BUDGET
    rl.DAILY_AI_BUDGET = 1
    try:
        assert rl.check_daily_budget(mock=True, cost=1.0)
    finally:
        rl.DAILY_AI_BUDGET = orig
        rl._budget_count = 0.0


def test_daily_budget_resets_on_new_day():
    import yonder.rate_limit as rl
    rl._budget_date = "1970-01-01"
    rl._budget_count = 9999.0
    orig = rl.DAILY_AI_BUDGET
    rl.DAILY_AI_BUDGET = 10
    try:
        # Today's date is different from 1970-01-01, so counter resets
        result = rl.check_daily_budget(mock=False, cost=1.0)
        assert result  # allowed after reset
        assert rl._budget_count == 1.0
    finally:
        rl.DAILY_AI_BUDGET = orig
        rl._budget_count = 0.0


def test_daily_budget_fractional_cost():
    import yonder.rate_limit as rl
    rl._budget_count = 0.0
    rl._budget_date = ""
    orig = rl.DAILY_AI_BUDGET
    rl.DAILY_AI_BUDGET = 2
    try:
        # 4 calls at 0.5 cost each = 2.0 total → 5th denied
        for _ in range(4):
            assert rl.check_daily_budget(mock=False, cost=0.5)
        assert not rl.check_daily_budget(mock=False, cost=0.5)
    finally:
        rl.DAILY_AI_BUDGET = orig
        rl._budget_count = 0.0


def test_daily_budget_no_overspend_with_fractional_cost():
    """Budget ceiling must not be exceeded: admission uses count+cost > limit.

    E.g. budget=1.0, cost=0.5 → allows exactly 2 calls, not 3.
    The old check (count >= limit) would allow a third call (total 1.5 > budget).
    """
    import yonder.rate_limit as rl
    rl._budget_count = 0.0
    rl._budget_date = ""
    orig = rl.DAILY_AI_BUDGET
    rl.DAILY_AI_BUDGET = 1
    try:
        assert rl.check_daily_budget(mock=False, cost=0.5)   # count → 0.5
        assert rl.check_daily_budget(mock=False, cost=0.5)   # count → 1.0
        # Third call: 1.0 + 0.5 = 1.5 > 1.0 → must be denied, not allowed
        assert not rl.check_daily_budget(mock=False, cost=0.5), (
            "Budget overspend: a third 0.5-cost call was allowed past a budget of 1.0"
        )
        assert rl._budget_count == 1.0  # counter must not have grown
    finally:
        rl.DAILY_AI_BUDGET = orig
        rl._budget_count = 0.0


def test_daily_budget_exact_boundary():
    """A call whose cost exactly equals the remaining balance must be allowed."""
    import yonder.rate_limit as rl
    rl._budget_count = 0.0
    rl._budget_date = ""
    orig = rl.DAILY_AI_BUDGET
    rl.DAILY_AI_BUDGET = 3
    try:
        assert rl.check_daily_budget(mock=False, cost=1.0)  # count → 1.0
        assert rl.check_daily_budget(mock=False, cost=1.0)  # count → 2.0
        # Remaining = 1.0, cost = 1.0: 2.0 + 1.0 = 3.0 == limit → exactly at boundary, allowed
        assert rl.check_daily_budget(mock=False, cost=1.0)
        assert rl._budget_count == 3.0
        # Next call: 3.0 + 1.0 > 3.0 → denied
        assert not rl.check_daily_budget(mock=False, cost=1.0)
    finally:
        rl.DAILY_AI_BUDGET = orig
        rl._budget_count = 0.0


def test_daily_budget_cost_exceeds_full_budget():
    """When a single call's cost exceeds the entire budget, it must be denied even at zero count."""
    import yonder.rate_limit as rl
    rl._budget_count = 0.0
    rl._budget_date = ""
    orig = rl.DAILY_AI_BUDGET
    rl.DAILY_AI_BUDGET = 1
    try:
        # cost=2.0 > budget=1: 0 + 2 > 1 → denied
        assert not rl.check_daily_budget(mock=False, cost=2.0)
        assert rl._budget_count == 0.0  # counter must not have been incremented
    finally:
        rl.DAILY_AI_BUDGET = orig
        rl._budget_count = 0.0


# ── daily_budget_status ───────────────────────────────────────────────────────

def test_daily_budget_status_returns_snapshot():
    import yonder.rate_limit as rl
    rl._budget_count = 42.5
    info = rl.daily_budget_status()
    assert info["used"] == 42.5
    assert "limit" in info
    assert "enabled" in info
    rl._budget_count = 0.0


# ── RATE_LIMIT_ENABLED=false bypass ──────────────────────────────────────────

def test_rate_limit_disabled_flag_bypasses_all():
    import yonder.rate_limit as rl
    orig = rl.RATE_LIMIT_ENABLED
    rl.RATE_LIMIT_ENABLED = False
    rl._windows.clear()
    rl._budget_count = 9999.0
    try:
        req = _make_request(sess="sess-disabled")
        r = asyncio.run(rl.check_search(req, "sess-disabled", mock=False))
        assert r.allowed
        assert rl.check_daily_budget(mock=False, cost=1.0)
    finally:
        rl.RATE_LIMIT_ENABLED = orig
        rl._budget_count = 0.0
        rl._windows.clear()
