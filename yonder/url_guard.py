"""SSRF guard for user-supplied BYOM base URLs.

Validates that a URL uses https and that its host does not resolve to any
loopback, link-local, private (RFC1918), or otherwise non-public address.
DNS is resolved and every returned IP is checked, so hostname tricks and
DNS-rebinding-style aliases for internal addresses are rejected.
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlsplit

# Hosts allowed to use plain http (none by default; keep for explicit opt-ins).
HTTP_ALLOWED_HOSTS: frozenset[str] = frozenset()

PRIVATE_ERROR = "URL points to a private/internal address"


class BYOMUrlError(ValueError):
    """Raised when a BYOM base URL fails SSRF validation."""


def _ip_is_public(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return not (
        ip.is_loopback
        or ip.is_link_local
        or ip.is_private
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def validate_byom_url(url: str) -> str:
    """Validate a BYOM base URL; return the normalized URL or raise BYOMUrlError."""
    url = (url or "").strip()
    if not url:
        raise BYOMUrlError("BYOM URL is empty")
    try:
        parts = urlsplit(url)
    except ValueError as exc:
        raise BYOMUrlError(f"Invalid URL: {exc}") from exc

    host = parts.hostname
    if not host:
        raise BYOMUrlError("URL has no hostname")

    if parts.scheme != "https" and not (
        parts.scheme == "http" and host.lower() in HTTP_ALLOWED_HOSTS
    ):
        raise BYOMUrlError("BYOM URL must use https")

    # Literal IP? Check directly without DNS.
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    if ip is not None:
        if not _ip_is_public(ip):
            raise BYOMUrlError(PRIVATE_ERROR)
        return url

    # Resolve DNS and check every returned address.
    port = parts.port or (443 if parts.scheme == "https" else 80)
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise BYOMUrlError(f"Could not resolve host {host!r}") from exc
    if not infos:
        raise BYOMUrlError(f"Could not resolve host {host!r}")
    for info in infos:
        addr = info[4][0]
        try:
            resolved = ipaddress.ip_address(addr.split("%")[0])
        except ValueError:
            raise BYOMUrlError(PRIVATE_ERROR)
        if not _ip_is_public(resolved):
            raise BYOMUrlError(PRIVATE_ERROR)
    return url
