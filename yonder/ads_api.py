"""Thin client for the ChatGPT Ads API.

Base URL: https://api.ads.openai.com/v1
Auth:     Bearer <OPENAI_ADS_API_KEY>

Guardrail: ``status`` is always ``"paused"`` — unconditional, never parameterised.
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
# Conservative default: 1 USD in micros
_DEFAULT_BUDGET_MICROS: int = 1_000_000


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
            r.raise_for_status()
            return r.json()

    def _post(self, path: str, data: dict[str, Any]) -> dict[str, Any]:
        with httpx.Client(timeout=30) as client:
            r = client.post(
                f"{self._base_url}{path}", json=data, headers=self._headers()
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
            r.raise_for_status()
            return r.json()

    # ── Ensure-or-create helpers ─────────────────────────────────────────────

    def ensure_campaign(self) -> str:
        """Return an existing campaign ID, or create one and store it."""
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
                    "type": "lifetime",
                    "amount_micros": _DEFAULT_BUDGET_MICROS,
                },
            },
        )
        cid = str(resp["id"])
        _cfg_set("campaign_id", cid)
        log.info("ads_api: created campaign %s", cid)
        return cid

    def ensure_ad_group(self, *, campaign_id: str) -> str:
        """Return an existing ad group ID, or create one and store it."""
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
            },
        )
        gid = str(resp["id"])
        _cfg_set("ad_group_id", gid)
        log.info("ads_api: created ad group %s", gid)
        return gid

    def ensure_brand_image(self) -> str:
        """Upload share_bg.jpg once and reuse the file_id on all subsequent calls."""
        stored = _cfg_get("brand_image_file_id")
        if stored:
            return stored

        img_bytes = _BRAND_IMAGE_PATH.read_bytes()
        resp = self._post_multipart(
            "/files",
            files={"file": ("share_bg.jpg", img_bytes, "image/jpeg")},
            data={"purpose": "ad_creative"},
        )
        fid = str(resp["id"])
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
                "format": "chat_card",
                "status": "paused",  # unconditional — never "active"
                "creative": {
                    "title": title,
                    "body": body,
                    "call_to_action": "Book now",
                    "landing_url": landing_url,
                    "image_file_id": file_id,
                },
            },
        )
        return str(resp["id"])
