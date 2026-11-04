"""GeneralNewsExtractor 线程安全回归：同一个实例可被多个线程并发复用。

每个任务使用不同长度/不同图片的页面，并用 Barrier 让同一批线程尽量同时
进入抽取，避免"相同输入即使缓存被污染也得到相同结果"的假阴性。
"""

from __future__ import annotations

import random
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor

from gne import GeneralNewsExtractor
from smoke_fixture import BUILTIN_SAMPLE_HTML


def _make_variant(index: int) -> str:
    filler = "".join(
        f"<p>{'相似正文' * (20 + index * 3)}</p>" for _ in range(3)
    )
    return BUILTIN_SAMPLE_HTML.replace(
        '<img src="//cdn.example.com/smoke.jpg" alt="示例图片">',
        f'{filler}<img src="//cdn.example.com/smoke{index}.jpg" alt="示例图片{index}">',
    )


class GneThreadSafetyTest(unittest.TestCase):
    def test_shared_extractor_across_threads(self) -> None:
        extractor = GeneralNewsExtractor()
        variants = [_make_variant(i) for i in range(8)]

        def fingerprint(html: str) -> tuple[str, int, tuple[str, ...]]:
            result = extractor.extract(html)
            return (
                result["title"],
                len(result["content"]),
                tuple(result["images"]),
            )

        expected = [fingerprint(html) for html in variants]
        rng = random.Random(20261104)
        # Barrier(8) + max_workers=8 要求任务数是 8 的整数倍：按变体数派生，
        # 避免将来改成非整数倍时最后一批不足 8 个线程永久阻塞。
        plan = [
            rng.choice(range(len(variants)))
            for _ in range(len(variants) * 8)
        ]
        barrier = threading.Barrier(8)

        def run_once(index: int) -> tuple[int, tuple[str, int, tuple[str, ...]]]:
            barrier.wait()
            return index, fingerprint(variants[index])

        with ThreadPoolExecutor(max_workers=8) as pool:
            outputs = list(pool.map(run_once, plan))

        for index, output in outputs:
            self.assertEqual(output, expected[index], f"variant {index} 抽取结果不一致")


if __name__ == "__main__":
    unittest.main()
