"""quant_picker.py 纯函数单元测试:入场建议、tier 分级、grouping 聚合、Top N 校验、摘要格式化。

不跑 multiprocessing / 不依赖真实缓存,只测可离线验证的逻辑。
"""

import pandas as pd
import pytest

from tradingagents.quant.quant_picker import (
    _aggregate,
    _compute_entry_advice,
    compute_top_n,
    format_top_picks_summary,
    get_tier_of,
    needs_full_data,
    pick,
)


# ============================================================
# _compute_entry_advice:入场建议生成
# ============================================================

class TestComputeEntryAdvice:
    def test_short_holding(self):
        info = {
            "holding_days": 5,
            "new_performance": {"win_rate": 0.60, "total_return": 0.25},
        }
        advice, holding = _compute_entry_advice(info)
        assert holding == 5
        assert "买:次日09:30开盘买入。短线5日,胜率60.0%/均收+25.00%" in advice
        assert "高开≥5%不买(追高风险)" in advice
        assert "低开≤-5%接近基线,谨慎可买" in advice

    def test_long_holding(self):
        info = {
            "holding_days": 15,
            "new_performance": {"win_rate": 0.70, "total_return": 2.2597},
        }
        advice, holding = _compute_entry_advice(info)
        assert holding == 15
        assert "买:次日10:00后,9:30-10:00收阳且10:00价在30min VWAP -1%~0%" in advice
        assert "中线15日,胜率70.0%/均收+225.97%" in advice

    def test_holding_days_none_defaults_to_short_5(self):
        advice, holding = _compute_entry_advice({})
        assert holding == 5
        assert "短线5日" in advice

    def test_zero_win_rate_and_return_shows_na(self):
        info = {"holding_days": 10, "new_performance": {}}
        advice, holding = _compute_entry_advice(info)
        assert "胜率N/A/均收N/A" in advice

    def test_no_new_performance_key(self):
        info = {"holding_days": 3}
        advice, _ = _compute_entry_advice(info)
        assert "短线3日" in advice
        assert "胜率N/A/均收N/A" in advice


# ============================================================
# needs_full_data:周/月/季策略需要全量数据
# ============================================================

class TestNeedsFullData:
    @pytest.mark.parametrize("module,expected", [
        ("tradingagents.quant.strategy.monthly_weekly_daily_resonance", True),
        ("tradingagents.quant.strategy.weekly_macd_golden_cross", True),
        ("tradingagents.quant.strategy.quarterly_xxx", True),
        ("tradingagents.quant.strategy.daily_breakout", False),
        ("tradingagents.quant.strategy.zz_lpb_ma10", False),
    ])
    def test_module_keyword(self, module, expected):
        assert needs_full_data({"module": module}) == expected

    def test_missing_module(self):
        assert needs_full_data({}) is False


# ============================================================
# get_tier_of:Final 分级 + 中线 M_ 前缀
# ============================================================

class TestGetTierOf:
    def test_short_keeps_base_tier(self, monkeypatch):
        from tradingagents.quant import quant_picker
        monkeypatch.setattr(quant_picker, "get_tier_of_final", lambda name: "S")
        assert get_tier_of("S_short", {"holding_days": 3}) == "S"

    def test_midline_adds_m_prefix(self, monkeypatch):
        from tradingagents.quant import quant_picker
        monkeypatch.setattr(quant_picker, "get_tier_of_final", lambda name: "A")
        assert get_tier_of("A_mid", {"holding_days": 15}) == "M_A"

    @pytest.mark.parametrize("name,tier,expected", [
        # 真实中线策略:holding_days>5,加 M_ 前缀
        ("M_mwd_res_loose", "S", "M_S"),
        ("M_m_rsi_bo_loose", "A", "M_A"),
    ])
    def test_real_midline_strategies(self, name, tier, expected):
        assert get_tier_of(name, {"holding_days": 15}) == expected

    def test_deprecated_falls_back_to_c(self, monkeypatch):
        from tradingagents.quant import quant_picker
        monkeypatch.setattr(
            quant_picker, "get_tier_of_final",
            lambda name: "DEPRECATED",
        )
        assert get_tier_of("any", {"holding_days": 3}) == "C"


# ============================================================
# _aggregate / compute_top_n:按 stock_code 分组聚合
# ============================================================

def _rec(code, tier, comp, win_rate, holding_days, strategy="S_xxx"):
    return {
        "date": pd.Timestamp("2026-07-17"),
        "strategy": strategy,
        "tier": tier,
        "strategy_comp": comp,
        "stock_code": code,
        "score": 1.0,
        "reason": "r",
        "holding_days": holding_days,
        "entry_advice": "a",
        "win_rate": win_rate,
    }


