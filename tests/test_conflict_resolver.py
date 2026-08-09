"""Conflict Resolver 4-state detection + 🟢🟡🟠🔴 label matrix."""

import pytest

from tradingagents.agents.conflict_resolver import (
    _assign_label,
    _detect_llm_rating,
    _detect_quant_state,
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
