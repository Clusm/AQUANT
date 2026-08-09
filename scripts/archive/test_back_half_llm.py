"""最小化测试:直接调用后半段 LLM 节点,定位 structured-output 400 错误根因。

跳过 quant picker / 7 analysts / data fetch,直接构造 state 喂给:
- Research Manager (deepseek-v4-pro + ResearchPlan schema)
- Trader (deepseek-v4-flash + TraderProposal schema)
- Portfolio Manager (deepseek-v4-pro + PortfolioDecision schema)

验证:
1. bind_structured 是否成功 (DeepSeek 通过代理时 with_structured_output 是否可用)
2. structured_llm.invoke 是否触发 400
3. 失败时 fallback 到 free text 是否正常
4. plain_llm.invoke 走 free text 单次调用是否成功 (验证代理本身可用)
5. 计时每个调用,量化"双倍耗时"代价
"""
from __future__ import annotations

import os
import sys
import time
import traceback
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# Windows 控制台 GBK 兼容
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from dotenv import load_dotenv
load_dotenv(_PROJECT_ROOT / ".env", override=True)

from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.llm_clients import create_llm_client
from tradingagents.agents.schemas import (
    PortfolioDecision, ResearchPlan, TraderProposal,
    render_pm_decision, render_research_plan, render_trader_proposal,
)
from tradingagents.agents.utils.structured import bind_structured, invoke_structured_or_freetext


def _make_llm(model: str):
    """Create an LLM client exactly as TradingAgentsGraph does."""
    client = create_llm_client(
        provider="deepseek",
        model=model,
        base_url=os.getenv("BACKEND_URL") or None,
    )
    return client.get_llm()


def _section(title: str) -> None:
    print(f"\n{'=' * 70}\n{title}\n{'=' * 70}")


def _trial(label: str, fn) -> None:
    t0 = time.time()
    try:
        result = fn()
        elapsed = time.time() - t0
        preview = str(result)[:200].replace("\n", " ")
        print(f"[OK ] {label:45s}  {elapsed:6.2f}s  -> {preview}")
    except Exception as exc:
        elapsed = time.time() - t0
        msg = f"{type(exc).__name__}: {str(exc)[:200]}"
        print(f"[ERR] {label:45s}  {elapsed:6.2f}s  -> {msg}")


def test_structured_bind(llm, schema, name):
    """Test 1: bind_structured alone (does with_structured_output succeed?)."""
    _trial(f"{name}: bind_structured", lambda: bind_structured(llm, schema, name))


def test_structured_invoke(llm, schema, name, prompt):
    """Test 2: full structured invoke path (bind + invoke + render)."""
    def go():
        structured_llm = bind_structured(llm, schema, name)
        if structured_llm is None:
            return "bind returned None"
        result = structured_llm.invoke(prompt)
        if name == "Research Manager":
            return render_research_plan(result)[:200]
        if name == "Trader":
            return render_trader_proposal(result)[:200]
        return render_pm_decision(result)[:200]
    _trial(f"{name}: structured invoke", go)


def test_plain_invoke(llm, name, prompt):
    """Test 3: plain free-text invoke (single call, no structured)."""
    _trial(f"{name}: plain invoke", lambda: llm.invoke(prompt).content[:200])


def test_invoke_structured_or_freetext(llm, schema, name, prompt, render):
    """Test 4: the canonical path used by all 3 agents."""
    structured_llm = bind_structured(llm, schema, name)
    def go():
        return invoke_structured_or_freetext(
            structured_llm, llm, prompt, render, name,
        )[:200]
    _trial(f"{name}: invoke_structured_or_freetext", go)


