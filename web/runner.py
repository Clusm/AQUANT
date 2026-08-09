"""Background thread runner for TradingAgentsGraph pipeline."""

from __future__ import annotations

import atexit
import threading
import traceback
from typing import Any

import pandas as pd

from web.history import (
    clear_incomplete_task,
    record_incomplete_task,
    save_quant_pick,
)
from web.progress import PIPELINE_STAGES, ProgressTracker, QuantProgressTracker
from web.stock_display import (
    normalize_report_state_mentions,
    normalize_stock_mentions,
    strip_think_tags,
)


_REPORT_KEY_TO_STAGE = {s["report_key"]: s["id"] for s in PIPELINE_STAGES}

_ANALYST_REPORT_KEYS = [
    "market_report", "sentiment_report", "news_report",
    "fundamentals_report", "policy_report", "hot_money_report", "lockup_report",
]

# Module-level shutdown event: set by atexit handler so daemon threads get a
# signal to clean up (checkpoint, incomplete task records) before the Python
# process exits. Without this, daemon threads are forcibly killed mid-write,
# leaving corrupted artifacts.
_SHUTDOWN_EVENT = threading.Event()


def _shutdown_daemon_threads() -> None:
    """Signal all daemon threads to clean up and give them a brief window."""
    _SHUTDOWN_EVENT.set()
    _SHUTDOWN_EVENT.wait(timeout=2.0)


atexit.register(_shutdown_daemon_threads)


def _discard_stopped_run(
    ticker: str,
    trade_date: str,
    config: dict,
    tracker: ProgressTracker,
) -> None:
    """Clear resumable artifacts for a user-stopped run."""
    from tradingagents.graph.checkpointer import clear_checkpoint

    clear_incomplete_task(ticker, trade_date)
    clear_checkpoint(config["data_cache_dir"], ticker, trade_date)
    tracker.mark_stopped()



def _detect_completed_stages(
    chunk: dict[str, Any],
    tracker: ProgressTracker,
) -> None:
    """Check the streamed chunk for newly completed stages."""
    for report_key in _ANALYST_REPORT_KEYS:
        stage_id = _REPORT_KEY_TO_STAGE[report_key]
        content = chunk.get(report_key, "")
        if content and tracker.stage_status(stage_id) != "done":
            report = normalize_stock_mentions(str(content), tracker.ticker, chunk)
            tracker.mark_stage_done(stage_id, strip_think_tags(report))

    dqs = chunk.get("data_quality_summary", "")
    if dqs and tracker.stage_status("quality_gate") != "done":
        tracker.mark_stage_done("quality_gate", normalize_stock_mentions(str(dqs), tracker.ticker, chunk))

    debate = chunk.get("investment_debate_state")
    if debate and isinstance(debate, dict):
        judge = debate.get("judge_decision", "")
        if judge and tracker.stage_status("debate") != "done":
            tracker.mark_stage_done("debate", normalize_stock_mentions(str(judge), tracker.ticker, chunk))

    trader_plan = chunk.get("trader_investment_plan", "")
    if trader_plan and tracker.stage_status("trader") != "done":
        report = normalize_stock_mentions(str(trader_plan), tracker.ticker, chunk)
        tracker.mark_stage_done("trader", strip_think_tags(report))

    risk = chunk.get("risk_debate_state")
    if risk and isinstance(risk, dict):
        risk_judge = risk.get("judge_decision", "")
        if risk_judge and tracker.stage_status("risk") != "done":
            tracker.mark_stage_done("risk", normalize_stock_mentions(str(risk_judge), tracker.ticker, chunk))

    final = chunk.get("final_trade_decision", "")
    if final and tracker.stage_status("pm") != "done":
        report = normalize_stock_mentions(str(final), tracker.ticker, chunk)
        tracker.mark_stage_done("pm", strip_think_tags(report))


def _infer_active_stage(tracker: ProgressTracker) -> None:
    """Set the current_stage to the first non-completed stage."""
    from web.progress import STAGE_IDS
    for sid in STAGE_IDS:
        if tracker.stage_status(sid) == "pending":
            tracker.mark_stage_active(sid)
            return


