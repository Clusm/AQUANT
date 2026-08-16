"""数据增量更新(简化版):从 sina K-line API 拉取最新交易日数据。

从 stock_pick_live/data_update_live.py 去 rich Progress 包装,
改为可选 progress_callback,便于 Web UI 集成。
"""
from __future__ import annotations

import time
from typing import Callable

import pandas as pd

from tradingagents.quant.data import cache as cm
from tradingagents.quant.sina_fetcher import (
    adaptive_datalen,
    fetch_bulk_incremental_sina,
    fetch_index_sina,
)


def check_cache_freshness(daily_df: pd.DataFrame,
                          now: pd.Timestamp | None = None) -> tuple[pd.Timestamp, int]:
    """返回 (缓存最新交易日, 距今天数)。now 可注入(测试确定性),默认当前时间。

    空缓存:max() 为 NaT,直接 normalize 会抛 AttributeError;返回 (today, 极大
    天数) 表示"没有数据、需要更新",让调用方走更新路径而非崩溃。
    """
    today = (now.normalize() if now is not None else pd.Timestamp.now()).normalize()
    if len(daily_df) == 0:
        return today, 10 ** 9
    last_date = pd.Timestamp(daily_df["trade_date"].max()).normalize()
    days_behind = (today - last_date).days
    return last_date, days_behind


def _get_last_shares_map(daily_df: pd.DataFrame) -> dict[str, float]:
    """从缓存获取每只股票最后的 outstanding_share(流通股本)。"""
    if "outstanding_share" not in daily_df.columns:
        return {}
    last_rows = daily_df.sort_values("trade_date").groupby("stock_code").last()
    return {
        str(code): float(shares)
        for code, shares in last_rows["outstanding_share"].items()
        if pd.notna(shares) and shares > 0
    }


def _get_last_close_map(daily_df: pd.DataFrame) -> dict[str, float]:
    """从缓存获取每只股票最后收盘价(用于修正增量首行 pre_close)。"""
    if "close" not in daily_df.columns:
        return {}
    last_rows = daily_df.sort_values("trade_date").groupby("stock_code").last()
    return {
        str(code): float(close)
        for code, close in last_rows["close"].items()
        if pd.notna(close) and close > 0
    }


