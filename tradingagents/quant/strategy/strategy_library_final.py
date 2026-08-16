"""Final 策略库 - 2026-08-16 top18 终态库 (24 家族去底部 6),替换旧 10 策略库,下游 API 兼容。

来源: stock_selector/strategy/strategy_library_active_top18.py (2026-08-16),逐字复制。
指标口径: OOS 2025-01-01~2026-07-14, 生产引擎 (market_filter 按各策略 engine_params),
来自 strategy_combo_opt 重跑审计 (P1_correlation_audit 2026-08-16 刷新版)。

关键接手须知 (压缩自源文件):
- 每个家族取冠军 (overlay 优化版优先, 否则基座保留), 配置自包含, 不依赖 overlay 文件
- 去除的底部 6 家族 (R46-R51 攻坚 23 提案全拒, 日线维度不可救, 见 EXCLUDED_BOTTOM6):
  M_m_tr_hlth_default (OOS -0.78) / M_mtf_default (2.10) / M_mwd_res_strict (2.67, n=24 样本不足)
  / M_w_adx_pb_loose (2.75) / M_m_rsi_bo_loose (3.44) / M_q_bo_pb_loose (3.77)
- 相关性 (2026-08-16 刷新): 18 条硬簇全部独立 (两两重叠活跃日 Spearman 最高 0.52,
  cp_str_kill5×cw60, 信号 Jaccard 仅 6.8%); 打分/组合注意软簇 (≥0.4) 封顶 1 票:
  软簇 C2 (mwd 系): M_mwd_res_loose / opt_M_mwd_res_default_kill13 / opt_M_lc_bo_v2_cw60 / YY_lpb_ma20_mid
  软簇 C3 (FC 系): opt_FC_GAP_AVOLC60_topk5 / opt_FC_19_1_topk8 / opt_FC_EV_A2_1_gc3m
- 实盘组合先例: body5 × kill13 等权 (corr 0.15, OOS sharpe 3.45 / mdd -12.1%);
  cw60 已验与 body5 corr 0.15 / kill13 0.30, 可作第三腿候选 (但加腿必稀释 comp, R15/R16 规律)

分级规则 (OOS comp): S: comp >= 8.0 (5 个) / A: 5.0 <= comp < 8.0 (11 个) / B: comp < 5.0 (2 个, 概念独立保留)
"""
from __future__ import annotations

from typing import Any

