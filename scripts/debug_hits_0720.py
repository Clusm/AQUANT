"""跑 2026-07-20 数据,列出每个策略命中的完整股票清单。"""
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

    test_date = pd.Timestamp("2026-07-20")
    print(f"测试日期: {test_date.date()}")
    print("=" * 90)

    strategies_cfg = get_all_strategies_final()
    portfolio = Portfolio(capital=20000, max_positions=2, calendar=get_calendar())

    all_hits = []
    for name, info in strategies_cfg.items():
        cls = _load_class(info["module"], info["class"])
        params = info.get("params", {}) or {}
        t0 = time.time()
        try:
            strat = cls(**params)
            signals = strat.generate_signals(daily_df, test_date, portfolio, top_k=2)
            elapsed = time.time() - t0
            n_hits = len(signals or [])
            codes = [s.code for s in (signals or [])]
            scores = [f"{s.score:.2f}" for s in (signals or [])]
            reasons = [s.reason for s in (signals or [])]
            flag = "OK " if n_hits > 0 else "0  "
            print(f"[{flag}] {name:30s} hits={n_hits}  {elapsed:5.1f}s  codes={codes}  scores={scores}  reasons={reasons}")
            for s in (signals or []):
                all_hits.append({
                    "strategy": name,
                    "code": s.code,
                    "score": s.score,
                    "reason": s.reason,
                    "tier": info.get("tier_label", "?"),
                })
        except Exception as e:
            elapsed = time.time() - t0
            print(f"[ERR] {name:30s} {type(e).__name__}: {str(e)[:150]}  {elapsed:.1f}s")

    print("\n" + "=" * 90)
    print(f"全部命中(共 {len(all_hits)} 条):")
    print(f"{'策略':<32}{'代码':<10}{'分数':<10}{'Tier':<8}理由")
    print("-" * 90)
    for h in all_hits:
        print(f"{h['strategy']:<32}{h['code']:<10}{h['score']:<10.2f}{h['tier']:<8}{h['reason']}")

    unique_codes = sorted(set(h["code"] for h in all_hits))
    print(f"\n命中股票去重({len(unique_codes)} 只): {unique_codes}")

    # 统计每只股票被多少策略命中
    from collections import Counter
    cnt = Counter(h["code"] for h in all_hits)
    print("\n股票命中数排名(被多策略命中 = 信号更强):")
    for code, n in cnt.most_common():
        strats = [h["strategy"].replace("M_", "").replace("ZZ_", "") for h in all_hits if h["code"] == code]
        print(f"  {code}  被 {n} 个策略命中  策略: {strats}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
