# TradingAgents/graph/trading_graph.py

import logging
import os
from pathlib import Path
import json
from datetime import datetime, timedelta
from typing import Dict, Any, Tuple, List, Optional

import pandas as pd

logger = logging.getLogger(__name__)

from langgraph.prebuilt import ToolNode

from tradingagents.llm_clients import create_llm_client

from tradingagents.agents import *
from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.agents.utils.memory import TradingMemoryLog
from tradingagents.dataflows.utils import safe_ticker_component
from tradingagents.dataflows.config import set_config

# Import the new abstract tool methods from agent_utils
from tradingagents.agents.utils.agent_utils import (
    get_stock_data,
    get_indicators,
    get_fundamentals,
    get_balance_sheet,
    get_cashflow,
    get_income_statement,
    get_news,
    get_insider_transactions,
    get_global_news,
    get_profit_forecast,
    get_hot_stocks,
    get_northbound_flow,
    get_concept_blocks,
    get_fund_flow,
    get_dragon_tiger_board,
    get_lockup_expiry,
    get_industry_comparison,
)

from .checkpointer import checkpoint_step, clear_checkpoint, get_checkpointer, thread_id
from .conditional_logic import ConditionalLogic
from .setup import GraphSetup
from .propagation import Propagator
from .reflection import Reflector
from .signal_processing import SignalProcessor


