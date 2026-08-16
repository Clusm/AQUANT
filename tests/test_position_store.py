"""买入计划/持仓跟踪数据层单元测试。"""
from __future__ import annotations

import sys
import types
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from tradingagents.quant.strategy.strategy_library_final import get_all_strategies_final
from web import position_store as ps


@pytest.fixture()
def isolated_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(ps, "_PLANS_FILE", tmp_path / "plans.json")
    ps._clear_daily_df_cache()
    yield tmp_path


def _sample_picks() -> pd.DataFrame:
    return pd.DataFrame([{
        "stock_code": "600000",
        "n_strategies": 2,
        "weighted_score": 1.45,
        "avg_win_rate": 0.62,
        "avg_holding_days": 10.0,
    }])


def _sample_records() -> list[dict]:
    lib = get_all_strategies_final()
    names = list(lib.keys())
    return [
        {"stock_code": "600000", "strategy": names[0], "tier": "S",
         "strategy_comp": 1.5, "win_rate": 0.6, "holding_days": 10,
         "entry_advice": "回踩买入"},
        {"stock_code": "600000", "strategy": names[1], "tier": "A",
         "strategy_comp": 1.2, "win_rate": 0.55, "holding_days": 20,
         "entry_advice": ""},
    ]


def test_create_and_transition(isolated_store):
    plan = ps.create_buy_plan("600000", "2026-07-14", _sample_picks(), _sample_records(), "浦发银行")
    assert plan["status"] == "planned"
    assert plan["name"] == "浦发银行"
    assert plan["quant"]["n_strategies"] == 2
    assert len(plan["strategies"]) == 2
    backtest = plan["strategies"][0].get("backtest", {})
    assert backtest.get("oos_total_return") is not None
    assert backtest.get("win_rate") is not None
    assert backtest.get("n_sells") >= 0
    assert plan["strategies"][0]["exit_policy"]["exit_type"] in {
        "信号出场", "固定持仓", "固定持仓 + 信号出场保护"
    }
    advice_lines = plan["strategies"][0]["exit_policy"].get("exit_advice", [])
    assert advice_lines
    assert any("ATR止损" in line for line in advice_lines)
    assert ps.get_plan(plan["plan_id"]) == plan

    updated = ps.confirm_buy(plan["plan_id"], "2026-07-15", 12.3, 100)
    assert updated["status"] == "filled"
    assert updated["buy"]["shares"] == 100

    closed = ps.close_position(plan["plan_id"], "2026-07-20", 13.0, "manual")
    assert closed["status"] == "closed"
    assert closed["sell"]["price"] == 13.0

    assert [p["plan_id"] for p in ps.list_plans("closed")] == [plan["plan_id"]]


def test_abandon_plan(isolated_store):
    plan = ps.create_buy_plan("600000", "2026-07-14", _sample_picks(), [], "测试")
    assert ps.abandon_plan(plan["plan_id"])["status"] == "abandoned"
    assert ps.confirm_buy(plan["plan_id"], "2026-07-15", 10.0) is None


def test_legacy_advice_mean_return_is_relabeled(isolated_store):
    picks = _sample_picks()
    records = [
        {"stock_code": "600000", "strategy": list(get_all_strategies_final())[0], "tier": "S",
         "strategy_comp": 1.0, "win_rate": 0.5, "holding_days": 10,
         "entry_advice": "胜率46.6%/均收+154.61%"},
    ]
    plan = ps.create_buy_plan("600000", "2026-07-14", picks, records, "测试")
    advice = plan["strategies"][0]["entry_advice"]
    assert "OOS累计收益+154.61%" in advice
    assert "均收" not in advice


def test_duplicate_plan_is_not_overwritten(isolated_store):
    first = ps.create_buy_plan("600000", "2026-07-14", _sample_picks(), [], "第一次")
    second = ps.create_buy_plan("600000", "2026-07-14", _sample_picks(), [], "第二次")
    assert first["plan_id"] != second["plan_id"]
    assert len(ps.list_plans()) == 2


def test_holding_days():
    assert ps.holding_days("2026-07-14", asof=date(2026, 7, 20)) == 6
    assert ps.holding_days("2026-07-20", asof=date(2026, 7, 14)) == 0


def test_trading_holding_days_uses_trading_calendar(monkeypatch: pytest.MonkeyPatch):
    from tradingagents.quant.utils import trading_calendar
    monkeypatch.setattr(
        trading_calendar, "trading_days",
        lambda start, end: [pd.Timestamp("2026-07-14"), pd.Timestamp("2026-07-15")],
    )
    assert ps.trading_holding_days("2026-07-14", asof=date(2026, 7, 15)) == 2


def test_trading_holding_days_falls_back_to_calendar_days(monkeypatch: pytest.MonkeyPatch):
    from tradingagents.quant.utils import trading_calendar
    monkeypatch.setattr(
        trading_calendar, "trading_days",
        lambda start, end: (_ for _ in ()).throw(RuntimeError("no calendar")),
    )
    assert ps.trading_holding_days("2026-07-14", asof=date(2026, 7, 20)) == 6


