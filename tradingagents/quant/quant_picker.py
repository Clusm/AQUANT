"""量化策略前置筛选层主入口。

调策略库(multiprocessing.Pool)产出 Top N 候选 + 命中策略 + 加权分 + 入场建议。
策略数由 get_all_strategies_final() 动态决定(当前 10 个有效,弃用的不参与)。
被 TradingAgents LangGraph 的 Quant Picker 节点调用,也可独立运行。

API:
    from tradingagents.quant import pick
    result = pick(today=pd.Timestamp("2026-07-17"), daily_cache_name="daily_main_board_liquid")
    top20_df = result["top_picks"]
"""
from __future__ import annotations

import importlib
import io
import multiprocessing as mp
import sys
import time
from typing import Callable

import pandas as pd

from tradingagents.quant import config
from tradingagents.quant.backtest.portfolio import Portfolio
from tradingagents.quant.data import cache as cm
from tradingagents.quant.strategy.strategy_library_final import (
    get_all_strategies_final, get_tier_of_final,
)
from tradingagents.quant.utils.trading_calendar import get_calendar


TIER_ORDER = ["S", "A", "B", "C", "M_S", "M_A", "M_B", "M_C"]
DEFAULT_TOP_K = 2


# ============================================================
# 缓存文件名自动检测
# ============================================================

def _find_cache(name: str) -> str:
    """自动检测缓存文件名(支持日期后缀变体 + 旧名回退)。"""
    cache_dir = config.CACHE_DIR
    if (cache_dir / f"{name}.parquet").exists():
        return name
    variants = sorted(cache_dir.glob(f"{name}*.parquet"))
    if variants:
        return variants[-1].stem
    if name == "daily_main_board":
        old = sorted(cache_dir.glob("daily_top*.parquet"))
        if old:
            return old[-1].stem
    return name


# ============================================================
# 策略元信息
# ============================================================

def get_tier_of(name: str, info: dict) -> str:
    """Final 库分级:短线 S/A/B/C,中线 M_S/M_A/M_B/M_C(基于 holding_days 区分)。

    2026-07-20 改用 get_tier_of_final(基于修复前视偏差+全周期验证后的新分级)。
    """
    base = get_tier_of_final(name)
    if base in ("DEPRECATED", "UNKNOWN"):
        base = "C"

    hd = info.get("holding_days", None)
    if hd is None or hd <= 5:
        return base
    return f"M_{base}"


def _compute_entry_advice(info: dict) -> tuple[str, int]:
    """根据 holding_days 生成进场建议。"""
    GAP_BUY = "低开≤-5%接近基线,谨慎可买"
    GAP_NOBUY = "高开≥5%不买(追高风险)"
    hd = info.get("holding_days", None)
    new_perf = info.get("new_performance") or {}
    win_rate = new_perf.get("win_rate", 0.0)
    total_return = new_perf.get("total_return", 0.0)
    wr_pct = f"{win_rate * 100:.1f}%" if win_rate else "N/A"
    ret_pct = f"{total_return * 100:+.2f}%" if total_return else "N/A"

    if hd is None or hd <= 5:
        holding = 5 if hd is None else hd
        advice = (
            f"买:次日09:30开盘买入。短线{holding}日,胜率{wr_pct}/均收{ret_pct}。"
            f"不买:{GAP_NOBUY}。"
            f"{GAP_BUY}。"
        )
        return (advice, holding)
    advice = (
        f"买:次日10:00后,9:30-10:00收阳且10:00价在30min VWAP -1%~0%"
        f"(轻度回踩)则买入。中线{hd}日,胜率{wr_pct}/均收{ret_pct}。"
        f"不买:不满足上述条件或{GAP_NOBUY}。"
        f"{GAP_BUY}。"
    )
    return (advice, hd)


def needs_full_data(info: dict) -> bool:
    """周线/月线/季度策略需要全量数据(EMA 路径依赖)。"""
    module = info.get("module", "")
    return any(k in module for k in ("weekly", "monthly", "quarterly"))


# ============================================================
# Worker 进程全局变量
# ============================================================

_WORKER_DAILY_DF_FULL: pd.DataFrame | None = None
_WORKER_DAILY_DF_SLICED: pd.DataFrame | None = None
_WORKER_TODAY: pd.Timestamp | None = None
_WORKER_TOP_K: int = DEFAULT_TOP_K


