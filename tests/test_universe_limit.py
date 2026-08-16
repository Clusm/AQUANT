"""universe 涨跌停过滤测试。

需求:当日收盘价处于涨停价或跌停价的股票不进入候选池。
同时验证该过滤可关闭、按板块使用不同涨跌停幅度、且不引入未来数据。
"""

from __future__ import annotations

import pandas as pd
import pytest

from tradingagents.quant.data import universe


class TestPriceLimitHelpers:
    def test_limit_pct_by_board(self):
        assert universe.price_limit_pct("600000") == 0.10
        assert universe.price_limit_pct("000001") == 0.10
        assert universe.price_limit_pct("300750") == 0.20
        assert universe.price_limit_pct("688017") == 0.20
        assert universe.price_limit_pct("830000") == 0.30

    def test_exact_limit_prices_main_board(self):
        # 10.00 元前收,主板涨停 11.00 / 跌停 9.00
        assert universe.is_at_price_limit("600000", close=11.00, pre_close=10.00)
        assert universe.is_at_price_limit("600000", close=9.00, pre_close=10.00)
        assert not universe.is_at_price_limit("600000", close=10.99, pre_close=10.00)
        assert not universe.is_at_price_limit("600000", close=9.01, pre_close=10.00)

    def test_tick_rounding(self):
        # 3.13 * 1.1 = 3.443 -> A股 tick 四舍五入为 3.44
        assert universe.is_at_price_limit("600000", close=3.44, pre_close=3.13)
        assert not universe.is_at_price_limit("600000", close=3.43, pre_close=3.13)

    def test_chinext_20pct(self):
        assert universe.is_at_price_limit("300750", close=12.00, pre_close=10.00)
        assert not universe.is_at_price_limit("300750", close=11.99, pre_close=10.00)

    def test_change_pct_fallback(self):
        # 旧缓存没有 pre_close 时,用 change_pct 近似判断
        assert universe.is_at_price_limit("600000", close=11.00, change_pct=9.80)
        assert not universe.is_at_price_limit("600000", close=11.00, change_pct=9.20)


class TestUniverseLimitFilter:
    def _make_daily(self, dates, codes, limit_code=None):
        rows = []
        for code in codes:
            for d in dates:
                close = 10.0
                pre_close = 10.0
                change_pct = 0.0
                if limit_code and code == limit_code and d == dates[-1]:
                    close = 11.0
                    change_pct = 10.0
                rows.append({
                    "stock_code": code,
                    "trade_date": d,
                    "open": close,
                    "high": close,
                    "low": close,
                    "close": close,
                    "volume": 1_000_000,
                    "amount": 10_000_000,
                    "pre_close": pre_close,
                    "change_pct": change_pct,
                })
        return pd.DataFrame(rows)

    @pytest.fixture(autouse=True)
    def _clean_universe_cache(self):
        universe._universe_topk_cache.clear()
        yield
        universe._universe_topk_cache.clear()

    def test_limit_up_stock_excluded_from_topk(self, monkeypatch):
        dates = pd.bdate_range("2026-01-01", periods=40).normalize()
        codes = [f"60{i:04d}" for i in range(10)]
        daily = self._make_daily(dates, codes, limit_code=codes[0])

        monkeypatch.setattr(universe, "get_st_codes_on_date", lambda *a, **k: set())
        monkeypatch.setattr(
            universe, "get_list_dates",
            lambda: {c: pd.Timestamp("2020-01-01") for c in codes},
        )
        monkeypatch.setattr(universe, "_get_calendar", lambda: pd.DatetimeIndex(dates))

        without_filter = universe.filter_universe_topk(
            daily, on_date=dates[-1], topk=10, min_listing_days=20,
            exclude_limit=False)
        with_filter = universe.filter_universe_topk(
            daily, on_date=dates[-1], topk=10, min_listing_days=20,
            exclude_limit=True)

        assert codes[0] in without_filter
        assert codes[0] not in with_filter
        assert len(with_filter) == 9
        assert set(with_filter) == set(codes[1:])
