"""持仓跟踪组件:已买入计划的收益、状态与卖出提示。"""
from __future__ import annotations

import pandas as pd
import streamlit as st

from web.position_store import get_latest_price, list_plans, trading_holding_days
from web.theme import esc

_SELL_REASON = {
    "manual": "手动卖出",
    "stop_loss": "止损卖出",
    "take_profit": "止盈卖出",
    "expire": "到期卖出",
}

def _return_pct(buy_price: float | None, latest: float | None) -> float | None:
    if not buy_price or not latest:
        return None
    return (latest - float(buy_price)) / float(buy_price)

def _status(plan: dict, latest: float | None, days: int) -> tuple[str, str]:
    if plan.get("status") == "closed":
        return "⚪ 已卖出", _SELL_REASON.get(plan.get("sell", {}).get("reason", ""), "已卖出")
    if plan.get("status") != "filled":
        return "🟡 计划中", "尚未买入"
    risk = plan.get("risk_rules", {})
    ret = _return_pct(float(plan.get("buy", {}).get("price", 0) or 0), latest)
    if ret is not None and ret <= float(risk.get("stop_loss_pct", -0.05)):
        return "🔴 止损预警", "建议按纪律止损"
    if ret is not None and ret >= float(risk.get("take_profit_pct", 0.08)):
        return "🟠 止盈预警", "可考虑分批止盈"
    expire = plan.get("risk_rules", {}).get("expire_days")
    if expire is not None and not plan.get("quant", {}).get("signal_exit"):
        if days >= int(float(expire)):
            return "🟡 建议到期", "持有天数已达计划期限"
    return "🟢 持有中", "按计划持有"

@st.fragment
def render_position_tracker() -> None:
    show_closed = st.toggle("显示已卖出持仓", key="track_show_closed")
    plans = [
        p for p in list_plans()
        if p.get("status") == "filled" or (show_closed and p.get("status") == "closed")
    ]
    if not plans:
        if show_closed:
            st.info("暂无持仓记录。")
        else:
            st.info("暂无持仓。先在「量化选股」Tab 点击「计划买入」,再在「买入计划」Tab 确认成功买入。")
        return

    viewing = st.session_state.get("tracking_plan_id")
    for plan in plans[:30]:
        buy = plan.get("buy", {})
        ticker = str(plan.get("ticker", ""))
        name = str(plan.get("name", "") or "--")
        buy_date = str(buy.get("date", ""))
        buy_price = float(buy.get("price", 0) or 0)
        latest = get_latest_price(ticker)
        days = trading_holding_days(buy_date)
        ret = _return_pct(buy_price, latest)
        status, note = _status(plan, latest, days)

        st.markdown(f"### {esc(name)} ({esc(ticker)}) · {esc(status)}")
        st.caption(note)
        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("买入日", buy_date or "--")
        c2.metric("买入价", "--" if not buy_price else f"{buy_price:.2f}")
        c3.metric("最新价", "--" if latest is None else f"{latest:.2f}")
        c4.metric("盈亏", "--" if ret is None else f"{ret * 100:+.1f}%")
        c5.metric("持有天数", f"{days} 交易日")

        today = pd.Timestamp.today().normalize()
        if buy_date and pd.Timestamp(buy_date).normalize() == today:
            st.warning("⚠️ A 股 T+1:今日买入的股票今天不能卖出,最早下一个交易日可卖。")
        if ret is not None:
            if ret <= -0.05:
                st.error("触发 -5% 止损线,建议卖出或严格止损。")
            elif ret >= 0.08:
                st.success("达到 +8% 止盈线,可考虑分批止盈。")
        if st.button(f"查看 / 卖出 {ticker}", key=f"track_view_{plan['plan_id']}"):
            st.session_state["tracking_plan_id"] = plan["plan_id"]
            st.rerun()
        if viewing == plan["plan_id"]:
            st.markdown("---")
            from web.components.buy_plan import render_buy_plan_detail
            render_buy_plan_detail(plan["plan_id"], key_prefix="track")
            st.markdown("---")