def _worker_init(daily_cache_name: str, today_str: str, top_k: int,
                 slice_days: int = 0):
    global _WORKER_DAILY_DF_FULL, _WORKER_DAILY_DF_SLICED
    global _WORKER_TODAY, _WORKER_TOP_K

    _WORKER_DAILY_DF_FULL = cm.load(daily_cache_name)
    _WORKER_DAILY_DF_FULL["trade_date"] = pd.to_datetime(
        _WORKER_DAILY_DF_FULL["trade_date"]).dt.normalize()

    if slice_days > 0:
        today_ts = pd.Timestamp(today_str)
        cutoff = today_ts - pd.Timedelta(days=slice_days)
        _WORKER_DAILY_DF_SLICED = _WORKER_DAILY_DF_FULL[
            _WORKER_DAILY_DF_FULL["trade_date"] >= cutoff
        ].reset_index(drop=True)
    else:
        _WORKER_DAILY_DF_SLICED = _WORKER_DAILY_DF_FULL

    _WORKER_TODAY = pd.Timestamp(today_str)
    _WORKER_TOP_K = top_k
    sys.stdout = io.StringIO()

    # 方案 A:尝试 load 预计算的 weekly_bars / monthly_bars parquet
    # 命中后填进 _WEEKLY_BARS_CACHE / _MONTHLY_BARS_CACHE
    # 策略调 get_weekly_bars / get_monthly_bars 时跳过 resample,省 ~35s
    precomputed_bars_loaded = False
    try:
        from tradingagents.quant.features.pipeline import (
            _resampled_cache_key, _WEEKLY_BARS_CACHE, _MONTHLY_BARS_CACHE,
        )
        weekly_name = f"{daily_cache_name}_weekly_bars"
        monthly_name = f"{daily_cache_name}_monthly_bars"
        if cm.exists(weekly_name) and cm.exists(monthly_name):
            # 校验 meta(source_end 匹配)
            w_meta = cm.load_meta(weekly_name) or {}
            src_end = str(_WORKER_DAILY_DF_FULL["trade_date"].max().date())
            if w_meta.get("source_end") == src_end:
                weekly_bars = cm.load(weekly_name)
                monthly_bars = cm.load(monthly_name)
                key = _resampled_cache_key(_WORKER_DAILY_DF_FULL)
                _WEEKLY_BARS_CACHE[key] = weekly_bars
                _MONTHLY_BARS_CACHE[key] = monthly_bars
                precomputed_bars_loaded = True
    except Exception:
        pass

    # 预热特征缓存 - worker 启动时算一次 build_features_vectorized
    try:
        from tradingagents.quant.features.pipeline import (
            build_features_vectorized, build_weekly_features, build_monthly_features,
        )
        for warm_df in (_WORKER_DAILY_DF_SLICED, _WORKER_DAILY_DF_FULL):
            if warm_df is None or len(warm_df) == 0:
                continue
            sorted_df = warm_df.sort_values(
                ["stock_code", "trade_date"]).reset_index(drop=True)
            # 预热 min_rows=30 的特征矩阵,所有策略共享此缓存。
            # 策略内部行数不够的票(如 monthly 需要 120 行)会被 rolling NaN 自然过滤,
            # 不需要为每个 min_rows 阈值单独缓存(否则内存 4× 且 key 不匹配策略调用)。
            build_features_vectorized(sorted_df, min_rows=30)
            # O1: 周月线指标共享缓存。needs_full_data=True 的策略(15+ 个)
            # 都调这两个函数,worker 启动时算一次,后续策略共享。
            # 若预计算 bars 已 load,get_weekly_bars/get_monthly_bars 命中 cache,
            # build_weekly/monthly 只算 merge,省 resample。
            build_weekly_features(sorted_df)
            build_monthly_features(sorted_df)
    except Exception:
        pass


def _build_sub_from_dict(cfg: dict):
    """递归构建子策略(用于 ensemble 策略的 strategies 字段是 dict spec 时)。"""
    module = importlib.import_module(cfg["module"])
    cls = getattr(module, cfg["class"])
    params = dict(cfg.get("params", {}))
    if "strategies" in params and isinstance(params["strategies"], list) \
            and params["strategies"] and isinstance(params["strategies"][0], dict):
        params["strategies"] = [_build_sub_from_dict(s) for s in params["strategies"]]
    return cls(**params)


