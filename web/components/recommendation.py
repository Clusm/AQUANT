"""Final recommendation rendering component (4-column label layout)."""

from __future__ import annotations

import re
from typing import Any

import streamlit as st

from tradingagents.dataflows.a_stock import get_stock_name
from web.theme import MUTED, SIGNAL

# 6 label combinations -> 4 buckets
RECOMMENDATION_LABELS = {
    "strong_buy": "🟢 强买",
    "watch": "🟡 关注",
    "conflict": "🟠 冲突",
    "discard": "🔴 弃",
}

# Label prefix to bucket id (matches conflict_resolver._detect_llm_rating output)
_LABEL_PREFIX_TO_BUCKET = {
    "🟢": "strong_buy",
    "🟡": "watch",
    "🟠": "conflict",
    "🔴": "discard",
}


def _parse_label_line(label_line: str) -> str:
    """Classify a single label line (e.g. "🟢 强买" / "强买" / "Buy")."""
    line = label_line.strip()
    for emoji, bucket in _LABEL_PREFIX_TO_BUCKET.items():
        if line.startswith(emoji):
            return bucket
    lowered = line.lower()
    if "strong" in lowered or "强买" in line:
        return "strong_buy"
    if "watch" in lowered or "关注" in line:
        return "watch"
    if "conflict" in lowered or "冲突" in line:
        return "conflict"
    # Legacy path: Conflict Resolver's final_signal_label may be missing and
    # the fallback is the raw 5-tier LLM rating (Buy/Overweight/...).
    if lowered in ("buy", "overweight"):
        return "strong_buy"
    if lowered == "hold":
        return "watch"
    if lowered in ("sell", "underweight"):
        return "discard"
    return "discard"


def parse_label(label_text: str) -> str:
    """Parse a label string into a bucket id.

    Accepts:
      - Full label like "🟢强买 300750" or "🟡关注 600519"
      - Bare emoji like "🟢" or "🔴"
      - Already-bucketed like "strong_buy"
      - Full final_ranked_decision markdown (extracts the "**标签**: X" line)
      - Legacy raw 5-tier LLM rating ("Buy" / "Hold" / ...)

    Returns one of: strong_buy, watch, conflict, discard. Falls back to
    "discard" if no recognized label is found (safer bucket - user must
    explicitly promote a stock out of it).
    """
    if not label_text:
        return "discard"
    text = str(label_text).strip()

    if text in RECOMMENDATION_LABELS:
        return text

    # Conflict Resolver renders "**标签**: 🟢 强买" in final_ranked_decision.
    # Extract that line so keyword scanning only sees the real label, not the
    # LLM preview / rationale (which can contain "关注"/"强买" etc.).
    m = re.search(r"标签[^\n:：]*[:：]\s*([^\n]+)", text)
    if m:
        return _parse_label_line(m.group(1))

    return _parse_label_line(text)


def render_recommendation(recommendations: dict[str, dict[str, Any]]) -> None:
    """Render the 4-column 🟢🟡🟠🔴 recommendation panel.

    Args:
        recommendations: {ticker: {"label": str, "final_ranked_decision": str,
                                    "final_trade_decision": str, "signal": str, ...}}
                         The label field is parsed by parse_label() to determine
                         which column to put the ticker in.

    Layout: 4 st.columns, each showing a colored header + the list of tickers
    in that bucket. Clicking a ticker (expander) shows the full ranked decision
    text.
    """
    buckets: dict[str, list[tuple[str, dict[str, Any]]]] = {
        "strong_buy": [],
        "watch": [],
        "conflict": [],
        "discard": [],
    }

    for ticker, info in recommendations.items():
        bucket = parse_label(info.get("label", ""))
        buckets[bucket].append((ticker, info))

    # 档内按置信分降序排(None 最后),高分优先可见
    for b in buckets:
        buckets[b].sort(key=lambda x: (x[1].get("conviction_score") is None,
                                       -(x[1].get("conviction_score") or 0)))

    cols = st.columns(4)

    column_meta = [
        ("strong_buy", "🟢 强买", SIGNAL["strong_buy"]),
        ("watch", "🟡 关注", SIGNAL["watch"]),
        ("conflict", "🟠 冲突", SIGNAL["conflict"]),
        ("discard", "🔴 弃", SIGNAL["discard"]),
    ]

    for col, (bucket_id, label, color) in zip(cols, column_meta, strict=True):
        items = buckets[bucket_id]
        count = len(items)
        col.markdown(
            f"""
            <div style="text-align:center; padding:0.6rem; border:2px solid {color};
                        border-radius:0.5rem; margin-bottom:0.5rem; background:rgba(0,0,0,0.2);">
                <div style="font-size:1.1rem; font-weight:700; color:{color};">{label}</div>
                <div style="font-size:0.85rem; color:{MUTED}; margin-top:0.2rem;">{count} 只</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        if not items:
            col.caption("（无）")
            continue
        for ticker, info in items:
            name = get_stock_name(ticker)
            score = info.get("conviction_score")
            score_tag = f" · 置信 {score}" if isinstance(score, (int, float)) else ""
            base_title = f"{name}（{ticker}）" if name else f"`{ticker}`"
            with col.expander(base_title + score_tag, expanded=False):
                final_ranked = info.get("final_ranked_decision", "")
                final_decision = info.get("final_trade_decision", "")
                signal = info.get("signal", "")
                if signal:
                    st.caption(f"信号: {signal}")
                if isinstance(score, (int, float)):
                    st.caption(f"置信分: {score}/100")
                # 完整显示,不再截断(外层 expander 默认收起,长文不撑爆 UI)
                if final_decision:
                    st.markdown("**最终决策:**")
                    st.markdown(final_decision)
                if final_ranked:
                    st.markdown("**分级标签:**")
                    st.markdown(final_ranked)
