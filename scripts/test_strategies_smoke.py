"""验证 6 个周月线策略改动后仍能正常生成信号。

取一个历史日期跑每个策略,检查:
1. 不报错
2. 能生成信号(hits > 0)——证明策略逻辑没被改坏

用 daily_main_board_liquid 缓存(2129 票,截至 2026-07-21)。
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pandas as pd

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from tradingagents.quant.backtest.portfolio import Portfolio
from tradingagents.quant.data import cache as cm
from tradingagents.quant.strategy.weekly_macd_golden_cross import WeeklyMacdGoldenCrossStrategy
from tradingagents.quant.strategy.monthly_macd_golden_cross import MonthlyMacdGoldenCrossStrategy
from tradingagents.quant.strategy.monthly_rsi_breakout import MonthlyRsiBreakoutStrategy
from tradingagents.quant.strategy.monthly_weekly_daily_resonance import MonthlyWeeklyDailyResonanceStrategy
from tradingagents.quant.strategy.weekly_rsi_breakout import WeeklyRsiBreakoutStrategy
from tradingagents.quant.strategy.monthly_cmf_breakout import MonthlyCmfBreakoutStrategy
from tradingagents.quant.utils.trading_calendar import get_calendar


STRATEGIES = [
    ("weekly_macd_golden_cross", WeeklyMacdGoldenCrossStrategy),
    ("monthly_macd_golden_cross", MonthlyMacdGoldenCrossStrategy),
    ("monthly_rsi_breakout", MonthlyRsiBreakoutStrategy),
    ("monthly_weekly_daily_resonance", MonthlyWeeklyDailyResonanceStrategy),
    ("weekly_rsi_breakout", WeeklyRsiBreakoutStrategy),
    ("monthly_cmf_breakout", MonthlyCmfBreakoutStrategy),
]


def main() -> int:
    daily_df = cm.load("daily_main_board_liquid")
    daily_df["trade_date"] = pd.to_datetime(daily_df["trade_date"]).dt.normalize()
    daily_df = daily_df.sort_values(["stock_code", "trade_date"]).reset_index(drop=True)

    # 取缓存里最近的一个交易日
    test_date = daily_df["trade_date"].max()
    print(f"测试日期: {test_date.date()}, 缓存票数: {daily_df['stock_code'].nunique()}")
    print("=" * 70)

    portfolio = Portfolio(capital=20000, max_positions=2, calendar=get_calendar())
    n_ok = 0
    n_with_signals = 0

    for name, cls in STRATEGIES:
        t0 = time.time()
        try:
            strat = cls()
            signals = strat.generate_signals(daily_df, test_date, portfolio, top_k=2)
            elapsed = time.time() - t0
            n_hits = len(signals or [])
            codes = [s.code for s in (signals or [])][:3]
            print(f"[OK] {name:40s} hits={n_hits} {elapsed:.1f}s  样例={codes}")
            n_ok += 1
            if n_hits > 0:
                n_with_signals += 1
        except Exception as e:
            elapsed = time.time() - t0
            err_name = getattr(type(e), "__name__", "Error")
            print(f"[ERR] {name:40s} {err_name}: {str(e)[:120]}  {elapsed:.1f}s")

    print("=" * 70)
    print(f"通过: {n_ok}/{len(STRATEGIES)}, 有信号: {n_with_signals}/{len(STRATEGIES)}")
    return 0 if n_ok == len(STRATEGIES) else 1


if __name__ == "__main__":
    sys.exit(main())
