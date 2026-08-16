"""Quant picker result rendering component (Top N table + selection)."""

from __future__ import annotations

from typing import Any

import pandas as pd
import streamlit as st

from web.position_store import create_buy_plan, list_plans
from web.theme import MUTED, TEXT, TIER, chip, esc, mono


def _prepare_display_df(
    picks_df: pd.DataFrame,
    name_map: dict[str, str] | None = None,
) -> pd.DataFrame:
    """Transform pick()["top_picks"] into the display DataFrame.

    Adds: rank (1-based), name (from name_map or "--"), win_rate_pct ("61.0%"),
    holding_d ("8.3d"). Pure pandas, no Streamlit — unit-testable.
    """
    df = picks_df.reset_index(drop=True).copy()
    df.insert(0, "rank", range(1, len(df) + 1))

    if name_map:
        df["name"] = df["stock_code"].map(lambda c: name_map.get(c, "--"))
    else:
        df["name"] = "--"

    # 历史数据可能缺指标列或含 NaN(如老缓存无 win_rate),缺失时用 0 填充
    # 而不是显示 "nan%" / "nand" 破坏表格
    for col, out in (("avg_win_rate", "win_rate_pct"), ("avg_holding_days", "holding_d")):
        s = df[col].fillna(0.0) if col in df.columns else pd.Series(0.0, index=df.index)
        if col == "avg_win_rate":
            df[out] = (s * 100).round(1).astype(str) + "%"
        else:
            # 999 = 信号出场策略(无固定持仓天数)
            df[out] = s.apply(lambda v: "信号出场" if v >= 999 else f"{round(float(v), 1)}d")
    return df


def _tier_chips_html(row: pd.Series) -> str:
    """S/A/B/C 分级命中徽章;短线与中线计数合并显示。

    中线策略的 tier 是 M_S/M_A/M_B/M_C,旧实现只读 n_S/n_A/n_B/n_C,
    导致 top18 中大量中线命中时该列看起来为空。
    """
    parts = []
    for tier, color in TIER.items():
        short = int(row.get(f"n_{tier}", 0) or 0)
        mid = int(row.get(f"n_M_{tier}", 0) or 0)
        total = short + mid
        if total <= 0:
            continue
        title = f"{tier} 级命中 {total} 个(短线 {short} / 中线 {mid})"
        parts.append(chip(f"{tier}{total}", color, title=title))
    return "".join(parts) or '<span style="color:#333;">·</span>'


def _select_all_updates(codes: list[str], new_val: bool, key_prefix: str) -> dict[str, bool]:
    """Compute the session_state updates for 全选 toggling.

    Returns {f"{key_prefix}_sel_{code}": new_val, ...} for every code, mirroring
    the individual checkbox keys. Pure function, no Streamlit — unit-testable.
    """
    return {f"{key_prefix}_sel_{code}": new_val for code in codes}


