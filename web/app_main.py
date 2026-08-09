"""TradingAgents A股分析 - Streamlit Web UI main code.

This module contains the streamlit execution code. It is imported and called
by web/app.py, which has the if __name__ == "__main__" guard to prevent
multiprocessing spawn workers from re-executing the streamlit code.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import streamlit as st

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from tradingagents.default_config import DEFAULT_CONFIG  # noqa: E402

from web.components.progress_panel import (  # noqa: E402
    render_progress,
    render_quant_progress,
)
from web.components.quant_pick import render_quant_picker  # noqa: E402
from web.components.recommendation import render_recommendation  # noqa: E402
from web.components.report_viewer import render_report  # noqa: E402
from web.components.sidebar import render_sidebar  # noqa: E402
from web.history import (  # noqa: E402
    clear_incomplete_task,
    extract_signal,
    get_history,
    get_quant_history,
    get_recommendation_history,
    load_analysis,
    load_quant_pick,
    load_recommendation,
    save_recommendation,
)
from web.progress import ProgressTracker, QuantProgressTracker  # noqa: E402
from web.runner import run_analysis_in_thread, run_quant_pick_in_thread  # noqa: E402


def _build_config() -> dict:
    config = DEFAULT_CONFIG.copy()
    config["llm_provider"] = st.session_state.get("llm_provider", "deepseek")
    config["deep_think_llm"] = st.session_state.get("deep_think_llm", "deepseek-v4-pro")
    config["quick_think_llm"] = st.session_state.get("quick_think_llm", "deepseek-v4-flash")
    backend_url = (st.session_state.get("llm_base_url") or os.getenv("BACKEND_URL") or "").strip()
    config["backend_url"] = backend_url or None
    config["data_vendors"] = {
        "core_stock_apis": "a_stock",
        "technical_indicators": "a_stock",
        "fundamental_data": "a_stock",
        "news_data": "a_stock",
        "signal_data": "a_stock",
    }
    config["max_debate_rounds"] = 1
    config["max_risk_discuss_rounds"] = 1
    config["checkpoint_enabled"] = True
    config["output_language"] = "Chinese"

    config["quant_layer_enabled"] = True
    config["quant_daily_cache_name"] = st.session_state.get(
        "quant_daily_cache", "daily_main_board_liquid",
    )
    config["quant_top_n_default"] = int(st.session_state.get("quant_top_n", "20"))
    config["quant_n_workers"] = int(st.session_state.get("quant_n_workers", 8))
    config["quant_top_k_per_strategy"] = 2
    config["quant_slice_days"] = 0
    return config


@st.cache_data(ttl=300)
def _build_name_map(codes: tuple[str, ...] | None = None) -> dict[str, str]:
    """Build {code: name} map. Tries mootdx first, falls back to Tencent batch API.

    Args:
        codes: optional tuple of stock codes (6-digit). When mootdx fails, only
               these codes are looked up via Tencent (faster + more robust than
               pulling the full mootdx list).
    Returns empty dict if both paths fail (picker falls back to "--").

    Cached for 5 min via st.cache_data - the mootdx / Tencent API calls
    were showing up as lag on every rerun when the quant picker tab is
    visible. Tuple (not list) so the arg is hashable.
    """
    try:
        from tradingagents.dataflows.a_stock import _build_name_code_map

        _, code_to_name = _build_name_code_map()
        if code_to_name:
            return code_to_name
    except Exception as exc:
        print(f"[name_map] mootdx failed: {type(exc).__name__}: {exc}", flush=True)

    if not codes:
        return {}

    try:
        from tradingagents.dataflows.a_stock import _tencent_quote

        result = _tencent_quote(list(codes))
        return {code: info.get("name", "") for code, info in result.items() if info.get("name")}
    except Exception as exc:
        print(f"[name_map] tencent fallback failed: {type(exc).__name__}: {exc}", flush=True)
        return {}


def _build_quant_contexts_for_batch(
    tickers: list[str],
    trade_date: str,
    config: dict,
) -> dict[str, str]:
    """Build per-ticker quant context strings for batch mode (Bug 1 fix).

    Reuses the saved quant pick JSON when available (instant: top_picks +
    all_records are already persisted by save_quant_pick). Falls back to
    TradingAgentsGraph.prepare_quant_contexts() - which runs pick() once
    (~3 min) - when no saved pick exists for this trade_date.

    Returns ``{ticker: context_string}``. Each ticker's context is then
    injected into its own analysis thread via ``run_analysis_in_thread(
    pre_quant_context=...)`` so the Quant Picker LangGraph node is a no-op
    for every ticker in the batch (the bug was: each ticker re-ran pick(),
    costing N x 3 minutes).
    """
    import pandas as pd
    from tradingagents.agents.quant_picker_node import _extract_ticker_context

    saved = load_quant_pick(trade_date)
    if saved and saved.get("all_records") is not None:
        today_raw = saved.get("today") or trade_date
        try:
            today_ts = pd.Timestamp(today_raw)
        except (TypeError, ValueError):
            today_ts = pd.Timestamp(trade_date)
        result = {
            "today": today_ts,
            "top_picks": pd.DataFrame(saved.get("top_picks", [])),
            "all_records": saved.get("all_records", []),
        }
        return {t: _extract_ticker_context(result, t) for t in tickers}

    from tradingagents.graph.trading_graph import TradingAgentsGraph
    graph = TradingAgentsGraph(debug=False, config=config)
    return graph.prepare_quant_contexts(tickers, trade_date)


def _render_quant_to_ai_entry(selected: list[str], trade_date: str) -> None:
    """Render the entry point from quant picker to AI deep analysis.

    Shows a selectbox of selected tickers (with Chinese names) and a button that
    triggers AI analysis directly without requiring tab switch.
    """
    name_map = _build_name_map(tuple(selected) if selected else None)
    st.markdown("---")
    st.markdown(f"**已选 {len(selected)} 只标的进入 AI 深度分析**")
    pick_col, btn_col = st.columns([3, 1])
    with pick_col:
        chosen = st.selectbox(
            "选择标的",
            options=selected,
            format_func=lambda c: f"{c} · {name_map.get(c, '--')}",
            key="quant_to_ai_ticker",
        )
    with btn_col:
        st.write("")

        def _start_analysis(chosen: str = chosen, trade_date: str = trade_date) -> None:
            st.session_state["_pending_input_ticker"] = chosen
            st.session_state["start_analysis"] = {
                "ticker": chosen,
                "trade_date": trade_date,
                "fresh": True,
                "source": "quant_picker",
            }
            st.session_state["viewing_history"] = None

        st.button(
            "开始 AI 分析",
            type="primary",
            use_container_width=True,
            on_click=_start_analysis,
        )

    # 多股票并行分析(选了 2 只以上时显示)
    if len(selected) > 1:
        st.markdown("---")
        batch_col1, batch_col2 = st.columns([3, 1])
        with batch_col1:
            st.caption(f"💡 或一次性并行分析全部 {len(selected)} 只标的(每只独立 LLM 流水线,同时跑)")
        with batch_col2:

            def _start_batch(selected: list[str] = selected, trade_date: str = trade_date) -> None:
                st.session_state["start_analysis_batch"] = {
                    "tickers": list(selected),
                    "trade_date": trade_date,
                    "fresh": True,
                }
                st.session_state["viewing_history"] = None

            st.button(
                f"并行分析全部 {len(selected)} 只",
                type="secondary",
                use_container_width=True,
                on_click=_start_batch,
            )


def _load_saved_quant_pick(data: dict, trade_date: str) -> None:
    """Load a saved quant pick result into session state and rerun."""
    import pandas as pd

    st.session_state["quant_tracker"] = None
    st.session_state["quant_picks"] = {
        "top_picks": pd.DataFrame(data.get("top_picks", [])),
        "per_strategy_stats": data.get("per_strategy_stats", {}),
        "all_records": data.get("all_records", []),
        "trade_date": trade_date,
        "elapsed": data.get("elapsed", 0),
        "n_strategies_run": data.get("n_strategies_run", 0),
        "n_strategies_error": data.get("n_strategies_error", 0),
    }
    st.rerun()


def main() -> None:
    """Render the TradingAgents A股分析 Streamlit UI."""
    st.set_page_config(
        page_title="Aquant投研工具",
        page_icon="📈",
        layout="wide",
        initial_sidebar_state="expanded",
    )

    st.markdown(
        """
        <style>
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700;900&display=swap');

        /* Hide Streamlit chrome for clean video recording.
           IMPORTANT: do NOT `display:none` the whole header OR the whole toolbar.
           In Streamlit >= 1.36 the "expand sidebar" button lives *inside* the
           toolbar (header > stToolbar > stExpandSidebarButton), so hiding either
           one makes a collapsed sidebar impossible to reopen (issue #36). Instead
           keep the header/toolbar in the DOM, make the header transparent, and
           hide only the individual chrome widgets we don't want on camera. */
        #MainMenu,
        footer,
        div[data-testid="stDecoration"],
        div[data-testid="stStatusWidget"],
        div[data-testid="stToolbarActions"],
        div[data-testid="stAppDeployButton"],
        span[data-testid="stMainMenu"] { display: none !important; }
        header[data-testid="stHeader"] {
            background: transparent !important;
            box-shadow: none !important;
        }
        /* Keep the sidebar collapse / expand controls always visible & clickable.
           Selector list spans multiple Streamlit versions. */
        button[data-testid="stExpandSidebarButton"],
        button[data-testid="stSidebarCollapseButton"],
        button[data-testid="collapsedControl"],
        [data-testid="stSidebarCollapsedControl"] {
            display: flex !important;
            visibility: visible !important;
            opacity: 1 !important;
        }

        html, body, [class*="css"] {
            font-family: 'Inter', -apple-system, sans-serif;
        }
        .stApp {
            background: #0a0a0a;
        }
        section[data-testid="stSidebar"] {
            background: #0f0f0f;
            border-right: 1px solid #1a1a1a;
        }
        .stMetric label { color: #888 !important; font-size: 0.8rem !important; }
        .stMetric [data-testid="stMetricValue"] {
            color: #ff5a1f !important;
            font-weight: 700 !important;
        }
        .stProgress > div > div > div {
            background: linear-gradient(90deg, #ff5a1f, #ff8c42) !important;
        }
        button[kind="primary"] {
            background: linear-gradient(135deg, #ff5a1f, #ff8c42) !important;
            border: none !important;
            font-weight: 700 !important;
            letter-spacing: 0.05em !important;
            box-shadow: 0 4px 15px rgba(255,90,31,0.3) !important;
            transition: all 0.2s ease !important;
        }
        button[kind="primary"]:hover {
            background: linear-gradient(135deg, #e04d15, #ff5a1f) !important;
            box-shadow: 0 6px 20px rgba(255,90,31,0.4) !important;
            transform: translateY(-1px) !important;
        }
        /* Secondary buttons (history items) */
        button[kind="secondary"] {
            background: #161616 !important;
            border: 1px solid #2a2a2a !important;
            color: #ccc !important;
            transition: all 0.2s ease !important;
        }
        button[kind="secondary"]:hover {
            background: #1e1e1e !important;
            border-color: #ff5a1f !important;
            color: #ff5a1f !important;
        }
        .stExpander {
            border: 1px solid #222 !important;
            border-radius: 8px !important;
        }
        .stTabs [data-baseweb="tab"] {
            color: #888 !important;
        }
        .stTabs [aria-selected="true"] {
            color: #ff5a1f !important;
            border-bottom-color: #ff5a1f !important;
        }
        div[data-testid="stDownloadButton"] button {
            background: #1a1a2e !important;
            border: 1px solid #ff5a1f !important;
            color: #ff5a1f !important;
        }
        /* Text input styling */
        input[data-testid="stTextInputRootElement"] input,
        .stTextInput input {
            background: #161616 !important;
            border-color: #2a2a2a !important;
            color: #f5f1eb !important;
        }
        .stTextInput input:focus {
            border-color: #ff5a1f !important;
            box-shadow: 0 0 0 1px #ff5a1f !important;
        }
        /* Date input styling */
        .stDateInput input {
            background: #161616 !important;
            border-color: #2a2a2a !important;
            color: #f5f1eb !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    # ── Sidebar ──────────────────────────────────────────────────────────────

    with st.sidebar:
        render_sidebar()

    # ── Handle "Start Analysis" trigger ──────────────────────────────────────

    start_req = st.session_state.pop("start_analysis", None)
    if start_req:
        if start_req.get("fresh"):
            from tradingagents.graph.checkpointer import clear_checkpoint

            clear_incomplete_task(start_req["ticker"], start_req["trade_date"])
            clear_checkpoint(
                DEFAULT_CONFIG["data_cache_dir"],
                start_req["ticker"],
                start_req["trade_date"],
            )

        tracker = ProgressTracker(
            ticker=start_req["ticker"],
            trade_date=start_req["trade_date"],
        )
        st.session_state["tracker"] = tracker
        st.session_state["viewing_history"] = None
        # 多 tracker 管理:清空 batch trackers,只保留单只
        st.session_state["trackers"] = {start_req["ticker"]: tracker}
        run_analysis_in_thread(
            ticker=start_req["ticker"],
            trade_date=start_req["trade_date"],
            config=_build_config(),
            tracker=tracker,
            ticker_source=start_req.get("source", "manual"),
        )

    # ── Handle "Start Analysis Batch" trigger (多股票并行) ──────────────────

    batch_req = st.session_state.pop("start_analysis_batch", None)
    if batch_req:
        tickers = batch_req.get("tickers", [])
        trade_date = batch_req.get("trade_date")
        fresh = batch_req.get("fresh", False)
        if fresh:
            from tradingagents.graph.checkpointer import clear_checkpoint
            for t in tickers:
                clear_incomplete_task(t, trade_date)
                clear_checkpoint(DEFAULT_CONFIG["data_cache_dir"], t, trade_date)

        config = _build_config()

        # Bug 1 fix: build quant contexts ONCE for the whole batch (either
        # reuse the saved pick() JSON if the user just ran quant picker, or
        # run pick() synchronously here). Each ticker's context is then
        # injected into its own thread so the Quant Picker LangGraph node
        # is a no-op - without this, each ticker re-runs pick() (~3 min/ea).
        if config.get("quant_layer_enabled") and tickers:
            with st.spinner("正在准备量化选股上下文(若缓存未命中需 3 分钟)..."):
                quant_contexts = _build_quant_contexts_for_batch(
                    tickers, trade_date, config,
                )
        else:
            quant_contexts = {t: "" for t in tickers}

        trackers: dict[str, ProgressTracker] = {}
        for t in tickers:
            tk = ProgressTracker(ticker=t, trade_date=trade_date)
            trackers[t] = tk
            run_analysis_in_thread(
                ticker=t,
                trade_date=trade_date,
                config=config,
                tracker=tk,
                pre_quant_context=quant_contexts.get(t, ""),
                ticker_source="quant_picker",
            )
        st.session_state["trackers"] = trackers
        # 默认查看第一只
        if tickers:
            st.session_state["tracker"] = trackers[tickers[0]]
        st.session_state["viewing_history"] = None
        st.session_state["force_tab"] = "ai"
        st.rerun()

    # ── Handle "Start Quant Pick" trigger ────────────────────────────────────

    start_quant = st.session_state.pop("start_quant_pick", None)
    if start_quant:
        import pandas as pd

        quant_tracker = QuantProgressTracker(
            trade_date=start_quant["trade_date"],
        )
        st.session_state["quant_tracker"] = quant_tracker
        st.session_state["quant_picks"] = None
        run_quant_pick_in_thread(
            today=pd.Timestamp(start_quant["trade_date"]),
            daily_cache_name=st.session_state.get(
                "quant_daily_cache", "daily_main_board_liquid",
            ),
            top_n=int(st.session_state.get("quant_top_n", "20")),
            n_workers=int(st.session_state.get("quant_n_workers", 8)),
            tracker=quant_tracker,
        )

    # ── Main area: 4-tab layout ─────────────────────────────────────────────

    tab_quant, tab_ai, tab_rec, tab_hist = st.tabs([
        "📊 量化选股",
        "🤖 AI 深度分析",
        "🎯 综合推荐",
        "📜 历史",
    ])

    # 程序化切换 tab:sidebar button 点击后设 force_tab,这里注入 JS 点击目标 tab
    _force_tab = st.session_state.pop("force_tab", None)
    if _force_tab:
        _tab_index = {"quant": 0, "ai": 1, "rec": 2, "hist": 3}.get(_force_tab, 0)
        st.components.v1.html(
            f"""
            <script>
            const tabs = parent.document.querySelectorAll('button[role="tab"]');
            if (tabs.length > {_tab_index}) {{
                tabs[{_tab_index}].click();
            }}
            </script>
            """,
            height=0,
        )

    # ── Tab 1: 量化选股 ──────────────────────────────────────────────────────

    with tab_quant:
        quant_tracker: QuantProgressTracker | None = st.session_state.get("quant_tracker")
        quant_picks = st.session_state.get("quant_picks")

        if quant_tracker and quant_tracker.is_running:
            render_quant_progress(quant_tracker)
            time.sleep(2)
            st.rerun()
        elif quant_tracker and quant_tracker.is_complete and quant_tracker.top_picks is not None:
            st.session_state["quant_picks"] = {
                "top_picks": quant_tracker.top_picks,
                "per_strategy_stats": quant_tracker.per_strategy_stats,
                "all_records": quant_tracker.all_records,
                "trade_date": quant_tracker.trade_date,
                "elapsed": quant_tracker.pick_elapsed,
                "n_strategies_run": quant_tracker.total_strategies,
                "n_strategies_error": quant_tracker.n_strategies_error,
            }
            st.success(
                f"选股完成 · {quant_tracker.total_strategies} 策略 · "
                f"错误 {quant_tracker.n_strategies_error} · "
                f"耗时 {quant_tracker.pick_elapsed:.1f}s"
            )
            selected = render_quant_picker(
                quant_tracker.top_picks,
                all_records=quant_tracker.all_records,
                name_map=_build_name_map(tuple(quant_tracker.top_picks["stock_code"].astype(str).tolist())),
                key_prefix="quant_tab",
            )
            if selected:
                st.session_state["quant_selected_tickers"] = selected
                _render_quant_to_ai_entry(selected, quant_tracker.trade_date)
        elif quant_picks and len(quant_picks.get("top_picks", [])) > 0:
            top_picks_df = quant_picks["top_picks"]
            st.success(
                f"历史选股 · {quant_picks['trade_date']} · "
                f"{quant_picks['n_strategies_run']} 策略 · "
                f"耗时 {quant_picks['elapsed']:.1f}s"
            )
            selected = render_quant_picker(
                top_picks_df,
                all_records=quant_picks.get("all_records") or None,
                name_map=_build_name_map(tuple(top_picks_df["stock_code"].astype(str).tolist())),
                key_prefix="quant_hist",
            )
            if selected:
                st.session_state["quant_selected_tickers"] = selected
                _render_quant_to_ai_entry(selected, quant_picks["trade_date"])
            if st.button("返回选股主页"):
                st.session_state["quant_picks"] = None
                st.rerun()
        elif quant_tracker and quant_tracker.error:
            st.error(f"选股失败: {quant_tracker.error}")
        else:
            from tradingagents.quant.strategy.strategy_library_final import get_all_strategies_final
            _n_strats = len(get_all_strategies_final())
            st.markdown(
                f"""
                <div style="text-align:center; padding:2rem; color:#888;">
                    <div style="font-size:3rem; margin-bottom:1rem;">📊</div>
                    <div style="font-size:1.2rem; margin-bottom:0.5rem;">
                        {_n_strats} 策略量化选股
                    </div>
                    <div style="font-size:0.9rem;">
                        纯量化层，不依赖 LLM。在左侧边栏配置 Top N 和 worker 数，<br>
                        点击「开始选股」启动 {_n_strats} 策略批量运行。
                    </div>
                </div>
                """,
                unsafe_allow_html=True,
            )

            quant_hist = get_quant_history()
            if quant_hist:
                st.markdown("---")
                st.markdown("#### 最近选股记录")
                for entry in quant_hist[:5]:
                    label = (
                        f"{entry['trade_date']} · Top {entry['n_picks']} · "
                        f"{entry['n_strategies_run']} 策略 · "
                        f"{entry['elapsed']:.0f}s"
                    )
                    if st.button(label, key=f"qhist_{entry['trade_date']}"):
                        data = load_quant_pick(entry["trade_date"])
                        if data:
                            _load_saved_quant_pick(data, entry["trade_date"])

    # ── Tab 2: AI 深度分析 ────────────────────────────────────────────────────

    with tab_ai:
        # 多 tracker 概览(并行分析时显示)
        all_trackers: dict = st.session_state.get("trackers") or {}
        if len(all_trackers) > 1:
            st.markdown(f"#### 并行分析 {len(all_trackers)} 只标的")
            overview_cols = st.columns(len(all_trackers))
            ticker_keys = list(all_trackers.keys())
            for col, t in zip(overview_cols, ticker_keys):
                tk = all_trackers[t]
                if tk.is_running:
                    status_emoji = "🔄"
                    status_text = f"{len(tk.completed_stages)}/12"
                elif tk.is_complete:
                    status_emoji = "✅"
                    status_text = "完成"
                elif tk.error:
                    status_emoji = "❌"
                    status_text = "出错"
                else:
                    status_emoji = "⏸"
                    status_text = "暂停"
                if col.button(
                    f"{status_emoji} {t} · {status_text}",
                    key=f"tracker_switch_{t}",
                    use_container_width=True,
                    type="primary" if st.session_state.get("tracker") is tk else "secondary",
                ):
                    st.session_state["tracker"] = tk
                    st.rerun()
            st.markdown("---")

        tracker: ProgressTracker | None = st.session_state.get("tracker")
        viewing_history: str | None = st.session_state.get("viewing_history")

        if viewing_history:
            try:
                state = load_analysis(viewing_history)
                signal = extract_signal(state)
                ticker = Path(viewing_history).parent.parent.name
                trade_date = Path(viewing_history).stem.replace("full_states_log_", "")
                render_report(state, ticker, trade_date, signal)
            except Exception as exc:
                st.error(f"加载失败: {exc}")

        elif tracker and tracker.is_running:
            render_progress(tracker)
            time.sleep(0.5)
            st.rerun()

        elif tracker and tracker.is_complete:
            render_report(
                tracker.final_state,
                tracker.ticker,
                tracker.trade_date,
                tracker.signal,
                elapsed=tracker.elapsed,
            )
            # 幂等保存:首次完成时落盘,后续 rerun 跳过(避免每次切 tab 都写盘)
            rec_key = f"rec_saved_{tracker.ticker}_{tracker.trade_date}"
            if not st.session_state.get(rec_key):
                try:
                    final_ranked = tracker.final_state.get("final_ranked_decision", "")
                    save_recommendation(
                        trade_date=tracker.trade_date,
                        ticker=tracker.ticker,
                        # 与批量路径一致:优先干净的 4 档标签(见下方 Bug 2 注释)
                        label=tracker.signal or final_ranked,
                        final_state_summary={
                            "final_trade_decision": tracker.final_state.get("final_trade_decision", ""),
                            "final_ranked_decision": final_ranked,
                            "signal": tracker.signal,
                            "quant_pick_context": tracker.final_state.get("quant_pick_context", ""),
                        },
                    )
                    st.session_state[rec_key] = True
                except Exception:
                    pass

        elif tracker and tracker.error:
            st.error(f"分析失败: {tracker.error}")
            st.caption("已完成阶段会保存在本地断点中；修复模型额度或配置后，可以继续未完成的部分。")
            if st.button("继续未完成任务", type="primary"):
                st.session_state["start_analysis"] = {
                    "ticker": tracker.ticker,
                    "trade_date": tracker.trade_date,
                }
                st.session_state["viewing_history"] = None
                st.rerun()

        else:
            quant_picks = st.session_state.get("quant_picks")
            selected = st.session_state.get("quant_selected_tickers") or []
            if quant_picks and selected:
                st.info(
                    f"已从量化选股层获取 {len(selected)} 只标的候选: {', '.join(selected)}。"
                    f"在左侧边栏输入其中一只代码，开始 AI 深度分析。"
                )

            st.markdown(
                """
                <div style="
                    display: flex;
                    flex-direction: column;
                    align-items: center;
                    justify-content: center;
                    min-height: 50vh;
                    text-align: center;
                ">
                    <div style="font-size: 3rem; margin-bottom: 1rem;">🤖</div>
                    <div style="font-size: 2rem; font-weight: 900; margin-bottom: 0.5rem;">
                        <span style="color: #ff5a1f;">AI</span>
                        <span style="color: #f5f1eb;">深度分析</span>
                    </div>
                    <div style="color: #888; font-size: 1rem; max-width: 500px; line-height: 1.6;">
                        7位AI分析师 -> 质量门控 -> 多空辩论 -> 风控评估 -> 最终决策<br>
                        在左侧输入股票代码，开始 AI 深度分析
                    </div>
                </div>
                """,
                unsafe_allow_html=True,
            )

        # 自动刷新:有任何 tracker 在跑就持续 rerun(放最后,不覆盖上面的渲染)。
        # 之前这里有个 if any_running: render_progress else: st.info 的分支,
        # 会强制 rerun 跳过下面的 elif tracker.is_complete,导致切到已完成
        # ticker 时卡在"其他标的分析中"信息提示。现在只保留 rerun 触发。
        if any(tk.is_running for tk in all_trackers.values()):
            time.sleep(0.5)
            st.rerun()

    # ── Tab 3: 综合推荐 ──────────────────────────────────────────────────────

    with tab_rec:
        # Bug 2 fix: batch mode iterates ALL trackers (not just the active
        # one) so every completed ticker's recommendation is auto-saved and
        # rendered. Previously only st.session_state["tracker"] (the active
        # ticker) showed up, so the other N-1 tickers in a batch were
        # silently dropped from the recommendation tab.
        all_trackers: dict = st.session_state.get("trackers") or {}
        recommendations: dict[str, dict] = {}

        for t, tk in all_trackers.items():
            if not tk.is_complete:
                continue
            final_ranked = tk.final_state.get("final_ranked_decision", "")
            # Prefer the clean Conflict Resolver label (e.g. "🟢 强买"); the full
            # final_ranked_decision markdown is only a fallback (parse_label can
            # still extract the **标签** line from it).
            label = tk.signal or final_ranked
            recommendations[t] = {
                "label": label,
                "final_ranked_decision": final_ranked,
                "final_trade_decision": tk.final_state.get("final_trade_decision", ""),
                "signal": tk.signal,
            }
            # Auto-save so the recommendation survives a page refresh and
            # shows up in the recommendation history tab. 幂等:同一
            # (trade_date, ticker) 首次完成时落盘,后续 rerun 跳过
            # (与单股路径 rec_key 一致,避免每次 rerun 都写盘)。
            rec_key = f"rec_saved_{t}_{tk.trade_date}"
            if not st.session_state.get(rec_key):
                try:
                    save_recommendation(
                        trade_date=tk.trade_date,
                        ticker=t,
                        label=label,
                        final_state_summary={
                            "final_trade_decision": tk.final_state.get("final_trade_decision", ""),
                            "final_ranked_decision": final_ranked,
                            "signal": tk.signal,
                            "quant_pick_context": tk.final_state.get("quant_pick_context", ""),
                        },
                    )
                    st.session_state[rec_key] = True
                except Exception:
                    pass

        rec_hist = get_recommendation_history()
        for entry in rec_hist[:10]:
            if entry["ticker"] in recommendations:
                continue
            try:
                data = load_recommendation(entry["trade_date"], entry["ticker"])
                recommendations[entry["ticker"]] = {
                    "label": data.get("label", ""),
                    "final_ranked_decision": data.get("final_ranked_decision", ""),
                    "final_trade_decision": data.get("final_trade_decision", ""),
                    "signal": data.get("signal", ""),
                }
            except Exception:
                pass

        if recommendations:
            st.markdown("### 综合推荐")
            st.caption("🟢强买 / 🟡关注 / 🟠冲突 / 🔴弃 · 综合 LLM 决策与量化层信号")
            render_recommendation(recommendations)
        else:
            st.markdown(
                """
                <div style="text-align:center; padding:2rem; color:#888;">
                    <div style="font-size:3rem; margin-bottom:1rem;">🎯</div>
                    <div style="font-size:1.2rem; margin-bottom:0.5rem;">
                        综合推荐
                    </div>
                    <div style="font-size:0.9rem;">
                        完成 AI 深度分析后，<br>
                        此处将按 🟢强买 / 🟡关注 / 🟠冲突 / 🔴弃 四类展示推荐结果。
                    </div>
                </div>
                """,
                unsafe_allow_html=True,
            )

    # ── Tab 4: 历史 ──────────────────────────────────────────────────────────

    with tab_hist:
        st.markdown("### 历史记录")

        hist_col1, hist_col2 = st.columns(2)

        with hist_col1:
            st.markdown("#### AI 分析历史")
            history = get_history()
            if not history:
                st.caption("暂无 AI 分析历史")
            else:
                for entry in history[:30]:
                    t, d = entry["ticker"], entry["date"]
                    label = f"{t} · {d}"
                    if st.button(label, key=f"hist_tab_{t}_{d}", use_container_width=True):
                        st.session_state["viewing_history"] = entry["path"]
                        st.session_state["start_analysis"] = None
                        st.session_state["force_tab"] = "ai"
                        st.rerun()

        with hist_col2:
            st.markdown("#### 量化选股历史")
            quant_hist = get_quant_history()
            if not quant_hist:
                st.caption("暂无量化选股历史")
            else:
                for entry in quant_hist[:30]:
                    label = (
                        f"{entry['trade_date']} · Top {entry['n_picks']} · "
                        f"{entry['elapsed']:.0f}s"
                    )
                    if st.button(label, key=f"qhist_tab_{entry['trade_date']}", use_container_width=True):
                        data = load_quant_pick(entry["trade_date"])
                        if data:
                            _load_saved_quant_pick(data, entry["trade_date"])

            quant_picks = st.session_state.get("quant_picks")
            if quant_picks and len(quant_picks.get("top_picks", [])) > 0:
                st.markdown("---")
                st.markdown(f"#### 历史选股 · {quant_picks['trade_date']}")
                st.caption(
                    f"{quant_picks['n_strategies_run']} 策略 · "
                    f"错误 {quant_picks.get('n_strategies_error', 0)} · "
                    f"耗时 {quant_picks['elapsed']:.1f}s"
                )
                selected = render_quant_picker(
                    quant_picks["top_picks"],
                    all_records=quant_picks.get("all_records") or None,
                    name_map=_build_name_map(tuple(quant_picks["top_picks"]["stock_code"].astype(str).tolist())),
                    key_prefix="quant_hist_tab",
                )
                if selected:
                    st.session_state["quant_selected_tickers"] = selected
                    _render_quant_to_ai_entry(selected, quant_picks["trade_date"])
                if st.button("清除历史查看"):
                    st.session_state["quant_picks"] = None
                    st.rerun()

        st.markdown("---")
        st.markdown("#### 推荐历史")
        if not rec_hist:
            st.caption("暂无推荐历史")
        else:
            for entry in rec_hist[:30]:
                label = f"{entry['trade_date']} · {entry['ticker']} · {entry['label'][:20]}"
                if st.button(label, key=f"rec_tab_{entry['trade_date']}_{entry['ticker']}", use_container_width=True):
                    data = load_recommendation(entry["trade_date"], entry["ticker"])
                    if data:
                        st.markdown(f"### {entry['ticker']} · {entry['trade_date']}")
                        if data.get("signal"):
                            st.caption(f"信号: {data['signal']}")
                        if data.get("final_trade_decision"):
                            st.markdown("**最终决策:**")
                            st.markdown(data["final_trade_decision"][:2000])
                        if data.get("final_ranked_decision"):
                            st.markdown("**分级标签:**")
                            st.markdown(data["final_ranked_decision"][:1000])