S_TIER_FINAL: dict[str, dict[str, Any]] = {
    "opt_M_w_macd_gc_strict_body5": {
        "name": "周线MACD金叉(严格,信号出场,mf_off,body0.5)",
        "module": "tradingagents.quant.strategy.weekly_macd_golden_cross",
        "class": "WeeklyMacdGoldenCrossStrategy",
        "params": {"golden_cross_recent_weeks": 1, "today_vol_min": 1.8, "body_ratio_min": 0.5, "enable_signal_exit": True, "exit_min_holding_days": 10},
        "engine_params": {"use_atr_exit": True, "atr_stop_mult": 1.9, "atr_trail_mult": 2.5, "atr_trail_trigger": 0.05, "market_filter": False, "max_holding_days": 999},
        "holding_days": 999,
        "composite_score": 12.57,
        "variant_of": "M_w_macd_gc_strict",
        "performance": {"total_return": 2.2949, "win_rate": 0.6667, "sharpe": 3.34, "max_drawdown": -0.1017, "profit_factor": 11.73, "n_trades": 48},
    },
    "M_mwd_res_loose": {
        "name": "月周日共振(宽松,信号出场)",
        "module": "tradingagents.quant.strategy.monthly_weekly_daily_resonance",
        "class": "MonthlyWeeklyDailyResonanceStrategy",
        "params": {"today_vol_min": 1.2, "body_ratio_min": 0.3, "today_ret_max": 0.08, "near_high_ratio": 0.85, "require_full_align": False, "enable_signal_exit": True, "exit_min_holding_days": 5},
        "engine_params": {"use_atr_exit": True, "atr_stop_mult": 1.9, "atr_trail_mult": 2.5, "atr_trail_trigger": 0.05, "market_filter": True, "max_holding_days": 999},
        "holding_days": 999,
        "composite_score": 9.24,
        "variant_of": None,
        "performance": {"total_return": 1.7006, "win_rate": 0.5833, "sharpe": 2.31, "max_drawdown": -0.1472, "profit_factor": 7.87, "n_trades": 48},
    },
    "opt_M_mwd_res_default_kill13": {
        "name": "月周日共振(默认,信号出场,mf_off,kill13)",
        "module": "tradingagents.quant.strategy.monthly_weekly_daily_resonance",
        "class": "MonthlyWeeklyDailyResonanceStrategy",
        "params": {"enable_signal_exit": True, "exit_min_holding_days": 10},
        "engine_params": {"use_atr_exit": True, "atr_stop_mult": 1.9, "atr_trail_mult": 2.5, "atr_trail_trigger": 0.05, "market_filter": False, "max_holding_days": 999, "breakeven_kill_days": 13},
        "holding_days": 999,
        "composite_score": 9.19,
        "variant_of": "M_mwd_res_default",
        "performance": {"total_return": 1.8332, "win_rate": 0.6364, "sharpe": 2.19, "max_drawdown": -0.2508, "profit_factor": 6.19, "n_trades": 44},
    },
    "opt_FC_GAP_AVOLC60_topk5": {
        "name": "FC缺口量能因子组合(GAP_AVOLC60_PVC10+neg_volume,topk5)",
        "module": "tradingagents.quant.strategy.factor_combo_rebalance",
        "class": "FactorComboRebalanceStrategy",
        "params": {"name": "FC_GAP_AVOLC60_PVC10_NV_U300_T10_K10", "factor_weights": {"GAP_AVOLC60_PVC10": 0.5, "neg_volume": 0.5}, "rebalance_every": 10, "universe_topk": 300, "top_k": 5, "direction": "top", "min_stocks_for_signal": 50},
        "engine_params": {"use_atr_exit": True, "atr_stop_mult": 1.9, "atr_trail_mult": 2.5, "atr_trail_trigger": 0.05, "market_filter": False, "max_holding_days": 10, "top_k": 10, "max_positions": 5, "initial_capital": 30000000},
        "holding_days": 10,
        "composite_score": 8.63,
        "variant_of": "FC_GAP_AVOLC60_PVC10_NV_U300_T10_K10",
        "performance": {"total_return": 1.4543, "win_rate": 0.5378, "sharpe": 2.67, "max_drawdown": -0.1303, "profit_factor": 2.30, "n_trades": 338},
    },
    "opt_M_w_bo_pb_loose_champion_exitmin3": {
        "name": "周线突破回踩(宽松,V3单组件出场,champion)",
        "module": "tradingagents.quant.strategy.weekly_breakout_pullback",
        "class": "WeeklyBreakoutPullbackStrategy",
        "params": {"pullback_max": 0.1, "today_vol_min": 1.2, "body_ratio_min": 0.3, "enable_signal_exit": True, "exit_min_holding_days": 3, "exit_stop_loss_pct": 0.12, "exit_ma_breach_days": 3},
        "engine_params": {"use_atr_exit": True, "atr_stop_mult": 1.9, "atr_trail_mult": 2.5, "atr_trail_trigger": 0.05, "market_filter": True, "max_holding_days": 10, "breakeven_kill_days": 5},
        "holding_days": 10,
        "composite_score": 8.04,
        "variant_of": "M_w_bo_pb_loose",
        "performance": {"total_return": 1.5461, "win_rate": 0.4658, "sharpe": 2.22, "max_drawdown": -0.1264, "profit_factor": 2.76, "n_trades": 146},
    },
}

