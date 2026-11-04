"""Shared helpers for the offline GNE regression baseline."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from gne import GeneralNewsExtractor, NoContentException
from url_extract import is_video_page

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
    meta = result.get("meta") or {}
    return {
        "title": (result.get("title") or "").strip(),
        "author": (result.get("author") or "").strip(),
        "publish_time": (result.get("publish_time") or "").strip(),
        "content_len": len(content),
        "content_sha1": hashlib.sha1(content.encode("utf-8")).hexdigest(),
        "images": list(result.get("images") or []),
        # meta 是 {name: content} 映射，整体入库会把样本内容复制进基线；
        # 用键列表 + 排序后 JSON 的 sha1 锁定，既覆盖变化又能定位到具体键。
        "meta_keys": sorted(str(key) for key in meta),
        "meta_sha1": hashlib.sha1(
            json.dumps(meta, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest(),
        # 只按 HTML 信号判定（URL 传空），覆盖 og:type 与 JSON-LD 两条规则；
        # 路径规则由 regression/test_video_detection.py 单独覆盖。
        "is_video": is_video_page("", html),
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
