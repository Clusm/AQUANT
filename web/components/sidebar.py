"""Sidebar: stock input, LLM config, and history list."""

from __future__ import annotations

from datetime import date

import streamlit as st

from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.graph.checkpointer import clear_checkpoint
from tradingagents.llm_clients.model_catalog import MODEL_OPTIONS
from web.history import (
    clear_incomplete_task,
    get_history,
    get_incomplete_history,
    record_incomplete_task,
)

# Provider display names in recommended order. 默认 DeepSeek(国内直连首选),
# 其余为可回退项——配好对应环境变量即可切换(见 openai_client._PROVIDER_CONFIG)。
_PROVIDERS: list[tuple[str, str]] = [
    ("DeepSeek", "deepseek"),
    ("OpenAI", "openai"),
    ("Anthropic", "anthropic"),
    ("Qwen 通义", "qwen"),
    ("GLM 智谱", "glm"),
    ("MiniMax", "minimax"),
    ("Ollama 本地", "ollama"),
]

_PROVIDER_DISPLAY = [name for name, _ in _PROVIDERS]
_PROVIDER_KEYS = [key for _, key in _PROVIDERS]


def _resolve_user_input(raw: str) -> tuple[str, str | None]:
    """Resolve raw user input to (ticker_code, error_msg).

    Accepts 6-digit codes or Chinese stock names (e.g. '宝光股份').
    Returns (code, None) on success or ("", error_msg) on failure.
    """
    from tradingagents.dataflows.a_stock import resolve_ticker

    try:
        code = resolve_ticker(raw)
        return code, None
    except ValueError as e:
        return "", str(e)


def _clear_analysis_artifacts(ticker: str, trade_date: str) -> None:
    clear_incomplete_task(ticker, trade_date)
    clear_checkpoint(DEFAULT_CONFIG["data_cache_dir"], ticker, trade_date)


def _render_analysis_controls(raw_ticker: str, trade_date_value: date) -> None:
    tracker = st.session_state.get("tracker")
    quant_tracker = st.session_state.get("quant_tracker")
    is_running = tracker is not None and tracker.is_running
    is_quant_running = quant_tracker is not None and quant_tracker.is_running
    trade_date = trade_date_value.strftime("%Y-%m-%d")

    pause_col, resume_col, stop_col = st.columns(3)

    # 量化选股用 multiprocessing.Pool,无法在策略执行中暂停(只能在策略间检查
    # stop_requested)。暂停/恢复对量化场景语义无效,所以 quant running 时禁用。
    pause_disabled = (
        not is_running
        or is_quant_running
        or tracker.is_paused
        or tracker.stop_requested
    )
    if pause_col.button(
        "暂停",
        key="sidebar_pause_analysis",
        use_container_width=True,
        disabled=pause_disabled,
        help="暂停 LLM 分析(量化选股不支持暂停,只能停止)",
    ):
        if tracker.pause():
            record_incomplete_task(
                tracker.ticker,
                tracker.trade_date,
                status="paused",
                completed_stages=tracker.completed_stages,
            )
        st.rerun()

    resume_disabled = (
        not is_running
        or is_quant_running
        or not tracker.is_paused
        or tracker.stop_requested
    )
    if resume_col.button(
        "恢复",
        key="sidebar_resume_analysis",
        use_container_width=True,
        disabled=resume_disabled,
    ):
        if tracker.resume():
            record_incomplete_task(
                tracker.ticker,
                tracker.trade_date,
                status="running",
                completed_stages=tracker.completed_stages,
            )
        st.rerun()

    # 停止按钮:LLM 分析和量化选股都支持
    can_stop = is_running or is_quant_running or bool(raw_ticker.strip())
    if stop_col.button(
        "停止",
        key="sidebar_stop_analysis",
        use_container_width=True,
        disabled=not can_stop,
    ):
        if is_quant_running and quant_tracker is not None:
            # 量化选股进行中:调 quant_tracker.request_stop(),
            # _run_quant 的 progress_callback 会检查 stop_requested 提前返回
            quant_tracker.request_stop()
            st.session_state["quant_tracker"] = None
            st.success("已停止量化选股,当前正在跑的策略完成后会退出。")
            st.rerun()
            return

        target_ticker = tracker.ticker if tracker is not None and tracker.ticker else ""
        target_date = (
            tracker.trade_date
            if tracker is not None and tracker.trade_date
            else trade_date
        )

        if not target_ticker:
            target_ticker, err = _resolve_user_input(raw_ticker)
            if err:
                st.error(f"❌ {err}")
                return

        if tracker is not None and tracker.is_running:
            tracker.request_stop()
            clear_incomplete_task(target_ticker, target_date)
        else:
            if tracker is not None:
                tracker.mark_stopped()
                st.session_state["tracker"] = None
            _clear_analysis_artifacts(target_ticker, target_date)

        st.session_state["viewing_history"] = None
        st.success("已清空当前进度；下一次开始分析会从头生成。")
        st.rerun()

    if tracker is not None and tracker.stop_requested:
        st.caption("正在停止并清空，收尾完成后可重新开始。")
    if quant_tracker is not None and quant_tracker.stop_requested:
        st.caption("正在停止量化选股,等待当前策略完成。")