def test_abandon_and_close_only_from_valid_status(isolated_store):
    plan = ps.create_buy_plan("600000", "2026-07-14", _sample_picks(), [], "测试")
    ps.confirm_buy(plan["plan_id"], "2026-07-15", 10.0)
    assert ps.abandon_plan(plan["plan_id"]) is None
    assert ps.close_position(plan["plan_id"], "2026-07-16", 11.0)["status"] == "closed"
    assert ps.close_position(plan["plan_id"], "2026-07-17", 12.0) is None
    assert ps.confirm_buy(plan["plan_id"], "2026-07-17", 12.0) is None


def test_corrupt_store_is_parked_not_overwritten(isolated_store):
    ps._clear_daily_df_cache()
    ps._PLANS_FILE.write_text("{broken", encoding="utf-8")
    assert ps.list_plans() == []
    ps.create_buy_plan("600000", "2026-07-14", _sample_picks(), [], "测试")
    assert len(ps.list_plans()) == 1
    corrupt = list(ps._PLANS_FILE.parent.glob("plans.corrupt-*.json"))
    assert len(corrupt) == 1


def test_nan_quant_metrics_are_safe(isolated_store):
    picks = pd.DataFrame([{
        "stock_code": "600000",
        "n_strategies": float("nan"),
        "weighted_score": float("nan"),
        "avg_win_rate": float("nan"),
        "avg_holding_days": float("nan"),
    }])
    plan = ps.create_buy_plan("600000", "2026-07-14", picks, [], "测试")
    assert plan["quant"]["n_strategies"] == 0
    assert plan["quant"]["weighted_score"] == 0.0
    assert plan["quant"]["avg_win_rate"] == 0.0
    assert plan["quant"]["avg_holding_days"] == 0.0


def test_latest_price_falls_back_when_ticker_missing_from_cache(monkeypatch: pytest.MonkeyPatch):
    ps._clear_daily_df_cache()
    df = pd.DataFrame({
        "stock_code": ["000001"],
        "trade_date": pd.to_datetime(["2026-07-14"]),
        "close": [10.0],
    })
    cache_mod = types.SimpleNamespace(exists=lambda name: True, load=lambda name: df)
    import tradingagents.dataflows.a_stock as a_stock
    import tradingagents.quant.data as data_pkg
    monkeypatch.setattr(data_pkg, "cache", cache_mod)
    monkeypatch.setitem(sys.modules, "tradingagents.quant.data.cache", cache_mod)
    monkeypatch.setattr(a_stock, "_tencent_quote", lambda codes: {"600000": {"price": 7.8}})
    assert ps.get_latest_price("600000") == 7.8


def test_limit_reference_prices(monkeypatch: pytest.MonkeyPatch):
    ps._clear_daily_df_cache()
    df = pd.DataFrame({
        "stock_code": ["600000", "600000"],
        "trade_date": pd.to_datetime(["2026-07-13", "2026-07-14"]),
        "close": [10.0, 10.5],
    })
    cache_mod = types.SimpleNamespace(exists=lambda name: True, load=lambda name: df)
    import tradingagents.quant.data as data_pkg
    monkeypatch.setattr(data_pkg, "cache", cache_mod)
    monkeypatch.setitem(sys.modules, "tradingagents.quant.data.cache", cache_mod)

    prices = ps.get_limit_reference_prices("600000", "2026-07-14")
    assert prices["close"] == 10.5
    assert prices["limit_pct"] == 0.10
    assert prices["limit_up"] == 11.55
    assert prices["limit_down"] == 9.45


def test_limit_reference_prices_uses_earlier_row():
    ps._clear_daily_df_cache()
    df = pd.DataFrame({
        "stock_code": ["300001"],
        "trade_date": pd.to_datetime(["2026-07-14"]),
        "close": [20.0],
    })
    cache_mod = types.SimpleNamespace(exists=lambda name: True, load=lambda name: df)
    old = sys.modules.get("tradingagents.quant.data.cache")
    import tradingagents.quant.data as data_pkg
    old_attr = getattr(data_pkg, "cache", None)
    data_pkg.cache = cache_mod
    sys.modules["tradingagents.quant.data.cache"] = cache_mod
    try:
        prices = ps.get_limit_reference_prices("300001", "2026-07-10")
        assert prices["close"] is None
        prices2 = ps.get_limit_reference_prices("300001")
        assert prices2["limit_up"] == 24.0
    finally:
        if old is None:
            sys.modules.pop("tradingagents.quant.data.cache", None)
        else:
            sys.modules["tradingagents.quant.data.cache"] = old
        if old_attr is None:
            data_pkg.cache = None
        else:
            data_pkg.cache = old_attr
