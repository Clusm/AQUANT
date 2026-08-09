"""Quant picker result rendering component (Top N table + selection)."""

from __future__ import annotations

from typing import Any

import pandas as pd
import streamlit as st


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

    # 历史数据可能缺指标列,缺失时用 0 填充而不是硬崩
    for col, out in (("avg_win_rate", "win_rate_pct"), ("avg_holding_days", "holding_d")):
        s = df[col] if col in df.columns else pd.Series(0.0, index=df.index)
        df[out] = (s * 100).round(1).astype(str) + "%" if col == "avg_win_rate" else s.round(1).astype(str) + "d"
    return df


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

    def _on_select_all() -> None:
        """When 全选 toggles, sync every individual checkbox session_state key."""
        new_val = st.session_state[f"{key_prefix}_select_all"]
        st.session_state.update(_select_all_updates(codes, new_val, key_prefix))

    selected: list[str] = []

    st.markdown(
        f'<div style="margin:0.3rem 0; font-size:0.85rem; color:#888;">'
        f'Top {len(df)} 候选 · 勾选标的进入 AI 深度分析</div>',
        unsafe_allow_html=True,
    )

    header = st.columns([0.5, 1.0, 1.6, 0.8, 1.0, 1.0, 0.8])
    header[0].checkbox(
        "全选",
        key=f"{key_prefix}_select_all",
        on_change=_on_select_all,
        help="勾选/取消全部候选",
    )
    header[1].markdown("**代码**")
    header[2].markdown("**名称**")
    header[3].markdown("**命中数**")
    header[4].markdown("**加权分**")
    header[5].markdown("**胜率**")
    header[6].markdown("**持仓天**")

    for _, row in df.iterrows():
        cols = st.columns([0.5, 1.0, 1.6, 0.8, 1.0, 1.0, 0.8])
        code = str(row["stock_code"])
        sel_key = f"{key_prefix}_sel_{code}"
        checked = cols[0].checkbox(
            "选",
            value=st.session_state.get(sel_key, False),
            key=sel_key,
            label_visibility="collapsed",
        )
        cols[1].markdown(f"`{code}`")
        cols[2].markdown(str(row["name"]))
        cols[3].markdown(str(int(row["n_strategies"])))
        cols[4].markdown(f"**{row['weighted_score']:.2f}**")
        cols[5].markdown(row["win_rate_pct"])
        cols[6].markdown(row["holding_d"])
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
