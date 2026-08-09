"""Tests for the sina incremental fetcher boundary-day indicators.

Coverage: increment_data/fetch_incremental_sina 增量窗口从缓存最后交易日+1
开始,若只在窗口内 shift(1) 算 pre_close/change_pct,首行恒为 NaN 且会被
cm.update 永久落盘。last_close 继承应修复该边界。
"""

from __future__ import annotations

import pandas as pd
import pytest

from tradingagents.quant import sina_fetcher


def _fake_kline(rows, code: str = "600000") -> pd.DataFrame:
    df = pd.DataFrame(rows)
    df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.normalize()
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = pd.to_numeric(df[col])
    df["stock_code"] = code
    return df


def test_incremental_first_row_pre_close_inherited(monkeypatch):
    monkeypatch.setattr(
        sina_fetcher, "fetch_kline_sina",
        lambda code, datalen=30: _fake_kline([
            {"trade_date": "2026-08-03", "open": 10.2, "high": 10.5,
             "low": 10.1, "close": 10.3, "volume": 1000},
            {"trade_date": "2026-08-04", "open": 10.4, "high": 10.8,
             "low": 10.3, "close": 10.7, "volume": 1200},
        ]),
    )

    df = sina_fetcher.fetch_incremental_sina(
        "600000", "2026-08-03", "2026-08-04", last_close=10.0,
    )

    assert len(df) == 2
    # 首行 pre_close 继承缓存最后收盘价,不再 NaN
    assert df.loc[0, "pre_close"] == 10.0
    assert not pd.isna(df.loc[0, "change_pct"])
    # 08-03 涨跌幅 = (10.3 - 10.0) / 10.0 * 100 = 3.0
    assert abs(df.loc[0, "change_pct"] - 3.0) < 1e-9
    # 非边界行仍按正常 pct_change 计算
    expected = (10.7 - 10.3) / 10.3 * 100
    assert abs(df.loc[1, "change_pct"] - expected) < 1e-9


def test_incremental_without_last_close_first_row_nan(monkeypatch):
    monkeypatch.setattr(
        sina_fetcher, "fetch_kline_sina",
        lambda code, datalen=30: _fake_kline([
            {"trade_date": "2026-08-03", "open": 10.2, "high": 10.5,
             "low": 10.1, "close": 10.3, "volume": 1000},
        ]),
    )

    df = sina_fetcher.fetch_incremental_sina("600000", "2026-08-03", "2026-08-04")

    # 无 last_close 时保持原行为(首行 NaN),不破坏既有调用方
    assert pd.isna(df.loc[0, "pre_close"])
    assert pd.isna(df.loc[0, "change_pct"])


def test_get_last_close_map():
    from tradingagents.quant.data_update import _get_last_close_map

    daily = pd.DataFrame({
        "stock_code": ["600000", "600000", "600001", "600001"],
        "trade_date": pd.to_datetime(
            ["2026-07-01", "2026-07-02", "2026-07-01", "2026-07-02"]
        ),
        "close": [9.5, 10.0, 3.0, 3.2],
    })

    assert _get_last_close_map(daily) == {"600000": 10.0, "600001": 3.2}
