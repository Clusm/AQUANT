"""Quant Picker LangGraph node.

Wraps tradingagents.quant.pick() into a LangGraph node. Runs the active
strategy library on the full A-share market, extracts the current ticker's
hit info from the Top N candidates, and writes a formatted context string to
state["quant_pick_context"].

Design:
- If state["quant_pick_context"] is already populated (batch mode pre-fill),
  the node is a no-op. This avoids re-running the 3-minute pick() per ticker.
- If empty (single-ticker propagate() mode), the node runs pick() and extracts
  the current ticker's info. Slow (~3 min) but functionally correct.
- The context string is appended to all 7 Analyst system messages via
  build_quant_context() in agent_utils.py.
"""
from __future__ import annotations

import logging
from typing import Any, Callable

import pandas as pd

from tradingagents.quant import pick as quant_pick

logger = logging.getLogger(__name__)


def _extract_ticker_context(result: dict, ticker: str) -> str:
    """Extract the current ticker's quant info from pick() result.

    Returns a formatted string with:
    - Top N candidates overview (so LLM sees the market context)
    - Current ticker's hit strategies, weighted score, win rate, entry advice
    - If current ticker not in Top N, note that explicitly
    """
    top_picks = result.get("top_picks")
    all_records = result.get("all_records", [])
    today = result.get("today")

    lines = [f"=== 量化选股上下文 (日期: {today.date() if today else 'N/A'}) ==="]

    # Top N overview
    if top_picks is not None and len(top_picks) > 0:
        lines.append(f"\n[全市场 Top {len(top_picks)} 候选]")
        lines.append("排名 | 代码 | 命中数 | 加权分 | 胜率")
        for i, row in top_picks.iterrows():
            # L1: avg_win_rate may be NaN for tickers with all-NaN win rates;
            # coerce to 0 to avoid "nan%" in the rendered context.
            wr = (row.get("avg_win_rate") or 0) * 100
            lines.append(
                f"{i+1} | {row['stock_code']} | "
                f"{int(row['n_strategies'])} | "
                f"{row['weighted_score']:.2f} | {wr:.1f}%"
            )
    else:
        lines.append("\n[全市场无候选 - 策略库均未生成信号]")

    # Current ticker detail
    ticker_str = str(ticker)
    ticker_records = [r for r in all_records if str(r.get("stock_code")) == ticker_str]

    if ticker_records:
        lines.append(f"\n[当前标的 {ticker_str} 量化命中详情]")
        total_comp = sum(r.get("strategy_comp", 0) for r in ticker_records)
        avg_wr = (
            sum(r.get("win_rate", 0) * r.get("strategy_comp", 0) for r in ticker_records) / total_comp
            if total_comp > 0 else 0
        )
        lines.append(f"命中策略数: {len(ticker_records)}")
        lines.append(f"加权分: {total_comp:.2f}")
        lines.append(f"加权胜率: {avg_wr*100:.1f}%")
        lines.append("\n命中策略:")
        # 查策略库拿 description / logic
        try:
            from tradingagents.quant.strategy.strategy_library_final import get_all_strategies_final
            _strat_lib = get_all_strategies_final()
        except Exception:
            _strat_lib = {}
        for r in ticker_records:
            hd = r.get("holding_days", "?")
            if isinstance(hd, (int, float)) and not pd.isna(hd):
                hd = int(hd)
            strat_name = r.get("strategy", "?")
            strat_info = _strat_lib.get(strat_name, {}) if _strat_lib else {}
            desc = strat_info.get("description", "")
            logic = strat_info.get("logic", "")
            lines.append(
                f"  - [{r.get('tier', '?')}] {strat_name} "
                f"(comp={r.get('strategy_comp', 0):.2f}, "
                f"胜率={r.get('win_rate', 0)*100:.0f}%, "
                f"持仓={hd}d)"
            )
            if desc:
                lines.append(f"    策略逻辑: {desc}")
            if logic:
                lines.append(f"    策略思路: {logic}")
            reason = r.get("reason", "")
            if reason:
                lines.append(f"    触发原因: {reason}")
        # M3: group entry advice by holding_days so short-term + mid-term hits
        # both show their distinct advice (previously only the first was shown,
        # misleading the user when e.g. hd=5 short-term and hd=15 mid-term both hit).
        advice_by_hd: dict[int, str] = {}
        for r in ticker_records:
            hd_raw = r.get("holding_days")
            try:
                hd_key = int(hd_raw) if hd_raw is not None else 0
            except (TypeError, ValueError):
                hd_key = 0
            advice = r.get("entry_advice", "")
            if advice and hd_key not in advice_by_hd:
                advice_by_hd[hd_key] = advice
        if advice_by_hd:
            lines.append("\n入场建议:")
            for hd_key in sorted(advice_by_hd):
                label = "短线" if hd_key <= 5 else "中线"
                lines.append(f"[{label} {hd_key}d] {advice_by_hd[hd_key]}")
    else:
        lines.append(f"\n[当前标的 {ticker_str} 未命中任何量化策略]")
        lines.append(
            "注意:该股票不在量化 Top N 候选中。可能是:"
            "(1) 流动性不足被 universe 过滤;"
            "(2) 策略库均未生成买入信号;"
            "(3) 该股票代码不在主板池中。"
        )

    return "\n".join(lines)