def main() -> int:
    _section("LLM 后半段调用诊断")
    print(f"backend_url: {os.getenv('BACKEND_URL')}")
    print(f"DEEPSEEK_API_KEY last 4: {(os.getenv('DEEPSEEK_API_KEY') or '')[-4:]}")

    # Build LLMs (no callbacks, just like the agent factories see them)
    print("\n[setup] 创建 LLM 客户端...")
    deep_llm = _make_llm("deepseek-v4-pro")
    quick_llm = _make_llm("deepseek-v4-flash")
    print(f"  deep  model_name={deep_llm.model_name}")
    print(f"  quick model_name={quick_llm.model_name}")

    # ----- Prompts (mirrors of the actual agent prompts) -----
    research_prompt = (
        "As the Research Manager, deliver a clear, actionable investment plan for "
        "ticker 600881 on 2026-07-17.\n\n"
        "Debate history:\n"
        "Bull Analyst: 量化层命中 1 个策略 (M_KKK_lvb_loose), 加权分 0.69, 胜率 50%, "
        "短线动量信号积极。\n"
        "Bear Analyst: T+1 锁仓风险, 涨停板效应退潮后流动性枯竭。\n\n"
        "Use the 5-tier Rating Scale (Buy / Overweight / Hold / Underweight / Sell).\n"
        "Write your entire response in Chinese."
    )

    trader_messages = [
        {"role": "system", "content": "You are a trading agent for A-share stocks."},
        {"role": "user", "content": (
            "Based on the investment plan: **Recommendation**: Buy\n"
            "**Rationale**: 量化信号 + 短线动量共振。\n"
            "Translate this into a concrete transaction proposal for 600881. "
            "Be specific about entry price, stop loss, position sizing. "
            "Write your entire response in Chinese."
        )},
    ]

    pm_prompt = (
        "As the Portfolio Manager, deliver the final trading decision for 600881.\n\n"
        "Research Manager's investment plan: **Recommendation**: Buy\n"
        "Trader's transaction proposal: **Action**: Buy, entry=12.50, stop=11.80, sizing=5%\n\n"
        "Risk Analysts Debate History:\n"
        "Aggressive: 量化 + 游资共振, 仓位可加到 8%。\n"
        "Conservative: T+1 锁仓, 建议仓位 3% 以内。\n"
        "Neutral: 折中 5%。\n\n"
        "Use the 5-tier Rating Scale (Buy / Overweight / Hold / Underweight / Sell).\n"
        "Write your entire response in Chinese."
    )

    # ----- Test 1: bind_structured -----
    _section("Test 1: bind_structured (with_structured_output 是否可用)")
    test_structured_bind(deep_llm, ResearchPlan, "Research Manager")
    test_structured_bind(quick_llm, TraderProposal, "Trader")
    test_structured_bind(deep_llm, PortfolioDecision, "Portfolio Manager")

    # ----- Test 2: structured invoke (完整路径, 会触发 400 if unsupported) -----
    _section("Test 2: structured invoke (bind + invoke + render)")
    test_structured_invoke(deep_llm, ResearchPlan, "Research Manager", research_prompt)
    test_structured_invoke(quick_llm, TraderProposal, "Trader", trader_messages)
    test_structured_invoke(deep_llm, PortfolioDecision, "Portfolio Manager", pm_prompt)

    # ----- Test 3: plain invoke (单次 free-text, 验证代理本身可用) -----
    _section("Test 3: plain invoke (free text, 验证代理可用性)")
    test_plain_invoke(deep_llm, "Research Manager", research_prompt)
    test_plain_invoke(quick_llm, "Trader", trader_messages)
    test_plain_invoke(deep_llm, "Portfolio Manager", pm_prompt)

    # ----- Test 4: invoke_structured_or_freetext (生产路径) -----
    _section("Test 4: invoke_structured_or_freetext (生产路径)")
    test_invoke_structured_or_freetext(
        deep_llm, ResearchPlan, "Research Manager", research_prompt, render_research_plan,
    )
    test_invoke_structured_or_freetext(
        quick_llm, TraderProposal, "Trader", trader_messages, render_trader_proposal,
    )
    test_invoke_structured_or_freetext(
        deep_llm, PortfolioDecision, "Portfolio Manager", pm_prompt, render_pm_decision,
    )

    _section("完成")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        traceback.print_exc()
        sys.exit(2)
