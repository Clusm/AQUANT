"""build_features_vectorized 按需列计算回归测试。

回归点:columns 子集必须与全量计算结果逐列一致;依赖解析必须自动补全
close_to_ma20 -> ma20 这类依赖;不同 columns 请求不能共享缓存 key。
"""

from __future__ import annotations

import pandas as pd

from tradingagents.quant.features.pipeline import (
    FEATURE_COLUMNS,
    build_features_vectorized,
    resolve_feature_columns,
)
from tradingagents.quant.features.strategy_features import required_feature_columns
from tradingagents.quant.strategy.low_vol_breakout import LowVolBreakoutStrategy


def _mk_daily(n_codes: int = 4, days: int = 80) -> pd.DataFrame:
    rows = []
    for i in range(n_codes):
        code = f"{600000 + i}"
        for j in range(days):
            rows.append({
                "stock_code": code,
                "trade_date": pd.Timestamp("2026-01-01") + pd.Timedelta(days=j),
                "open": 10.0 + i + j * 0.01,
                "high": 10.4 + i + j * 0.01,
                "low": 9.8 + i + j * 0.01,
                "close": 10.1 + i + j * 0.01,
                "volume": 1_000_000 + j * 1000,
                "amount": 10_100_000 + j * 10000,
                "outstanding_share": 1e9,
                "turnover_ratio": 0.01 + j * 0.0001,
                "pre_close": 10.0 + i + (j - 1) * 0.01,
                "change_pct": 0.1,
            })
    return pd.DataFrame(rows)


def test_subset_matches_full_columns():
    daily = _mk_daily()
    subset = [
        "ma5", "ma10", "ma20", "ma60", "above_ma20", "close_to_ma20",
        "volume_ratio_5", "ret_5d", "macd_golden_cross", "rsi_14",
    ]
    full = build_features_vectorized(daily, min_rows=30)
    sub = build_features_vectorized(daily, min_rows=30, columns=subset)
    for col in subset:
        assert full[col].equals(sub[col]), col


def test_resolve_dependencies():
    resolved = resolve_feature_columns({"close_to_ma20", "ma_alignment"})
    assert {"ma5", "ma10", "ma20", "close_to_ma20", "ma_alignment"} <= resolved


def test_all_columns_remains_default():
    daily = _mk_daily(n_codes=2, days=40)
    full = build_features_vectorized(daily, min_rows=30)
    for col in FEATURE_COLUMNS:
        assert col in full.columns, col


def test_strategy_required_columns_are_known():
    strat = LowVolBreakoutStrategy()
    cols = required_feature_columns(strat)
    assert cols is not None
    assert "ma20" in cols
    assert "volume_ratio_5" in cols