def _worker_run(args: tuple) -> dict:
    name, info, tier, comp, needs_full = args[:5]
    global _WORKER_DAILY_DF_FULL, _WORKER_DAILY_DF_SLICED
    global _WORKER_TODAY, _WORKER_TOP_K

    daily_df = _WORKER_DAILY_DF_FULL if needs_full else _WORKER_DAILY_DF_SLICED

    entry_advice, holding_days = _compute_entry_advice(info)
    new_perf = info.get("new_performance") or {}
    win_rate = float(new_perf.get("win_rate", 0.0))

    t0 = time.time()
    try:
        module = importlib.import_module(info["module"])
        cls = getattr(module, info["class"])
        params = dict(info.get("params", {}))
        if "strategies" in params and isinstance(params["strategies"], list) \
                and params["strategies"] and isinstance(params["strategies"][0], dict):
            params["strategies"] = [_build_sub_from_dict(s) for s in params["strategies"]]
        strat = cls(**params)

        portfolio = Portfolio(capital=20000, max_positions=2, calendar=get_calendar())
        signals = strat.generate_signals(
            daily_df, _WORKER_TODAY, portfolio, top_k=_WORKER_TOP_K)
        elapsed = time.time() - t0
        sig_records = []
        for sig in (signals or []):
            sig_records.append({
                "code": sig.code,
                "score": float(sig.score),
                "reason": sig.reason,
            })
        return {
            "name": name, "tier": tier, "comp": comp,
            "n_hits": len(sig_records), "elapsed": elapsed,
            "error": None, "signals": sig_records,
            "needs_full": needs_full,
            "entry_advice": entry_advice, "holding_days": holding_days,
            "win_rate": win_rate,
        }
    except Exception as e:
        elapsed = time.time() - t0
        err_name = getattr(type(e), "__name__", "Error")
        return {
            "name": name, "tier": tier, "comp": comp,
            "n_hits": 0, "elapsed": elapsed,
            "error": f"{err_name}: {str(e)[:120]}",
            "signals": [],
            "needs_full": needs_full,
            "entry_advice": entry_advice, "holding_days": holding_days,
            "win_rate": win_rate,
        }


# ============================================================
# 聚合
# ============================================================

def _aggregate(all_records: list[dict]) -> pd.DataFrame:
    """聚合信号:按 stock_code group,算命中数 + 加权分 + 加权胜率 + 建议持仓天数。"""
    empty_cols = ["stock_code", "n_strategies", "weighted_score",
                  "avg_win_rate", "avg_holding_days"] + [f"n_{t}" for t in TIER_ORDER]
    if not all_records:
        return pd.DataFrame(columns=empty_cols)
    df = pd.DataFrame(all_records)
    # M4: holding_days is conceptually an int (5/10/15/20). Use Int64 nullable
    # int so downstream display shows "5d" not "5.0d" and NaN -> pd.NA is visible.
    if "holding_days" in df.columns:
        df["holding_days"] = pd.to_numeric(df["holding_days"], errors="coerce").astype("Int64")
    agg_rows = []
    for code, g in df.groupby("stock_code"):
        total_comp = float(g["strategy_comp"].sum())
        if total_comp > 0:
            weighted_wr = float((g["win_rate"] * g["strategy_comp"]).sum() / total_comp)
            hd = g["holding_days"].fillna(5).astype(float)
            weighted_hd = float((hd * g["strategy_comp"]).sum() / total_comp)
        else:
            weighted_wr = float(g["win_rate"].mean()) if "win_rate" in g.columns else 0.0
            hd = g["holding_days"].fillna(5).astype(float)
            weighted_hd = float(hd.mean()) if len(hd) > 0 else 5.0
        row = {
            "stock_code": code,
            "n_strategies": len(g),
            "weighted_score": total_comp,
            "avg_win_rate": weighted_wr,
            "avg_holding_days": weighted_hd,
        }
        for tier in TIER_ORDER:
            row[f"n_{tier}"] = int((g["tier"] == tier).sum())
        agg_rows.append(row)
    agg = pd.DataFrame(agg_rows)
    return agg.sort_values("weighted_score", ascending=False).reset_index(drop=True)


def compute_top_n(all_records: list[dict], top_n: int = 20) -> pd.DataFrame:
    """聚合 all_records,返回 Top N DataFrame(按 weighted_score 降序)。"""
    return _aggregate(all_records).head(top_n)


# ============================================================
# 主入口
# ============================================================

