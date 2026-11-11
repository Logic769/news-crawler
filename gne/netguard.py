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

    这里只做「请求前」的快速拒绝。真正的 DNS rebinding 防护在
    url_extract._validated_create_connection 里：每次连接都以「解析出的真实 IP」
    再过一遍本函数（IP 字面量 URL），解析与连接之间不留重新解析的窗口。
    该函数是加固手段，不能替代 API Key 访问控制。
    """
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in url):
        raise UnsafeUrlError("URL 不允许包含控制字符")
    try:
        parts = urlsplit(url)
    except ValueError as exc:
        raise UnsafeUrlError(f"URL 格式不合法: {exc}") from exc
    if parts.scheme not in {"http", "https"}:
        raise UnsafeUrlError("只允许 http:// 或 https:// 地址")
    if parts.username is not None or parts.password is not None:
        raise UnsafeUrlError("URL 不允许包含用户信息(user:pass@)")
    host = parts.hostname
    if not host:
        raise UnsafeUrlError("URL 缺少主机名")
    if allow_private:
        return
    if not _host_is_public(host):
        raise UnsafeUrlError("目标地址属于内网、回环或保留地址，已拒绝")
