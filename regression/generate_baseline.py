"""重新生成 tests/ 样本的抽取基线。

用法（仓库根目录）：
    python -m regression.generate_baseline

只有在确认抽取行为的变化是“预期改进”时才更新基线，并在提交信息里说明原因；
真正的回归应当让它失败，而不是改基线。
"""

from __future__ import annotations

import json

from regression.baseline_utils import BASELINE_PATH, build_baseline


def main() -> None:
    baseline = build_baseline()
    BASELINE_PATH.write_text(
        json.dumps(baseline, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"已写入 {BASELINE_PATH}，样本数 {baseline['sample_count']}")


if __name__ == "__main__":
    main()