def pick(
    today: pd.Timestamp,
    daily_cache_name: str = "daily_main_board_liquid",
    top_k: int = 2,
    n_workers: int = 8,
    slice_days: int = 0,
    strategies: dict | None = None,
    top_n: int = 20,
    progress_callback: Callable[[int, int, dict], None] | None = None,
    stop_check: Callable[[], bool] | None = None,
) -> dict:
    """跑策略库,产出 Top N 候选 + 全量信号记录 + 每策略统计。

    Args:
        today: 选股日期(收盘后)
        daily_cache_name: 日线数据缓存文件名(不含 .parquet 后缀)
        top_k: 每个策略返回的前 N 只股票
        n_workers: 并行 worker 数(Windows 必须 spawn,worker 启动慢)
        slice_days: 切片天数(0=全量,短线策略可用切片加速,周月季策略自动用全量)
        strategies: 策略字典,None 时用 get_all_strategies_final()(10 个有效,弃用的不参与选股)
        top_n: 返回 Top N 候选(默认 20)
        progress_callback: 进度回调 fn(completed, total, latest_result_dict)

    Returns:
        {
            "top_picks": pd.DataFrame,       # Top N 候选,按 weighted_score 降序
            "all_records": list[dict],       # 全量信号记录(每条 = 一只股票被一个策略命中)
            "per_strategy_stats": dict,      # {策略名: {tier, comp, n_hits, elapsed, error, ...}}
            "elapsed": float,                # 总耗时(秒)
            "today": pd.Timestamp,
            "n_strategies_run": int,
            "n_strategies_error": int,
        }
    """
    if strategies is None:
        strategies = get_all_strategies_final()

    # L5: top_n runtime validation. Comment in default_config says 5/10/20,
    # enforce it here so silent typos don't produce oversized result sets.
    # Validated before cache check so bad-arg errors surface even without data.
    if top_n not in (5, 10, 20):
        raise ValueError(
            f"top_n must be one of 5/10/20, got {top_n}. "
            f"Larger values inflate LLM cost without backtest support."
        )

    daily_cache_name = _find_cache(daily_cache_name)

    # M2: fail fast in main process if cache file is missing. Without this,
    # the error surfaces inside _worker_init (spawn) and crashes the entire
    # Pool, leaving the user with an opaque BrokenProcessPool traceback.
    cache_file = config.CACHE_DIR / f"{daily_cache_name}.parquet"
    if not cache_file.exists():
        raise FileNotFoundError(
            f"Daily cache not found: {cache_file}. "
            f"Run data_update first to build {daily_cache_name}.parquet."
        )

    # Date sanity: if today is beyond the cache's latest date (e.g. user
    # selected today's date but cache hasn't been updated yet), strategies
    # would find no eligible stocks and return empty signals. Fall back to
    # the cache's latest trade date so the user gets useful results.
    today = pd.Timestamp(today).normalize()
    _date_col = pd.read_parquet(cache_file, columns=["trade_date"])["trade_date"]
    _date_col = pd.to_datetime(_date_col).dt.normalize()
    latest_in_cache = _date_col.max()
    if today > latest_in_cache:
        print(
            f"[quant_picker] today={today.date()} is beyond cache latest "
            f"{latest_in_cache.date()}; falling back to {latest_in_cache.date()}. "
            f"Run data_update to refresh cache.",
            flush=True,
        )
        today = latest_in_cache

    tasks = []
    for name, info in strategies.items():
        tier = get_tier_of(name, info)
        comp = float(info.get("new_composite_score") or info.get("composite_score") or 0.0)
        needs_full = needs_full_data(info) if slice_days > 0 else True
        tasks.append((name, info, tier, comp, needs_full))

    # 慢任务先投递避免拖尾(全量策略优先)
    tasks.sort(key=lambda t: not t[4])

    n_total = len(tasks)
    all_records: list[dict] = []
    per_strategy_stats: dict[str, dict] = {}
    t_start = time.time()

    ctx = mp.get_context("spawn")
    today_str = today.strftime("%Y-%m-%d")

    with ctx.Pool(
        processes=n_workers,
        initializer=_worker_init,
        initargs=(daily_cache_name, today_str, top_k, slice_days),
    ) as pool:
        for i, result in enumerate(pool.imap_unordered(_worker_run, tasks), 1):
            # stop_check: 让 web UI 的"停止"按钮在当前策略完成后立即生效,
            # 而不是等所有策略跑完。break 后 Pool __exit__ 会 pool.terminate()
            # 立即结束剩余 worker。
            if stop_check is not None and stop_check():
                break
            for sig in result["signals"]:
                all_records.append({
                    "date": today,
                    "strategy": result["name"],
                    "tier": result["tier"],
                    "strategy_comp": result["comp"],
                    "stock_code": sig["code"],
                    "score": sig["score"],
                    "reason": sig["reason"],
                    "holding_days": result["holding_days"],
                    "entry_advice": result["entry_advice"],
                    "win_rate": result["win_rate"],
                })
            per_strategy_stats[result["name"]] = {
                "tier": result["tier"], "comp": result["comp"],
                "n_hits": result["n_hits"], "elapsed": result["elapsed"],
                "error": result["error"], "needs_full": result["needs_full"],
                "holding_days": result["holding_days"],
                "entry_advice": result["entry_advice"],
                "win_rate": result["win_rate"],
            }
            if progress_callback is not None:
                progress_callback(i, n_total, result)

    elapsed_total = time.time() - t_start
    top_picks = compute_top_n(all_records, top_n=top_n)
    n_errors = sum(1 for s in per_strategy_stats.values() if s["error"])

    return {
        "top_picks": top_picks,
        "all_records": all_records,
        "per_strategy_stats": per_strategy_stats,
        "elapsed": elapsed_total,
        "today": today,
        "n_strategies_run": n_total,
        "n_strategies_error": n_errors,
    }


