"""Conflict Resolver 4-state detection + 🟢🟡🟠🔴 label matrix."""

import pytest

from tradingagents.agents.conflict_resolver import (
    _assign_label,
    _detect_llm_rating,
    _detect_quant_state,
    compute_conviction,
)

HIT = "该股命中策略数: 2, 加权分: 5.21, 加权胜率: 41.7%"
MISS = "该股未命中任何量化策略"
SKIPPED = ""
ERROR = "[量化层错误] TimeoutError: xxx"


@pytest.mark.parametrize("ctx,expected", [
    (HIT, "hit"),
    (MISS, "miss"),
    (SKIPPED, "skipped"),
    # 量化层异常 ≠ miss:不得生成负面锚,必须单独识别
    (ERROR, "error"),
])
def test_detect_quant_state(ctx, expected):
    state, _ = _detect_quant_state(ctx)
    assert state == expected


@pytest.mark.parametrize("pm,expected", [
    ("**Rating**: Buy", "Buy"),
    ("Rating: Overweight", "Overweight"),
    ("评级: Hold", "Hold"),
    ("Decision: Underweight", "Underweight"),
    ("最终评级：**减持 (Underweight)**", "Underweight"),
    ("评级：买入", "Buy"),
    ("最终评级：减持", "Underweight"),
    ("**Rating**: Sell", "Sell"),
    ("", "Unknown"),
    ("没有评级", "Unknown"),
])
def test_detect_llm_rating(pm, expected):
    assert _detect_llm_rating(pm) == expected


@pytest.mark.parametrize("qstate,pm,expected_label", [
    ("hit", "Buy", "🟢 强买"),
    ("hit", "Hold", "🟡 关注"),
    ("hit", "Sell", "🟠 冲突"),
    ("hit", "Unknown", "🟡 关注"),
    ("miss", "Buy", "🟡 关注"),
    ("miss", "Sell", "🔴 弃"),
    ("miss", "Hold", "🔴 弃"),
    ("miss", "Unknown", "🔴 弃"),
    ("skipped", "Buy", "🟢 强买"),
    ("skipped", "Hold", "🟡 关注"),
    ("skipped", "Sell", "🔴 弃"),
    ("skipped", "Unknown", "🟡 关注"),
    # error 态同 skipped:尊重 LLM,不降级
    ("error", "Buy", "🟢 强买"),
    ("error", "Hold", "🟡 关注"),
    ("error", "Sell", "🔴 弃"),
    ("error", "Unknown", "🟡 关注"),
])
def test_assign_label_matrix(qstate, pm, expected_label):
    label, _, _ = _assign_label(qstate, {}, pm)
    assert label == expected_label


HIT_STRONG = {"n_strategies": 2, "weighted_score": 8.0, "win_rate": 0.6}
HIT_WEAK = {"n_strategies": 1, "weighted_score": 0.0, "win_rate": 0.0}


@pytest.mark.parametrize("qstate,qinfo,pm,expected", [
    # 量化强 + LLM 买入 = 最高分
    ("hit", HIT_STRONG, "Buy", 96),
    ("hit", HIT_STRONG, "Overweight", 96),
    # 量化命中 + LLM Hold:量化主导,高分关注
    ("hit", HIT_STRONG, "Hold", 84),
    # 量化命中 + LLM 看空(冲突档):中低分,量化占优但需用户判断
    ("hit", HIT_STRONG, "Underweight", 51),
    ("hit", HIT_STRONG, "Sell", 51),
    # 无 LLM 评级 = 信息缺失,压低(实测 hit+Unknown 表现差)
    ("hit", HIT_STRONG, "Unknown", 61),
    ("hit", HIT_WEAK, "Unknown", 40),
    # 量化未命中:分数受限,即使 LLM 看多也只 25(实测 miss+Buy 差)
    ("miss", {}, "Buy", 25),
    ("miss", {}, "Hold", 18),
    ("miss", {}, "Sell", 10),
    ("miss", {}, "Unknown", 15),
    # skipped/error:量化未参与,尊重 LLM
    ("skipped", {}, "Buy", 60),
    ("skipped", {}, "Unknown", 45),
    ("skipped", {}, "Sell", 30),
    ("error", {}, "Buy", 60),
    # 上限截断 100
    ("hit", {"n_strategies": 3, "weighted_score": 50.0, "win_rate": 1.0}, "Buy", 100),
])
def test_compute_conviction(qstate, qinfo, pm, expected):
    assert compute_conviction(qstate, qinfo, pm) == expected
