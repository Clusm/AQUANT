"""Real-time progress display for the analysis pipeline."""

from __future__ import annotations

import streamlit as st

from web.progress import PIPELINE_STAGES, ProgressTracker, QuantProgressTracker


def _status_badge(status: str) -> str:
    if status == "done":
        return '<span style="color:#22c55e; font-size:1.3rem;">●</span>'
    if status == "active":
        return '<span style="color:#ff5a1f; font-size:1.3rem;">◉</span>'
    return '<span style="color:#333; font-size:1.3rem;">○</span>'


def _format_time(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    return f"{m}:{s:02d}"


def render_progress(tracker: ProgressTracker) -> None:
    """Render the pipeline progress panel."""

    st.markdown(
        f"""
        <div style="text-align:center; margin:1rem 0 0.5rem;">
            <span style="font-size:1.6rem; font-weight:700; color:#f5f1eb;">
                分析进行中
            </span>
            <span style="font-size:1.1rem; color:#888; margin-left:0.8rem;">
                {tracker.ticker}
            </span>
        </div>
        """,
        unsafe_allow_html=True,
    )

    if tracker.stop_requested:
        st.caption("正在停止当前分析并清空内容；收尾完成后可重新开始。")
        return

    if tracker.is_paused:
        st.caption("当前分析已暂停。")

    completed = len(tracker.completed_stages)
    total = len(PIPELINE_STAGES)
    pct = completed / total if total else 0
    st.progress(pct, text=f"{completed}/{total} 阶段完成  ·  {_format_time(tracker.elapsed)}")

    if tracker.quant_skip_hint:
        st.info(tracker.quant_skip_hint)

    analyst_stages = PIPELINE_STAGES[:7]
    post_stages = PIPELINE_STAGES[7:]

    st.markdown(
        '<div style="margin:0.5rem 0 0.3rem; font-size:0.85rem; color:#888;">ANALYSTS</div>',
        unsafe_allow_html=True,
    )

    cols = st.columns(len(analyst_stages))
    for col, stage in zip(cols, analyst_stages):
        status = tracker.stage_status(stage["id"])
        badge = _status_badge(status)
        label_color = "#f5f1eb" if status == "active" else "#888" if status == "pending" else "#22c55e"
        col.markdown(
            f"""
            <div style="text-align:center; padding:0.5rem 0;">
                {badge}<br>
                <span style="font-size:0.75rem; color:{label_color};">{stage['name']}</span>
            </div>
            """,
            unsafe_allow_html=True,
        )

    st.markdown(
        '<div style="margin:0.8rem 0 0.3rem; font-size:0.85rem; color:#888;">PIPELINE</div>',
        unsafe_allow_html=True,
    )

    cols2 = st.columns(len(post_stages))
    for col, stage in zip(cols2, post_stages):
        status = tracker.stage_status(stage["id"])
        badge = _status_badge(status)
        label_color = "#f5f1eb" if status == "active" else "#888" if status == "pending" else "#22c55e"
        col.markdown(
            f"""
            <div style="text-align:center; padding:0.5rem 0;">
                {badge}<br>
                <span style="font-size:0.75rem; color:{label_color};">{stage['name']}</span>
            </div>
            """,
            unsafe_allow_html=True,
        )

    st.markdown("---")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("LLM 调用", tracker.llm_calls)
    c2.metric("工具调用", tracker.tool_calls)
    c3.metric("输入 Tokens", f"{tracker.tokens_in:,}")
    c4.metric("输出 Tokens", f"{tracker.tokens_out:,}")

    if tracker.error:
        st.error(f"错误: {tracker.error}")

    completed_reports = [
        (stage["name"], stage["icon"], tracker.stage_reports[stage["id"]])
        for stage in PIPELINE_STAGES
        if stage["id"] in tracker.stage_reports
    ]

    if completed_reports:
        st.markdown(
            '<div style="margin:0.5rem 0 0.3rem; font-size:0.85rem; color:#888;">'
            f"REPORTS ({len(completed_reports)})</div>",
            unsafe_allow_html=True,
        )
        for name, icon, report in reversed(completed_reports):
            is_latest = (name == completed_reports[-1][0])
            with st.expander(f"{icon} {name}", expanded=is_latest):
                st.markdown(report[:3000])


def render_quant_progress(tracker: QuantProgressTracker) -> None:
    """Render the quant pick progress panel (independent of PIPELINE_STAGES).

    Shows:
      - (optional) data increment phase: progress bar of incremental fetch
      - Overall progress bar (completed / total strategies)
      - Latest strategy name + per-strategy stats (tier, comp, hits, elapsed)
      - Error count + elapsed time
      - Final Top N table once pick() returns
    """
    st.markdown(
        f"""
        <div style="text-align:center; margin:1rem 0 0.5rem;">
            <span style="font-size:1.6rem; font-weight:700; color:#f5f1eb;">
                量化选股进行中
            </span>
            <span style="font-size:1.1rem; color:#888; margin-left:0.8rem;">
                {tracker.trade_date}
            </span>
        </div>
        """,
        unsafe_allow_html=True,
    )

    if tracker.stop_requested:
        st.caption("正在停止当前选股并清空内容；收尾完成后可重新开始。")
        return

    # === 数据增量更新阶段 ===
    if tracker.data_update_active:
        st.markdown(
            '<div style="margin:0.5rem 0 0.3rem; font-size:0.85rem; color:#888;">'
            '📥 数据增量更新</div>',
            unsafe_allow_html=True,
        )
        last_date = tracker.data_update_last_date
        last_str = last_date.strftime("%Y-%m-%d") if last_date is not None else "?"
        st.caption(
            f"缓存落后 {tracker.data_update_days_behind} 天 "
            f"(最新: {last_str}) · 增量拉取 sina K-line"
        )
        total = tracker.data_update_total or 1
        completed = tracker.data_update_completed
        pct = min(1.0, completed / total)
        st.progress(
            pct,
            text=f"{completed}/{total} 只 · 失败 {tracker.data_update_failed} · "
                 f"当前: {tracker.data_update_latest_code}",
        )
        # 数据更新阶段不显示策略进度,直接返回
        return

    # === 策略执行阶段 ===
    completed = tracker.completed_strategies
    total = tracker.total_strategies
    pct = tracker.strategy_progress_pct
    if tracker.is_complete:
        st.progress(1.0, text=f"完成 {total} 策略  ·  {_format_time(tracker.pick_elapsed)}")
    else:
        st.progress(
            pct,
            text=f"{completed}/{total} 策略  ·  {_format_time(tracker.elapsed)}",
        )

    # Worker 初始化阶段提示(completed=0 表示还没有策略完成,worker 在预热特征缓存)
    if completed == 0 and not tracker.is_complete:
        st.info(
            "⏳ 正在启动 worker 进程 + 预热特征缓存(预计 60-90s)...\n\n"
            "各 worker 加载日线数据并计算日线/周线/月线特征。"
            "第一个策略完成后进度条开始移动。"
        )
    elif tracker.latest_strategy and not tracker.is_complete:
        st.caption(f"最新完成: {tracker.latest_strategy}")

    # 已完成策略列表(按完成顺序,最多显示 12 个)
    if tracker.per_strategy_stats and not tracker.is_complete:
        with st.expander(f"已完成策略详情({completed}/{total})", expanded=False):
            stats_items = list(tracker.per_strategy_stats.items())
            for name, s in reversed(stats_items[-12:]):
                tier = s.get("tier", "?")
                hits = s.get("n_hits", 0)
                elapsed = s.get("elapsed", 0.0)
                err = s.get("error")
                status = "ERR" if err else f"hits={hits}"
                st.markdown(
                    f"- `[{tier}]` **{name}** — {status} · {elapsed:.1f}s"
                    + (f"  ⚠️ {err[:80]}" if err else "")
                )

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("完成策略", completed)
    c2.metric("总策略数", total)
    c3.metric("错误数", tracker.n_strategies_error)
    c4.metric("耗时", _format_time(tracker.pick_elapsed or tracker.elapsed))

    if tracker.error:
        st.error(f"错误: {tracker.error}")

    if tracker.is_complete and tracker.top_picks is not None:
        st.markdown(
            f'<div style="margin:0.5rem 0 0.3rem; font-size:0.85rem; color:#888;">'
            f'TOP PICKS ({len(tracker.top_picks)})</div>',
            unsafe_allow_html=True,
        )
        st.dataframe(tracker.top_picks, use_container_width=True, hide_index=True)
