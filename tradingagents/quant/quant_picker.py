"""量化策略前置筛选层主入口。

调策略库(multiprocessing.Pool)产出 Top N 候选 + 命中策略 + 加权分 + 入场建议。
策略数由 get_all_strategies_final() 动态决定(当前 top18 终态库:S=5/A=11/B=2)。
被 TradingAgents LangGraph 的 Quant Picker 节点调用,也可独立运行。

API:
    from tradingagents.quant import pick
    result = pick(today=pd.Timestamp("2026-07-17"), daily_cache_name="daily_main_board")
    top20_df = result["top_picks"]
"""
from __future__ import annotations

import hashlib
import importlib
import io
import json
import multiprocessing as mp
import sys
import time
from typing import Callable

import pandas as pd

from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.quant import config
from tradingagents.quant.backtest.portfolio import Portfolio
from tradingagents.quant.data import cache as cm
from tradingagents.quant.strategy.strategy_library_final import (
    get_all_strategies_final,
    get_tier_of_final,
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


SIGNAL_EXIT_DAYS = 999  # 库条目 holding_days=999 表示"信号出场"(无固定持仓天数)


def _compute_entry_advice(info: dict) -> tuple[str, int]:
    """根据 holding_days 生成进场建议。"""
    GAP_BUY = "低开≤-5%接近基线,谨慎可买"
    GAP_NOBUY = "高开≥5%不买(追高风险)"
    hd = info.get("holding_days", None)
    new_perf = info.get("new_performance") or {}
    win_rate = new_perf.get("win_rate", 0.0)
    total_return = new_perf.get("total_return", 0.0)
    wr_pct = f"{win_rate * 100:.1f}%" if win_rate else "N/A"
    ret_pct = f"OOS累计收益{total_return * 100:+.1f}%" if total_return else "OOS累计收益N/A"

    if hd is not None and hd >= SIGNAL_EXIT_DAYS:
        advice = (
            f"买:次日10:00后,9:30-10:00收阳且10:00价在30min VWAP -1%~0%"
            f"(轻度回踩)则买入。中线持有,策略信号出场(无固定天数),"
            f"胜率{wr_pct}/{ret_pct}。"
            f"不买:不满足上述条件或{GAP_NOBUY}。"
            f"{GAP_BUY}。"
        )
        return (advice, hd)

    if hd is None or hd <= 5:
        holding = 5 if hd is None else hd
        advice = (
            f"买:次日09:30开盘买入。短线{holding}日,胜率{wr_pct}/{ret_pct}。"
            f"不买:{GAP_NOBUY}。"
            f"{GAP_BUY}。"
        )
        return (advice, holding)
    advice = (
        f"买:次日10:00后,9:30-10:00收阳且10:00价在30min VWAP -1%~0%"
        f"(轻度回踩)则买入。中线{hd}日,胜率{wr_pct}/{ret_pct}。"
        f"不买:不满足上述条件或{GAP_NOBUY}。"
        f"{GAP_BUY}。"
    )
    return (advice, hd)


def needs_full_data(info: dict) -> bool:
    """周线/月线/季度策略需要全量数据(EMA 路径依赖)。"""
    module = info.get("module", "")
    return any(k in module for k in ("weekly", "monthly", "quarterly"))


# FC 因子策略在 universe 过滤前就做全市场截面 rank(pct=True),提前裁剪 universe
# 会改变截面 rank 的压缩比例,导致选股结果漂移。因此这两类策略永远使用完整日线。
_PRUNE_EXCLUDED_MODULES = frozenset({
    "factor_combo_rebalance",
    "factor_ranked_event",
})


def _module_name(info: dict) -> str:
    """返回策略模块短名(用于 universe-prune 白名单判断)。"""
    return str(info.get("module", "")).rsplit(".", 1)[-1]


def _can_prune_universe(info: dict) -> bool:
    """该策略是否可在 feature 预计算前裁剪到自身 universe。"""
    return _module_name(info) not in _PRUNE_EXCLUDED_MODULES


def _task_universe_topk(info: dict) -> int:
    """策略实例实际使用的 universe_topk。

    库条目显式传 300 时以条目为准;未显式传时,所有规则策略类默认 500。
    """
    params = info.get("params") or {}
    raw = params.get("universe_topk", 500)
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 500


def _slice_daily_df(df: pd.DataFrame, today: pd.Timestamp, slice_days: int) -> pd.DataFrame:
    cutoff = today - pd.Timedelta(slice_days, unit="D")
    return df[df["trade_date"] >= cutoff].reset_index(drop=True)


def _universe_data_fp(daily_df: pd.DataFrame) -> str:
    """universe 缓存的内容指纹:日线日期范围/行数/股票数/close 总和。"""
    try:
        start = pd.Timestamp(daily_df["trade_date"].min()).normalize()
        end = pd.Timestamp(daily_df["trade_date"].max()).normalize()
        payload = (
            f"{start.date()}|{end.date()}|{len(daily_df)}|"
            f"{int(daily_df['stock_code'].nunique())}|{float(daily_df['close'].sum()):.6f}"
        )
    except Exception:
        payload = f"fallback|{len(daily_df)}"
    return hashlib.md5(payload.encode("utf-8")).hexdigest()[:12]  # nosec B324 - 本地缓存指纹,非安全哈希


def _universe_aux_fp() -> str:
    """universe 还依赖 ST 历史/上市日期表/交易日历缓存;任一变化则缓存失效。"""
    parts: list[str] = []
    for pattern in ("st_history_*.parquet", "all_codes.parquet", "trading_calendar.parquet"):
        files = sorted(config.CACHE_DIR.glob(pattern))
        if not files:
            parts.append(f"{pattern}:none")
            continue
        newest = max(f.stat().st_mtime for f in files)
        parts.append(
            f"{pattern}:{','.join(f'{f.name}:{f.stat().st_size}:{int(f.stat().st_mtime)}' for f in files[:8])}"
        )
        parts.append(f"{pattern}:maxmtime:{int(newest)}")
    raw = "|".join(parts)
    return hashlib.md5(raw.encode("utf-8")).hexdigest()[:12]  # nosec B324 - 本地缓存指纹,非安全哈希


def _universe_cache_signature(topks: list[int]) -> str:
    """价格/涨跌停等过滤参数也参与缓存命名,配置变化自动失效。"""
    parts = [
        f"price={DEFAULT_CONFIG.get('quant_price_min')}-{DEFAULT_CONFIG.get('quant_price_max')}",
        f"limit={bool(DEFAULT_CONFIG.get('quant_exclude_limit_up_down', True))}",
        f"topk={'-'.join(str(t) for t in sorted(topks))}",
    ]
    raw = "|".join(parts)
    return hashlib.md5(raw.encode("utf-8")).hexdigest()[:12]  # nosec B324 - 本地缓存指纹,非安全哈希


def _load_cached_universe_groups(
    daily_df: pd.DataFrame,
    today: pd.Timestamp,
    topks: list[int],
    cache_name: str,
) -> dict[int, list[str]] | None:
    """读取磁盘缓存的 universe 代码列表;不存在/内容不匹配返回 None。"""
    if not DEFAULT_CONFIG.get("quant_universe_cache", True):
        return None
    cache_dir = config.CACHE_DIR / "universe_cache"
    if not cache_dir.exists():
        return None
    data_fp = _universe_data_fp(daily_df)
    aux_fp = _universe_aux_fp()
    sig_fp = _universe_cache_signature(topks)
    cache_file = (
        cache_dir
        / f"{cache_name}_{today:%Y%m%d}_{data_fp}_{aux_fp}_{sig_fp}.json"
    )
    if not cache_file.exists():
        return None
    try:
        payload = json.loads(cache_file.read_text(encoding="utf-8"))
        groups = {int(k): [str(c) for c in v] for k, v in payload["groups"].items()}
        if not groups or set(groups) != set(topks):
            return None
        return groups
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        return None


def _save_universe_groups_cache(
    groups: dict[int, list[str]],
    daily_df: pd.DataFrame,
    today: pd.Timestamp,
    topks: list[int],
    cache_name: str,
) -> None:
    """写 universe 代码列表缓存,并清理同 cache+date 的旧指纹文件。"""
    if not DEFAULT_CONFIG.get("quant_universe_cache", True) or not groups:
        return
    cache_dir = config.CACHE_DIR / "universe_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    data_fp = _universe_data_fp(daily_df)
    aux_fp = _universe_aux_fp()
    sig_fp = _universe_cache_signature(topks)
    stem = f"{cache_name}_{today:%Y%m%d}_{data_fp}_{aux_fp}_{sig_fp}"
    cache_file = cache_dir / f"{stem}.json"
    tmp = cache_dir / f"{stem}.tmp"
    payload = {
        "trade_date": today.strftime("%Y-%m-%d"),
        "daily_cache": cache_name,
        "data_fp": data_fp,
        "aux_fp": aux_fp,
        "signature_fp": sig_fp,
        "groups": {str(k): v for k, v in groups.items()},
    }
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(cache_file)
    # 旧指纹文件已经是过期数据,清理避免目录无限增长
    for old in cache_dir.glob(f"{cache_name}_{today:%Y%m%d}_*.json"):
        if old.name != cache_file.name:
            old.unlink(missing_ok=True)


def _build_universe_groups(daily_df: pd.DataFrame, strategies: dict,
                           today: pd.Timestamp,
                           cache_name: str = "daily_main_board") -> dict[int, list[str]]:
    """为所有可裁剪任务预计算 universe 代码集合(主进程算一次,worker 复用)。

    与策略内 _resolve_universe 走同一个 filter_universe_topk:主板/历史 ST/
    上市天数/停牌/价格(默认 3-70 元)/当日涨跌停过滤都在排序前生效,
    再按 20 日均额取 top K。因此 universe-prune 不会绕过任何现有过滤。
    """
    from tradingagents.quant.data.universe import filter_universe_topk

    topks = sorted({
        _task_universe_topk(info)
        for info in strategies.values()
        if _can_prune_universe(info)
    })
    if not topks:
        return {}

    cached = _load_cached_universe_groups(daily_df, today, topks, cache_name)
    if cached is not None:
        return cached

    groups: dict[int, list[str]] = {}
    for topk in topks:
        codes = filter_universe_topk(daily_df, on_date=today, topk=topk)
        if codes:
            groups[topk] = codes
    _save_universe_groups_cache(groups, daily_df, today, topks, cache_name)
    return groups



# ============================================================
# Worker 进程全局变量
# ============================================================

_WORKER_DAILY_DF_FULL: pd.DataFrame | None = None
_WORKER_DAILY_DF_SLICED: pd.DataFrame | None = None
_WORKER_DAILY_GROUPS_FULL: dict[int, pd.DataFrame] = {}
_WORKER_DAILY_GROUPS_SLICED: dict[int, pd.DataFrame] = {}
_WORKER_TODAY: pd.Timestamp | None = None
_WORKER_TOP_K: int = DEFAULT_TOP_K


def _worker_init(daily_cache_name: str, today_str: str, top_k: int,
                 slice_days: int = 0, universe_groups: dict | None = None,
                 retain_full: bool = True, warm_features: bool = True):
    global _WORKER_DAILY_DF_FULL, _WORKER_DAILY_DF_SLICED
    global _WORKER_DAILY_GROUPS_FULL, _WORKER_DAILY_GROUPS_SLICED
    global _WORKER_TODAY, _WORKER_TOP_K

    full_df = cm.load(daily_cache_name)
    full_df["trade_date"] = pd.to_datetime(full_df["trade_date"]).dt.normalize()

    today_ts = pd.Timestamp(today_str)
    _WORKER_TODAY = today_ts
    _WORKER_TOP_K = top_k
    sys.stdout = io.StringIO()

    # Universe-prune 优化:主进程已算好每个任务 universe 的代码集合。
    # 双 Pool 模式下规则策略 worker 不需要保留全市场 DataFrame;
    # FC 因子 worker 则 retain_full=True,保留完整日线。
    _WORKER_DAILY_GROUPS_FULL = {}
    _WORKER_DAILY_GROUPS_SLICED = {}
    if universe_groups:
        for topk, codes in universe_groups.items():
            code_set = set(codes)
            subset = full_df[full_df["stock_code"].isin(code_set)]
            _WORKER_DAILY_GROUPS_FULL[int(topk)] = subset.sort_values(
                ["stock_code", "trade_date"]).reset_index(drop=True)
        if slice_days > 0:
            _WORKER_DAILY_GROUPS_SLICED = {
                topk: _slice_daily_df(subset, today_ts, slice_days)
                for topk, subset in _WORKER_DAILY_GROUPS_FULL.items()
            }

    if retain_full:
        _WORKER_DAILY_DF_FULL = full_df
        _WORKER_DAILY_DF_SLICED = (
            _slice_daily_df(full_df, today_ts, slice_days)
            if slice_days > 0 else full_df
        )
    else:
        # 规则策略 worker:裁剪完成后释放全市场 DataFrame,降低峰值内存
        _WORKER_DAILY_DF_FULL = None
        _WORKER_DAILY_DF_SLICED = None
        del full_df

    # 方案 A:尝试 load 预计算的 weekly_bars / monthly_bars parquet
    # 命中后填进 _WEEKLY_BARS_CACHE / _MONTHLY_BARS_CACHE
    # 策略调 get_weekly_bars / get_monthly_bars 时跳过 resample,省 ~35s
    if _WORKER_DAILY_DF_FULL is not None:
        try:
            from tradingagents.quant.features.pipeline import (
                _MONTHLY_BARS_CACHE,
                _WEEKLY_BARS_CACHE,
                _resampled_cache_key,
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
                    print("[worker] precomputed weekly/monthly bars loaded", flush=True)
        except Exception as exc:
            print(f"[worker] precomputed bars skipped: {exc}", flush=True)

    # 预热特征缓存。启用 universe-prune 或双 Pool 模式时跳过全市场预热:
    # 裁剪后的每个策略第一次 build 通常只需 3-6s,而全市场预热要 60-90s
    # 且每个 worker 多占用 ~2.9GB 峰值内存(day/weekly/monthly 特征矩阵)。
    if universe_groups or not warm_features:
        print("[worker] skipping full-market feature warm-up", flush=True)
        return

    try:
        from tradingagents.quant.features.pipeline import (
            build_features_vectorized,
            build_monthly_features,
            build_weekly_features,
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
    global _WORKER_DAILY_GROUPS_FULL, _WORKER_DAILY_GROUPS_SLICED
    global _WORKER_TODAY, _WORKER_TOP_K

    # Universe-prune 数据选择:
    # - 可裁剪的规则策略 -> 使用预先裁剪好的 universe DataFrame
    # - FC 因子策略 / 未启用裁剪 -> 使用完整 DataFrame
    if _WORKER_DAILY_GROUPS_FULL and _can_prune_universe(info):
        topk = _task_universe_topk(info)
        if needs_full:
            daily_df = _WORKER_DAILY_GROUPS_FULL[topk]
        elif _WORKER_DAILY_GROUPS_SLICED:
            daily_df = _WORKER_DAILY_GROUPS_SLICED[topk]
        else:
            daily_df = _WORKER_DAILY_GROUPS_FULL[topk]
    else:
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
            # 999=信号出场策略不参与持仓天数加权;全为 999 时保留 999 语义
            hd = g["holding_days"].fillna(5).astype(float)
            fixed = g.assign(_hd=hd)["_hd"]
            fixed = fixed[fixed < SIGNAL_EXIT_DAYS]
            if len(fixed) > 0:
                w = g.loc[fixed.index, "strategy_comp"]
                weighted_hd = float((fixed * w).sum() / w.sum())
            else:
                weighted_hd = float(SIGNAL_EXIT_DAYS)
        else:
            weighted_wr = float(g["win_rate"].mean()) if "win_rate" in g.columns else 0.0
            hd = g["holding_days"].fillna(5).astype(float)
            fixed = hd[hd < SIGNAL_EXIT_DAYS]
            weighted_hd = float(fixed.mean()) if len(fixed) > 0 else float(SIGNAL_EXIT_DAYS)
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
    daily_cache_name: str = "daily_main_board",
    top_k: int = 2,
    n_workers: int = 8,
    slice_days: int = 0,
    strategies: dict | None = None,
    top_n: int = 20,
    progress_callback: Callable[[int, int, dict], None] | None = None,
    stop_check: Callable[[], bool] | None = None,
    prune_universe: bool | None = None,
) -> dict:
    """跑策略库,产出 Top N 候选 + 全量信号记录 + 每策略统计。

    Args:
        today: 选股日期(收盘后)
        daily_cache_name: 日线数据缓存文件名(不含 .parquet 后缀)
        top_k: 每个策略返回的前 N 只股票
        n_workers: 并行 worker 数(Windows 必须 spawn,worker 启动慢)
        slice_days: 切片天数(0=全量,短线策略可用切片加速,周月季策略自动用全量)
        strategies: 策略字典,None 时用 get_all_strategies_final()(18 个终态策略)
        top_n: 返回 Top N 候选(默认 20)
        progress_callback: 进度回调 fn(completed, total, latest_result_dict)
        prune_universe: None 时读 DEFAULT_CONFIG["quant_universe_prune"]。
            开启后 worker 对规则策略只计算其 universe(top 300/500)的特征,
            跳过全市场 60-90s 预热;FC 因子策略保持完整 universe。

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

    # 主进程一次性计算各任务 universe 代码集合,spawn 后以 initargs 传给 worker。
    # 8 个 worker 无需各自重复做 6-7s 的 filter_universe_topk。
    universe_groups: dict | None = None
    prune_enabled = (
        DEFAULT_CONFIG.get("quant_universe_prune", True)
        if prune_universe is None
        else bool(prune_universe)
    )
    if prune_enabled and strategies:
        daily_for_universe = None
        try:
            daily_for_universe = cm.load(daily_cache_name)
            daily_for_universe["trade_date"] = pd.to_datetime(
                daily_for_universe["trade_date"]).dt.normalize()
            universe_groups = _build_universe_groups(
                daily_for_universe, strategies, today,
                cache_name=daily_cache_name)
            if not universe_groups:
                universe_groups = None
        except Exception as exc:
            # ST 历史缓存缺失 / 日历拉取失败等场景不要阻断整个 pick();
            # 回退到老的全市场路径(策略内部会给出同样的错误)。
            print(
                f"[quant_picker] universe-prune skipped: "
                f"{type(exc).__name__}: {exc}",
                flush=True,
            )
            universe_groups = None
        finally:
            del daily_for_universe

    def _record_result(result: dict, index: int) -> None:
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
            progress_callback(index, n_total, result)

    def _run_pool(pool_tasks: list[tuple], *, workers: int,
                  init_universe_groups: dict | None,
                  retain_full: bool, warm_features: bool,
                  start_index: int) -> tuple[int, bool]:
        """顺序执行一个任务池,返回 (下一个进度序号, 是否被 stop 中断)。"""
        index = start_index
        with ctx.Pool(
            processes=workers,
            initializer=_worker_init,
            initargs=(
                daily_cache_name, today_str, top_k, slice_days,
                init_universe_groups, retain_full, warm_features,
            ),
        ) as pool:
            for result in pool.imap_unordered(_worker_run, pool_tasks):
                # stop_check: 让 web UI 的"停止"按钮在当前策略完成后立即生效,
                # 而不是等所有策略跑完。break 后 Pool __exit__ 会 pool.terminate()
                # 立即结束剩余 worker。
                if stop_check is not None and stop_check():
                    return index, True
                index += 1
                _record_result(result, index)
        return index, False

    # 双 Pool:规则策略只保留裁剪后的 universe DataFrame;FC 因子策略单独
    # 一个全市场 Pool。相比旧单 Pool,8 个 worker 不再全部保留完整日线。
    pruned_tasks = [t for t in tasks if _can_prune_universe(t[1])]
    full_tasks = [t for t in tasks if not _can_prune_universe(t[1])]

    if universe_groups and pruned_tasks and full_tasks:
        # 1) 规则策略池:worker 裁剪后释放全市场 DataFrame
        next_index, stopped = _run_pool(
            pruned_tasks,
            workers=max(1, min(n_workers, len(pruned_tasks))),
            init_universe_groups=universe_groups,
            retain_full=False,
            warm_features=False,
            start_index=0,
        )
        # 2) FC 因子策略池:完整日线,worker 数不超过任务数
        if not stopped and not (stop_check is not None and stop_check()):
            next_index, _ = _run_pool(
                full_tasks,
                workers=max(1, min(n_workers, len(full_tasks))),
                init_universe_groups=None,
                retain_full=True,
                warm_features=False,
                start_index=next_index,
            )
    else:
        _run_pool(
            tasks,
            workers=n_workers,
            init_universe_groups=universe_groups,
            retain_full=True,
            warm_features=True,
            start_index=0,
        )

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
        wr_raw = row.get("avg_win_rate", 0)
        wr = wr_raw * 100 if pd.notna(wr_raw) else 0.0
        hd_raw = row.get("avg_holding_days", 0)
        hd = hd_raw if pd.notna(hd_raw) else 0.0
        hd_str = "信号出场" if hd >= SIGNAL_EXIT_DAYS else f"{hd:.1f}"
        lines.append(
            f"{i+1:<4}{code:<10}{name:<12}"
            f"{int(row['n_strategies']):<6}{row['weighted_score']:<8.2f}"
            f"{wr:<8.1f}{hd_str:<6}"
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
            hd = r["holding_days"]
            hd_str = "信号出场" if hd is not None and hd >= SIGNAL_EXIT_DAYS else f"{hd}d"
            lines.append(
                f"  - [{r['tier']}] {r['strategy']} (comp={r['strategy_comp']:.2f}, "
                f"胜率={r['win_rate']*100:.0f}%, 持仓={hd_str})"
            )
        if len(recs) > 5:
            lines.append(f"  ... 还有 {len(recs) - 5} 个策略")

    return "\n".join(lines)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="量化策略选股(top18 终态库:S=5/A=11/B=2)")
    parser.add_argument("--today", default=None, help="选股日期 YYYY-MM-DD,默认今天")
    parser.add_argument("--cache", default="daily_main_board", help="日线缓存文件名")
    parser.add_argument("--top-k", type=int, default=2, help="每策略返回前 N 只")
    parser.add_argument("--top-n", type=int, default=20, help="最终 Top N(5/10/20)")
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
