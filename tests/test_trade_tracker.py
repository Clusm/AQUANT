"""交易记录与策略跟踪组件的纯计算逻辑测试。"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from tradingagents.quant.strategy.strategy_library_final import get_all_strategies_final
from web import position_store as ps
from web.components.trade_tracker import _closed_trade_rows, _strategy_stats


@pytest.fixture()
def closed_plan(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(ps, "_PLANS_FILE", tmp_path / "plans.json")
    lib = get_all_strategies_final()
    names = list(lib.keys())
    picks = pd.DataFrame([{
        "stock_code": "600000", "n_strategies": 1, "weighted_score": 1.0,
        "avg_win_rate": 0.6, "avg_holding_days": 10.0,
    }])
    records = [{
        "stock_code": "600000", "strategy": names[0], "tier": "S",
        "strategy_comp": 1.0, "win_rate": 0.6, "holding_days": 10,
        "entry_advice": "",
    }]
    plan = ps.create_buy_plan("600000", "2026-08-14", picks, records, "浦发银行")
    ps.confirm_buy(plan["plan_id"], "2026-08-17", 10.0, 100)
    ps.close_position(plan["plan_id"], "2026-08-20", 11.0, "take_profit")
    return plan["plan_id"]


def test_closed_trade_rows_calc_return(closed_plan):
    plan = ps.get_plan(closed_plan)
    rows = _closed_trade_rows([plan])
    assert len(rows) == 1
    assert rows[0]["收益"] == pytest.approx(0.10)
    assert rows[0]["代码"] == "600000"
    assert rows[0]["卖出原因"] == "take_profit"


def test_closed_trade_skips_missing_prices(closed_plan):
    plan = ps.get_plan(closed_plan)
    plan["buy"]["price"] = 0
    assert _closed_trade_rows([plan]) == []


def test_strategy_stats_attributes_trade(closed_plan):
    plan = ps.get_plan(closed_plan)
    df = _strategy_stats([plan])
    assert len(df) == 1
    row = df.iloc[0]
    assert row["实盘次数"] == 1
    assert row["盈利次数"] == 1
    assert row["实盘胜率"] == 1.0
    assert row["平均收益"] == pytest.approx(0.10)
    assert row["回测胜率"] == pytest.approx(0.6)