class TestAggregate:
    def test_grouping_weighted_metrics(self):
        records = [
            _rec("600000", "S", 10.0, 0.8, 5, "S_a"),
            _rec("600000", "A", 5.0, 0.6, 15, "A_b"),
            _rec("000001", "B", 7.0, 0.5, 10, "B_c"),
        ]
        agg = _aggregate(records)
        assert list(agg["stock_code"]) == ["600000", "000001"]  # 按 weighted_score 降序
        row0 = agg[agg["stock_code"] == "600000"].iloc[0]
        row1 = agg[agg["stock_code"] == "000001"].iloc[0]
        assert row0["n_strategies"] == 2
        assert row0["weighted_score"] == pytest.approx(15.0)
        # 加权胜率 = (0.8*10 + 0.6*5) / 15
        assert row0["avg_win_rate"] == pytest.approx((0.8 * 10 + 0.6 * 5) / 15)
        # 加权持仓天 = (5*10 + 15*5) / 15
        assert row0["avg_holding_days"] == pytest.approx((5 * 10 + 15 * 5) / 15)
        assert row1["n_strategies"] == 1
        assert row1["weighted_score"] == pytest.approx(7.0)

    def test_tier_counts(self):
        records = [
            _rec("600000", "S", 1.0, 0.5, 5, "a"),
            _rec("600000", "M_S", 1.0, 0.5, 15, "b"),
            _rec("600000", "A", 1.0, 0.5, 5, "c"),
        ]
        agg = _aggregate(records)
        row = agg[agg["stock_code"] == "600000"].iloc[0]
        assert row["n_S"] == 1
        assert row["n_M_S"] == 1
        assert row["n_A"] == 1
        assert row["n_B"] == 0

    def test_empty_records(self):
        agg = _aggregate([])
        assert len(agg) == 0
        assert "stock_code" in agg.columns

    def test_compute_top_n_slices_and_sorts(self):
        records = [_rec(f"{i:06d}", "A", float(i), 0.5, 5) for i in range(1, 8)]
        top5 = compute_top_n(records, top_n=5)
        assert len(top5) == 5
        assert top5.iloc[0]["weighted_score"] == pytest.approx(7.0)
        assert top5.iloc[4]["weighted_score"] == pytest.approx(3.0)


# ============================================================
# format_top_picks_summary:摘要格式化
# ============================================================

def _result_dict():
    top = pd.DataFrame([
        {"stock_code": "600000", "n_strategies": 2, "weighted_score": 15.0,
         "avg_win_rate": 0.7, "avg_holding_days": 8.3},
    ])
    return {
        "top_picks": top,
        "today": pd.Timestamp("2026-07-17"),
        "n_strategies_run": 10,
        "n_strategies_error": 0,
        "elapsed": 733.6,
        "all_records": [
            {"stock_code": "600000", "tier": "S", "strategy": "S_a",
             "strategy_comp": 10.0, "win_rate": 0.8, "holding_days": 5},
            {"stock_code": "600000", "tier": "A", "strategy": "A_b",
             "strategy_comp": 5.0, "win_rate": 0.6, "holding_days": 15},
        ],
    }


class TestFormatTopPicksSummary:
    def test_header_and_detail(self):
        text = format_top_picks_summary(_result_dict())
        assert "=== 量化选股 Top 1 (日期: 2026-07-17) ===" in text
        assert "策略数: 10, 错误: 0" in text
        assert "=== 命中策略详情 ===" in text
        assert "[S] S_a" in text
        assert "[A] A_b" in text

    def test_name_map_resolution(self):
        text = format_top_picks_summary(_result_dict(), name_map={"600000": "浦发银行"})
        assert "浦发银行" in text

    def test_empty_top_returns_hint(self):
        result = {**_result_dict(), "top_picks": pd.DataFrame()}
        assert format_top_picks_summary(result) == "无候选股票(策略库均未生成信号)"


# ============================================================
# pick() 参数校验:top_n 只允许 5/10/20
# ============================================================

class TestPickValidation:
    @pytest.mark.parametrize("bad_top_n", [7, 30, 3])
    def test_top_n_validation(self, bad_top_n):
        with pytest.raises(ValueError, match="top_n must be one of 5/10/20"):
            pick(today=pd.Timestamp("2026-07-17"), strategies={}, top_n=bad_top_n)

    @pytest.mark.parametrize("ok_top_n", [5, 10, 20])
    def test_top_n_accepts_valid(self, ok_top_n, monkeypatch, tmp_path):
        # 合法 top_n 不应触发参数校验 ValueError;
        # 用空缓存目录让它更快走到 FileNotFoundError,证明校验已通过。
        from tradingagents.quant import config
        monkeypatch.setattr(config, "_CACHE_DIR", tmp_path)
        with pytest.raises(FileNotFoundError):
            pick(today=pd.Timestamp("2026-07-17"), strategies={}, top_n=ok_top_n)