A_TIER_FINAL: dict[str, dict[str, Any]] = {
    "opt_FC_EV_W4_1_difpos": {
        "name": "周线MACD金叉事件因子排名(RANGE_POS3/20D等权,difpos,filter30%)",
        "module": "tradingagents.quant.strategy.factor_ranked_event",
        "class": "FactorRankedEventStrategy",
        "params": {"name": "FC_EV_W4_1", "event_type": "weekly_macd_golden_cross", "factor_weights": {"RANGE_POS3": 0.5, "RANGE_POS_20D": 0.5}, "event_params": {"require_bullish": True, "body_ratio_min": 0.5, "today_vol_min": 1.5, "golden_cross_recent_weeks": 1, "dif_above_zero": True}, "filter_top_pct": 30.0, "selection_mode": "ranked", "universe_topk": 300, "top_k": 10, "direction": "top", "min_stocks_for_signal": 1},
        "engine_params": {"use_atr_exit": True, "atr_stop_mult": 1.9, "atr_trail_mult": 2.5, "atr_trail_trigger": 0.05, "market_filter": False, "max_holding_days": 20, "top_k": 10, "max_positions": 10, "initial_capital": 30000000},
        "holding_days": 20,
        "composite_score": 7.45,
        "variant_of": "FC_EV_W4_1",
        "performance": {"total_return": 0.8213, "win_rate": 0.5688, "sharpe": 2.56, "max_drawdown": -0.0880, "profit_factor": 3.07, "n_trades": 232},
    },
    "opt_M_cp_str_default_kill5": {
        "name": "连续强势收盘(默认,信号出场,kill5)",
        "module": "tradingagents.quant.strategy.continuous_strong_close",
        "class": "ContinuousStrongCloseStrategy",
        "params": {"enable_signal_exit": True, "exit_min_holding_days": 5},
        "engine_params": {"use_atr_exit": True, "atr_stop_mult": 1.9, "atr_trail_mult": 2.5, "atr_trail_trigger": 0.05, "market_filter": True, "max_holding_days": 999, "breakeven_kill_days": 5},
        "holding_days": 999,
        "composite_score": 6.98,
        "variant_of": "M_cp_str_default",
        "performance": {"total_return": 0.9101, "win_rate": 0.4444, "sharpe": 2.00, "max_drawdown": -0.1342, "profit_factor": 5.85, "n_trades": 36},
    },
    "opt_M_lc_bo_v2_cw60": {
        "name": "60d长横盘突破v2(宽松,信号出场)",
        "module": "tradingagents.quant.strategy.long_consolidation_breakout_v2",
        "class": "LongConsolidationBreakoutV2Strategy",
        "params": {"range_max": 0.3, "ret_long_max": 0.2, "today_vol_min": 1.5, "body_ratio_min": 0.4, "today_ret_min": 0.01, "today_ret_max": 0.08, "require_full_align": False, "enable_signal_exit": True, "exit_min_holding_days": 5, "consolidation_window": 60},
        "engine_params": {"use_atr_exit": True, "atr_stop_mult": 1.9, "atr_trail_mult": 2.5, "atr_trail_trigger": 0.05, "market_filter": True, "max_holding_days": 999},
        "holding_days": 999,
        "composite_score": 6.69,
        "variant_of": "M_lc_bo_v2_loose",
        "performance": {"total_return": 0.7709, "win_rate": 0.5000, "sharpe": 1.97, "max_drawdown": -0.1093, "profit_factor": 6.98, "n_trades": 32},
    },
    "HHH_bamb_default": {
        "name": "多头排列回踩MA20反弹(信号出场)",
        "module": "tradingagents.quant.strategy.bull_align_ma20_bounce",
        "class": "BullAlignMa20BounceStrategy",
        "params": {"ma_band": 0.02, "today_ret_min": 0.02, "today_vol_min": 1.3, "body_ratio_min": 0.4, "require_above_ma": "ma20", "require_full_align": True, "enable_signal_exit": True, "exit_min_holding_days": 2},
        "engine_params": {"use_atr_exit": True, "atr_stop_mult": 2.5, "atr_trail_mult": 3.0, "atr_trail_trigger": 0.07, "market_filter": True, "max_holding_days": 999},
        "holding_days": 999,
        "composite_score": 6.37,
        "variant_of": None,
        "performance": {"total_return": 0.7973, "win_rate": 0.5000, "sharpe": 1.79, "max_drawdown": -0.1164, "profit_factor": 7.27, "n_trades": 28},
    },
    "opt_M_w_adx_dmi_loose_maxhold20_kill5": {
        "name": "周线ADX+DMI(宽松,持仓20d,kill5)",
        "module": "tradingagents.quant.strategy.weekly_adx_dmi_breakout",
        "class": "WeeklyAdxDmiBreakoutStrategy",
        "params": {"adx_threshold": 20.0, "cross_recent_weeks": 3, "today_vol_min": 1.2, "body_ratio_min": 0.3},
        "engine_params": {"use_atr_exit": True, "atr_stop_mult": 1.9, "atr_trail_mult": 2.5, "atr_trail_trigger": 0.05, "market_filter": True, "max_holding_days": 20, "breakeven_kill_days": 5},
        "holding_days": 20,
        "composite_score": 6.33,
        "variant_of": "M_w_adx_dmi_loose",
        "performance": {"total_return": 0.9741, "win_rate": 0.3600, "sharpe": 1.94, "max_drawdown": -0.1444, "profit_factor": 3.03, "n_trades": 50},
    },
    "M_m_macd_gc_default": {
        "name": "月线MACD金叉(默认,持仓10d)",
        "module": "tradingagents.quant.strategy.monthly_macd_golden_cross",
        "class": "MonthlyMacdGoldenCrossStrategy",
        "params": {},
        "engine_params": {"use_atr_exit": True, "atr_stop_mult": 1.9, "atr_trail_mult": 2.5, "atr_trail_trigger": 0.05, "market_filter": True, "max_holding_days": 10},
        "holding_days": 10,
        "composite_score": 6.32,
        "variant_of": None,
        "performance": {"total_return": 0.9139, "win_rate": 0.6571, "sharpe": 1.83, "max_drawdown": -0.1757, "profit_factor": 3.72, "n_trades": 70},
    },
    "opt_M_m_bo_loose_kill7": {
        "name": "月线突破(宽松,信号出场,kill7)",
        "module": "tradingagents.quant.strategy.monthly_breakout",
        "class": "MonthlyBreakoutStrategy",
        "params": {"breakout_window": 180, "today_vol_min": 1.2, "body_ratio_min": 0.3, "today_ret_max": 0.08, "near_high_ratio": 0.9, "require_full_align": False, "enable_signal_exit": True, "exit_min_holding_days": 5},
        "engine_params": {"use_atr_exit": True, "atr_stop_mult": 1.9, "atr_trail_mult": 2.5, "atr_trail_trigger": 0.05, "market_filter": True, "max_holding_days": 999, "breakeven_kill_days": 7},
        "holding_days": 999,
        "composite_score": 6.21,
        "variant_of": "M_m_bo_loose",
        "performance": {"total_return": 1.1010, "win_rate": 0.5676, "sharpe": 1.77, "max_drawdown": -0.1838, "profit_factor": 2.69, "n_trades": 74},
    },
    "opt_vpt_topk3_uniq_topk300": {
        "name": "量价趋势确认(topk3,universe300,实验性救活)",
        "module": "tradingagents.quant.strategy.volume_price_trend",
        "class": "VolumePriceTrendStrategy",
        "params": {"lookback": 200, "universe_topk": 300, "require_above_ma": "ma60", "require_ma5_gt_ma10": True, "vol_ratio_20_min": 1.0, "vol_ratio_5d_avg_min": 1.0, "ret_5d_min": 0.01, "ret_5d_max": 0.12, "close_to_ma5_max": 0.05, "body_ratio_min": 0.0, "enable_signal_exit": True, "exit_min_holding_days": 15, "exit_vol_ratio_min": 0.7, "today_vol_min": 0.8},
        "engine_params": {"use_atr_exit": True, "atr_stop_mult": 1.9, "atr_trail_mult": 2.5, "atr_trail_trigger": 0.05, "market_filter": True, "max_holding_days": 999, "min_signal_vol_ratio": 2.1, "top_k": 3, "max_positions": 3},
        "holding_days": 999,
        "composite_score": 6.15,
        "variant_of": "volume_price_trend",
        "performance": {"total_return": 0.7261, "win_rate": 0.5455, "sharpe": 1.87, "max_drawdown": -0.0931, "profit_factor": 4.01, "n_trades": 66},
    },
    "opt_FC_19_1_topk8": {
        "name": "FC因子组合19(OVNSHARE_AVC5_PVC10+DIST_HIGH3_AMTVC10,topk8)",
        "module": "tradingagents.quant.strategy.factor_combo_rebalance",
        "class": "FactorComboRebalanceStrategy",
        "params": {"name": "FC_19_1", "factor_weights": {"OVNSHARE_AVC5_PVC10": 0.5, "DIST_HIGH3_AMTVC10": 0.5}, "rebalance_every": 10, "universe_topk": 300, "top_k": 8, "direction": "top", "min_stocks_for_signal": 50},
        "engine_params": {"use_atr_exit": True, "atr_stop_mult": 1.9, "atr_trail_mult": 2.5, "atr_trail_trigger": 0.05, "market_filter": False, "max_holding_days": 10, "top_k": 10, "max_positions": 8, "initial_capital": 30000000},
        "holding_days": 10,
        "composite_score": 6.02,
        "variant_of": "FC_19_1",
        "performance": {"total_return": 0.8401, "win_rate": 0.5426, "sharpe": 2.07, "max_drawdown": -0.1303, "profit_factor": 1.66, "n_trades": 564},
    },
    "YY_lpb_ma20_mid": {
        "name": "龙头回调MA20反弹(信号出场)",
        "module": "tradingagents.quant.strategy.leader_pullback_bounce",
        "class": "LeaderPullbackBounceStrategy",
        "params": {"ma_target": "ma20", "ma_band": 0.02, "wave1_min": 0.5, "today_ret_min": 0.03, "today_vol_min": 1.5, "body_ratio_min": 0.5, "require_above_ma": "ma20", "enable_signal_exit": True, "exit_min_holding_days": 2, "exit_wave1_floor": 0.25},
        "engine_params": {"use_atr_exit": True, "atr_stop_mult": 2.5, "atr_trail_mult": 3.0, "atr_trail_trigger": 0.07, "market_filter": True, "max_holding_days": 5},
        "holding_days": 5,
        "composite_score": 5.31,
        "variant_of": None,
        "performance": {"total_return": 0.5727, "win_rate": 0.4706, "sharpe": 1.65, "max_drawdown": -0.1077, "profit_factor": 3.90, "n_trades": 34},
    },
    "M_KKK_lvb_loose": {
        "name": "低波动横盘突破(中线,持仓10d)",
        "module": "tradingagents.quant.strategy.low_vol_breakout",
        "class": "LowVolBreakoutStrategy",
        "params": {"vol_window": 60, "vol_max": 0.08, "ret_60d_max": 0.3, "today_ret_min": 0.02, "today_vol_min": 1.3, "body_ratio_min": 0.4, "require_above_ma": "ma20"},
        "engine_params": {"use_atr_exit": True, "atr_stop_mult": 1.9, "atr_trail_mult": 2.5, "atr_trail_trigger": 0.05, "market_filter": True, "max_holding_days": 10},
        "holding_days": 10,
        "composite_score": 5.16,
        "variant_of": None,
        "performance": {"total_return": 0.8154, "win_rate": 0.5200, "sharpe": 1.56, "max_drawdown": -0.1362, "profit_factor": 2.35, "n_trades": 100},
    },
}

