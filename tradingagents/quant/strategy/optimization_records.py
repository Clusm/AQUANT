"""策略优化记录(来自 stock_selector, 2026-08-16).

数据源: stock_selector/strategy_combo_opt/outputs/cache/portfolio_metrics_v2_oos.json
口径: OOS 2025-01-01~2026-07-14, 生产引擎, 费用/滑点已计入。
total_return 是区间累计收益,不是单笔或持有期平均收益。
"""
from __future__ import annotations

from typing import Any

STRATEGY_OPT_RECORDS: dict[str, dict[str, Any]] = {
    "opt_M_w_macd_gc_strict_body5": {
        "oos_total_return": 2.2953,
        "win_rate": 0.6667,
        "sharpe": 3.34,
        "max_drawdown": -0.1018,
        "profit_factor": 11.73,
        "n_sells": 24,
        "n_trades": 48,
        "avg_holding_days": 18.2
    },
    "opt_M_mwd_res_default_kill13": {
        "oos_total_return": 1.8335,
        "win_rate": 0.6364,
        "sharpe": 2.19,
        "max_drawdown": -0.2507,
        "profit_factor": 6.19,
        "n_sells": 22,
        "n_trades": 44,
        "avg_holding_days": 26.8
    },
    "M_mwd_res_loose": {
        "oos_total_return": 1.7009,
        "win_rate": 0.5833,
        "sharpe": 2.31,
        "max_drawdown": -0.1468,
        "profit_factor": 7.87,
        "n_sells": 24,
        "n_trades": 48,
        "avg_holding_days": 23.0
    },
    "opt_M_w_bo_pb_loose_champion_exitmin3": {
        "oos_total_return": 1.5461,
        "win_rate": 0.4658,
        "sharpe": 2.22,
        "max_drawdown": -0.1261,
        "profit_factor": 2.76,
        "n_sells": 73,
        "n_trades": 146,
        "avg_holding_days": 6.2
    },
    "M_m_macd_gc_default": {
        "oos_total_return": 0.9142,
        "win_rate": 0.6571,
        "sharpe": 1.83,
        "max_drawdown": -0.1755,
        "profit_factor": 3.72,
        "n_sells": 35,
        "n_trades": 70,
        "avg_holding_days": 10.2
    },
    "opt_FC_GAP_AVOLC60_topk5": {
        "oos_total_return": 1.4537,
        "win_rate": 0.5385,
        "sharpe": 2.67,
        "max_drawdown": -0.1304,
        "profit_factor": 2.3,
        "n_sells": 169,
        "n_trades": 338,
        "avg_holding_days": 9.9
    },
    "opt_M_w_adx_dmi_loose_maxhold20_kill5": {
        "oos_total_return": 0.9736,
        "win_rate": 0.36,
        "sharpe": 1.94,
        "max_drawdown": -0.1439,
        "profit_factor": 3.03,
        "n_sells": 25,
        "n_trades": 50,
        "avg_holding_days": 13.4
    },
    "HHH_bamb_default": {
        "oos_total_return": 0.7973,
        "win_rate": 0.5,
        "sharpe": 1.79,
        "max_drawdown": -0.1156,
        "profit_factor": 7.27,
        "n_sells": 14,
        "n_trades": 28,
        "avg_holding_days": 15.2
    },
    "YY_lpb_ma20_mid": {
        "oos_total_return": 0.5734,
        "win_rate": 0.4706,
        "sharpe": 1.65,
        "max_drawdown": -0.1077,
        "profit_factor": 3.9,
        "n_sells": 17,
        "n_trades": 34,
        "avg_holding_days": 5.1
    },
    "opt_M_m_bo_loose_kill7": {
        "oos_total_return": 1.1014,
        "win_rate": 0.5676,
        "sharpe": 1.77,
        "max_drawdown": -0.1837,
        "profit_factor": 2.69,
        "n_sells": 37,
        "n_trades": 74,
        "avg_holding_days": 14.8
    },
    "opt_vpt_topk3_uniq_topk300": {
        "oos_total_return": 0.7258,
        "win_rate": 0.5455,
        "sharpe": 1.87,
        "max_drawdown": -0.0929,
        "profit_factor": 4.01,
        "n_sells": 33,
        "n_trades": 66,
        "avg_holding_days": 21.5
    },
    "opt_FC_19_1_topk8": {
        "oos_total_return": 0.8396,
        "win_rate": 0.5426,
        "sharpe": 2.07,
        "max_drawdown": -0.13,
        "profit_factor": 1.66,
        "n_sells": 282,
        "n_trades": 564,
        "avg_holding_days": 9.4
    },
    "opt_M_cp_str_default_kill5": {
        "oos_total_return": 0.9099,
        "win_rate": 0.4444,
        "sharpe": 2.0,
        "max_drawdown": -0.1338,
        "profit_factor": 5.85,
        "n_sells": 18,
        "n_trades": 36,
        "avg_holding_days": 18.0
    },
    "M_KKK_lvb_loose": {
        "oos_total_return": 0.8152,
        "win_rate": 0.52,
        "sharpe": 1.56,
        "max_drawdown": -0.1358,
        "profit_factor": 2.35,
        "n_sells": 50,
        "n_trades": 100,
        "avg_holding_days": 9.5
    },
    "opt_FC_EV_W4_1_difpos": {
        "oos_total_return": 0.8207,
        "win_rate": 0.569,
        "sharpe": 2.56,
        "max_drawdown": -0.0885,
        "profit_factor": 3.07,
        "n_sells": 116,
        "n_trades": 232,
        "avg_holding_days": 15.9
    },
    "opt_FC_EV_A2_1_gc3m": {
        "oos_total_return": 0.5633,
        "win_rate": 0.5085,
        "sharpe": 1.54,
        "max_drawdown": -0.1608,
        "profit_factor": 2.09,
        "n_sells": 118,
        "n_trades": 236,
        "avg_holding_days": 16.9
    },
    "opt_M_lc_bo_v2_cw60": {
        "oos_total_return": 0.7709,
        "win_rate": 0.5,
        "sharpe": 1.97,
        "max_drawdown": -0.109,
        "profit_factor": 6.98,
        "n_sells": 16,
        "n_trades": 32,
        "avg_holding_days": 23.8
    },
    "M_m_rsi_bo_strict": {
        "oos_total_return": 0.6405,
        "win_rate": 0.4583,
        "sharpe": 1.1,
        "max_drawdown": -0.2362,
        "profit_factor": 2.29,
        "n_sells": 24,
        "n_trades": 48,
        "avg_holding_days": 16.8
    }
}


def get_optimization_record(name: str) -> dict[str, Any] | None:
    """Return the OOS optimization record for a strategy, if available."""
    return STRATEGY_OPT_RECORDS.get(str(name))
