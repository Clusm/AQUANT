"""Conflict Resolver LangGraph node.

Merges the quant pre-filter signal (systematic, active strategy library)
with the LLM Portfolio Manager decision (deep reasoning) and outputs a
final ranked decision with a 🟢/🟡/🟠/🔴 label.

Simplified implementation (no Compare LLM):
- Rule-based label assignment, no LLM call
- Reads state["quant_pick_context"] (quant signal) and state["final_trade_decision"] (LLM signal)
- Outputs state["final_ranked_decision"] as markdown string

Three quant states (not just hit/miss):
- "hit": quant layer ran and ticker hit >=1 strategy (positive signal).
         quant_pick_context is populated with hit info.
- "miss": quant layer ran but ticker hit 0 strategies (negative anchor).
          quant_pick_context contains "未命中任何量化策略" text.
          Appropriate to penalize LLM Buy -> 🟡关注.
- "skipped": quant layer intentionally skipped. quant_pick_context is empty.
             This only happens for MANUAL tickers (ticker_source="manual")
             where quant_picker_node skips injection to avoid penalizing
             the user's explicit selection. Conflict Resolver must NOT
             downgrade LLM verdict in this case.
- "error": quant layer crashed (quant_picker_node writes "[量化层错误] ...").
           No reliable quant signal - treated like "skipped" (NO downgrade of
           the LLM verdict). An error is NOT a real miss, so it must not
           create a negative anchor.

Label rules (per INTEGRATION_PLAN.md section 7, updated for 3-state logic):
- 🟢 强买: quant hit + LLM Buy/Overweight, OR quant skipped + LLM Buy/Overweight
- 🟡 关注: quant hit + LLM Hold, OR quant miss + LLM Buy/Overweight,
           OR quant skipped + LLM Hold, OR quant hit/skipped + LLM Unknown
- 🟠 冲突: quant hit + LLM Sell/Underweight (user judgment needed)
- 🔴 弃: quant miss + LLM Sell/Underweight, OR quant miss + LLM Hold,
         OR quant skipped + LLM Sell, OR quant miss + LLM Unknown
"""
from __future__ import annotations

import logging
import re
from typing import Any, Callable

logger = logging.getLogger(__name__)


def _detect_quant_state(quant_context: str) -> tuple[str, dict]:
    """Classify quant signal into 4 states.

    Returns (state, info) where state is one of:
    - "hit": quant layer ran and ticker hit >=1 strategy (positive signal)
    - "miss": quant layer ran but ticker hit 0 strategies (negative anchor)
    - "skipped": quant layer intentionally skipped (manual ticker not in saved
                 Top N, or no saved JSON). Should NOT penalize LLM decision.
    - "error": quant layer crashed. No reliable signal, should NOT penalize
               the LLM decision (treated like skipped).

    Note: empty quant_context only occurs for manual tickers (quant_picker_node
    skips injection to avoid negative anchor). For quant_picker tickers,
    context is always populated with either hit info or "未命中" text.
    """
    if not quant_context:
        return "skipped", {}

    # Quant layer crashed (quant_picker_node writes "[量化层错误] ...").
    # An error is NOT a real miss: no negative anchor, must not downgrade
    # the LLM verdict.
    if quant_context.startswith("[量化层错误]"):
        return "error", {}

    # Look for "命中策略数: N"
    m = re.search(r"命中策略数:\s*(\d+)", quant_context)
    n_strategies = int(m.group(1)) if m else 0

    # Look for "加权分: X.XX"
    m = re.search(r"加权分:\s*([\d.]+)", quant_context)
    weighted_score = float(m.group(1)) if m else 0.0

    # Look for "加权胜率: XX.X%"
    m = re.search(r"加权胜率:\s*([\d.]+)%", quant_context)
    win_rate = float(m.group(1)) / 100 if m else 0.0

    # "未命中任何量化策略" means explicit miss
    no_hit = "未命中任何量化策略" in quant_context

    if n_strategies > 0 and not no_hit:
        return "hit", {
            "n_strategies": n_strategies,
            "weighted_score": weighted_score,
            "win_rate": win_rate,
        }
    return "miss", {}


