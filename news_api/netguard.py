"""Reject extract targets that point at local or private networks."""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlsplit


class UnsafeUrlError(ValueError):
    """Raised when a user-supplied extraction URL is not allowed."""


def _is_public_ip(value: str) -> bool:
    try:
        return ipaddress.ip_address(value).is_global
    except ValueError:
        return False


def _host_is_public(host: str) -> bool:
    lowered = host.lower().strip("[]")
    if lowered == "localhost" or lowered.endswith(".localhost"):
        return False
    try:
        return ipaddress.ip_address(lowered).is_global
    except ValueError:
        pass
    try:
        infos = socket.getaddrinfo(lowered, None, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise UnsafeUrlError(f"无法解析目标主机: {host}") from exc
    addresses = []
    for info in infos:
        address = str(info[4][0]).split("%", 1)[0]
        if address:
            addresses.append(address)
    if not addresses:
        raise UnsafeUrlError(f"无法解析目标主机: {host}")
    return all(_is_public_ip(address) for address in addresses)


def ensure_url_allowed(url: str, *, allow_private: bool = False) -> None:
    """Validate an extract URL; reject loopback/private/link-local/reserved hosts.

    DNS is checked once before fetching. Redirects are still followed by urllib,
    so this is a hardening measure, not a replacement for API-key access control.
    """
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"}:
        raise UnsafeUrlError("只允许 http:// 或 https:// 地址")
    host = parts.hostname
    if not host:
        raise UnsafeUrlError("URL 缺少主机名")
    if allow_private:
        return
    if not _host_is_public(host):
        raise UnsafeUrlError("目标地址属于内网、回环或保留地址，已拒绝")