def _run(
    ticker: str,
    trade_date: str,
    config: dict,
    tracker: ProgressTracker,
    pre_quant_context: str = "",
    ticker_source: str = "manual",
) -> None:
    """Execute the full pipeline in the current thread.

    ``pre_quant_context``: when non-empty, injected into the initial state so
    the Quant Picker LangGraph node is a no-op. In batch mode, the caller
    runs pick() once and passes each ticker's extracted context here so we
    don't pay the ~3-minute pick() cost N times.

    ``ticker_source``: "manual" (user typed ticker) or "quant_picker"
    (selected from Top N). The quant_picker_node uses this to decide
    whether to fall back to running pick() when no saved JSON exists:
    manual input never triggers a full-market scan, quant_picker does.
    """
    from cli.stats_handler import StatsCallbackHandler
    from tradingagents.graph.trading_graph import TradingAgentsGraph

    stats = StatsCallbackHandler()

    graph = TradingAgentsGraph(
        debug=True,
        config=config,
        callbacks=[stats],
    )

    init_state, args, _ = graph.prepare_graph_run(
        ticker,
        trade_date,
        callbacks=[stats],
    )

    # Inject the ticker source so quant_picker_node knows whether to fall
    # back to pick() when no saved JSON exists. Manual input = never run
    # pick(); quant_picker = run pick() if needed.
    if init_state is not None:
        init_state["ticker_source"] = ticker_source
    elif config.get("checkpoint_enabled"):
        try:
            graph.graph.update_state(
                args["config"],
                {"ticker_source": ticker_source},
            )
        except Exception as exc:
            import logging
            logging.getLogger(__name__).warning(
                "Failed to inject ticker_source for %s: %s", ticker, exc,
            )

    # Set quant layer hint for manual tickers (no quant pick available)
    if ticker_source == "manual" and not pre_quant_context:
        if config.get("quant_layer_enabled", True):
            tracker.quant_skip_hint = "手动选股，量化层未参与评估(仅 LLM 分析)"

    # Inject pre-computed quant context (batch mode). Two cases:
    # - Fresh run (init_state is not None): patch init state directly.
    # - Resume run (init_state is None): patch checkpoint state via
    #   update_state so Quant Picker stays a no-op after restart.
    if pre_quant_context:
        if init_state is not None:
            init_state["quant_pick_context"] = pre_quant_context
        elif config.get("checkpoint_enabled"):
            try:
                graph.graph.update_state(
                    args["config"],
                    {"quant_pick_context": pre_quant_context},
                )
            except Exception as exc:
                # Don't let a failed state-patch abort the run; the worst
                # case is Quant Picker re-runs pick() for this ticker.
                import logging
                logging.getLogger(__name__).warning(
                    "Failed to inject pre_quant_context for %s: %s", ticker, exc,
                )

    last_chunk: dict[str, Any] = {}

    try:
        def _close_and_discard() -> None:
            graph.close_graph_run()
            _discard_stopped_run(ticker, trade_date, config, tracker)

        if tracker.stop_requested:
            _close_and_discard()
            return

        stream = graph.graph.stream(init_state, **args)
        while True:
            if _SHUTDOWN_EVENT.is_set():
                _close_and_discard()
                return
            tracker.wait_if_paused()
            if tracker.stop_requested:
                _close_and_discard()
                return
            try:
                chunk = next(stream)
            except StopIteration:
                break

            if tracker.stop_requested:
                _close_and_discard()
                return

            last_chunk = chunk
            _detect_completed_stages(chunk, tracker)
            _infer_active_stage(tracker)
            record_incomplete_task(
                ticker,
                trade_date,
                status="paused" if tracker.is_paused else "running",
                completed_stages=tracker.completed_stages,
            )

            s = stats.get_stats()
            tracker.update_stats(s["llm_calls"], s["tool_calls"], s["tokens_in"], s["tokens_out"])

        if tracker.stop_requested:
            _close_and_discard()
            return

        if not last_chunk:
            raise RuntimeError("分析没有返回任何结果，请清理断点后重试。")

        # #55: 报告标的统一显示为「代码+名称」，须在 finalize 落盘前归一化 last_chunk
        normalize_report_state_mentions(last_chunk, ticker)

        signal = graph.finalize_graph_run(ticker, trade_date, last_chunk)
        if tracker.stop_requested:
            _close_and_discard()
            return

        tracker.mark_complete(last_chunk, signal)
        clear_incomplete_task(ticker, trade_date)
    finally:
        graph.close_graph_run()


