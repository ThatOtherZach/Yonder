#!/usr/bin/env python3
"""Smoke-test one newly created share through the published Yonder host.

This intentionally uses the public Escape flow instead of querying production
storage directly.  The POST creates one fresh share record; no save, delete,
or update action is sent for any existing trip.
"""

from __future__ import annotations

import argparse
import html
import sys
import uuid
from dataclasses import dataclass
from datetime import date, timedelta
from html.parser import HTMLParser
from urllib.parse import urlparse

import httpx

DEFAULT_PUBLIC_ORIGIN = "https://yonder.city"
DEFAULT_TIMEOUT_SECONDS = 180.0


class SmokeCheckError(RuntimeError):
    """Raised when the published share flow does not have the expected shape."""


@dataclass(frozen=True)
class ShareLinks:
    """Share targets rendered on the result boarding pass."""

    copied_link: str
    qr_target: str
    qr_image_src: str


class _ShareHTMLParser(HTMLParser):
    """Extract only the user-visible share controls from an explore response."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.copy_links: list[str] = []
        self.qr_targets: list[str] = []
        self.qr_image_srcs: list[str] = []
        self._qr_anchor_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key: value or "" for key, value in attrs}
        share_url = values.get("data-share-url", "").strip()
        if share_url:
            self.copy_links.append(html.unescape(share_url))

        classes = set(values.get("class", "").split())
        if tag == "a" and "bp-qr" in classes:
            target = values.get("href", "").strip()
            if target:
                self.qr_targets.append(html.unescape(target))
            self._qr_anchor_depth = 1
        elif self._qr_anchor_depth:
            self._qr_anchor_depth += 1
            if tag == "img":
                source = values.get("src", "").strip()
                if source:
                    self.qr_image_srcs.append(html.unescape(source))

    def handle_endtag(self, tag: str) -> None:
        if self._qr_anchor_depth:
            self._qr_anchor_depth -= 1


def _one_unique(values: list[str], label: str) -> str:
    unique = list(dict.fromkeys(value for value in values if value))
    if len(unique) != 1:
        raise SmokeCheckError(
            f"Expected one {label}, found {len(unique)}"
            + (f": {unique!r}" if unique else "")
        )
    return unique[0]


def extract_share_links(response_html: str) -> ShareLinks:
    """Extract and validate the copy-link and QR controls from HTML."""

    parser = _ShareHTMLParser()
    parser.feed(response_html)
    return ShareLinks(
        copied_link=_one_unique(parser.copy_links, "copy link"),
        qr_target=_one_unique(parser.qr_targets, "QR target"),
        qr_image_src=_one_unique(parser.qr_image_srcs, "QR image"),
    )


def _origin(value: str) -> tuple[str, str, int | None]:
    parsed = urlparse(value)
    try:
        port = parsed.port
    except ValueError as exc:
        raise SmokeCheckError(f"Invalid URL: {value!r}") from exc
    return parsed.scheme.lower(), parsed.hostname.lower() if parsed.hostname else "", port


def _validate_base_url(value: str) -> str:
    parsed = urlparse(value.rstrip("/"))
    try:
        has_port = parsed.port is not None
    except ValueError:
        has_port = True
    if (
        parsed.scheme.lower() != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or has_port
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
    ):
        raise SmokeCheckError("--base-url must be an HTTPS origin without a path or credentials")
    return f"https://{parsed.hostname.lower()}"


def validate_share_links(links: ShareLinks, public_origin: str) -> None:
    """Ensure both rendered controls point to the same published share path."""

    expected = public_origin.rstrip("/")
    expected_origin = _origin(expected)
    if links.copied_link != links.qr_target:
        raise SmokeCheckError(
            "The copy link and QR target differ: "
            f"{links.copied_link!r} != {links.qr_target!r}"
        )

    parsed = urlparse(links.copied_link)
    if _origin(links.copied_link) != expected_origin or not parsed.path.startswith("/t/"):
        raise SmokeCheckError(
            f"Share link is not a published /t/ URL on {expected}: {links.copied_link!r}"
        )
    if parsed.query or parsed.fragment:
        raise SmokeCheckError(f"Share link unexpectedly has query or fragment: {links.copied_link!r}")

    if not links.qr_image_src.startswith("data:image/"):
        raise SmokeCheckError("QR control did not render an inline image")


def _ensure_ok(response: httpx.Response, action: str) -> None:
    if response.is_error:
        detail = response.text.replace("\n", " ").strip()[-240:]
        raise SmokeCheckError(
            f"{action} returned HTTP {response.status_code}"
            + (f": {detail}" if detail else "")
        )


def _smoke(public_origin: str, timeout_seconds: float) -> str:
    origin = public_origin.rstrip("/")
    run_id = uuid.uuid4().hex
    depart = (date.today() + timedelta(days=45)).isoformat()
    prompt = f"YVR to NRT published share smoke {run_id}"
    headers = {"User-Agent": "yonder-published-share-smoke/1"}
    form = {
        "prompt": prompt,
        "force_mode": "escape",
        "multi_city": "false",
        "return_flight": "false",
        "origin": "YVR",
        "depart": depart,
        "vibe": "adventure",
    }

    with httpx.Client(
        follow_redirects=True,
        timeout=httpx.Timeout(timeout_seconds),
        headers=headers,
    ) as client:
        created = client.post(f"{origin}/explore", data=form)
        _ensure_ok(created, "Creating the smoke share")
        links = extract_share_links(created.text)
        validate_share_links(links, origin)

        # Fetch each independently: this covers both the copied-link and QR
        # navigation paths even though they should resolve to the same URL.
        copied_page = client.get(links.copied_link)
        _ensure_ok(copied_page, "Copied share link")
        qr_page = client.get(links.qr_target)
        _ensure_ok(qr_page, "QR target")

    if copied_page.url != qr_page.url:
        raise SmokeCheckError(
            f"Copied and QR requests resolved differently: {copied_page.url} != {qr_page.url}"
        )

    shared_html = copied_page.text
    required_markers = (
        'class="trip-share-bar',
        'id="share-url-text"',
        'id="copy-share-url"',
        'class="boarding-pass',
    )
    missing = [marker for marker in required_markers if marker not in shared_html]
    if missing:
        raise SmokeCheckError(
            "Shared page is reachable but missing boarding-pass markers: "
            + ", ".join(missing)
        )
    if links.copied_link not in shared_html:
        raise SmokeCheckError("Shared page does not render the copied canonical URL")

    return links.copied_link


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-url",
        default=DEFAULT_PUBLIC_ORIGIN,
        help=f"published origin to check (default: {DEFAULT_PUBLIC_ORIGIN})",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT_SECONDS,
        help=f"HTTP timeout in seconds (default: {DEFAULT_TIMEOUT_SECONDS:g})",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        base_url = _validate_base_url(args.base_url)
        if args.timeout <= 0:
            raise SmokeCheckError("--timeout must be greater than zero")
        share_url = _smoke(base_url, args.timeout)
    except (SmokeCheckError, httpx.HTTPError) as exc:
        print(f"Published share smoke check failed: {exc}", file=sys.stderr)
        return 1

    print(f"Published share smoke check passed: {share_url}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())