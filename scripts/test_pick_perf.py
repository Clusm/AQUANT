"""验证方案 A+B 改动后的 pick() 耗时。

对比改动前(9 策略各跑 build_features_for_stock 循环 ~30s/策略)
与改动后(共享 build_features_vectorized 缓存 ~1-2s/策略)。
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pandas as pd

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from tradingagents.quant.quant_picker import pick


def main() -> None:
    def cb(completed: int, total: int, latest: dict) -> None:
        err = "ERR" if latest.get("error") else "OK"
        print(f"[{completed}/{total}] {latest['name']:30s} ({latest['tier']}) "
              f"hits={latest['n_hits']} {err} {latest['elapsed']:.1f}s", flush=True)

    print("=" * 70)
    print("跑 pick() 验证方案 A+B 耗时改善")
    print("=" * 70)

    t0 = time.time()
    result = pick(
        today=pd.Timestamp("2026-07-21"),
        daily_cache_name="daily_main_board_liquid",
        top_k=2,
        n_workers=8,
        slice_days=0,
        top_n=10,
        progress_callback=cb,
    )
    elapsed = time.time() - t0

    print("=" * 70)
    print(f"总耗时: {elapsed:.1f}s")
    print(f"策略数: {result['n_strategies_run']}, 错误: {result['n_strategies_error']}")
    print(f"Top 10 候选:")
    top = result["top_picks"]
    if len(top) > 0:
        cols = [c for c in ["stock_code", "strategy", "weighted_score", "n_strategies"] if c in top.columns]
        print(top[cols].to_string())
    else:
        print("  (无候选)")


if __name__ == "__main__":
    main()