def increment_data(daily_df: pd.DataFrame, idx_df: pd.DataFrame,
                   daily_cache_name: str, index_cache_name: str,
                   max_workers: int = 32,
                   codes: list[str] | None = None,
                   progress_callback: Callable[[int, int, dict], None] | None = None,
                   stop_check: Callable[[], bool] | None = None,
                   skip_inactive_days: int = 30,
                   now: pd.Timestamp | None = None
                   ) -> tuple[pd.DataFrame, pd.DataFrame, str]:
    """增量拉取最新交易日数据(sina K-line API)。

    性能优化(2026-07):
    - max_workers 默认 32(原 8),配合 sina_fetcher 模块级 Session(HTTP keep-alive)
    - skip_inactive_days: 跳过缓存里最近 N 天无成交的股票(疑似退市/长期停牌),
      避免无效请求浪费配额。30 天默认可砍掉 ~10-15% 请求量。

    Args:
        codes: 如果提供,只拉取这些股票(用于 universe-only 更新)
        progress_callback: fn(completed, total, stats_dict),stats_dict 含 succeeded/failed
        stop_check: 每批轮询的停止信号;返回 True 时尽快返回(已拉取部分会
            落盘,下次续跑),不再更新指数缓存。
        skip_inactive_days: 缓存里超过 N 天无成交的股票跳过(默认 30)
        now: 当前时间(测试注入,默认 pd.Timestamp.now())

    Returns: (daily_df, idx_df, message)
    """
    last_date, days_behind = check_cache_freshness(daily_df, now)
    today = (now.normalize() if now is not None else pd.Timestamp.now()).normalize()
    is_weekend = today.weekday() >= 5
    if days_behind <= 0 or (is_weekend and days_behind <= 2):
        return daily_df, idx_df, f"缓存已最新({last_date.date()}),无需更新"

    # 空缓存:无"最后交易日"可延展,增量无从谈起;直接返回清晰提示,
    # 避免 last_date=NaT 在下方 strftime 抛晦涩的 ValueError
    if len(daily_df) == 0:
        return daily_df, idx_df, "缓存为空,无法增量更新。请先运行全量构建(download_all)初始化数据。"

    start = (last_date + pd.Timedelta(1, unit="D")).strftime("%Y-%m-%d")
    end = today.strftime("%Y-%m-%d")
    all_codes = daily_df["stock_code"].astype(str).unique().tolist()

    # 过滤疑似退市/长期停牌:缓存里最近 skip_inactive_days 天无成交的股票跳过
    active_cutoff = last_date - pd.Timedelta(skip_inactive_days, unit="D")
    recent_active = daily_df[daily_df["trade_date"] >= active_cutoff]["stock_code"].astype(str).unique()
    active_set = set(recent_active)
    inactive_skipped = len(all_codes) - len(active_set)

    if codes is not None:
        code_set = set(str(c) for c in codes)
        fetch_codes = [c for c in all_codes if c in code_set and c in active_set]
        scope = f"仅 universe {len(fetch_codes)} 只 (全量 {len(all_codes)})"
    else:
        fetch_codes = [c for c in all_codes if c in active_set]
        scope = f"全量 {len(fetch_codes)} 只 (跳过 {inactive_skipped} 只疑似退市)"

    last_shares_map = _get_last_shares_map(daily_df)
    has_shares = len(last_shares_map)
    last_close_map = _get_last_close_map(daily_df)

    msg_lines: list[str] = [
        f"增量更新: {last_date.date()} -> {end} ({days_behind} 天)",
        f"范围: {scope}",
        f"数据源: sina K-line API (Session keep-alive, {max_workers} workers)",
        f"继承 outstanding_share: {has_shares} 只",
    ]

    inc_df, failed = fetch_bulk_incremental_sina(
        fetch_codes, start, end,
        last_shares_map=last_shares_map,
        last_close_map=last_close_map,
        max_workers=max_workers,
        progress_callback=progress_callback,
        stop_check=stop_check,
    )

    n_new_rows = 0
    if len(inc_df) > 0:
        n_new_rows = len(inc_df)
        daily_df = cm.update(daily_cache_name, inc_df,
                             on=["stock_code", "trade_date"])
        msg_lines.append(
            f"日线缓存已更新: +{n_new_rows} 行, "
            f"最新 {daily_df['trade_date'].max().date()}, 失败 {len(failed)} 只")
    else:
        msg_lines.append(f"[警告] 无新数据,失败 {len(failed)} 只")

    if stop_check is not None and stop_check():
        msg_lines.append("已请求停止,跳过指数更新(已拉取部分已落盘,下次续跑)")
        return daily_df, idx_df, "\n  ".join(msg_lines)

    try:
        idx_datalen = adaptive_datalen(days_behind)
        new_idx = fetch_index_sina("sh000001", datalen=idx_datalen)
        if len(new_idx) > 0:
            new_idx["trade_date"] = pd.to_datetime(new_idx["trade_date"]).dt.normalize()
            new_idx = new_idx[new_idx["trade_date"] > last_date].copy()
            if len(new_idx) > 0:
                new_idx["amount"] = new_idx["volume"] * (
                    new_idx["open"] + new_idx["high"] + new_idx["low"] + new_idx["close"]) / 4
                idx_df = cm.update(index_cache_name, new_idx, on=["trade_date"])
                msg_lines.append(
                    f"指数缓存已更新: 最新 {idx_df['trade_date'].max().date()}")
    except Exception as e:
        msg_lines.append(f"[指数拉取失败] {e}")

    return daily_df, idx_df, "\n  ".join(msg_lines)


def _stale_codes(daily_df: pd.DataFrame, target: pd.Timestamp | None = None) -> list[str]:
    """返回最后交易日 < target(默认全局 max)的股票代码列表(落后股票)。"""
    if target is None:
        target = pd.Timestamp(daily_df["trade_date"].max()).normalize()
    last = daily_df.groupby("stock_code")["trade_date"].max()
    return [str(c) for c, lst in last.items() if lst < target]