def _detect_llm_rating(final_trade_decision: str) -> str:
    """Extract LLM rating from final_trade_decision markdown.

    Handles three LLM output shapes:
    1. Structured path: ``**Rating**: Buy`` (rendered by render_pm_decision in schemas.py)
    2. Free-text fallback: LLM still tends to emit "Rating: Buy" or "评级: Buy" because
       the prompt forces a 5-tier Rating Scale. Match both English and Chinese labels.
    3. Bare keyword: LLM ends with a single rating word on the last line.

    Returns one of: 'Buy', 'Overweight', 'Hold', 'Underweight', 'Sell', 'Unknown'.
    """
    if not final_trade_decision:
        return "Unknown"

    VALID = ("Buy", "Overweight", "Hold", "Underweight", "Sell")
    # Match the rating keyword allowing internal hyphens (e.g. "under-weight") and
    # case-insensitive. Trailing word boundary prevents partial matches like "Buyer".
    KW = r"(Buy|Overweight|Hold|Underweight|Sell|Under-weight|Over-weight)\b"

    # Pattern 1: **Rating**: Buy / Rating: **Buy** / **Rating**:**Buy**
    m = re.search(
        r"\*?\*?Rating\*?\*?\s*[:：]\s*\*?\*?\s*" + KW,
        final_trade_decision, re.IGNORECASE,
    )
    if m:
        return _normalize_rating(m.group(1), VALID)

    # Pattern 2: Chinese label - "评级" / "推荐" / "决策" / "建议" + : + rating
    m = re.search(
        r"(?:评级|推荐|决策|建议|最终评级|投资评级)\s*[:：]\s*\*?\*?\s*" + KW,
        final_trade_decision, re.IGNORECASE,
    )
    if m:
        return _normalize_rating(m.group(1), VALID)

    # Pattern 3: "Decision: Buy" (English free-text variant)
    m = re.search(
        r"\b(?:Decision|Final Decision|Recommendation)\s*[:：]\s*\*?\*?\s*" + KW,
        final_trade_decision, re.IGNORECASE,
    )
    if m:
        return _normalize_rating(m.group(1), VALID)

    # Pattern 4: Trader-style fallback (kept for safety; final_trade_decision is PM output
    # so this rarely matches, but if PM echoes the Trader's proposal it still works).
    m = re.search(
        r"FINAL TRANSACTION PROPOSAL:\s*\*\*\s*" + KW + r"\s*\*\*",
        final_trade_decision, re.IGNORECASE,
    )
    if m:
        return _normalize_rating(m.group(1), VALID)

    # Pattern 5: Last non-empty line is just a rating keyword (possibly with markdown
    # emphasis or trailing punctuation).
    lines = [ln.strip() for ln in final_trade_decision.strip().splitlines() if ln.strip()]
    if lines:
        last = lines[-1].strip("*_` ").rstrip(".。! !?").strip()
        for v in VALID:
            if last.lower() == v.lower():
                return v

    return "Unknown"


def _normalize_rating(raw: str, valid: tuple[str, ...]) -> str:
    """Normalize matched rating string to canonical form (e.g. 'under-weight' -> 'Underweight')."""
    canonical = raw.strip().lower().replace("-", "").capitalize()
    for v in valid:
        if canonical == v:
            return v
    return "Unknown"