def _render_llm_config() -> None:
    """Render LLM provider and model selection controls."""

    provider_idx = st.selectbox(
        "LLM 供应商",
        range(len(_PROVIDERS)),
        format_func=lambda i: _PROVIDER_DISPLAY[i],
        key="llm_provider_idx",
        help="选择你配置了 API Key 的供应商",
    )
    provider_key = _PROVIDER_KEYS[provider_idx]
    st.session_state["llm_provider"] = provider_key

    if provider_key in MODEL_OPTIONS:
        quick_options = MODEL_OPTIONS[provider_key]["quick"]
        deep_options = MODEL_OPTIONS[provider_key]["deep"]

        quick_labels = [label for label, _ in quick_options]
        quick_values = [value for _, value in quick_options]
        deep_labels = [label for label, _ in deep_options]
        deep_values = [value for _, value in deep_options]

        quick_idx = st.selectbox(
            "快速思考模型",
            range(len(quick_options)),
            format_func=lambda i: quick_labels[i],
            key="quick_model_idx",
            help="用于常规分析任务，速度优先",
        )
        st.session_state["quick_think_llm"] = quick_values[quick_idx]

        deep_idx = st.selectbox(
            "深度思考模型",
            range(len(deep_options)),
            format_func=lambda i: deep_labels[i],
            key="deep_model_idx",
            help="用于辩论/决策等需要深度推理的任务",
        )
        st.session_state["deep_think_llm"] = deep_values[deep_idx]
    else:
        custom_quick = st.text_input("快速思考模型 ID", key="custom_quick_model")
        custom_deep = st.text_input("深度思考模型 ID", key="custom_deep_model")
        st.session_state["quick_think_llm"] = custom_quick
        st.session_state["deep_think_llm"] = custom_deep

    st.text_input(
        "API Base URL（第三方/代理，可选）",
        key="llm_base_url",
        placeholder="例: https://your-proxy.com/v1",
        help=(
            "通过第三方中转/代理访问模型时填写网关地址；"
            "留空则用所选供应商的官方地址。"
            "API Key 从 .env 读取:DeepSeek=DEEPSEEK_API_KEY、"
            "OpenAI=OPENAI_API_KEY、Anthropic=ANTHROPIC_API_KEY、"
            "Qwen=DASHSCOPE_API_KEY、GLM=ZHIPU_API_KEY、MiniMax=MINIMAX_API_KEY、"
            "Ollama=本地无需 key。也可在 .env 里设 BACKEND_URL 代替此处。"
        ),
    )


def _render_quant_config() -> None:
    """Render quant pick layer controls (Top N, workers, cache)."""

    from tradingagents.quant.strategy.strategy_library_final import get_all_strategies_final
    _n_strats = len(get_all_strategies_final())

    st.selectbox(
        "Top N 候选",
        options=["10", "20", "5"],
        key="quant_top_n",
        help=f"{_n_strats} 策略最终返回 Top N 候选数。10=默认(平衡覆盖与 API 成本),5=快速验证,20=最大覆盖。",
    )

    st.number_input(
        "并行 worker 数",
        min_value=1,
        max_value=16,
        value=8,
        step=1,
        key="quant_n_workers",
        help="multiprocessing.Pool 大小（Windows spawn，启动较慢）。",
    )

    st.selectbox(
        "日线缓存",
        options=[
            "daily_main_board_liquid",
            "daily_main_board",
        ],
        key="quant_daily_cache",
        help=(
            "daily_main_board_liquid=流动性前 80%(~2433 股,数据采集层已截断,推荐);"
            "daily_main_board=全量主板(~3042 股,慢但覆盖广)。"
        ),
    )