def create_quant_picker_node(config: dict | None = None) -> Callable[[Any], dict]:
    """Factory: create a Quant Picker LangGraph node.

    Args:
        config: Optional config dict with quant_layer_enabled, quant_daily_cache_name,
                quant_top_n_default, quant_n_workers, quant_slice_days, quant_top_k_per_strategy.
                If None, reads from default_config.

    Returns:
        node_fn(state) -> dict: writes "quant_pick_context" to state.
    """
    def quant_picker_node(state) -> dict:
        # If pre-populated (batch mode), no-op
        existing = state.get("quant_pick_context", "") if state else ""
        if existing:
            logger.info("Quant pick context pre-populated, skipping pick()")
            return {}

        # Resolve config
        if config is None:
            from tradingagents.default_config import DEFAULT_CONFIG
            cfg = DEFAULT_CONFIG
        else:
            cfg = config

        if not cfg.get("quant_layer_enabled", True):
            logger.info("Quant layer disabled, skipping pick()")
            return {}

        ticker = state.get("company_of_interest") if state else None
        trade_date = state.get("trade_date") if state else None
        if not ticker or not trade_date:
            logger.warning("Missing company_of_interest or trade_date, skipping pick()")
            return {}

        # Source-based branching: "manual" (user typed ticker) vs
        # "quant_picker" (selected from Top N). For manual input, we never
        # trigger a full-market scan (~3 min, 8 worker spawn) - if a saved
        # quant pick JSON exists for this trade_date we reuse it, otherwise
        # we skip the quant layer entirely. We also suppress the "未命中"
        # negative anchor when the manual-input ticker isn't in the saved
        # Top N, since the user didn't ask for a quant opinion.
        ticker_source = (state.get("ticker_source") if state else None) or "manual"

        # === Shared: try to load saved quant pick JSON ===
        saved_result = None
        try:
            from pathlib import Path
            import json as _json
            saved_path = Path.home() / ".tradingagents" / "quant_picks" / f"{trade_date}.json"
            if saved_path.exists():
                with open(saved_path, encoding="utf-8") as f:
                    saved = _json.load(f)
                if saved.get("all_records") is not None:
                    today_raw = saved.get("today") or trade_date
                    try:
                        today_ts = pd.Timestamp(today_raw)
                    except (TypeError, ValueError):
                        today_ts = pd.Timestamp(trade_date)
                    saved_result = {
                        "today": today_ts,
                        "top_picks": pd.DataFrame(saved.get("top_picks", [])),
                        "all_records": saved.get("all_records", []),
                    }
        except Exception as exc:
            logger.warning(
                "Failed to load saved quant pick for %s: %s",
                trade_date, exc,
            )

        if ticker_source == "manual":
            # Manual input: never trigger pick(). Only inject context if the
            # saved JSON exists AND the ticker is in it (positive signal).
            if saved_result is None:
                logger.info(
                    "Manual ticker %s: no saved quant pick for %s, skipping quant layer",
                    ticker, trade_date,
                )
                return {"quant_pick_context": ""}
            ticker_str = str(ticker)
            ticker_records = [
                r for r in saved_result["all_records"]
                if str(r.get("stock_code")) == ticker_str
            ]
            if not ticker_records:
                logger.info(
                    "Manual ticker %s not in saved Top N for %s, skipping quant context (no negative anchor)",
                    ticker, trade_date,
                )
                return {"quant_pick_context": ""}
            context = _extract_ticker_context(saved_result, ticker)
            logger.info(
                "Manual ticker %s hit saved quant pick for %s, injecting context",
                ticker, trade_date,
            )
            return {"quant_pick_context": context}

        # === ticker_source == "quant_picker" (or default) ===
        # Reuse saved JSON if loaded; otherwise run pick().
        if saved_result is not None:
            context = _extract_ticker_context(saved_result, ticker)
            logger.info(
                "Reused saved quant pick JSON for %s (skipped pick() + 8 workers)",
                trade_date,
            )
            return {"quant_pick_context": context}

        logger.info("Running quant pick for ticker=%s date=%s", ticker, trade_date)
        try:
            today = pd.Timestamp(trade_date)
            result = quant_pick(
                today=today,
                daily_cache_name=cfg.get("quant_daily_cache_name", "daily_main_board_liquid"),
                top_k=cfg.get("quant_top_k_per_strategy", 2),
                n_workers=cfg.get("quant_n_workers", 8),
                slice_days=cfg.get("quant_slice_days", 0),
                top_n=cfg.get("quant_top_n_default", 20),
            )
            context = _extract_ticker_context(result, ticker)
            logger.info(
                "Quant pick done: %d strategies run, %d errors, top_picks=%d, elapsed=%.1fs",
                result.get("n_strategies_run", 0),
                result.get("n_strategies_error", 0),
                len(result.get("top_picks", [])),
                result.get("elapsed", 0),
            )
            return {"quant_pick_context": context}
        except Exception as e:
            logger.warning("Quant pick failed: %s - continuing without quant context", e)
            return {"quant_pick_context": f"[量化层错误] {type(e).__name__}: {str(e)[:200]}"}

    return quant_picker_node