class TradingAgentsGraph:
    """Main class that orchestrates the trading agents framework."""

    def __init__(
        self,
        selected_analysts=["market", "social", "news", "fundamentals", "policy", "hot_money", "lockup"],
        debug=False,
        config: Dict[str, Any] = None,
        callbacks: Optional[List] = None,
    ):
        """Initialize the trading agents graph and components.

        Args:
            selected_analysts: List of analyst types to include
            debug: Whether to run in debug mode
            config: Configuration dictionary. If None, uses default config
            callbacks: Optional list of callback handlers (e.g., for tracking LLM/tool stats)
        """
        self.debug = debug
        self.config = config or DEFAULT_CONFIG
        self.callbacks = callbacks or []

        # Update the interface's config
        set_config(self.config)

        # Create necessary directories
        os.makedirs(self.config["data_cache_dir"], exist_ok=True)
        os.makedirs(self.config["results_dir"], exist_ok=True)

        # Initialize LLMs with provider-specific thinking configuration
        llm_kwargs = self._get_provider_kwargs()

        # Add callbacks to kwargs if provided (passed to LLM constructor)
        if self.callbacks:
            llm_kwargs["callbacks"] = self.callbacks

        deep_client = create_llm_client(
            provider=self.config["llm_provider"],
            model=self.config["deep_think_llm"],
            base_url=self.config.get("backend_url"),
            **llm_kwargs,
        )
        quick_client = create_llm_client(
            provider=self.config["llm_provider"],
            model=self.config["quick_think_llm"],
            base_url=self.config.get("backend_url"),
            **llm_kwargs,
        )

        self.deep_thinking_llm = deep_client.get_llm()
        self.quick_thinking_llm = quick_client.get_llm()
        
        self.memory_log = TradingMemoryLog(self.config)

        # Create tool nodes
        self.tool_nodes = self._create_tool_nodes()

        # Initialize components
        self.conditional_logic = ConditionalLogic(
            max_debate_rounds=self.config["max_debate_rounds"],
            max_risk_discuss_rounds=self.config["max_risk_discuss_rounds"],
        )
        self.graph_setup = GraphSetup(
            self.quick_thinking_llm,
            self.deep_thinking_llm,
            self.tool_nodes,
            self.conditional_logic,
            config=self.config,
        )

        self.propagator = Propagator()
        self.reflector = Reflector(self.quick_thinking_llm)
        self.signal_processor = SignalProcessor(self.quick_thinking_llm)

        # State tracking
        self.curr_state = None
        self.ticker = None
        self.log_states_dict = {}  # date to full state dict

        # Set up the graph: keep the workflow for recompilation with a checkpointer.
        self.workflow = self.graph_setup.setup_graph(selected_analysts)
        self.graph = self.workflow.compile()
        self._checkpointer_ctx = None

    def _get_provider_kwargs(self) -> Dict[str, Any]:
        """Get provider-specific kwargs for LLM client creation."""
        kwargs = {}
        provider = self.config.get("llm_provider", "").lower()

        if provider == "google":
            thinking_level = self.config.get("google_thinking_level")
            if thinking_level:
                kwargs["thinking_level"] = thinking_level

        elif provider == "openai":
            reasoning_effort = self.config.get("openai_reasoning_effort")
            if reasoning_effort:
                kwargs["reasoning_effort"] = reasoning_effort

        elif provider == "anthropic":
            effort = self.config.get("anthropic_effort")
            if effort:
                kwargs["effort"] = effort

        return kwargs

    def _create_tool_nodes(self) -> Dict[str, ToolNode]:
        """Create tool nodes for different data sources using abstract methods."""
        return {
            "market": ToolNode(
                [
                    # Core stock data tools
                    get_stock_data,
                    # Technical indicators
                    get_indicators,
                ]
            ),
            "social": ToolNode(
                [
                    # News tools for social media analysis
                    get_news,
                ]
            ),
            "news": ToolNode(
                [
                    # News and insider information
                    get_news,
                    get_global_news,
                    get_insider_transactions,
                ]
            ),
            "fundamentals": ToolNode(
                [
                    get_fundamentals,
                    get_balance_sheet,
                    get_cashflow,
                    get_income_statement,
                    get_profit_forecast,
                    get_industry_comparison,
                ]
            ),
            "policy": ToolNode(
                [
                    get_news,
                    get_global_news,
                ]
            ),
            "hot_money": ToolNode(
                [
                    get_stock_data,
                    get_news,
                    get_insider_transactions,
                    get_hot_stocks,
                    get_northbound_flow,
                    get_concept_blocks,
                    get_fund_flow,
                    get_dragon_tiger_board,
                    get_industry_comparison,
                ]
            ),
            "lockup": ToolNode(
                [
                    get_insider_transactions,
                    get_news,
                    get_fundamentals,
                    get_lockup_expiry,
                ]
            ),
        }

    def _fetch_returns(
        self, ticker: str, trade_date: str, holding_days: int = 5
    ) -> Tuple[Optional[float], Optional[float], Optional[int]]:
        """Fetch raw and alpha return for ticker over holding_days from trade_date.

        Returns (raw_return, alpha_return, actual_holding_days) or
        (None, None, None) if price data is unavailable (too recent, delisted,
        or network error).

        Benchmark: CSI 300 (沪深300, code 000300). Tries Yahoo Finance first
        (standard), falls back to Sina HTTP API for A-stock users behind the
        GFW. When both fail, returns raw_return with alpha=0 (no benchmark).
        """
        start = datetime.strptime(trade_date, "%Y-%m-%d")
        end = start + timedelta(days=holding_days + 7)  # buffer for weekends/holidays
        end_str = end.strftime("%Y-%m-%d")

        # yfinance 是可选 vendor(非 A 股默认),懒加载避免 graph 模块硬依赖。
        # 不可用/失败时静默降级到下面的 Sina fallback(行为不变)。
        try:
            import yfinance as yf
        except Exception:
            yf = None

        # Fetch stock price
        stock = None
        if yf is not None:
            try:
                stock = yf.Ticker(ticker).history(start=trade_date, end=end_str)
            except Exception:
                logger.warning("yfinance failed for %s, trying A-stock fallback", ticker)

        # Fallback: try A-stock local data source for price
        if stock is None or (isinstance(stock, pd.DataFrame) and (len(stock) < 2)):
            try:
                from tradingagents.dataflows.a_stock import _sina_kline_fallback
                code = ticker.replace(".SS", "").replace(".SZ", "").replace("SH", "").replace("SZ", "")
                if code.startswith(("6", "0", "3")):
                    stock = _sina_kline_fallback(code, trade_date, end_str)
            except Exception as exc:
                logger.warning("A-stock price fallback also failed for %s: %s", ticker, exc)

        if stock is None or (isinstance(stock, pd.DataFrame) and len(stock) < 2):
            return None, None, None

        # Fetch benchmark (CSI 300)
        benchmark = None
        if yf is not None:
            try:
                benchmark = yf.Ticker("000300.SS").history(start=trade_date, end=end_str)
            except Exception:
                logger.warning("yfinance CSI 300 benchmark failed, trying A-stock fallback")

        # Fallback: Sina HTTP API for CSI 300 index
        if benchmark is None or (isinstance(benchmark, pd.DataFrame) and len(benchmark) < 2):
            try:
                from tradingagents.dataflows.a_stock import _sina_kline_fallback
                csi300 = _sina_kline_fallback("000300", trade_date, end_str)
                if not csi300.empty and len(csi300) >= 2:
                    benchmark = csi300
            except Exception as exc:
                logger.warning("Sina CSI 300 fallback failed: %s", exc)

        if benchmark is None or (isinstance(benchmark, pd.DataFrame) and len(benchmark) < 2):
            # No benchmark available — return raw return with alpha=0
            try:
                actual_days = min(holding_days, len(stock) - 1)
                raw = float(
                    (stock["Close"].iloc[actual_days] - stock["Close"].iloc[0])
                    / stock["Close"].iloc[0]
                )
                logger.info("No benchmark for %s on %s, returning raw=%.4f alpha=0", ticker, trade_date, raw)
                return raw, 0.0, actual_days
            except Exception:
                return None, None, None

        try:
            actual_days = min(holding_days, len(stock) - 1, len(benchmark) - 1)
            raw = float(
                (stock["Close"].iloc[actual_days] - stock["Close"].iloc[0])
                / stock["Close"].iloc[0]
            )
            bench_ret = float(
                (benchmark["Close"].iloc[actual_days] - benchmark["Close"].iloc[0])
                / benchmark["Close"].iloc[0]
            )
            alpha = raw - bench_ret
            return raw, alpha, actual_days
        except Exception as e:
            logger.warning(
                "Could not resolve outcome for %s on %s (will retry next run): %s",
                ticker, trade_date, e,
            )
            return None, None, None

    def _resolve_pending_entries(self, ticker: str) -> None:
        """Resolve pending log entries for ticker at the start of a new run.

        Fetches returns for each same-ticker pending entry, generates reflections,
        then writes all updates in a single atomic batch write to avoid redundant I/O.
        Skips entries whose price data is not yet available (too recent or delisted).

        Trade-off: only same-ticker entries are resolved per run.  Entries for
        other tickers accumulate until that ticker is run again.
        """
        pending = [e for e in self.memory_log.get_pending_entries() if e["ticker"] == ticker]
        if not pending:
            return

        updates = []
        for entry in pending:
            raw, alpha, days = self._fetch_returns(ticker, entry["date"])
            if raw is None:
                continue  # price not available yet — try again next run
            try:
                reflection = self.reflector.reflect_on_final_decision(
                    final_decision=entry.get("decision", ""),
                    raw_return=raw,
                    alpha_return=alpha,
                )
            except Exception as exc:
                # Reflection is best-effort: a transient LLM error (rate
                # limit, gateway 5xx, timeout) shouldn't abort the whole
                # pipeline. Skip this entry's reflection and let the next
                # same-ticker run retry.
                logger.warning(
                    "Reflection failed for %s on %s (skipped): %s",
                    ticker, entry["date"], exc,
                )
                reflection = "（反思调用失败,本次跳过）"
            updates.append({
                "ticker": ticker,
                "trade_date": entry["date"],
                "raw_return": raw,
                "alpha_return": alpha,
                "holding_days": days,
                "reflection": reflection,
            })

        if updates:
            self.memory_log.batch_update_with_outcomes(updates)

    def propagate(self, company_name, trade_date):
        """Run the trading agents graph for a company on a specific date.

        When ``checkpoint_enabled`` is set in config, the graph is recompiled
        with a per-ticker SqliteSaver so a crashed run can resume from the last
        successful node on a subsequent invocation with the same ticker+date.
        """
        return self._run_graph(company_name, trade_date)

    def prepare_quant_contexts(self, tickers: List[str], trade_date: str,
                               progress_callback=None) -> Dict[str, str]:
        """Run quant pick() once and build per-ticker context strings.

        This is the batch-mode optimization: run the active strategy library
        once (~3 min), then extract each ticker's hit info into a context
        string. The context is injected into initial_state so the Quant
        Picker LangGraph node is a no-op for each ticker in the batch.

        Args:
            tickers: List of tickers to extract context for.
            trade_date: Trade date string (YYYY-MM-DD).
            progress_callback: Optional fn(completed, total, latest_result_dict).

        Returns:
            Dict {ticker: quant_context_string}. Tickers not in Top N get an
            empty-string context (analysts run without quant context).
        """
        from tradingagents.quant import pick as quant_pick
        from tradingagents.agents.quant_picker_node import _extract_ticker_context

        if not self.config.get("quant_layer_enabled", True):
            logger.info("Quant layer disabled, returning empty contexts")
            return {t: "" for t in tickers}

        today = pd.Timestamp(trade_date)
        result = quant_pick(
            today=today,
            daily_cache_name=self.config.get("quant_daily_cache_name", "daily_main_board_liquid"),
            top_k=self.config.get("quant_top_k_per_strategy", 2),
            n_workers=self.config.get("quant_n_workers", 8),
            slice_days=self.config.get("quant_slice_days", 0),
            top_n=self.config.get("quant_top_n_default", 20),
            progress_callback=progress_callback,
        )

        contexts: Dict[str, str] = {}
        for ticker in tickers:
            contexts[ticker] = _extract_ticker_context(result, ticker)
        return contexts

    def propagate_batch(self, tickers: List[str], trade_date: str,
                        quant_contexts: Optional[Dict[str, str]] = None,
                        callbacks: Optional[List] = None) -> List[Tuple[str, Dict[str, Any], Any]]:
        """Run the graph for multiple tickers on the same date.

        Pre-populates ``quant_pick_context`` for each ticker so the Quant Picker
        node is a no-op (avoids re-running the 3-minute pick() per ticker).

        Args:
            tickers: List of ticker codes to analyze.
            trade_date: Trade date string (YYYY-MM-DD).
            quant_contexts: Optional dict {ticker: quant_context_string}. If a
                           ticker is missing, its quant_pick_context stays empty
                           (analysts run without quant context).
            callbacks: Optional callbacks passed to each run.

        Returns:
            List of (ticker, final_state, signal) tuples. Failed tickers have
            final_state=None and signal=None.
        """
        if quant_contexts is None:
            quant_contexts = {}

        results: List[Tuple[str, Dict[str, Any], Any]] = []
        for ticker in tickers:
            ctx = quant_contexts.get(ticker, "")
            try:
                final_state, signal = self._run_graph(
                    ticker, trade_date, pre_quant_context=ctx, callbacks=callbacks
                )
                results.append((ticker, final_state, signal))
            except Exception as e:
                logger.warning("Batch run failed for %s: %s", ticker, e)
                results.append((ticker, None, None))
        return results

    def prepare_graph_run(
        self,
        company_name,
        trade_date,
        callbacks: Optional[List] = None,
    ) -> Tuple[Optional[Dict[str, Any]], Dict[str, Any], Optional[int]]:
        """Prepare graph input/args for a fresh or resumed run.

        Returns ``(initial_state, args, checkpoint_step)``. When a checkpoint
        already exists, ``initial_state`` is ``None`` so LangGraph resumes the
        existing thread instead of replaying completed nodes.
        """
        self.ticker = company_name

        # Resolve any pending memory-log entries for this ticker before the pipeline runs.
        self._resolve_pending_entries(company_name)

        checkpoint_enabled = self.config.get("checkpoint_enabled")
        resume_step = None

        # Recompile with a checkpointer if the user opted in.
        if checkpoint_enabled:
            self._checkpointer_ctx = get_checkpointer(
                self.config["data_cache_dir"], company_name
            )
            saver = self._checkpointer_ctx.__enter__()
            self.graph = self.workflow.compile(checkpointer=saver)

            resume_step = checkpoint_step(
                self.config["data_cache_dir"], company_name, str(trade_date)
            )
            if resume_step is not None:
                logger.info(
                    "Resuming from step %d for %s on %s",
                    resume_step,
                    company_name,
                    trade_date,
                )
            else:
                logger.info("Starting fresh for %s on %s", company_name, trade_date)

        args = self.propagator.get_graph_args(callbacks=callbacks)

        # Inject thread_id so same ticker+date resumes, different date starts fresh.
        if checkpoint_enabled:
            tid = thread_id(company_name, str(trade_date))
            args.setdefault("config", {}).setdefault("configurable", {})["thread_id"] = tid

        if checkpoint_enabled and resume_step is not None:
            return None, args, resume_step

        # Initialize state only for fresh runs. Passing a new initial state to
        # LangGraph would start a new run and replay completed nodes.
        past_context = self.memory_log.get_past_context(company_name)
        init_agent_state = self.propagator.create_initial_state(
            company_name, trade_date, past_context=past_context
        )
        return init_agent_state, args, resume_step

    def finalize_graph_run(self, company_name, trade_date, final_state):
        """Persist a completed run and clear its checkpoint."""
        self.curr_state = final_state

        # Log state to disk.
        self._log_state(trade_date, final_state)

        # Store decision for deferred reflection on the next same-ticker run.
        self.memory_log.store_decision(
            ticker=company_name,
            trade_date=trade_date,
            final_trade_decision=final_state["final_trade_decision"],
        )

        # Clear checkpoint on successful completion to avoid stale state.
        if self.config.get("checkpoint_enabled"):
            clear_checkpoint(
                self.config["data_cache_dir"], company_name, str(trade_date)
            )

        # Prefer the 4-tier merged label from Conflict Resolver (🟢强买/🟡关注/
        # 🟠冲突/🔴弃) so downstream consumers see the quant+LLM merged signal,
        # not just the raw LLM rating. Fall back to parse_rating() on
        # final_ranked_decision or final_trade_decision when Conflict Resolver
        # hasn't run (legacy path) or final_signal_label is empty.
        signal_label = final_state.get("final_signal_label", "")
        if signal_label:
            return signal_label
        signal_source = final_state.get("final_ranked_decision") or final_state.get("final_trade_decision", "")
        return self.process_signal(signal_source)

    def close_graph_run(self) -> None:
        """Close the active checkpointer context, if any."""
        if self._checkpointer_ctx is not None:
            self._checkpointer_ctx.__exit__(None, None, None)
            self._checkpointer_ctx = None
            self.graph = self.workflow.compile()

    def _run_graph(self, company_name, trade_date,
                   pre_quant_context: Optional[str] = None,
                   callbacks: Optional[List] = None):
        """Execute the graph and write the resulting state to disk and memory log.

        Args:
            company_name: Ticker to analyze.
            trade_date: Trade date string.
            pre_quant_context: If provided, injected into initial_state so the
                              Quant Picker node skips pick(). Used by propagate_batch.
                              Also injected on checkpoint resume so the node stays
                              a no-op after restart.
            callbacks: Optional graph callbacks.
        """
        init_agent_state, args, _ = self.prepare_graph_run(
            company_name, trade_date, callbacks=callbacks
        )

        # Inject pre-computed quant context.
        # - Fresh run (init_agent_state is not None): patch init state directly.
        # - Resume run (init_agent_state is None): patch the checkpoint state via
        #   update_state so Quant Picker sees quant_pick_context as pre-populated
        #   and stays a no-op. Without this, resume re-runs pick() (~12 min).
        if pre_quant_context:
            if init_agent_state is not None:
                init_agent_state["quant_pick_context"] = pre_quant_context
            elif self.config.get("checkpoint_enabled"):
                try:
                    self.graph.update_state(
                        args["config"],
                        {"quant_pick_context": pre_quant_context},
                    )
                    logger.info(
                        "Injected pre_quant_context into checkpoint state for %s",
                        company_name,
                    )
                except Exception as exc:
                    logger.warning(
                        "Failed to patch checkpoint state with pre_quant_context for %s: %s",
                        company_name, exc,
                    )

        try:
            if self.debug:
                trace = []
                for chunk in self.graph.stream(init_agent_state, **args):
                    msgs = chunk.get("messages") or []
                    if msgs:
                        msgs[-1].pretty_print()
                    trace.append(chunk)
                # 空流兜底(理论上不会发生,防 IndexError)
                final_state = trace[-1] if trace else self.graph.invoke(init_agent_state, **args)
            else:
                final_state = self.graph.invoke(init_agent_state, **args)

            signal = self.finalize_graph_run(company_name, trade_date, final_state)
            return final_state, signal
        finally:
            self.close_graph_run()

    def _log_state(self, trade_date, final_state):
        """Log the final state to a JSON file."""
        self.log_states_dict[str(trade_date)] = {
            "company_of_interest": final_state["company_of_interest"],
            "trade_date": final_state["trade_date"],
            "market_report": final_state["market_report"],
            "sentiment_report": final_state["sentiment_report"],
            "news_report": final_state["news_report"],
            "fundamentals_report": final_state["fundamentals_report"],
            "policy_report": final_state.get("policy_report", ""),
            "hot_money_report": final_state.get("hot_money_report", ""),
            "lockup_report": final_state.get("lockup_report", ""),
            "investment_debate_state": {
                "bull_history": final_state["investment_debate_state"]["bull_history"],
                "bear_history": final_state["investment_debate_state"]["bear_history"],
                "history": final_state["investment_debate_state"]["history"],
                "current_response": final_state["investment_debate_state"][
                    "current_response"
                ],
                "judge_decision": final_state["investment_debate_state"][
                    "judge_decision"
                ],
            },
            "trader_investment_decision": final_state["trader_investment_plan"],
            "risk_debate_state": {
                "aggressive_history": final_state["risk_debate_state"]["aggressive_history"],
                "conservative_history": final_state["risk_debate_state"]["conservative_history"],
                "neutral_history": final_state["risk_debate_state"]["neutral_history"],
                "history": final_state["risk_debate_state"]["history"],
                "judge_decision": final_state["risk_debate_state"]["judge_decision"],
            },
            "investment_plan": final_state["investment_plan"],
            "final_trade_decision": final_state["final_trade_decision"],
            "quant_pick_context": final_state.get("quant_pick_context", ""),
            "final_ranked_decision": final_state.get("final_ranked_decision", ""),
        }

        # Save to file. Reject ticker values that would escape the
        # results directory when joined as a path component.
        safe_ticker = safe_ticker_component(self.ticker)
        directory = Path(self.config["results_dir"]) / safe_ticker / "TradingAgentsStrategy_logs"
        directory.mkdir(parents=True, exist_ok=True)

        log_path = directory / f"full_states_log_{trade_date}.json"
        with open(log_path, "w", encoding="utf-8") as f:
            json.dump(self.log_states_dict[str(trade_date)], f, indent=4)

    def process_signal(self, full_signal):
        """Process a signal to extract the core decision."""
        return self.signal_processor.process_signal(full_signal)