def run_analysis_in_thread(
    ticker: str,
    trade_date: str,
    config: dict,
    tracker: ProgressTracker,
    pre_quant_context: str = "",
    ticker_source: str = "manual",
) -> threading.Thread:
    """Launch the pipeline in a daemon thread. Returns the thread handle.

    ``pre_quant_context``: optional pre-computed quant context string for
    this ticker. When set, the Quant Picker node is a no-op (skips the
    ~3-minute pick() call). Used by batch mode to share one pick() result
    across N tickers.

    ``ticker_source``: "manual" (user typed ticker) or "quant_picker"
    (selected from Top N). The quant_picker_node uses this to decide
    whether to fall back to running pick() when no saved JSON exists:
    manual input never triggers a full-market scan, quant_picker does.
    """
    tracker.ticker = ticker
    tracker.trade_date = trade_date
    tracker.is_running = True
    tracker.mark_stage_active("market")
    record_incomplete_task(
        ticker,
        trade_date,
        status="running",
        completed_stages=tracker.completed_stages,
    )

    def _target() -> None:
        try:
            _run(
                ticker,
                trade_date,
                config,
                tracker,
                pre_quant_context=pre_quant_context,
                ticker_source=ticker_source,
            )
        except Exception as exc:
            if tracker.stop_requested:
                try:
                    _discard_stopped_run(ticker, trade_date, config, tracker)
                except Exception:
                    traceback.print_exc()
                return
            traceback.print_exc()
            record_incomplete_task(
                ticker,
                trade_date,
                status="error",
                error=str(exc),
                completed_stages=tracker.completed_stages,
            )
            tracker.mark_error(str(exc))

    t = threading.Thread(target=_target, daemon=True)
    t.start()
    return t


# ============================================================
# Quant pick runner (no LangGraph, no checkpoint API)
# ============================================================

