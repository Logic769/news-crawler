"""Shared helpers for the offline GNE regression baseline."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from gne import GeneralNewsExtractor, NoContentException

ROOT = Path(__file__).resolve().parent.parent
SAMPLE_DIR = ROOT / "tests"
BASELINE_PATH = Path(__file__).resolve().parent / "baseline.json"


def case_key(path: Path) -> str:
    """仓库内相对路径（posix 风格），作为基线的稳定键。"""
    return path.relative_to(ROOT).as_posix()


def sample_paths() -> list[Path]:
    return sorted(SAMPLE_DIR.rglob("*.html"))


def extract_case(html: str, extractor: GeneralNewsExtractor) -> dict[str, Any]:
    """把一次抽取压缩成可稳定比对的结构。

    正文只存长度与 sha1：完整正文既占体积，也会把样本内容复制进仓库，
    而逐字比对用 sha1 已经足够严格。
    """
    try:
        result = extractor.extract(html)
    except NoContentException:
        return {"error": "NoContentException"}
    content = result.get("content") or ""
    return {
        "title": (result.get("title") or "").strip(),
        "author": (result.get("author") or "").strip(),
        "publish_time": (result.get("publish_time") or "").strip(),
        "content_len": len(content),
        "content_sha1": hashlib.sha1(content.encode("utf-8")).hexdigest(),
        "images": list(result.get("images") or []),
    }


def build_baseline(extractor: GeneralNewsExtractor | None = None) -> dict[str, Any]:
    from gne import __version__

    extractor = extractor or GeneralNewsExtractor()
    cases = {
        case_key(path): extract_case(path.read_text(encoding="utf-8"), extractor)
        for path in sample_paths()
    }
    return {
        "gne_version": __version__,
        "sample_count": len(cases),
        "cases": cases,
    }


def load_baseline() -> dict[str, Any]:
    with BASELINE_PATH.open(encoding="utf-8") as fh:
        return json.load(fh)