B_TIER_FINAL: dict[str, dict[str, Any]] = {
    "opt_FC_EV_A2_1_gc3m": {
        "name": "月线MACD金叉事件因子排名(RANGE_POS3/20D 0.6/0.4,gc3m,filter30%)",
        "module": "tradingagents.quant.strategy.factor_ranked_event",
        "class": "FactorRankedEventStrategy",
        "params": {"name": "FC_EV_A2_1", "event_type": "monthly_macd_golden_cross", "factor_weights": {"RANGE_POS3": 0.6, "RANGE_POS_20D": 0.4}, "event_params": {"universe_topk": 300, "golden_cross_recent_months": 3}, "filter_top_pct": 30.0, "selection_mode": "ranked", "universe_topk": 300, "top_k": 10, "direction": "top", "min_stocks_for_signal": 1},
        "engine_params": {"use_atr_exit": True, "atr_stop_mult": 1.9, "atr_trail_mult": 2.5, "atr_trail_trigger": 0.05, "market_filter": False, "max_holding_days": 20, "top_k": 10, "max_positions": 10, "initial_capital": 30000000},
        "holding_days": 20,
        "composite_score": 4.49,
        "variant_of": "FC_EV_A2_1",
        "performance": {"total_return": 0.5629, "win_rate": 0.5085, "sharpe": 1.54, "max_drawdown": -0.1608, "profit_factor": 2.09, "n_trades": 236},
    },
    "M_m_rsi_bo_strict": {
        "name": "月线RSI突破(严格,信号出场)",
        "module": "tradingagents.quant.strategy.monthly_rsi_breakout",
        "class": "MonthlyRsiBreakoutStrategy",
        "params": {"rsi_min": 60.0, "rsi_max": 75.0, "today_vol_min": 1.8, "body_ratio_min": 0.6, "enable_signal_exit": True, "exit_min_holding_days": 3},
        "engine_params": {"use_atr_exit": True, "atr_stop_mult": 1.9, "atr_trail_mult": 2.5, "atr_trail_trigger": 0.05, "market_filter": True, "max_holding_days": 999},
        "holding_days": 999,
        "composite_score": 3.72,
        "variant_of": None,
        "performance": {"total_return": 0.6400, "win_rate": 0.4583, "sharpe": 1.10, "max_drawdown": -0.2357, "profit_factor": 2.29, "n_trades": 48},
    },
}