def render_quant_picker(
    picks_df: pd.DataFrame | None,
    *,
    all_records: list[dict[str, Any]] | None = None,
    name_map: dict[str, str] | None = None,
    key_prefix: str = "quant_picker",
    trade_date: str | None = None,
) -> list[str]:
    """Render the Top N quant picker table with row-level selection checkboxes.

    Args:
        picks_df: DataFrame returned by pick()["top_picks"]. Columns include
                  stock_code, n_strategies, weighted_score, avg_win_rate,
                  avg_holding_days, n_S, n_A, n_B, n_C, n_M_S, n_M_A, n_M_B, n_M_C.
        all_records: optional list of per-strategy signal dicts (for the
                     "命中策略" detail column). If None, detail is hidden.
        name_map: optional {code: name} for showing 中文名 alongside code.
        key_prefix: Streamlit widget key prefix (avoid collision when this
                    component is rendered in multiple tabs).
        trade_date: optional signal trade date; when set, each row renders a
                    "计划买入" button that persists a next-day buy plan.

    Returns:
        list of selected stock codes (6-digit strings), preserving the
        on-screen order. Empty list if picks_df is None/empty or no rows
        selected.
    """
    if picks_df is None or len(picks_df) == 0:
        from tradingagents.quant.strategy.strategy_library_final import get_all_strategies_final
        _n = len(get_all_strategies_final())
        st.info(f"无候选股票({_n} 策略均未生成信号)。可调整日期或缓存后重试。")
        return []

    df = _prepare_display_df(picks_df, name_map)

    codes: list[str] = df["stock_code"].astype(str).tolist()

    plan_feedback = st.session_state.pop("plan_feedback", None)
    if plan_feedback:
        st.toast(plan_feedback)

    planned_by_key: dict[str, str] = {}
    if trade_date:
        for _plan in list_plans("planned"):
            if str(_plan.get("trade_date")) == str(trade_date):
                planned_by_key[str(_plan.get("ticker"))] = str(_plan.get("plan_id"))

    def _on_select_all() -> None:
        """When 全选 toggles, sync every individual checkbox session_state key."""
        new_val = st.session_state[f"{key_prefix}_select_all"]
        st.session_state.update(_select_all_updates(codes, new_val, key_prefix))

    selected: list[str] = []

    st.markdown(
        f'<div style="margin:0.3rem 0; font-size:0.85rem; color:{MUTED};">'
        f'Top {len(df)} 候选 · 勾选标的进入 AI 深度分析 · 点击「计划买入」创建次日买入计划</div>',
        unsafe_allow_html=True,
    )

    # 列:勾选 / 代码 / 名称 / 分级 / 命中数 / 加权分 / 胜率 / 持仓天 / 计划买入
    widths = [0.45, 0.9, 1.3, 1.5, 0.6, 0.85, 0.8, 0.7, 1.0]
    header = st.columns(widths)
    header[0].checkbox(
        "全选",
        key=f"{key_prefix}_select_all",
        on_change=_on_select_all,
        help="勾选/取消全部候选",
    )
    header[1].markdown(mono("代码", MUTED), unsafe_allow_html=True)
    header[2].markdown("**名称**")
    header[3].markdown(mono("分级 S/A/B/C", MUTED), unsafe_allow_html=True)
    header[4].markdown(mono("命中", MUTED), unsafe_allow_html=True)
    header[5].markdown(mono("加权分", MUTED), unsafe_allow_html=True)
    header[6].markdown(mono("胜率", MUTED), unsafe_allow_html=True)
    header[7].markdown(mono("持仓天", MUTED), unsafe_allow_html=True)
    header[8].markdown(mono("计划买入", MUTED), unsafe_allow_html=True)

    for _, row in df.iterrows():
        cols = st.columns(widths)
        code = str(row["stock_code"])
        sel_key = f"{key_prefix}_sel_{code}"
        checked = cols[0].checkbox(
            "选",
            value=st.session_state.get(sel_key, False),
            key=sel_key,
            label_visibility="collapsed",
        )
        cols[1].markdown(mono(code, TEXT), unsafe_allow_html=True)
        cols[2].markdown(esc(str(row["name"])), unsafe_allow_html=True)
        cols[3].markdown(_tier_chips_html(row), unsafe_allow_html=True)
        cols[4].markdown(mono(str(int(row["n_strategies"])), MUTED), unsafe_allow_html=True)
        cols[5].markdown(mono(f"{row['weighted_score']:.2f}", TEXT, bold=True), unsafe_allow_html=True)
        cols[6].markdown(mono(row["win_rate_pct"], TEXT), unsafe_allow_html=True)
        cols[7].markdown(mono(row["holding_d"], MUTED), unsafe_allow_html=True)

        plan_key = f"{key_prefix}_plan_{code}"
        if not trade_date:
            cols[8].button("—", key=plan_key, disabled=True, help="缺少信号日期,无法创建计划")
        elif code in planned_by_key:
            cols[8].button("✅ 已计划", key=plan_key, disabled=True, help="该股票已创建买入计划")
        else:
            def _add_plan(
                c: str = code,
                td: str = trade_date,
                nm: str = str(row["name"]),
                picks: pd.DataFrame = picks_df,
                recs: list[dict[str, Any]] = all_records or [],
            ) -> None:
                try:
                    created = create_buy_plan(c, td, picks, recs, nm)
                    st.session_state["plan_feedback"] = (
                        f"✅ {nm}({c}) 已加入买入计划,计划买入日 {created.get('plan_date', '--')}"
                    )
                    st.session_state["force_tab"] = "plan"
                except Exception as exc:
                    st.session_state["plan_feedback"] = f"❌ {nm}({c}) 创建买入计划失败: {exc}"

            cols[8].button("📋 计划买入", key=plan_key, on_click=_add_plan,
                           help=f"按 {trade_date} 信号创建买入计划")

        if checked:
            selected.append(code)

    if all_records:
        with st.expander("命中策略详情", expanded=False):
            by_code: dict[str, list[dict]] = {}
            for rec in all_records:
                by_code.setdefault(str(rec.get("stock_code")), []).append(rec)
            for _, row in df.iterrows():
                code = str(row["stock_code"])
                recs = by_code.get(code, [])
                if not recs:
                    continue
                recs_sorted = sorted(recs, key=lambda r: r.get("strategy_comp", 0), reverse=True)
                st.markdown(f"**#{int(row['rank'])} {code}** (加权分 {row['weighted_score']:.2f})")
                for r in recs_sorted[:5]:
                    st.markdown(
                        f"- [{r.get('tier', '?')}] {r.get('strategy', '?')} "
                        f"(comp={r.get('strategy_comp', 0):.2f}, "
                        f"胜率={r.get('win_rate', 0)*100:.0f}%, "
                        f"持仓={r.get('holding_days', '?')}d)"
                    )
                if len(recs_sorted) > 5:
                    st.caption(f"... 还有 {len(recs_sorted) - 5} 个策略")

    return selected
