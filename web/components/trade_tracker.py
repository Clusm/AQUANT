"""交易记录与策略跟踪组件:实盘平仓收益 + 策略实际表现。"""
from __future__ import annotations

from collections import defaultdict

import pandas as pd
import streamlit as st

from web.position_store import get_latest_price, list_plans, trading_holding_days


def _closed_trade_rows(plans: list[dict]) -> list[dict]:
    rows = []
    for plan in plans:
        if plan.get("status") != "closed":
            continue
        buy = plan.get("buy", {})
        sell = plan.get("sell", {})
        buy_price = float(buy.get("price", 0) or 0)
        sell_price = float(sell.get("price", 0) or 0)
        if buy_price <= 0 or sell_price <= 0:
            continue
        pnl = (sell_price - buy_price) / buy_price
        strategies = [str(s.get("strategy", "")) for s in plan.get("strategies", [])]
        sell_date = str(sell.get("date", ""))
        asof = None
        try:
            asof = pd.Timestamp(sell_date).date()
        except (TypeError, ValueError):
            asof = None
        rows.append({
            "代码": plan.get("ticker", ""),
            "名称": plan.get("name", "--"),
            "买入日": buy.get("date", "--"),
            "买入价": buy_price,
            "卖出日": sell_date,
            "卖出价": sell_price,
            "收益": pnl,
            "持有交易日": trading_holding_days(str(buy.get("date", "")), asof=asof),
            "卖出原因": str(sell.get("reason", "manual")),
            "命中策略": " / ".join(strategies[:3]) or "--",
        })
    rows.sort(key=lambda r: r["卖出日"], reverse=True)
    return rows


def _strategy_stats(closed_plans: list[dict]) -> pd.DataFrame:
    stats: dict[str, dict] = defaultdict(
        lambda: {"returns": [], "expected_win": []}
    )
    for plan in closed_plans:
        buy_price = float(plan.get("buy", {}).get("price", 0) or 0)
        sell_price = float(plan.get("sell", {}).get("price", 0) or 0)
        if buy_price <= 0 or sell_price <= 0:
            continue
        pnl = (sell_price - buy_price) / buy_price
        for strat in plan.get("strategies", []):
            name = str(strat.get("strategy", ""))
            if not name:
                continue
            item = stats[name]
            item["returns"].append(pnl)
            win_rate = float(strat.get("win_rate", 0.0) or 0.0)
            if win_rate > 0:
                item["expected_win"].append(win_rate)

    out = []
    for name, item in stats.items():
        returns = item["returns"]
        wins = sum(1 for r in returns if r > 0)
        expected = (
            sum(item["expected_win"]) / len(item["expected_win"])
            if item["expected_win"]
            else None
        )
        out.append({
            "策略": name,
            "实盘次数": len(returns),
            "盈利次数": wins,
            "实盘胜率": wins / len(returns) if returns else None,
            "平均收益": sum(returns) / len(returns) if returns else None,
            "回测胜率": expected,
        })
    out.sort(key=lambda r: -(r["实盘胜率"] or 0))
    return pd.DataFrame(out)


def render_trade_tracker() -> None:
    plans = list_plans()
    closed_plans = [p for p in plans if p.get("status") == "closed"]
    rows = _closed_trade_rows(closed_plans)
    open_plans = [p for p in plans if p.get("status") == "filled"]

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("已平仓交易", len(rows))
    wins = sum(1 for r in rows if r["收益"] > 0)
    c2.metric("实盘胜率", "--" if not rows else f"{wins / len(rows) * 100:.1f}%")
    avg = sum(r["收益"] for r in rows) / len(rows) if rows else None
    c3.metric("平均收益", "--" if avg is None else f"{avg * 100:+.2f}%")
    total = sum(r["收益"] for r in rows) if rows else None
    c4.metric("累计收益", "--" if total is None else f"{total * 100:+.2f}%")
    c5.metric("当前持仓", len(open_plans))

    st.markdown("### 实盘交易记录")
    if rows:
        display = pd.DataFrame(rows)
        display["收益"] = display["收益"].map(lambda v: f"{v * 100:+.2f}%")
        display["买入价"] = display["买入价"].map(lambda v: f"{v:.2f}")
        display["卖出价"] = display["卖出价"].map(lambda v: f"{v:.2f}")
        st.dataframe(display, use_container_width=True, hide_index=True)
    else:
        st.info("暂无已平仓交易。在「买入计划」确认买入,再在「持仓跟踪」确认卖出后会自动进入本页。")

    st.markdown("### 策略实际表现跟踪")
    if not rows:
        st.caption("至少完成一笔卖出后,这里会按命中策略归集实盘收益、实盘胜率,并与回测胜率对比。")
    else:
        df = _strategy_stats(closed_plans)
        if df.empty:
            st.caption("已完成交易没有关联策略,无法跟踪。")
        else:
            display = df.copy()
            for col, fmt in (
                ("实盘胜率", "{:.1%}"),
                ("平均收益", "{:+.2%}"),
                ("回测胜率", "{:.1%}"),
            ):
                if col in display.columns:
                    display[col] = display[col].map(
                        lambda v, f=fmt: "--" if v is None or pd.isna(v) else f.format(v)
                    )
            st.dataframe(display, use_container_width=True, hide_index=True)
            st.caption("归因说明:同一只股票命中多个策略时,该笔收益会同时计入每个命中策略,用于观察策略组合表现。")

    if open_plans:
        st.markdown("### 持仓中浮动收益")
        open_rows = []
        for plan in open_plans:
            buy = plan.get("buy", {})
            buy_price = float(buy.get("price", 0) or 0)
            latest = get_latest_price(str(plan.get("ticker", "")))
            if buy_price <= 0:
                continue
            pnl = (latest - buy_price) / buy_price if latest else None
            open_rows.append({
                "代码": plan.get("ticker", ""),
                "名称": plan.get("name", "--"),
                "买入日": buy.get("date", "--"),
                "买入价": buy_price,
                "最新价": latest,
                "浮动收益": pnl,
                "持有交易日": trading_holding_days(str(buy.get("date", ""))),
            })
        if open_rows:
            odf = pd.DataFrame(open_rows)
            odf["买入价"] = odf["买入价"].map(lambda v: f"{v:.2f}")
            odf["最新价"] = odf["最新价"].map(lambda v: "--" if pd.isna(v) else f"{v:.2f}")
            odf["浮动收益"] = odf["浮动收益"].map(lambda v: "--" if v is None or pd.isna(v) else f"{v * 100:+.2f}%")
            st.dataframe(odf, use_container_width=True, hide_index=True)
