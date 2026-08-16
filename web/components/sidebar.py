"""Sidebar: stock input, LLM config, and history list."""

from __future__ import annotations

import os
from datetime import date

import streamlit as st

from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.graph.checkpointer import clear_checkpoint
from web.history import (
    clear_incomplete_task,
    get_history,
    get_incomplete_history,
    record_incomplete_task,
)
from web.theme import BRAND_GRADIENT, MUTED, SURFACE_2, TEXT
from web.user_config import load_user_config, save_user_config

# 模型数据源:按用户要求只保留 DeepSeek 官方与 OpenCode 中转。
# 两者都使用 DeepSeek 模型目录;OpenCode 仅把 base_url 指向中转网关。
_GATEWAYS: list[tuple[str, str]] = [
    ("DeepSeek 官方", "deepseek_official"),
    ("OpenCode 中转", "opencode"),
]

_GATEWAY_DISPLAY = [name for name, _ in _GATEWAYS]
_GATEWAY_KEYS = [key for _, key in _GATEWAYS]


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
    """Render LLM data-source controls.

    Model selection is intentionally hidden: quick/deep models are pinned to
    DeepSeek-V4-Flash / DeepSeek-V4-Pro in web/app_main.py ``_build_config()``.
    Data source / API Key / OpenCode URL are persisted in
    ~/.tradingagents/web_config.json so a Streamlit restart keeps them.
    """
    saved = load_user_config()

    # 首次进入页面时恢复上次选择(已有 session_state 时不做覆盖)
    saved_gateway = saved.get("llm_gateway")
    if "llm_gateway_idx" not in st.session_state and saved_gateway in _GATEWAY_KEYS:
        st.session_state["llm_gateway_idx"] = _GATEWAY_KEYS.index(saved_gateway)
    if "llm_api_key" not in st.session_state:
        st.session_state["llm_api_key"] = saved.get("llm_api_key", "")
    if "opencode_base_url_input" not in st.session_state:
        st.session_state["opencode_base_url_input"] = (
            saved.get("llm_base_url") or os.getenv("BACKEND_URL") or ""
        )

    _current = st.session_state.get("llm_gateway_idx")
    if not isinstance(_current, int) or not 0 <= _current < len(_GATEWAYS):
        st.session_state["llm_gateway_idx"] = 0

    gateway_idx = st.selectbox(
        "模型数据源",
        range(len(_GATEWAYS)),
        format_func=lambda i: _GATEWAY_DISPLAY[i],
        key="llm_gateway_idx",
        help="DeepSeek 官方=直连官方地址;OpenCode 中转=使用你的 OpenCode Go 网关",
    )
    gateway_key = _GATEWAY_KEYS[gateway_idx]
    st.session_state["llm_gateway"] = gateway_key
    # 无论官方还是中转,模型都是 DeepSeek 目录
    st.session_state["llm_provider"] = "deepseek"
    if saved.get("llm_gateway") != gateway_key:
        save_user_config({"llm_gateway": gateway_key})

    st.text_input(
        "API Key",
        key="llm_api_key",
        type="password",
        placeholder="留空则读取 .env 的 DEEPSEEK_API_KEY",
        help="保存在本机 ~/.tradingagents/web_config.json,重启后自动恢复。",
    )
    api_key_value = st.session_state.get("llm_api_key") or ""
    if api_key_value and not api_key_value.isascii():
        st.error("API Key 包含中文/空格等非 ASCII 字符,请重新粘贴完整的 key。")
        api_key_value = ""
        st.session_state["llm_api_key"] = ""
    if saved.get("llm_api_key") != api_key_value:
        save_user_config({"llm_api_key": api_key_value})

    if gateway_key == "opencode":
        default_base = os.getenv("BACKEND_URL", "")
        entered = st.text_input(
            "OpenCode 网关地址",
            key="opencode_base_url_input",
            placeholder="例: https://opencode.ai/proxy/v1",
            help=(
                "填写 OpenCode Go 套餐的网关地址,留空则读取 .env 的 BACKEND_URL。"
                "API Key 使用 DEEPSEEK_API_KEY(即你的 OpenCode Go key)。"
            ),
        )
        st.session_state["llm_base_url"] = entered or default_base
        if saved.get("llm_base_url") != (entered or ""):
            save_user_config({"llm_base_url": entered or ""})
    else:
        st.session_state["llm_base_url"] = ""


def render_sidebar() -> None:
    """Render the sidebar with input controls and history."""

    st.markdown(
        f"""
        <div style="text-align:center; margin-bottom:1.2rem; background:{SURFACE_2};
                    border:1px solid #2a2a2a; border-radius:16px; padding:1.1rem 0.7rem 0.9rem;">
            <div style="font-size:1.7rem; font-weight:900; letter-spacing:-0.01em;">
                <span style="background:{BRAND_GRADIENT};-webkit-background-clip:text;
                             background-clip:text;color:transparent;">Aquant</span>
                <span style="color:{TEXT};">投研工具</span>
            </div>
            <div style="font-size:0.82rem; color:{MUTED}; margin-top:0.35rem;">
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

    # 分析日期不再提供人工选择:统一使用今天(量化层会按缓存最新日期自动回退)。
    trade_date = date.today()

    with st.expander("⚙️ 模型配置", expanded=False):
        _render_llm_config()

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
