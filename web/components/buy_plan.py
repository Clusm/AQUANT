"""买入计划详情与列表组件。"""
from __future__ import annotations

from datetime import date

import streamlit as st

from tradingagents.quant.strategy.optimization_records import get_optimization_record
from tradingagents.quant.strategy.strategy_library_final import get_all_strategies_final
from web.position_store import (
    _strategy_exit_policy,
    abandon_plan,
    close_position,
    confirm_buy,
    enrich_plan_llm,
    get_latest_price,
    get_limit_reference_prices,
    get_plan,
    list_plans,
)
from web.theme import MUTED, SIGNAL, esc

_STATUS_META = {
    "planned": ("🟡", "计划买入"),
    "filled": ("🟢", "持仓中"),
    "closed": ("⚪", "已卖出"),
    "abandoned": ("🔴", "已放弃"),
}

def _pct(v: float) -> str:
    return f"{v * 100:.1f}%"

def _price(v: float | None) -> str:
    return "--" if v is None else f"{v:.2f}"


def _date_or_today(value: str) -> date:
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        return date.today()


def _backtest_metrics(strategy: dict) -> dict:
    """Prefer the persisted optimization record; rebuild for legacy plans."""
    backtest = strategy.get("backtest")
    if isinstance(backtest, dict) and backtest:
        return backtest
    strategy_name = str(strategy.get("strategy", ""))
    info = get_all_strategies_final().get(strategy_name, {})
    perf = info.get("performance") or {}
    opt = get_optimization_record(strategy_name) or {}
    return {
        "oos_total_return": float(opt.get("oos_total_return", perf.get("total_return", 0.0)) or 0.0),
        "win_rate": float(opt.get("win_rate", perf.get("win_rate", 0.0)) or 0.0),
        "sharpe": float(opt.get("sharpe", perf.get("sharpe", 0.0)) or 0.0),
        "max_drawdown": float(opt.get("max_drawdown", perf.get("max_drawdown", 0.0)) or 0.0),
        "profit_factor": float(opt.get("profit_factor", perf.get("profit_factor", 0.0)) or 0.0),
        "n_sells": int(opt.get("n_sells", 0) or 0),
        "n_trades": int(opt.get("n_trades", 0) or 0),
        "avg_holding_days": float(opt.get("avg_holding_days", 0.0) or 0.0),
    }

def _signal_label_meta(label: str) -> tuple[str, str]:
    if "🟢" in label or "强买" in label:
        return SIGNAL["strong_buy"], label or "🟢 强买"
    if "🟡" in label or "关注" in label:
        return SIGNAL["watch"], label or "🟡 关注"
    if "🟠" in label or "冲突" in label:
        return SIGNAL["conflict"], label or "🟠 冲突"
    if "🔴" in label or "弃" in label:
        return SIGNAL["discard"], label or "🔴 弃"
    return MUTED, label or "暂无 LLM 分析"

def _plan_header(plan: dict) -> None:
    ticker = str(plan.get("ticker", ""))
    name = str(plan.get("name", "") or "--")
    status = plan.get("status", "planned")
    emoji, label = _STATUS_META.get(status, ("⚪", status))
    st.markdown(
        f"## {esc(name)} ({esc(ticker)}) · {emoji} {esc(label)}",
        unsafe_allow_html=True,
    )
    st.caption(
        f"信号日 {plan.get('trade_date', '')} · 计划买入日 {plan.get('plan_date', '')} · "
        f"创建于 {str(plan.get('created_at', ''))[:16]}"
    )

