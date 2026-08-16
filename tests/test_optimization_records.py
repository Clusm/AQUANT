"""stock_selector 策略优化记录固化数据测试。"""
from __future__ import annotations

from tradingagents.quant.strategy.optimization_records import (
    STRATEGY_OPT_RECORDS,
    get_optimization_record,
)
from tradingagents.quant.strategy.strategy_library_final import get_all_strategies_final


def test_all_active_strategies_have_records():
    lib = get_all_strategies_final()
    assert set(STRATEGY_OPT_RECORDS) == set(lib)


def test_champion_oos_record_is_cumulative_not_avg_holding_return():
    rec = get_optimization_record("opt_M_w_bo_pb_loose_champion_exitmin3")
    assert rec["oos_total_return"] == 1.5461
    assert rec["win_rate"] == 0.4658
    assert rec["n_sells"] == 73
    assert rec["avg_holding_days"] == 6.2
