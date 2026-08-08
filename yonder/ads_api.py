"""Thin client for the ChatGPT Ads API.

Base URL: https://api.ads.openai.com/v1
Auth:     Bearer <OPENAI_ADS_API_KEY>

Guardrail: ``status`` is always ``"paused"`` — unconditional, never parameterised.

Payload shapes follow the OpenAI Ads API reference (2025):
  POST /campaigns        → budget.lifetime_spend_limit_micros (min 1 000 000)
  POST /ad_groups        → bidding_config.billing_event_type + max_bid_micros
  POST /ads              → name (required), creative.type, creative.target_url,
                           creative.file_id
  POST /upload           → multipart file; response key is ``file_id``
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import httpx

from yonder.db import get_conn

log = logging.getLogger(__name__)

_ADS_BASE = "https://api.ads.openai.com/v1"
_CAMPAIGN_NAME = "Yonder Destinations"
_AD_GROUP_NAME = "Yonder Destination Ads"
_BRAND_IMAGE_PATH = Path(__file__).parent / "static" / "share_bg.jpg"
# Minimum allowed by the API is 1 000 000 (= $1.00 in micros)
_DEFAULT_BUDGET_MICROS: int = 1_000_000
# Default max bid: 60 000 micros = $0.06 CPM (impression billing)
_DEFAULT_MAX_BID_MICROS: int = 60_000


def _cfg_get(key: str) -> str | None:
    """Read a value from ad_pipeline_config."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT value FROM ad_pipeline_config WHERE key = %s", (key,)
        ).fetchone()
    return str(row["value"]) if row else None


def _cfg_set(key: str, value: str) -> None:
    """Write (upsert) a value into ad_pipeline_config."""
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO ad_pipeline_config (key, value) VALUES (%s, %s) "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
            (key, value),
        )
        conn.commit()


class AdsApiClient:
    """Minimal ChatGPT Ads API wrapper.

    All methods are synchronous (called from thread-pool executors or tests).
    The base_url can be overridden in tests to point at a stub server.
    """

    def __init__(self, *, api_key: str, base_url: str = _ADS_BASE) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")

    # ── HTTP helpers ────────────────────────────────────────────────────────

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

    def _get(self, path: str) -> dict[str, Any]:
        with httpx.Client(timeout=30) as client:
            r = client.get(f"{self._base_url}{path}", headers=self._headers())
            if r.is_error:
                log.error(
                    "ads_api: GET %s → %d  body=%s",
                    path, r.status_code, r.text[:500],
                )
            r.raise_for_status()
            return r.json()

    def _post(self, path: str, data: dict[str, Any]) -> dict[str, Any]:
        with httpx.Client(timeout=30) as client:
            r = client.post(
                f"{self._base_url}{path}", json=data, headers=self._headers()
            )
            if r.is_error:
                log.error(
                    "ads_api: POST %s → %d  body=%s",
                    path, r.status_code, r.text[:500],
                )
            r.raise_for_status()
            return r.json()

    def _post_multipart(
        self, path: str, *, files: dict[str, Any], data: dict[str, Any]
    ) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {self._api_key}"}
        with httpx.Client(timeout=60) as client:
            r = client.post(
                f"{self._base_url}{path}",
                headers=headers,
                files=files,
                data=data,
            )
            if r.is_error:
                log.error(
                    "ads_api: POST(multipart) %s → %d  body=%s",
                    path, r.status_code, r.text[:500],
                )
            r.raise_for_status()
            return r.json()

    # ── Ensure-or-create helpers ─────────────────────────────────────────────

    def ensure_campaign(self) -> str:
        """Return an existing campaign ID, or create one and store it.

        Budget uses ``lifetime_spend_limit_micros`` as required by the API
        (minimum 1 000 000).  The old ``budget.type`` / ``budget.amount_micros``
        shape was rejected with 400 Unknown parameter.
        """
        stored = _cfg_get("campaign_id")
        if stored:
            try:
                self._get(f"/campaigns/{stored}")
                return stored
            except Exception:
                pass  # stored ID no longer valid — fall through to create

        resp = self._post(
            "/campaigns",
            {
                "name": _CAMPAIGN_NAME,
                "status": "paused",
                "budget": {
                    "lifetime_spend_limit_micros": _DEFAULT_BUDGET_MICROS,
                },
            },
        )
        cid = str(resp["id"])
        _cfg_set("campaign_id", cid)
        log.info("ads_api: created campaign %s", cid)
        return cid

    def ensure_ad_group(self, *, campaign_id: str) -> str:
        """Return an existing ad group ID, or create one and store it.

        ``bidding_config`` is required by the API (billing_event_type +
        max_bid_micros).  Impression billing at a $0.06 CPM default.
        """
        stored = _cfg_get("ad_group_id")
        if stored:
            try:
                self._get(f"/ad_groups/{stored}")
                return stored
            except Exception:
                pass

        resp = self._post(
            "/ad_groups",
            {
                "campaign_id": campaign_id,
                "name": _AD_GROUP_NAME,
                "status": "paused",
                "bidding_config": {
                    "billing_event_type": "impression",
                    "max_bid_micros": _DEFAULT_MAX_BID_MICROS,
                },
            },
        )
        gid = str(resp["id"])
        _cfg_set("ad_group_id", gid)
        log.info("ads_api: created ad group %s", gid)
        return gid

    def ensure_brand_image(self) -> str:
        """Upload share_bg.jpg once and reuse the file_id on all subsequent calls.

        The upload endpoint is ``POST /upload`` (not ``/files``).  The response
        key is ``file_id`` (not ``id``).
        """
        stored = _cfg_get("brand_image_file_id")
        if stored:
            return stored

        img_bytes = _BRAND_IMAGE_PATH.read_bytes()
        resp = self._post_multipart(
            "/upload",
            files={"file": ("share_bg.jpg", img_bytes, "image/jpeg")},
            data={},
        )
        fid = str(resp["file_id"])
        _cfg_set("brand_image_file_id", fid)
        log.info("ads_api: uploaded brand image, file_id=%s", fid)
        return fid

    def create_ad(
        self,
        *,
        ad_group_id: str,
        title: str,
        body: str,
        landing_url: str,
        file_id: str,
    ) -> str:
        """Create a paused chat_card ad.

        Status is **always** ``"paused"`` — this guardrail is unconditional and
        can never be overridden by the caller.

        Field mapping (API schema):
          - ``creative.type``       = "chat_card"  (not top-level ``format``)
          - ``creative.target_url`` = landing URL  (not ``landing_url``)
          - ``creative.file_id``    = image file   (not ``image_file_id``)
          - ``name``                = required ad name (not shown to end users)
        """
        # Enforce creative character limits
        title = title[:50]
        body = body[:100]
        if len(title) < 3:
            title = (title + "   ")[:3]

        resp = self._post(
            "/ads",
            {
                "ad_group_id": ad_group_id,
                "name": title,  # required; not shown to end users
                "status": "paused",  # unconditional — never "active"
                "creative": {
                    "type": "chat_card",
                    "title": title,
                    "body": body,
                    "target_url": landing_url,
                    "file_id": file_id,
                },
            },
        )
        return str(resp["id"])
