"""Render the completed analysis report with expandable sections and PDF download."""

from __future__ import annotations

import json
import re
from typing import Any

import streamlit as st

from web.pdf_export import generate_markdown, generate_pdf
from web.stock_display import (
    normalize_stock_mentions,
    signal_style,
    stock_display_label,
    strip_think_tags,
)
from web.theme import BORDER_LIGHT, MUTED, SURFACE_2, TEXT, esc, icon_heading

_ANALYST_SECTIONS = [
    ("market_report", "技术分析"),
    ("sentiment_report", "市场情绪"),
    ("news_report", "新闻舆情"),
    ("fundamentals_report", "基本面"),
    ("policy_report", "政策分析"),
    ("hot_money_report", "游资追踪"),
    ("lockup_report", "解禁/减持"),
]


def _safe_filename_label(label: str) -> str:
    cleaned = re.sub(r'[\\/:*?"<>|\s]+', "_", label).strip("_")
    return cleaned or "report"


def _display_report_text(text: Any, ticker: str, final_state: dict[str, Any]) -> str:
    cleaned = strip_think_tags(str(text))
    return normalize_stock_mentions(cleaned, ticker, final_state)


@st.cache_data(ttl=600, show_spinner=False)
def _generate_pdf_bytes(
    state_json: str,
    ticker: str,
    trade_date: str,
    signal: str,
) -> bytes:
    """Cache PDF generation: reports can be 10+ pages and every tab switch
    re-renders the whole page. JSON string is the cache key (dict is unhashable).
    """
    return generate_pdf(json.loads(state_json), ticker, trade_date, signal)


def render_report(
    final_state: dict[str, Any],
    ticker: str,
    trade_date: str,
    signal: str,
    elapsed: float | None = None,
) -> None:
    """Render the full analysis report."""

    color, _ = signal_style(signal)
    ticker_label = stock_display_label(ticker, final_state)

    stats_html = ""
    if elapsed is not None:
        m, s = divmod(int(elapsed), 60)
        stats_html = f'<div style="font-size:0.9rem; color:{MUTED}; margin-top:0.3rem;">耗时 {m}:{s:02d}</div>'

    conviction = final_state.get("conviction_score")
    conviction_html = (
        f'<div style="font-size:1.4rem; font-weight:800; color:{color}; margin-top:0.4rem;">'
        f'置信 {conviction}/100</div>'
        if isinstance(conviction, (int, float))
        else ""
    )

    card_html = f"""<style>body{{margin:0;padding:0;background:transparent;}}</style>
    <div style="
            background: radial-gradient(800px 240px at 50% -20%, rgba({', '.join(str(int(color[i:i+2], 16)) for i in (1, 3, 5))}, 0.28),
                transparent 60%), {SURFACE_2};
            border: 1px solid {BORDER_LIGHT};
            border-radius: 18px;
            padding: 1.9rem 1.5rem 1.7rem;
            text-align: center;
            margin: 0.6rem 0 1.4rem;
        ">
            <div style="font-size:0.8rem; color:{MUTED}; letter-spacing:2.5px;">TRADING SIGNAL</div>
            <div style="font-size:3.2rem; font-weight:900; color:{color}; margin:0.35rem 0; line-height:1.1;">
                {esc(signal)}
            </div>
            <div style="font-size:1.15rem; color:{TEXT};">
                {esc(ticker_label)} · {esc(trade_date)}
            </div>
            {conviction_html}
            {stats_html}
        </div>"""
    st.html(card_html)

    st.caption("本报告由 AI 自动生成，仅供学习研究，不构成投资建议。")

    # Markdown export always works (no font dependency); PDF is generated
    # lazily and guarded so a PDF/font failure never crashes the results page.
    col_md, col_pdf = st.columns([1, 1])
    with col_md:
        md_text = generate_markdown(final_state, ticker, trade_date, signal)
        st.download_button(
            "下载 Markdown",
            data=md_text.encode("utf-8"),
            file_name=f"Aquant_{_safe_filename_label(ticker_label)}_{trade_date}.md",
            mime="text/markdown",
            use_container_width=True,
        )
    with col_pdf:
        try:
            pdf_bytes = _generate_pdf_bytes(
                json.dumps(final_state, ensure_ascii=False, sort_keys=True, default=str),
                ticker,
                trade_date,
                signal,
            )
            st.download_button(
                "下载 PDF",
                data=pdf_bytes,
                file_name=f"Aquant_{_safe_filename_label(ticker_label)}_{trade_date}.pdf",
                mime="application/pdf",
                use_container_width=True,
            )
        except Exception as exc:  # noqa: BLE001 — never let PDF crash the page
            st.button(
                "PDF 不可用",
                disabled=True,
                use_container_width=True,
                help=f"PDF 生成失败，请改用 Markdown 导出。原因：{exc}",
            )

    st.markdown("---")

    quant_ctx = final_state.get("quant_pick_context", "")
    if quant_ctx:
        with st.expander("量化选股上下文", expanded=False):
            st.markdown(quant_ctx)

    inv_plan = final_state.get("investment_plan", "")
    if inv_plan:
        st.markdown(icon_heading("briefcase", "最终投资建议"), unsafe_allow_html=True)
        st.markdown(_display_report_text(inv_plan, ticker, final_state))
        st.markdown("---")

    st.markdown(icon_heading("users", "分析师报告"), unsafe_allow_html=True)

    for key, title in _ANALYST_SECTIONS:
        content = final_state.get(key, "")
        if not content:
            continue
        with st.expander(title, expanded=False):
            st.markdown(_display_report_text(content, ticker, final_state))

    debate = final_state.get("investment_debate_state")
    if debate and isinstance(debate, dict):
        st.markdown(icon_heading("scale", "多空辩论"), unsafe_allow_html=True)
        tab_bull, tab_bear, tab_judge = st.tabs(["多方", "空方", "研究经理"])
        with tab_bull:
            st.markdown(_display_report_text(debate.get("bull_history", "") or "无数据", ticker, final_state))
        with tab_bear:
            st.markdown(_display_report_text(debate.get("bear_history", "") or "无数据", ticker, final_state))
        with tab_judge:
            st.markdown(_display_report_text(debate.get("judge_decision", "") or "无数据", ticker, final_state))

    trader_decision = final_state.get("trader_investment_decision", "")
    if trader_decision:
        with st.expander("交易员决策", expanded=False):
            st.markdown(_display_report_text(trader_decision, ticker, final_state))

    risk = final_state.get("risk_debate_state")
    if risk and isinstance(risk, dict):
        st.markdown(icon_heading("shield", "风控评估"), unsafe_allow_html=True)
        tab_agg, tab_con, tab_neu, tab_rj = st.tabs(["激进", "保守", "中性", "风控决策"])
        with tab_agg:
            st.markdown(_display_report_text(risk.get("aggressive_history", "") or "无数据", ticker, final_state))
        with tab_con:
            st.markdown(_display_report_text(risk.get("conservative_history", "") or "无数据", ticker, final_state))
        with tab_neu:
            st.markdown(_display_report_text(risk.get("neutral_history", "") or "无数据", ticker, final_state))
        with tab_rj:
            st.markdown(_display_report_text(risk.get("judge_decision", "") or "无数据", ticker, final_state))

    dqs = final_state.get("data_quality_summary", "")
    if dqs:
        with st.expander("数据质量", expanded=False):
            st.markdown(_display_report_text(dqs, ticker, final_state))