def _run_quant(
    today: pd.Timestamp,
    daily_cache_name: str,
    top_n: int,
    n_workers: int,
    top_k: int,
    slice_days: int,
    tracker: QuantProgressTracker,
) -> None:
    """Run quant pick() in-thread, feeding progress to QuantProgressTracker.

    Unlike _run(), this does NOT touch LangGraph checkpoint APIs - the quant
    layer runs *before* the LangGraph pipeline and has no resume semantics.
    Stop is honored between strategies via progress_callback (imap_unordered
    will finish the in-flight worker but no new callback fires after stop).

    Pipeline:
      1. (optional) increment_data() if cache is behind today
      2. pick() with stop_check
    """
    from tradingagents.quant.data import cache as cm
    from tradingagents.quant.data_update import (
        check_cache_freshness, increment_data,
    )
    from tradingagents.quant.quant_picker import pick
    from web.background_fetcher import acquire_fetch_lock

    # === 1. 增量数据更新(若缓存落后于 today)===
    try:
        daily_df = cm.load(daily_cache_name)
        last_date, days_behind = check_cache_freshness(daily_df)
        # 周末容忍:周六/周日 days_behind<=2 视为最新
        is_weekend = pd.Timestamp.now().weekday() >= 5
        needs_update = days_behind > 0 and not (is_weekend and days_behind <= 2)
        if needs_update:
            tracker.mark_data_update_active(daily_cache_name, last_date, days_behind)
            idx_df = cm.load("index_000001") if cm.exists("index_000001") else pd.DataFrame()

            def _data_progress(completed: int, total: int, stats: dict) -> None:
                if tracker.stop_requested:
                    return
                tracker.mark_data_update_progress(completed, total, stats)

            # acquire fetch lock:避免与 background_fetcher 并发写同一个 parquet
            # (background_fetcher 启动时可能正在做全量构建或增量更新)
            with acquire_fetch_lock():
                try:
                    increment_data(
                        daily_df, idx_df,
                        daily_cache_name=daily_cache_name,
                        index_cache_name="index_000001",
                        max_workers=32,
                        progress_callback=_data_progress,
                        stop_check=lambda: tracker.stop_requested,
                    )
                except Exception as exc:
                    traceback.print_exc()
                    print(f"[quant] 数据增量更新失败,继续用旧缓存: {exc}", flush=True)

            tracker.mark_data_update_done()
            if tracker.stop_requested:
                tracker.mark_stopped()
                return
    except Exception as exc:
        traceback.print_exc()
        print(f"[quant] 缓存读取失败,跳过增量更新: {exc}", flush=True)

    # === 2. 量化选股 ===
    def progress_callback(completed: int, total: int, latest: dict) -> None:
        if tracker.stop_requested:
            return
        tracker.mark_strategy_done(latest["name"], latest)

    try:
        result = pick(
            today=today,
            daily_cache_name=daily_cache_name,
            top_k=top_k,
            n_workers=n_workers,
            slice_days=slice_days,
            top_n=top_n,
            progress_callback=progress_callback,
            stop_check=lambda: tracker.stop_requested,
        )

        if tracker.stop_requested:
            tracker.mark_stopped()
            return

        # pick() may adjust `today` if it falls beyond the cache's latest date
        # (e.g. user selected today but cache hasn't been refreshed). Use the
        # adjusted date from the result for tracker + saved history.
        actual_today = pd.Timestamp(result["today"]).normalize()
        if actual_today != today.normalize():
            tracker.trade_date = actual_today.strftime("%Y-%m-%d")

        tracker.mark_pick_complete(
            top_picks=result["top_picks"],
            elapsed=result["elapsed"],
            n_run=result["n_strategies_run"],
            n_error=result["n_strategies_error"],
            all_records=result.get("all_records", []),
        )

        trade_date = actual_today.strftime("%Y-%m-%d")
        try:
            save_quant_pick(trade_date, result)
        except Exception:
            traceback.print_exc()
    except Exception as exc:
        if tracker.stop_requested:
            tracker.mark_stopped()
            return
        traceback.print_exc()
        tracker.mark_error(str(exc))


def run_quant_pick_in_thread(
    today: pd.Timestamp,
    daily_cache_name: str,
    top_n: int,
    n_workers: int,
    tracker: QuantProgressTracker,
    top_k: int = 2,
    slice_days: int = 0,
) -> threading.Thread:
    """Launch the quant pick() in a daemon thread. Returns the thread handle.

    Args:
        today: trade date (close-of-day)
        daily_cache_name: e.g. "daily_main_board_liquid"
        top_n: 5/10/20 (validated inside pick())
        n_workers: parallel workers (Windows spawn, slow startup)
        tracker: QuantProgressTracker instance (must be created by caller)
        top_k: per-strategy Top K (default 2)
        slice_days: 0 = full history (default), otherwise slice N days

    Sets tracker.total_strategies before thread starts so the UI can render
    the progress bar immediately.
    """
    tracker.trade_date = today.strftime("%Y-%m-%d")
    tracker.is_running = True
    tracker.is_complete = False
    tracker.is_paused = False
    tracker.stop_requested = False
    tracker.error = None
    from tradingagents.quant.strategy.strategy_library_final import get_all_strategies_final
    tracker.total_strategies = len(get_all_strategies_final())
    tracker.completed_strategies = 0
    tracker.per_strategy_stats.clear()
    tracker.top_picks = None
    tracker.pick_elapsed = 0.0
    tracker.n_strategies_error = 0

    def _target() -> None:
        if _SHUTDOWN_EVENT.is_set():
            tracker.mark_stopped()
            return
        _run_quant(
            today=today,
            daily_cache_name=daily_cache_name,
            top_n=top_n,
            n_workers=n_workers,
            top_k=top_k,
            slice_days=slice_days,
            tracker=tracker,
        )

    t = threading.Thread(target=_target, daemon=True)
    t.start()
    return t
