"""Offline smoke test used by deploy scripts.

Checks that the project can be imported, dates parse correctly, GNE can
extract from a bundled sample page, and SQLite can be initialized.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path


def main() -> None:
    if sys.version_info < (3, 10):
        raise SystemExit("Python 3.10+ is required.")

    import playwright  # noqa: F401
    from gne import GeneralNewsExtractor
    from news_crawler import init_db, load_config, parse_datetime
    import url_extract  # noqa: F401

    root = Path(__file__).resolve().parent

    date_cases = {
        "2026-09-04 10:00:00": "2026-09-04",
        "2026年09月04日 18:44:09": "2026-09-04",
        "04 2026.09": "2026-09-04",
    }
    for text, expected in date_cases.items():
        parsed = parse_datetime(text)
        if parsed is None or parsed.strftime("%Y-%m-%d") != expected:
            raise AssertionError(f"date parse failed: {text!r} -> {parsed}")

    sample_html = (root / "tests" / "163" / "1.html").read_text(
        encoding="utf-8",
        errors="ignore",
    )
    result = GeneralNewsExtractor().extract(sample_html)
    if not result.get("title") or not result.get("content"):
        raise AssertionError("GNE failed to extract sample page")

    config = load_config(str(root / "sources.json"))
    if not isinstance(config.get("sources"), list):
        raise AssertionError("sources.json is not a valid config template")

    with tempfile.TemporaryDirectory() as tmp:
        conn = init_db(str(Path(tmp) / "smoke.db"))
        try:
            conn.execute("SELECT 1 FROM articles LIMIT 1")
        finally:
            conn.close()

    print("SMOKE TEST PASSED")


if __name__ == "__main__":
    main()