def render_buy_plan_detail(plan_id: str, *, key_prefix: str = "plan") -> None:
    plan = get_plan(plan_id)
    if not plan:
        st.info("计划不存在或已删除。")
        return
    plan = enrich_plan_llm(plan)
    _plan_header(plan)

    quant = plan.get("quant", {})
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("命中策略", quant.get("n_strategies", 0))
    c2.metric("加权分", f"{float(quant.get('weighted_score', 0.0)):.2f}")
    c3.metric("加权胜率", _pct(float(quant.get("avg_win_rate", 0.0))))
    hd = float(quant.get("avg_holding_days", 0.0))
    c4.metric("建议持仓", "信号出场" if hd >= 999 else f"{hd:.0f} 天")

    limits = get_limit_reference_prices(str(plan.get("ticker", "")), plan.get("trade_date"))
    default_buy_price = float(limits.get("close") or 10.0)
    if limits.get("limit_up") is not None:
        st.markdown("### 次日涨跌停参考价")
        st.caption(f"以 {limits.get('asof', '--')} 收盘价 {limits.get('close', '--')} 为基准")
        lc1, lc2, lc3 = st.columns(3)
        lc1.metric("涨停参考价", f"{limits['limit_up']:.2f}")
        lc2.metric("跌停参考价", f"{limits['limit_down']:.2f}")
        lc3.metric("涨跌幅限制", f"{float(limits.get('limit_pct', 0.0)) * 100:.0f}%")

    st.markdown("### 命中策略与出场规则")
    for strat in plan.get("strategies", []):
        policy = strat.get("exit_policy", {})
        if not isinstance(policy, dict) or not policy.get("exit_advice"):
            info = get_all_strategies_final().get(str(strat.get("strategy", "")), {})
            policy = _strategy_exit_policy(info, str(strat.get("strategy", "")))
        title = (
            f"- **[{esc(str(strat.get('tier', '?')))}] {esc(str(strat.get('strategy', '?')))}** "
            f"comp={float(strat.get('comp', 0.0)):.2f} · 胜率 {_pct(float(strat.get('win_rate', 0.0)))}"
        )
        st.markdown(title)
        st.caption(f"持仓:{policy.get('exit_days_text', '--')}")

        for _advice in policy.get("exit_advice", []):
            st.caption(f"• {_advice}")

        backtest = _backtest_metrics(strat)
        if backtest.get("oos_total_return"):
            st.caption(
                f"回测(OOS):累计收益 {backtest['oos_total_return'] * 100:+.1f}% · "
                f"最大回撤 {backtest['max_drawdown'] * 100:.1f}% · "
                f"Sharpe {backtest['sharpe']:.2f} · "
                f"盈亏比 {backtest['profit_factor']:.2f} · "
                f"卖出 {backtest['n_sells']} 笔 · "
                f"平均持仓 {backtest['avg_holding_days']:.1f} 天"
            )
        if policy.get("signal_enabled") and policy.get("signal_note"):
            st.caption(f"信号出场:{policy.get('signal_note')}")
        advice = str(strat.get("entry_advice", ""))
        if advice:
            # 旧记录曾把 OOS 累计收益误标为“均收”,展示前统一更正口径。
            advice = advice.replace("均收", "OOS累计收益")
            st.caption(f"入场建议:{advice}")

    st.markdown("### 通用风险规则")
    risk = plan.get("risk_rules", {})
    st.caption(
        f"止损 {risk.get('stop_loss_pct', -0.05) * 100:.0f}% / "
        f"止盈 {risk.get('take_profit_pct', 0.08) * 100:.0f}% / "
        f"到期 {risk.get('expire_days', '--')} 天"
    )

    llm = plan.get("llm", {})
    st.markdown("### LLM 分析")
    if llm.get("label") or llm.get("final_trade_decision"):
        color, label = _signal_label_meta(str(llm.get("label", "")))
        conviction = llm.get("conviction_score")
        conv = f" · 置信 {conviction}/100" if isinstance(conviction, (int, float)) else ""
        st.markdown(f"<span style='color:{color};font-weight:800;'>{esc(label)}</span>{conv}", unsafe_allow_html=True)
        if llm.get("final_trade_decision"):
            with st.expander("查看 LLM 最终决策", expanded=False):
                st.markdown(str(llm["final_trade_decision"]))
    else:
        st.info("该股票还没有 LLM 分析结果。可到「AI 深度分析」Tab 先运行分析。")

    if plan.get("status") == "planned":
        st.markdown("### 操作")
        ac1, ac2 = st.columns([1, 1])
        with ac1:
            with st.form(key=f"{key_prefix}_confirm_buy_{plan_id}"):
                buy_date = st.date_input(
                    "买入日期",
                    value=_date_or_today(str(plan.get("plan_date", ""))),
                    key=f"{key_prefix}_buy_date_{plan_id}",
                )
                buy_price = st.number_input(
                    "买入价格", min_value=0.01, value=default_buy_price, step=0.01,
                    key=f"{key_prefix}_buy_price_{plan_id}",
                )
                shares = st.number_input("买入数量(0 表示不记录)", min_value=0, value=0, step=100, key=f"{key_prefix}_shares_{plan_id}")
                if st.form_submit_button("确认买入", type="primary"):
                    confirm_buy(plan_id, buy_date.strftime("%Y-%m-%d"), float(buy_price), int(shares))
                    st.rerun()
        with ac2:
            if st.button("放弃计划", key=f"{key_prefix}_abandon_{plan_id}"):
                abandon_plan(plan_id)
                st.rerun()
    elif plan.get("status") == "filled":
        st.markdown("### 持仓")
        buy = plan.get("buy", {})
        latest = get_latest_price(str(plan.get("ticker", "")))
        st.caption(f"买入 {buy.get('date', '--')} @ {_price(float(buy.get('price', 0)) or None)}")
        st.caption(f"最新价 {_price(latest)}")
        with st.form(key=f"{key_prefix}_close_position_{plan_id}"):
            sell_date = st.date_input("卖出日期", value=date.today(), key=f"{key_prefix}_sell_date_{plan_id}")
            sell_price = st.number_input(
                "卖出价格", min_value=0.01, value=float(latest or 10.0), step=0.01,
                key=f"{key_prefix}_sell_price_{plan_id}",
            )
            reason = st.selectbox("卖出原因", ["manual", "stop_loss", "take_profit", "expire"], key=f"{key_prefix}_sell_reason_{plan_id}")
            if st.form_submit_button("确认卖出", type="primary"):
                close_position(plan_id, sell_date.strftime("%Y-%m-%d"), float(sell_price), reason)
                st.rerun()
    elif plan.get("status") == "closed":
        st.markdown("### 卖出记录")
        sell = plan.get("sell", {})
        buy_price = float(plan.get("buy", {}).get("price", 0) or 0)
        sell_price = float(sell.get("price", 0) or 0)
        reason = sell.get("reason", "manual")
        reason_text = {
            "manual": "手动卖出",
            "stop_loss": "止损卖出",
            "take_profit": "止盈卖出",
            "expire": "到期卖出",
        }.get(reason, reason)
        st.caption(
            f"卖出 {sell.get('date', '--')} @ {_price(sell_price)} · {reason_text}"
        )
        if buy_price > 0 and sell_price > 0:
            pnl = (sell_price - buy_price) / buy_price
            st.caption(f"已实现盈亏 {pnl * 100:+.1f}%")