# 下游兼容字段 (quant_picker.py 等在用): new_performance / new_composite_score / tier_label / description / logic
for _tier, _tier_dict in (("S", S_TIER_FINAL), ("A", A_TIER_FINAL), ("B", B_TIER_FINAL)):
    for _entry in _tier_dict.values():
        _perf = _entry["performance"]
        _entry["new_performance"] = dict(_perf)
        _entry["new_composite_score"] = _entry["composite_score"]
        _entry["tier_label"] = _tier
        _entry["description"] = _entry["name"]
        _entry["logic"] = (
            f"OOS 区间(2025-01-01~2026-07-14)累计收益 {_perf['total_return'] * 100:+.1f}%"
            f"({_perf['n_trades']} 笔),不是单笔或持有期平均收益;"
            f"最大回撤 {_perf['max_drawdown'] * 100:.1f}%;"
            f"胜率 {_perf['win_rate'] * 100:.1f}%"
        )
del _tier, _tier_dict, _entry, _perf

NEW_TIERS_FINAL: dict[str, list[str]] = {
    "S": list(S_TIER_FINAL),
    "A": list(A_TIER_FINAL),
    "B": list(B_TIER_FINAL),
}

ACTIVE_FINAL: set[str] = set()
for _t in ("S", "A", "B"):
    ACTIVE_FINAL.update(NEW_TIERS_FINAL[_t])


