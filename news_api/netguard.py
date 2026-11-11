"""Backward-compatible re-export of the shared network guard in gne.netguard."""

from gne.netguard import UnsafeUrlError, ensure_url_allowed

__all__ = ["UnsafeUrlError", "ensure_url_allowed"]
