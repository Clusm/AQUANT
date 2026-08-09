"""逐日跑全部 10 策略,定位 0 命中根因。

最近 5 个交易日,每日跑每个策略,打印:
- 每个策略 hits 数
- 每个策略 filter 各阶段剩余票数(找过滤最狠的那条)
- 全策略合计 hits

用来回答:0 命中是策略逻辑/参数问题,还是当日市场没机会。
"""
from __future__ import annotations

import sys
import time
import traceback
from pathlib import Path

import pandas as pd

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from tradingagents.quant.backtest.portfolio import Portfolio
from tradingagents.quant.data import cache as cm
from tradingagents.quant.strategy.strategy_library_final import (
    get_all_strategies_final,
)
from tradingagents.quant.utils.trading_calendar import get_calendar


def _load_class(module_path: str, class_name: str):
    import importlib
    m = importlib.import_module(module_path)
    return getattr(m, class_name)


def main() -> int:
    daily_df = cm.load("daily_main_board_liquid")
    daily_df["trade_date"] = pd.to_datetime(daily_df["trade_date"]).dt.normalize()
    daily_df = daily_df.sort_values(["stock_code", "trade_date"]).reset_index(drop=True)

    dates = sorted(daily_df["trade_date"].unique(), reverse=True)
    test_dates = dates[:5]
    print(f"测试 {len(test_dates)} 个交易日: {[d.date() for d in test_dates]}")
    print("=" * 90)

    strategies_cfg = get_all_strategies_final()
    print(f"策略数: {len(strategies_cfg)}")
    for name, info in strategies_cfg.items():
        print(f"  - {name:30s} {info['module'].split('.')[-1]}.{info['class']}")
    print("=" * 90)

    portfolio = Portfolio(capital=20000, max_positions=2, calendar=get_calendar())
    summary_rows = []

    for test_date in test_dates:
        date_str = test_date.date()
        print(f"\n>>> {date_str}")
        daily_sub = daily_df[daily_df["trade_date"] <= test_date].copy()
        print(f"    数据子集: {len(daily_sub)} 行, {daily_sub['stock_code'].nunique()} 票")

        date_total_hits = 0
        for name, info in strategies_cfg.items():
            cls = _load_class(info["module"], info["class"])
            params = info.get("params", {}) or {}
            t0 = time.time()
            try:
                strat = cls(**params)
                signals = strat.generate_signals(daily_sub, test_date, portfolio, top_k=2)
                elapsed = time.time() - t0
                n_hits = len(signals or [])
                date_total_hits += n_hits
                codes = [s.code for s in (signals or [])][:3]
                flag = "OK " if n_hits > 0 else "0  "
                print(f"    [{flag}] {name:30s} hits={n_hits:3d}  {elapsed:5.1f}s  {codes}")
                summary_rows.append({"date": date_str, "strategy": name, "hits": n_hits, "elapsed": elapsed})
            except Exception as e:
                elapsed = time.time() - t0
                err_name = getattr(type(e), "__name__", "Error")
                msg = str(e)[:150]
                print(f"    [ERR] {name:30s} {err_name}: {msg}  {elapsed:.1f}s")
                traceback.print_exc()
                summary_rows.append({"date": date_str, "strategy": name, "hits": -1, "elapsed": elapsed})

        print(f"    >>> {date_str} 全策略合计 hits = {date_total_hits}")

    print("\n" + "=" * 90)
    print("汇总(每策略每日 hits):")
    summary = pd.DataFrame(summary_rows)
    pivot = summary.pivot(index="strategy", columns="date", values="hits")
    print(pivot.to_string())
    print("\n各策略 5 日合计 hits:")
    print(pivot.sum(axis=1).sort_values(ascending=False).to_string())
    print(f"\n5 日总命中数: {summary[summary['hits'] > 0]['hits'].sum()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