def get_all_strategies_final() -> dict[str, dict]:
    """返回 18 个有效策略(S=5 A=11 B=2)。"""
    all_strats = {**S_TIER_FINAL, **A_TIER_FINAL, **B_TIER_FINAL}
    return {n: all_strats[n] for n in ACTIVE_FINAL if n in all_strats}


def get_tier_of_final(name: str) -> str:
    """返回策略的分级(S/A/B/UNKNOWN)。"""
    for tier, names in NEW_TIERS_FINAL.items():
        if name in names:
            return tier
    return "UNKNOWN"


FINAL_STATS = {"total": 18, "active": 18, "deprecated": 0, "S": 5, "A": 11, "B": 2, "C": 0, "cycles": "top18 (2026-08-16, OOS 2025-01-01~2026-07-14)"}


# ====== 去除的底部 6 家族 (2026-08-16, R46-R51 攻坚 23 提案全拒) ======
# OOS comp <= 3.77 / mdd -29%~-37%, 信号定义本身弱 (日线质量不足),
# 入场收紧/放宽、出场结构、死代码激活、资金利用率四类机制全部证伪,
# 等权组合中只起稀释作用, 实盘排除。
EXCLUDED_BOTTOM6: dict[str, dict[str, Any]] = {
    "M_m_tr_hlth_default": {"oos_comp": -0.78, "mdd": -0.3750, "reason": "健康破位/日线腿/斜率/3仓 4 连拒"},
    "M_mtf_default": {"oos_comp": 2.10, "mdd": -0.2180, "reason": "MA5出场/3仓 全 overfit"},
    "M_mwd_res_strict": {"oos_comp": 2.67, "mdd": -0.1310, "reason": "突破窗 10d no-op, n=24 无法统计"},
    "M_w_adx_pb_loose": {"oos_comp": 2.75, "mdd": -0.2980, "reason": "缩量mask/DI门/3仓 全拒"},
    "M_m_rsi_bo_loose": {"oos_comp": 3.44, "mdd": -0.2390, "reason": "RSI拐头 -0.75/3仓稀释"},
    "M_q_bo_pb_loose": {"oos_comp": 3.77, "mdd": -0.3120, "reason": "MA10出场/同日剔除/3仓 全拒"},
}