def _assign_label(quant_state: str, quant_info: dict, llm_rating: str) -> tuple[str, str, str]:
    """Assign label based on quant state + LLM signal.

    quant_state is one of "hit" / "miss" / "skipped".
    See module docstring for the full label matrix.
    """
    n_strat = quant_info.get("n_strategies", 0)
    ws = quant_info.get("weighted_score", 0.0)
    wr = quant_info.get("win_rate", 0.0)

    llm_buy_like = llm_rating in ("Buy", "Overweight")
    llm_sell_like = llm_rating in ("Sell", "Underweight")
    llm_hold = llm_rating == "Hold"
    llm_unknown = llm_rating == "Unknown"

    # ── skipped / error: quant layer not consulted or crashed ──────────
    # Respect LLM verdict without quant-based downgrade. skipped = manual
    # ticker (user explicitly chose it), error = quant layer failed; neither
    # should create a negative anchor.
    if quant_state in ("skipped", "error"):
        no_quant_reason = (
            "手动选股,量化层未参与评估。" if quant_state == "skipped"
            else "量化层运行异常,未提供有效量化信号。"
        )
        if llm_buy_like:
            label = "🟢 强买"
            recommendation = "Buy"
            rationale = (
                f"{no_quant_reason}LLM 决策 {llm_rating},"
                "尊重 LLM 判断,无量化层降级。"
            )
        elif llm_hold:
            label = "🟡 关注"
            recommendation = "Watch"
            rationale = f"{no_quant_reason}LLM 决策 Hold,建议关注。"
        elif llm_sell_like:
            label = "🔴 弃"
            recommendation = "Skip"
            rationale = (
                f"{no_quant_reason}LLM 决策 {llm_rating} 看空,建议放弃。"
            )
        else:
            label = "🟡 关注"
            recommendation = "Watch"
            rationale = (
                f"{no_quant_reason}LLM 决策未识别({llm_rating}),建议关注。"
            )
        return label, recommendation, rationale

    # ── hit or miss: quant layer ran ────────────────────────────────────
    quant_hit = quant_state == "hit"

    if quant_hit and llm_buy_like:
        label = "🟢 强买"
        recommendation = "Buy"
        rationale = (
            f"量化层命中 {n_strat} 个策略(加权分 {ws:.2f},胜率 {wr*100:.1f}%),"
            f"LLM 决策 {llm_rating},双重买入信号共振。"
        )
    elif quant_hit and llm_hold:
        label = "🟡 关注"
        recommendation = "Watch"
        rationale = (
            f"量化层命中 {n_strat} 个策略(加权分 {ws:.2f},胜率 {wr*100:.1f}%),"
            f"但 LLM 决策 Hold。量化看好但 LLM 谨慎,建议关注。"
        )
    elif quant_hit and llm_sell_like:
        label = "🟠 冲突"
        recommendation = "User Judgment"
        rationale = (
            f"量化层命中 {n_strat} 个策略(加权分 {ws:.2f},胜率 {wr*100:.1f}%)看好,"
            f"但 LLM 决策 {llm_rating} 看空。量化与 LLM 信号冲突,需用户自行判断。"
        )
    elif not quant_hit and llm_buy_like:
        label = "🟡 关注"
        recommendation = "Watch"
        rationale = (
            f"量化层未命中,但 LLM 决策 {llm_rating} 看好。"
            "LLM 独立信号,无量化共振,建议谨慎关注。"
        )
    elif not quant_hit and llm_sell_like:
        label = "🔴 弃"
        recommendation = "Skip"
        rationale = (
            f"量化层未命中,LLM 决策 {llm_rating} 看空。双重负面信号,建议放弃。"
        )
    elif not quant_hit and llm_hold:
        label = "🔴 弃"
        recommendation = "Skip"
        rationale = "量化层未命中,LLM 决策 Hold。无买入信号,建议放弃。"
    else:
        # quant_hit + llm_unknown, or other edge cases
        if quant_hit:
            label = "🟡 关注"
            recommendation = "Watch"
            rationale = (
                f"量化层命中 {n_strat} 个策略(加权分 {ws:.2f},胜率 {wr*100:.1f}%),"
                f"LLM 决策未识别({llm_rating})。仅量化信号,建议关注。"
            )
        else:
            label = "🔴 弃"
            recommendation = "Skip"
            rationale = "量化层未命中,LLM 决策未识别。无任何买入信号,建议放弃。"

    return label, recommendation, rationale