def format_top_picks_summary(result: dict, name_map: dict[str, str] | None = None) -> str:
    """格式化 Top N 候选为可读字符串(用于 LLM 注入或日志输出)。"""
    top = result.get("top_picks")
    if top is None or len(top) == 0:
        return "无候选股票(策略库均未生成信号)"

    lines = [f"=== 量化选股 Top {len(top)} (日期: {result['today'].date()}) ==="]
    lines.append(f"策略数: {result['n_strategies_run']}, 错误: {result['n_strategies_error']}, 耗时: {result['elapsed']:.1f}s")
    lines.append("")
    lines.append(f"{'排名':<4}{'代码':<10}{'名称':<12}{'命中数':<6}{'加权分':<8}{'胜率':<8}{'持仓天':<6}")
    for i, row in top.iterrows():
        code = row["stock_code"]
        name = (name_map or {}).get(code, "--")
        wr = row.get("avg_win_rate", 0) * 100
        hd = row.get("avg_holding_days", 0)
        lines.append(
            f"{i+1:<4}{code:<10}{name:<12}"
            f"{int(row['n_strategies']):<6}{row['weighted_score']:<8.2f}"
            f"{wr:<8.1f}{hd:<6.1f}"
        )

    # 附上每只股票命中的策略详情
    lines.append("")
    lines.append("=== 命中策略详情 ===")
    by_code: dict[str, list[dict]] = {}
    for rec in result["all_records"]:
        by_code.setdefault(rec["stock_code"], []).append(rec)
    for i, row in top.iterrows():
        code = row["stock_code"]
        recs = by_code.get(code, [])
        if not recs:
            continue
        lines.append(f"\n#{i+1} {code} (加权分 {row['weighted_score']:.2f}):")
        for r in recs[:5]:  # 最多列 5 个策略
            lines.append(
                f"  - [{r['tier']}] {r['strategy']} (comp={r['strategy_comp']:.2f}, "
                f"胜率={r['win_rate']*100:.0f}%, 持仓={r['holding_days']}d)"
            )
        if len(recs) > 5:
            lines.append(f"  ... 还有 {len(recs) - 5} 个策略")

    return "\n".join(lines)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="量化策略选股(10 个有效策略,弃用的不参与)")
    parser.add_argument("--today", default=None, help="选股日期 YYYY-MM-DD,默认今天")
    parser.add_argument("--cache", default="daily_main_board_liquid", help="日线缓存文件名")
    parser.add_argument("--top-k", type=int, default=2, help="每策略返回前 N 只")
    parser.add_argument("--top-n", type=int, default=20, help="最终 Top N")
    parser.add_argument("--workers", type=int, default=8, help="并行 worker 数")
    parser.add_argument("--slice-days", type=int, default=0, help="切片天数(0=全量)")
    args = parser.parse_args()

    today = pd.Timestamp(args.today) if args.today else pd.Timestamp.now().normalize()

    def progress(completed: int, total: int, latest: dict):
        err = "ERR" if latest.get("error") else "OK"
        print(f"[{completed}/{total}] {latest['name']} ({latest['tier']}) "
              f"hits={latest['n_hits']} {err} {latest['elapsed']:.1f}s",
              flush=True)

    result = pick(
        today=today,
        daily_cache_name=args.cache,
        top_k=args.top_k,
        n_workers=args.workers,
        slice_days=args.slice_days,
        top_n=args.top_n,
        progress_callback=progress,
    )

    print()
    print(format_top_picks_summary(result))
