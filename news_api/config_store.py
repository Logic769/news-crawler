"""Read/write access to the crawler's sources.local.json.

The crawler keeps its site list in a private JSON file (never committed).  The
admin console must edit it through the API instead of asking operators to edit
the file on the server.  Two rules make that safe:

* never lose unknown keys: the file contains per-site pagination/api blocks that
  the admin form does not understand; we edit only the fields we own.
* never leak secrets: API-source headers can contain tokens, so list responses
  only expose a boolean "has_api" and never echo headers.
"""

from __future__ import annotations

import datetime as dt
import errno
import json
import os
import shutil
import threading
from pathlib import Path
from typing import Any

_WRITE_LOCK = threading.Lock()

# Fields the admin console may edit.  Everything else stays byte-for-byte intact.
_EDITABLE = (
    "name",
    "list_url",
    "category",
    "region",
    "timezone",
    "render",
    "enabled",
    "item_xpath",
    "title_xpath",
    "url_xpath",
    "date_xpath",
)


def _backup_dir() -> Path:
    raw = os.environ.get("NEWS_BACKUP_DIR", "").strip()
    if raw:
        return Path(raw)
    return Path("backups")


def _read(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"sources": []}
    if not isinstance(data, dict) or not isinstance(data.get("sources"), list):
        raise ValueError("sources.local.json 结构不合法：顶层必须含 sources 数组")
    return data


def _write(path: Path, data: dict[str, Any]) -> None:
    """Atomic write + timestamped backup; keeps a failed save from truncating config."""
    backup_root = _backup_dir()
    if path.exists():
        backup_root.mkdir(parents=True, exist_ok=True)
        stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        shutil.copy2(path, backup_root / f"sources_local_{stamp}.json")
    text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    try:
        os.replace(tmp, path)
    except OSError as exc:
        # Docker 单文件 bind mount（./sources.local.json:/app/sources.local.json）
        # 的挂载点本身不能被 rename 覆盖，Linux 返回 EBUSY。此时退回原地写：
        # 上面已先落过备份，失败也不会让配置无迹可寻。
        if exc.errno not in (errno.EBUSY, errno.EXDEV):
            raise
        path.write_text(text, encoding="utf-8")
        tmp.unlink(missing_ok=True)


def _public(site: dict[str, Any]) -> dict[str, Any]:
    api = site.get("api")
    return {
        "name": str(site.get("name") or ""),
        "list_url": str(site.get("list_url") or ""),
        "category": str(site.get("category") or ""),
        "region": str(site.get("region") or ""),
        "timezone": str(site.get("timezone") or ""),
        "render": bool(site.get("render", False)),
        "enabled": bool(site.get("enabled", True)),
        "item_xpath": str(site.get("item_xpath") or ""),
        "title_xpath": str(site.get("title_xpath") or ""),
        "url_xpath": str(site.get("url_xpath") or ""),
        "date_xpath": str(site.get("date_xpath") or ""),
        "has_api": isinstance(api, dict) and bool(api),
    }


def list_sites(path: str | Path) -> list[dict[str, Any]]:
    data = _read(Path(path))
    return [_public(site) for site in data["sources"] if isinstance(site, dict)]


def get_site(path: str | Path, name: str) -> dict[str, Any] | None:
    for site in list_sites(path):
        if site["name"] == name:
            return site
    return None


def create_site(path: str | Path, payload: dict[str, Any]) -> dict[str, Any]:
    with _WRITE_LOCK:
        data = _read(Path(path))
        name = str(payload.get("name") or "").strip()
        if not name:
            raise ValueError("站点 name 不能为空")
        if any(str(site.get("name") or "") == name for site in data["sources"]):
            raise ValueError(f"站点已存在: {name}")
        site = {key: payload[key] for key in _EDITABLE if key in payload}
        site["name"] = name
        site.setdefault("timezone", data.get("timezone") or "Asia/Shanghai")
        site.setdefault("enabled", True)
        data["sources"].append(site)
        _write(Path(path), data)
        return _public(site)


def update_site(path: str | Path, name: str, payload: dict[str, Any]) -> dict[str, Any] | None:
    with _WRITE_LOCK:
        data = _read(Path(path))
        for index, site in enumerate(data["sources"]):
            if not isinstance(site, dict) or str(site.get("name") or "") != name:
                continue
            for key in _EDITABLE:
                if key == "name":
                    continue
                if key in payload:
                    site[key] = payload[key]
            data["sources"][index] = site
            _write(Path(path), data)
            return _public(site)
    return None


def delete_site(path: str | Path, name: str) -> bool:
    with _WRITE_LOCK:
        data = _read(Path(path))
        remaining = [
            site
            for site in data["sources"]
            if not (isinstance(site, dict) and str(site.get("name") or "") == name)
        ]
        if len(remaining) == len(data["sources"]):
            return False
        data["sources"] = remaining
        _write(Path(path), data)
        return True
