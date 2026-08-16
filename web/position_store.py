"""买入计划与持仓跟踪的本地持久化数据层。

数据文件:
    ~/.tradingagents/positions/plans.json

计划状态机:
    planned -> filled -> closed
    planned -> abandoned
"""
from __future__ import annotations

import json
import threading
import time
import uuid
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.quant.strategy.optimization_records import get_optimization_record
from tradingagents.quant.strategy.strategy_library_final import get_all_strategies_final

_PLANS_FILE = Path.home() / ".tradingagents" / "positions" / "plans.json"
_LOCK = threading.Lock()

# Daily parquet is ~50MB; loading it once per rendered card makes the position
# tracker O(positions x parquet-reads). Cache the DataFrame for a short window.
_DAILY_DF_TTL = 60.0
_DAILY_DF_CACHE: dict[str, tuple[float, pd.DataFrame]] = {}
_DAILY_DF_LOCK = threading.Lock()

STOP_LOSS_PCT = -0.05
TAKE_PROFIT_PCT = 0.08

_SIGNAL_EXIT_NOTES: dict[str, str] = {
    "WeeklyMacdGoldenCrossStrategy": "周线多头破位,或月线收盘跌破 MMA3",
    "MonthlyWeeklyDailyResonanceStrategy": "周线多头破位,或月线收盘跌破 MMA3",
    "ContinuousStrongCloseStrategy": "月线跌破 MMA3,或周线跌破 WMA5",
    "LongConsolidationBreakoutV2Strategy": "月线跌破 MMA3,或周线跌破 WMA5",
    "BullAlignMa20BounceStrategy": "收盘跌破 MA20,或 MA5/10/20 多头排列破位",
    "MonthlyBreakoutStrategy": "月线跌破 MMA3,或周线跌破 WMA5",
    "VolumePriceTrendStrategy": "收盘跌破 MA20;量比5日均值<0.7;量比<0.5",
    "MonthlyRsiBreakoutStrategy": "月线 RSI<55,或 RSI 连续 2 月下降",
    "WeeklyBreakoutPullbackStrategy": "收盘跌破入场价×(1-12%),或持有3日后收盘跌破 MA5",
    "LeaderPullbackBounceStrategy": "收盘跌破 MA20,或 wave1 涨幅回吐至 25% 以下",
}


