"""12 策略最新数据选股测试。

绕过 cli.main,直接调 quant_picker.pick()。
输出 JSON:outputs/test_quant_pick_v3_12strats.json
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

os.environ.setdefault(
    "QUANT_CACHE_DIR",
    r"C:\Users\Tao\Desktop\新建文件夹 (4)\stock_selector\outputs\cache",
)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import pandas as pd

from tradingagents.quant.quant_picker import pick
from tradingagents.quant.strategy.strategy_library_final import (
    get_all_strategies_final,
    get_tier_of_final,
)


def _log(msg: str):
    print(msg, flush=True)


def main():
    today = pd.Timestamp("2026-07-17")
    cache = "daily_main_board"

    strats = get_all_strategies_final()
    _log(f"策略库: {len(strats)} 个有效策略")
    _log(f"今天: {today.date()}, cache: {cache}")
    _log("effective tiers:")
    for n in strats:
        _log(f"  {n:30s}  tier={get_tier_of_final(n)}  comp={strats[n].get('new_composite_score')}")
    _log("=" * 80)

    def progress(c, t, l):
        _log(f"  [{c}/{t}] {l['name']:30s} ({l['tier']:5s}) hits={l['n_hits']} "
             f"{'ERR' if l.get('error') else 'OK'} {l['elapsed']:.1f}s  "
             f"{l.get('error', '')}")

    t0 = time.time()
    result = pick(
        today=today,
        daily_cache_name=cache,
        top_k=2,
        n_workers=8,
        slice_days=0,
        top_n=20,
        progress_callback=progress,
    )
    elapsed = time.time() - t0
    _log(f"\n总耗时: {elapsed:.1f}s")
    _log(f"n_strategies_run: {result['n_strategies_run']}")
    _log(f"n_strategies_error: {result['n_strategies_error']}")

    top = result["top_picks"]
    if top is None or len(top) == 0:
        _log("无候选")
    else:
        _log(f"\nTop {len(top)}:")
        _log(top.to_string(index=False))

    # 序列化保存
    payload = {
        "today": result["today"].strftime("%Y-%m-%d"),
        "elapsed": round(result["elapsed"], 2),
        "n_strategies_run": result["n_strategies_run"],
        "n_strategies_error": result["n_strategies_error"],
        "top_picks": top.to_dict(orient="records") if top is not None else [],
        "all_records": result["all_records"],
        "per_strategy_stats": result["per_strategy_stats"],
    }
    out_path = Path("outputs/test_quant_pick_v3_12strats.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"\nJSON 已写入: {out_path}")


if __name__ == "__main__":
    main()