def create_conflict_resolver() -> Callable[[Any], dict]:
    """Factory: create a Conflict Resolver LangGraph node.

    The node reads state["quant_pick_context"] and state["final_trade_decision"],
    merges them via rule-based logic, and writes the result to
    state["final_ranked_decision"] as a markdown string.
    """
    def conflict_resolver_node(state) -> dict:
        try:
            quant_context = state.get("quant_pick_context", "") if state else ""
            final_trade_decision = state.get("final_trade_decision", "") if state else ""
            ticker = state.get("company_of_interest", "?") if state else "?"
            trade_date = state.get("trade_date", "?") if state else "?"

            quant_state, quant_info = _detect_quant_state(quant_context)
            llm_rating = _detect_llm_rating(final_trade_decision)
            label, recommendation, rationale = _assign_label(quant_state, quant_info, llm_rating)

            # Build quant signal summary
            if quant_state == "hit":
                quant_signal = (
                    f"命中 {quant_info['n_strategies']} 策略, "
                    f"加权分 {quant_info['weighted_score']:.2f}, "
                    f"胜率 {quant_info['win_rate']*100:.1f}%"
                )
            elif quant_state == "miss":
                quant_signal = "未命中任何量化策略"
            elif quant_state == "error":
                quant_signal = "量化层运行异常,未提供量化信号"
            else:
                quant_signal = "量化层未参与评估(手动选股,无负面锚)"

            # Build LLM signal summary (truncate to keep final_ranked_decision readable)
            llm_signal_preview = final_trade_decision[:500] + "..." if len(final_trade_decision) > 500 else final_trade_decision
            llm_signal = f"Rating: {llm_rating}\n{llm_signal_preview}"

            # Assemble final markdown
            lines = [
                f"# 综合推荐: {ticker} ({trade_date})",
                "",
                f"**标签**: {label}",
                f"**建议**: {recommendation}",
                "",
                "## 量化信号",
                quant_signal,
                "",
                "## LLM 信号",
                llm_signal,
                "",
                "## 综合研判",
                rationale,
                "",
                "---",
                "注:量化层为系统性信号(策略库),LLM 为深度推理。冲突时展示双方观点,用户做最终判断。手动选股时量化层不参与评估,尊重 LLM 判断。",
            ]
            final_ranked_decision = "\n".join(lines)

            logger.info(
                "Conflict resolved for %s: label=%s, quant_state=%s, llm_rating=%s",
                ticker, label, quant_state, llm_rating,
            )

            return {"final_ranked_decision": final_ranked_decision, "final_signal_label": label}
        except Exception as exc:
            # Never crash the LangGraph pipeline. Fall back to a discard label
            # with the error embedded so the user sees something went wrong.
            logger.exception("Conflict resolver failed for state=%r", state)
            try:
                ticker = (state.get("company_of_interest") if hasattr(state, "get") else "?") or "?"
                trade_date = (state.get("trade_date") if hasattr(state, "get") else "?") or "?"
            except Exception:
                ticker, trade_date = "?", "?"
            err_msg = f"[冲突解决失败] {type(exc).__name__}: {str(exc)[:200]}"
            fallback = "\n".join([
                f"# 综合推荐: {ticker} ({trade_date})",
                "",
                "**标签**: 🔴 弃",
                "**建议**: Skip",
                "",
                "## 综合研判",
                err_msg,
                "",
                "注:Conflict Resolver 节点异常,已降级为弃档。请检查上游 final_trade_decision / quant_pick_context 字段。",
            ])
            return {"final_ranked_decision": fallback, "final_signal_label": "🔴 弃"}

    return conflict_resolver_node