def render_sidebar() -> None:
    """Render the sidebar with input controls and history."""

    st.markdown(
        """
        <div style="text-align:center; margin-bottom:1.5rem;">
            <span style="font-size:2rem; font-weight:800; color:#ff5a1f;">Aquant</span><span style="font-size:2rem; font-weight:800; color:#f5f1eb;">投研工具</span>
            <div style="font-size:0.85rem; color:#888; margin-top:0.2rem;">
                A股多Agent投研系统
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    st.markdown("---")
    st.markdown("#### 新建分析")

    _render_background_fetcher_status()

    if "_pending_input_ticker" in st.session_state:
        st.session_state["input_ticker"] = st.session_state.pop("_pending_input_ticker")

    ticker = st.text_input(
        "股票代码",
        placeholder="例: 300750 或 宁德时代",
        key="input_ticker",
        help="输入6位A股代码或中文股票全称",
    )

    trade_date = st.date_input(
        "分析日期",
        value=date.today(),
        key="input_date",
    )

    with st.expander("⚙️ 模型配置", expanded=False):
        _render_llm_config()

    with st.expander("📊 量化选股配置", expanded=False):
        _render_quant_config()

    tracker = st.session_state.get("tracker")
    is_busy = tracker is not None and tracker.is_running
    is_stopping = is_busy and tracker.stop_requested

    quant_tracker = st.session_state.get("quant_tracker")
    is_quant_busy = quant_tracker is not None and quant_tracker.is_running
    is_quant_stopping = is_quant_busy and quant_tracker.stop_requested

    btn_col1, btn_col2 = st.columns(2)
    with btn_col1:
        if st.button(
            "开始选股" if not is_quant_busy else "停止中..." if is_quant_stopping else "选股中...",
            key="sidebar_start_quant_pick",
            use_container_width=True,
            disabled=is_quant_busy,
            type="primary",
        ):
            st.session_state["start_quant_pick"] = {
                "trade_date": trade_date.strftime("%Y-%m-%d"),
                "fresh": True,
            }
            st.session_state["viewing_history"] = None
    with btn_col2:
        if st.button(
            "开始分析" if not is_busy else "停止中..." if is_stopping else "分析中...",
            key="sidebar_start_analysis",
            use_container_width=True,
            disabled=is_busy or not ticker,
            type="primary",
        ):
            resolved_code, err = _resolve_user_input(ticker)
            if err:
                st.error(f"❌ {err}")
            else:
                if resolved_code != ticker.strip():
                    st.success(f"✅ {ticker.strip()} → {resolved_code}")
                st.session_state["start_analysis"] = {
                    "ticker": resolved_code,
                    "trade_date": trade_date.strftime("%Y-%m-%d"),
                    "fresh": True,
                    "source": "manual",
                }
                st.session_state["viewing_history"] = None

    _render_analysis_controls(ticker, trade_date)

    st.markdown("---")
    st.markdown("#### 未完成任务")

    incomplete = get_incomplete_history()
    if not incomplete:
        st.caption("暂无未完成任务")
    else:
        for entry in incomplete[:10]:
            t, d = entry["ticker"], entry["trade_date"]
            status_label = {
                "error": "出错",
                "paused": "已暂停",
                "running": "进行中",
            }.get(entry.get("status"), "可继续")
            step = entry.get("checkpoint_step")
            step_label = f" · step {step}" if step is not None else ""
            label = f"{t}  ·  {d}  ·  {status_label}{step_label}"
            if st.button(
                label,
                key=f"resume_{t}_{d}",
                use_container_width=True,
                disabled=is_busy,
            ):
                st.session_state["start_analysis"] = {
                    "ticker": t,
                    "trade_date": d,
                    "source": "manual",
                }
                st.session_state["viewing_history"] = None
                st.session_state["force_tab"] = "ai"
                st.rerun()

    st.markdown("---")
    st.markdown("#### 历史记录")

    history = get_history()
    if not history:
        st.caption("暂无历史记录")
        return

    for entry in history[:20]:
        t, d = entry["ticker"], entry["date"]
        label = f"{t}  ·  {d}"
        if st.button(label, key=f"hist_{t}_{d}", use_container_width=True):
            st.session_state["viewing_history"] = entry["path"]
            st.session_state["start_analysis"] = None
            st.session_state["force_tab"] = "ai"
            st.rerun()

    st.markdown("---")


def _render_background_fetcher_status() -> None:
    """显示后台主板数据拉取状态(web app 启动时自动触发)。"""
    try:
        from web.background_fetcher import get_status
        status = get_status()
    except Exception:
        return

    if status["status"] == "idle":
        return

    st_markdown_map = {
        "running": ("🟡", "后台增量更新中"),
        "done": ("🟢", "后台增量完成"),
        "skip": ("⚪", "后台增量跳过"),
        "error": ("🔴", "后台增量失败"),
    }
    icon, label = st_markdown_map.get(status["status"], ("⚪", status["status"]))

    with st.expander(f"{icon} {label} · {status['stage']}", expanded=False):
        if status["message"]:
            st.caption(status["message"])
        if status["status"] == "running" and status["progress"] > 0:
            st.progress(status["progress"] / 100.0)
        if status["started_at"]:
            st.caption(f"开始: {status['started_at'][:19].replace('T', ' ')}")
        if status["finished_at"]:
            st.caption(f"完成: {status['finished_at'][:19].replace('T', ' ')}")
    st.caption("⚠️ 仅供学习研究，不构成投资建议")
