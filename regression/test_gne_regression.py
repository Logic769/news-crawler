"""离线回归：用仓库自带的 HTML 样本锁定 GNE 的抽取结果。

这些样本来自上游 GeneralNewsExtractor 的 tests/，共 62 个、覆盖 31 个站点。
此前它们的比对基线只存在于维护者本机，新 clone 无法复现“62/62 一致”。
现在基线随仓库提交，CI 用标准库 unittest 直接跑，不引入额外依赖。

用法（仓库根目录）：
    python -m unittest regression.test_gne_regression -v
刷新基线：
    python -m regression.generate_baseline
"""

from __future__ import annotations

import unittest
from pathlib import Path
from typing import Any, ClassVar

from gne import GeneralNewsExtractor
from regression.baseline_utils import (
    build_baseline,
    case_key,
    extract_case,
    load_baseline,
    sample_paths,
)


class GneSampleRegressionTest(unittest.TestCase):
    maxDiff = None
    baseline: ClassVar[dict[str, Any]]
    extractor: ClassVar[GeneralNewsExtractor]
    samples: ClassVar[list[Path]]

    @classmethod
    def setUpClass(cls) -> None:
        cls.baseline = load_baseline()
        cls.extractor = GeneralNewsExtractor()
        cls.samples = sample_paths()

    def test_sample_set_matches_baseline(self) -> None:
        expected = set(self.baseline["cases"])
        actual = {case_key(path) for path in self.samples}
        self.assertEqual(
            sorted(expected - actual),
            [],
            "基线中的样本已不在 tests/ 下，请确认后重新生成基线",
        )
        self.assertEqual(
            sorted(actual - expected),
            [],
            "tests/ 下出现未纳入基线的新样本，请运行 python -m regression.generate_baseline",
        )

    def test_all_samples_match_baseline(self) -> None:
        for path in self.samples:
            key = case_key(path)
            expected = self.baseline["cases"].get(key)
            if expected is None:
                continue
            with self.subTest(sample=key):
                html = path.read_text(encoding="utf-8")
                actual = extract_case(html, self.extractor)
                self.assertEqual(actual, expected, f"{key} 的抽取结果与基线不一致")

    def test_baseline_is_reproducible(self) -> None:
        """基线必须能由当前代码重新生成，避免手工编辑导致漂移。"""
        # gne_version 每次发版都会 bump，与抽取行为无关；把它排除在比对之外，
        # 否则发布流程一升版本号就得重建基线。
        regenerated = {
            key: value
            for key, value in build_baseline(self.extractor).items()
            if key != "gne_version"
        }
        stored = {
            key: value for key, value in self.baseline.items() if key != "gne_version"
        }
        self.assertEqual(
            regenerated,
            stored,
            "baseline.json 与当前代码的抽取结果不一致；"
            "确认变化符合预期后运行 python -m regression.generate_baseline",
        )


if __name__ == "__main__":
    unittest.main()