@st.fragment
def render_buy_plans() -> None:
    plans = list_plans()
    if not plans:
        st.info("暂无买入计划。请在量化选股结果中点击「计划买入」。")
        return

    _FILTER_META = [
        ("全部", None),
        ("计划中", "planned"),
        ("持仓中", "filled"),
        ("已卖出", "closed"),
        ("已放弃", "abandoned"),
    ]
    filter_labels = [label for label, _ in _FILTER_META]
    status_label = st.radio(
        "状态筛选",
        filter_labels,
        horizontal=True,
        key="plan_status_filter",
        label_visibility="collapsed",
    )
    status_value = dict(_FILTER_META)[status_label]
    plans = [p for p in plans if status_value is None or p.get("status") == status_value]

    if not plans:
        st.caption("当前筛选条件下暂无计划。")
        return

    viewing = st.session_state.get("viewing_plan_id")
    for plan in plans[:30]:
        emoji, label = _STATUS_META.get(plan.get("status", ""), ("⚪", plan.get("status")))
        caption = (
            f"{emoji} {label} · {plan.get('ticker')} {plan.get('name')} · "
            f"信号日 {plan.get('trade_date')} → 计划买入日 {plan.get('plan_date')}"
        )
        if st.button(caption, key=f"view_plan_{plan['plan_id']}", use_container_width=True):
            st.session_state["viewing_plan_id"] = plan["plan_id"]
            st.rerun()
    if viewing:
        st.markdown("---")
        render_buy_plan_detail(viewing)