def backfill_stale(
    daily_df: pd.DataFrame,
    cache_name: str,
    *,
    window_days: int = 7,
    chunk_size: int = 180,
    cooldown: float = 0.0,
    max_workers: int = 16,
    probe: Callable[[], bool] | None = None,
    fetch_batch: Callable[..., tuple[pd.DataFrame, list[str]]] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    progress_callback: Callable[[int, int, dict], None] | None = None,
    stop_check: Callable[[], bool] | None = None,
) -> tuple[pd.DataFrame, list[str]]:
    """回补落后股票(最后交易日 < 全局最新日)到最新交易日。

    设计:increment_data 的 freshness gate 按全局 max 判定,单股落后不会被
    增量路径补上,回补是增量更新的必要补充。此函数把回补内聚为共享实现
    (此前散落在 incremental_update / backfill_main_board 两处,一处单突发
    456 风险),分块 + 探测冷却 + 每块即时落盘,中断可续跑。

    依赖注入使函数可离线测试:
      - fetch_batch: 单批拉取(默认 sina_fetcher.fetch_bulk_incremental_sina)
      - probe: 端点可用性探测;返回 False 时按 cooldown 冷却等待
      - sleep: 冷却实现(测试注入 noop)
      - stop_check: 停止信号(与 increment_data 同契约)

    Returns:
        (merged_df, remaining_stale) — merged 为已落盘的合并缓存 DataFrame;
        remaining_stale 非空表示被中断(456 或 stop),可据此续跑。
    """
    target = pd.Timestamp(daily_df["trade_date"].max()).normalize()
    stale = _stale_codes(daily_df, target)
    total = len(stale)
    if total == 0:
        return daily_df, []

    if fetch_batch is None:
        fetch_batch = fetch_bulk_incremental_sina

    start = (target - pd.Timedelta(window_days - 1, unit="D")).strftime("%Y-%m-%d")
    end = target.strftime("%Y-%m-%d")
    shares_map = _get_last_shares_map(daily_df)
    close_map = _get_last_close_map(daily_df)

    while stale:
        if stop_check is not None and stop_check():
            break
        if probe is not None:
            while not probe():
                sleep(cooldown)
        chunk = stale[:chunk_size]
        inc_df, failed = fetch_batch(
            chunk, start, end,
            last_shares_map=shares_map,
            last_close_map=close_map,
            max_workers=max_workers,
            stop_check=stop_check,
        )
        if len(inc_df) > 0:
            daily_df = cm.update(cache_name, inc_df, on=["stock_code", "trade_date"])
        # 只对本次 chunk 更新过的代码重判是否追平,避免每块对全量缓存反复
        # groupby(缓存几百万行时,块数 × 全量 groupby 会浪费数秒)
        chunk_set = set(chunk)
        last_by_code = daily_df[
            daily_df["stock_code"].isin(chunk_set)
        ].groupby("stock_code")["trade_date"].max()
        stale = [c for c in stale if c not in last_by_code.index or last_by_code[c] < target]
        if progress_callback is not None:
            progress_callback(total - len(stale), total, {
                "remaining": len(stale),
                "failed": len(failed),
            })
        if stale:
            sleep(cooldown)

    return daily_df, stale


def get_cache_status(daily_df: pd.DataFrame,
                     now: pd.Timestamp | None = None) -> dict:
    """返回缓存状态摘要(用于 UI 显示)。now 可注入(测试确定性)。"""
    last_date, days_behind = check_cache_freshness(daily_df, now)
    today = (now.normalize() if now is not None else pd.Timestamp.now()).normalize()
    return {
        "last_date": last_date,
        "days_behind": days_behind,
        "n_rows": len(daily_df),
        "n_stocks": int(daily_df["stock_code"].nunique()) if len(daily_df) > 0 else 0,
        "needs_update": days_behind > 0 and not (today.weekday() >= 5 and days_behind <= 2),
    }