def _load() -> dict[str, dict[str, Any]]:
    if not _PLANS_FILE.exists():
        return {}
    try:
        data = json.loads(_PLANS_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        # Corrupt file: park it aside instead of letting the next save
        # silently overwrite the only copy.
        try:
            _PLANS_FILE.replace(
                _PLANS_FILE.with_name(f"plans.corrupt-{int(time.time())}.json")
            )
        except OSError:
            pass
        return {}
    except OSError:
        return {}


def _save(data: dict[str, dict[str, Any]]) -> None:
    _PLANS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = _PLANS_FILE.with_name(f".plans.{uuid.uuid4().hex}.tmp")
    try:
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(_PLANS_FILE)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


def _daily_df(cache_name: str) -> pd.DataFrame | None:
    """Load a quant daily cache with a 60s in-process TTL."""
    now = time.monotonic()
    with _DAILY_DF_LOCK:
        cached = _DAILY_DF_CACHE.get(cache_name)
        if cached is not None and now - cached[0] < _DAILY_DF_TTL:
            return cached[1]

    try:
        from tradingagents.quant.data import cache as cm

        if not cm.exists(cache_name):
            return None
        df = cm.load(cache_name)
    except Exception:
        return None

    with _DAILY_DF_LOCK:
        _DAILY_DF_CACHE[cache_name] = (now, df)
    return df


def _clear_daily_df_cache() -> None:
    """Test hook / future data-refresh hook."""
    with _DAILY_DF_LOCK:
        _DAILY_DF_CACHE.clear()


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if pd.isna(number):
        return default
    return number


def _safe_int(value: Any, default: int = 0) -> int:
    return int(_safe_float(value, float(default)))


def _next_trade_date(trade_date: str) -> str:
    try:
        from tradingagents.quant.utils.trading_calendar import next_trading_day

        return next_trading_day(pd.Timestamp(trade_date)).strftime("%Y-%m-%d")
    except Exception:
        d = pd.Timestamp(trade_date).normalize() + pd.Timedelta(days=1)
        while d.weekday() >= 5:
            d += pd.Timedelta(days=1)
        return d.strftime("%Y-%m-%d")


def _strategy_exit_policy(
    info: dict[str, Any], strategy_name: str = "",
) -> dict[str, Any]:
    """从策略库条目 + stock_selector 优化记录提取持仓/出场建议。"""
    ep = info.get("engine_params") or {}
    params = info.get("params") or {}
    holding = int(info.get("holding_days", 0) or 0)
    max_hold = int(ep.get("max_holding_days", holding) or holding)
    signal_enabled = bool(params.get("enable_signal_exit", False))
    if max_hold >= 999:
        exit_type = "信号出场"
        exit_days_text = "无固定持仓天数"
    elif signal_enabled:
        exit_type = "固定持仓 + 信号出场保护"
        exit_days_text = f"最长 {max_hold} 个交易日"
    else:
        exit_type = "固定持仓"
        exit_days_text = f"{max_hold} 个交易日"

    signal_note = _SIGNAL_EXIT_NOTES.get(str(info.get("class", "")), "")
    use_atr = bool(ep.get("use_atr_exit", False))
    atr_stop = ep.get("atr_stop_mult")
    atr_trail = ep.get("atr_trail_mult")
    atr_trigger = ep.get("atr_trail_trigger")
    kill_days = ep.get("breakeven_kill_days")

    # 给初次使用者的人类可读操作建议。ATR 口径与 stock_selector
    # livermore_engine 一致:固定止损线用入场 ATR,移动止盈线用当日 ATR。
    advice_parts: list[str] = []
    if use_atr and atr_stop is not None:
        advice_parts.append(
            f"ATR止损:收盘跌破「入场价 - {atr_stop}×ATR14(入场日)」时卖出"
        )
    if use_atr and atr_trail is not None and atr_trigger is not None:
        advice_parts.append(
            f"移动止盈:昨日浮盈≥{float(atr_trigger) * 100:.0f}% 后启用,"
            f"收盘跌破「持仓期最高收盘价 - {atr_trail}×当日ATR」时卖出"
        )
    if kill_days is not None:
        advice_parts.append(
            f"保本kill:持有到第 {int(kill_days)} 个交易日时,"
            "如果昨日收盘仍不盈利,当日一次性平仓(只清理慢亏单)"
        )
    if exit_type == "信号出场":
        advice_parts.append("该策略没有固定持有上限,以策略信号出场为主,ATR/kill 只做兜底")
    else:
        advice_parts.append(
            f"该策略固定持有 {max_hold} 个交易日,建议持有到期,不因小波动手动卖出"
        )

    opt = get_optimization_record(strategy_name) or {}
    avg_hd = opt.get("avg_holding_days")
    if avg_hd:
        advice_parts.append(f"OOS 实际平均持仓约 {avg_hd:.1f} 天,可作为观察参考")
    module = str(info.get("module", ""))
    if "factor_combo" in module or "factor_ranked_event" in module:
        advice_parts.append("因子/组合策略回测口径为多股组合,小资金仅参考选股方向,不要照搬仓位参数")

    policy: dict[str, Any] = {
        "exit_type": exit_type,
        "holding_days": max_hold,
        "exit_days_text": exit_days_text,
        "signal_enabled": signal_enabled,
        "signal_note": signal_note or "以策略回测参数为准",
        "min_holding_days": int(params.get("exit_min_holding_days", 0) or 0),
        "breakeven_kill_days": kill_days,
        "use_atr_exit": use_atr,
        "atr_stop_mult": atr_stop,
        "atr_trail_mult": atr_trail,
        "atr_trail_trigger": atr_trigger,
        "exit_advice": advice_parts,
    }
    return policy


def _plan_id(ticker: str, trade_date: str) -> str:
    return f"{ticker}_{trade_date}_{uuid.uuid4().hex[:8]}"


def create_buy_plan(
    ticker: str,
    trade_date: str,
    top_picks: pd.DataFrame,
    all_records: list[dict[str, Any]],
    name: str = "",
) -> dict[str, Any]:
    """从量化选股结果创建一条买入计划。"""
    row_df = top_picks[top_picks["stock_code"].astype(str) == str(ticker)]
    if row_df.empty:
        raise ValueError(f"{ticker} 不在 Top N 候选列表中")

    row = row_df.iloc[0]
    lib = get_all_strategies_final()
    records = [r for r in all_records if str(r.get("stock_code")) == str(ticker)]
    records = sorted(records, key=lambda r: float(r.get("strategy_comp", 0.0)), reverse=True)

    strategies = []
    for rec in records:
        strategy_name = str(rec.get("strategy", ""))
        strat_info = lib.get(strategy_name, {})
        opt = get_optimization_record(strategy_name) or {}
        perf = strat_info.get("performance") or {}
        strategies.append({
            "strategy": strategy_name,
            "tier": rec.get("tier", ""),
            "comp": float(rec.get("strategy_comp", 0.0)),
            "win_rate": float(rec.get("win_rate", 0.0)),
            "holding_days": rec.get("holding_days"),
            "entry_advice": str(rec.get("entry_advice", "")).replace("均收", "OOS累计收益"),
            "description": strat_info.get("description", ""),
            "exit_policy": _strategy_exit_policy(strat_info, strategy_name),
            "backtest": {
                "oos_total_return": _safe_float(opt.get("oos_total_return", perf.get("total_return", 0.0))),
                "win_rate": _safe_float(opt.get("win_rate", perf.get("win_rate", 0.0))),
                "sharpe": _safe_float(opt.get("sharpe", perf.get("sharpe", 0.0))),
                "max_drawdown": _safe_float(opt.get("max_drawdown", perf.get("max_drawdown", 0.0))),
                "profit_factor": _safe_float(opt.get("profit_factor", perf.get("profit_factor", 0.0))),
                "n_sells": int(opt.get("n_sells", 0) or 0),
                "n_trades": int(opt.get("n_trades", 0) or 0),
                "avg_holding_days": _safe_float(opt.get("avg_holding_days", 0.0)),
            },
        })

    avg_hd = _safe_float(row.get("avg_holding_days", 0))
    signal_exit = any(s["exit_policy"]["exit_type"] == "信号出场" for s in strategies)

    plan = {
        "plan_id": _plan_id(ticker, trade_date),
        "ticker": str(ticker),
        "name": name or "--",
        "trade_date": str(trade_date),
        "plan_date": _next_trade_date(trade_date),
        "status": "planned",  # planned / filled / closed / abandoned
        "created_at": _now(),
        "updated_at": _now(),
        "quant": {
            "n_strategies": _safe_int(row.get("n_strategies", 0)),
            "weighted_score": _safe_float(row.get("weighted_score", 0.0)),
            "avg_win_rate": _safe_float(row.get("avg_win_rate", 0.0)),
            "avg_holding_days": avg_hd,
            "signal_exit": signal_exit,
        },
        "strategies": strategies,
        "llm": {},
        "buy": {},
        "sell": {},
        "risk_rules": {
            "stop_loss_pct": STOP_LOSS_PCT,
            "take_profit_pct": TAKE_PROFIT_PCT,
            "expire_days": avg_hd,
        },
    }

    with _LOCK:
        data = _load()
        data[plan["plan_id"]] = plan
        _save(data)
    return plan


def get_plan(plan_id: str) -> dict[str, Any] | None:
    with _LOCK:
        return _load().get(plan_id)


def list_plans(status: str | None = None) -> list[dict[str, Any]]:
    with _LOCK:
        plans = list(_load().values())
    plans.sort(key=lambda p: p.get("created_at", ""), reverse=True)
    if status:
        plans = [p for p in plans if p.get("status") == status]
    return plans


def update_plan(plan_id: str, patch: dict[str, Any]) -> dict[str, Any] | None:
    with _LOCK:
        data = _load()
        plan = data.get(plan_id)
        if not plan:
            return None
        plan.update(patch)
        plan["updated_at"] = _now()
        _save(data)
        return plan


def confirm_buy(plan_id: str, buy_date: str, buy_price: float,
                shares: int = 0) -> dict[str, Any] | None:
    """确认成功买入,计划进入持仓跟踪(状态检查+写入在同一把锁内)。"""
    with _LOCK:
        data = _load()
        plan = data.get(plan_id)
        if not plan or plan.get("status") != "planned":
            return None
        plan["status"] = "filled"
        plan["buy"] = {
            "date": str(buy_date),
            "price": _safe_float(buy_price),
            "shares": _safe_int(shares),
            "confirmed_at": _now(),
        }
        plan["updated_at"] = _now()
        _save(data)
        return plan


def abandon_plan(plan_id: str) -> dict[str, Any] | None:
    """Only a planned (not-yet-filled) plan can be abandoned."""
    with _LOCK:
        data = _load()
        plan = data.get(plan_id)
        if not plan or plan.get("status") != "planned":
            return None
        plan["status"] = "abandoned"
        plan["updated_at"] = _now()
        _save(data)
        return plan


def close_position(plan_id: str, sell_date: str, sell_price: float,
                   reason: str = "manual") -> dict[str, Any] | None:
    """Close a filled position(状态检查+写入在同一把锁内)。"""
    with _LOCK:
        data = _load()
        plan = data.get(plan_id)
        if not plan or plan.get("status") != "filled":
            return None
        plan["status"] = "closed"
        plan["sell"] = {
            "date": str(sell_date),
            "price": _safe_float(sell_price),
            "reason": reason,
            "closed_at": _now(),
        }
        plan["updated_at"] = _now()
        _save(data)
        return plan


def enrich_plan_llm(plan: dict[str, Any]) -> dict[str, Any]:
    """从历史 LLM 分析日志中补充四档标签/置信分。"""
    if plan.get("llm"):
        return plan

    results_dir = Path(DEFAULT_CONFIG["results_dir"])
    ticker = str(plan["ticker"])

    trade_date = str(plan["trade_date"])
    log_path = results_dir / ticker / "TradingAgentsStrategy_logs" / f"full_states_log_{trade_date}.json"
    if not log_path.exists():
        return plan

    try:
        state = json.loads(log_path.read_text(encoding="utf-8"))
        label = state.get("final_signal_label") or ""
        if not label and state.get("final_ranked_decision"):
            import re

            m = re.search(r"标签\s*[:：]\s*([^\n]+)", state["final_ranked_decision"])
            label = m.group(1).strip() if m else ""
        update_plan(plan["plan_id"], {
            "llm": {
                "label": label,
                "conviction_score": state.get("conviction_score"),
                "final_trade_decision": state.get("final_trade_decision", "")[:1200],
                "data_quality_summary": state.get("data_quality_summary", "")[:800],
                "source": str(log_path),
            },
        })
        plan = get_plan(plan["plan_id"]) or plan
    except (OSError, json.JSONDecodeError):
        pass
    return plan


def get_limit_reference_prices(ticker: str,
                               trade_date: str | None = None) -> dict[str, Any]:
    """返回 signal-date close 为基准的下一交易日涨跌停参考价。

    下一交易日的涨跌停价格以信号日(或此前最近交易日)收盘价为基准,
    按板块涨跌幅限制和 0.01 元 tick 取整。
    """
    empty: dict[str, Any] = {
        "asof": None, "close": None, "limit_pct": None,
        "limit_up": None, "limit_down": None,
    }
    try:
        from tradingagents.quant.data.universe import _round_tick, price_limit_pct

        cache_name = DEFAULT_CONFIG.get("quant_daily_cache_name", "daily_main_board")
        df = _daily_df(cache_name)
        if df is None:
            return empty
        sub = df[df["stock_code"].astype(str) == str(ticker)].sort_values("trade_date")
        if trade_date:
            mask = pd.to_datetime(sub["trade_date"]) <= pd.Timestamp(str(trade_date))
            sub = sub[mask]
        if sub.empty or "close" not in sub.columns:
            return empty
        row = sub.iloc[-1]
        close = float(row.get("close", 0) or 0)
        if close <= 0:
            return empty
        pct = price_limit_pct(str(ticker))
        return {
            "asof": str(row.get("trade_date", "")),
            "close": close,
            "limit_pct": pct,
            "limit_up": _round_tick(close * (1 + pct)),
            "limit_down": _round_tick(close * (1 - pct)),
        }
    except Exception:
        return empty


def get_latest_price(ticker: str) -> float | None:
    """优先读本地日线缓存最新收盘;缓存缺失/无该股时回退腾讯实时行情。"""
    local_price: float | None = None
    try:
        cache_name = DEFAULT_CONFIG.get("quant_daily_cache_name", "daily_main_board")
        df = _daily_df(cache_name)
        if df is not None:
            sub = df[df["stock_code"].astype(str) == str(ticker)].sort_values("trade_date")
            if len(sub) > 0 and "close" in sub.columns:
                local_price = _safe_float(sub["close"].iloc[-1])
                if local_price > 0:
                    return local_price
    except Exception:
        local_price = None

    try:
        from tradingagents.dataflows.a_stock import _tencent_quote

        info = _tencent_quote([str(ticker)])
        price = info.get(str(ticker), {}).get("price")
        return _safe_float(price) if price else None
    except Exception:
        return None


def holding_days(buy_date: str, asof: date | None = None) -> int:
    """Natural (calendar) days between buy_date and asof."""
    if not buy_date:
        return 0
    d = pd.Timestamp(buy_date).normalize()
    end = pd.Timestamp(asof or date.today()).normalize()
    return max(0, int((end - d).days))


def trading_holding_days(buy_date: str, asof: date | None = None) -> int:
    """Trading-session holding count (both endpoints included when trading days).

    Expiry rules in the strategy library are measured in trading days; calendar
    days would fire 1-2 days early around every weekend. Falls back to calendar
    days if the local trading calendar is unavailable.
    """
    if not buy_date:
        return 0
    end = (asof or date.today()).isoformat()
    try:
        from tradingagents.quant.utils.trading_calendar import trading_days

        days = trading_days(str(buy_date), end)
        return max(0, len(days))
    except Exception:
        return holding_days(buy_date, asof)
