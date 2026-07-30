"""SSRF validation tests for the BYOM base URL guard."""

import pytest

from yonder.url_guard import BYOMUrlError, validate_byom_url


@pytest.mark.parametrize(
    "url",
    [
        "https://localhost/v1",
        "https://127.0.0.1/v1",
        "https://127.0.0.1:5000/v1",
        "https://10.0.0.5/v1",
        "https://172.16.4.2/v1",
        "https://192.168.1.10/v1",
        "https://169.254.169.254/latest/meta-data",
        "https://[::1]/v1",
        "https://0.0.0.0/v1",
    ],
    ids=lambda u: u,
)
def test_private_and_internal_hosts_rejected(url):
    with pytest.raises(BYOMUrlError):
        validate_byom_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "http://api.openai.com/v1",  # plain http not allowed
        "ftp://api.openai.com/v1",
        "",
        "https:///v1",  # no hostname
    ],
    ids=lambda u: repr(u),
)
def test_bad_scheme_or_missing_host_rejected(url):
    with pytest.raises(BYOMUrlError):
        validate_byom_url(url)


def test_public_https_url_accepted(monkeypatch):
    # Stub DNS so the test is hermetic: host resolves to a public IP.
    import socket

    def fake_getaddrinfo(host, port, **kw):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("104.18.6.192", port))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    assert validate_byom_url("https://api.openai.com/v1") == "https://api.openai.com/v1"


def test_dns_rebinding_to_private_ip_rejected(monkeypatch):
    # Hostname that resolves to a private address must be rejected,
    # even though the hostname string looks harmless.
    import socket

    def fake_getaddrinfo(host, port, **kw):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.168.0.7", port))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    with pytest.raises(BYOMUrlError, match="private/internal"):
        validate_byom_url("https://innocent-looking-host.example.com/v1")


def test_metadata_ip_via_dns_rejected(monkeypatch):
    import socket

    def fake_getaddrinfo(host, port, **kw):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.169.254", port))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    with pytest.raises(BYOMUrlError, match="private/internal"):
        validate_byom_url("https://metadata.example.com/v1")


def test_unresolvable_host_rejected(monkeypatch):
    import socket

    def fake_getaddrinfo(host, port, **kw):
        raise socket.gaierror("NXDOMAIN")

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    with pytest.raises(BYOMUrlError, match="resolve"):
        validate_byom_url("https://does-not-exist.example.invalid/v1")
