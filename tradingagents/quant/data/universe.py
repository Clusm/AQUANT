"""股票池构建:主板过滤 + 历史 ST + 流动性 + 新股 + 停牌。

修复:
- B1: ST 状态用历史推断(fetch_st_history),不再用当前快照(前视)
- B2: filter_universe_topk 基于历史成交额排序选 top K,去幸存者偏差
- B4: 月度快照用上月最后一个交易日,确保无前视
"""
from __future__ import annotations

import pandas as pd

from tradingagents.quant.config import (CACHE_DIR, UNIVERSE_LIQUIDITY_TURNOVER, UNIVERSE_MIN_LISTING_DAYS)
from tradingagents.quant.data import cache as cache_mod
from tradingagents.quant.utils.trading_calendar import get_calendar


SH_MAIN_PREFIX = "60"
SZ_MAIN_PREFIX = "00"


def is_main_board(code: str) -> bool:
    return code.startswith(SH_MAIN_PREFIX) or code.startswith(SZ_MAIN_PREFIX)


def _lazy_fetcher():
    """延迟导入 fetcher(依赖 akshare/adata,实时选股场景可能不需要)。"""
    from tradingagents.quant.data import fetcher
    return fetcher


def get_main_board_codes() -> list[str]:
    """获取全部主板 A 股代码(60xxxx + 00xxxx)。"""
    df = _lazy_fetcher().fetch_all_stock_codes()
    return [c for c in df["stock_code"].tolist() if is_main_board(c)]


def get_st_codes() -> set[str]:
    """当前 ST/*ST 股票代码集合(快照,有前视)。

    已废弃:回测应使用 get_st_codes_on_date。
    保留仅为兼容性(如实时选股场景)。
    """
    df = _lazy_fetcher().fetch_st_stocks()
    if len(df) == 0:
        return set()
    return set(df["stock_code"].tolist())


_st_history_cache: dict[tuple[str | None, str | None], pd.DataFrame] = {}


def load_st_history(start_date: str | None = None, end_date: str | None = None) -> pd.DataFrame:
    """加载历史 ST 状态(带内存缓存,按 (start, end) 键)。

    优先用传入的 start/end 找缓存文件;失败则扫描所有 st_history_* 缓存。
    缓存按日期范围键区分,避免同进程内不同 range 的调用串用首份数据。
    """
    key = (start_date, end_date)
    if key in _st_history_cache:
        return _st_history_cache[key]

    candidates = []
    if start_date and end_date:
        candidates.append(f"st_history_{start_date}_{end_date}")
    # 扫描所有 st_history 缓存
    for f in CACHE_DIR.glob("st_history_*.parquet"):
        candidates.append(f.stem)

    for name in candidates:
        if cache_mod.exists(name):
            df = cache_mod.load(name)
            _st_history_cache[key] = df
            return df

    df = pd.DataFrame()
    _st_history_cache[key] = df
    return df


def get_st_codes_on_date(date: pd.Timestamp,
                         st_history: pd.DataFrame | None = None) -> set[str]:
    """B1: 获取指定日期的 ST 股票集合(基于历史推断)。

    st_history: fetch_st_history 返回的 DataFrame。若为 None,尝试加载缓存。
    若缓存不存在或为空,raise RuntimeError--不允许回退到当前 ST 快照(会引入
    前视偏差:用今天的 ST 列表过滤历史日期,导致幸存者偏差)。

    历史某日若无记录,用 <= date 的最近一次记录(同股票 ST 状态变化不频繁)。
    """
    date = pd.Timestamp(date).normalize()
    if st_history is None:
        st_history = load_st_history()

    if len(st_history) == 0:
        raise RuntimeError(
            f"ST 历史缓存为空,无法判断 {date.date()} 的 ST 状态。"
            "请先运行 python -m tradingagents.quant.data_update.fetch_st_history "
            "拉取 ST 历史数据,否则会引入前视偏差(用当前 ST 列表过滤历史日期)。"
        )

    on_day = st_history[st_history["trade_date"] == date]
    if len(on_day) == 0:
        # 该日无记录,用最近的过去记录
        past = st_history[st_history["trade_date"] <= date]
        if len(past) == 0:
            return set()
        latest_date = past["trade_date"].max()
        on_day = st_history[st_history["trade_date"] == latest_date]

    return set(on_day.loc[on_day["is_st"] == 1, "stock_code"].tolist())


def get_list_dates() -> dict[str, pd.Timestamp]:
    """获取股票上市日期(带模块级缓存)。"""
    global _list_dates_cache
    if _list_dates_cache is not None:
        return _list_dates_cache
    df = _lazy_fetcher().fetch_all_stock_codes()
    _list_dates_cache = {row["stock_code"]: row["list_date"] for _, row in df.iterrows()
                         if pd.notna(row["list_date"])}
    return _list_dates_cache


_list_dates_cache: dict[str, pd.Timestamp] | None = None


def filter_universe(daily_df: pd.DataFrame, *,
                    on_date: pd.Timestamp | None = None,
                    liquidity_threshold: float = UNIVERSE_LIQUIDITY_TURNOVER,
                    liquidity_window: int = 20,
                    min_listing_days: int = UNIVERSE_MIN_LISTING_DAYS,
                    suspended_window: int = 5,
                    st_history: pd.DataFrame | None = None) -> list[str]:
    """根据日线数据过滤股票池(基于历史信息,无前视)。

    过滤条件:
    1. 主板(60/00)
    2. 非 ST(用历史 ST 状态,B1)
    3. 过去 liquidity_window 日均成交额 >= liquidity_threshold
    4. 上市 >= min_listing_days 个交易日(用 on_date 之前的全量数据计数)
    5. 最近 suspended_window 日有成交(未停牌)
    """
    if on_date is None:
        on_date = daily_df["trade_date"].max()
    else:
        on_date = pd.Timestamp(on_date).normalize()

    st_codes = get_st_codes_on_date(on_date, st_history)
    list_dates = get_list_dates()

    window_start = on_date - pd.Timedelta(days=liquidity_window * 2)
    df_window = daily_df[(daily_df["trade_date"] <= on_date) &
                         (daily_df["trade_date"] >= window_start)].copy()

    full_history_counts = daily_df[daily_df["trade_date"] <= on_date].groupby("stock_code").size()

    eligible: list[str] = []
    for code, grp in df_window.groupby("stock_code"):
        if not is_main_board(code):
            continue
        if code in st_codes:
            continue

        grp = grp.sort_values("trade_date")
        if len(grp) == 0:
            continue

        recent = grp.tail(liquidity_window)
        if len(recent) < liquidity_window:
            continue

        if recent["amount"].mean() < liquidity_threshold:
            continue

        last_date = grp["trade_date"].max()
        days_since_last = (on_date - last_date).days
        if days_since_last > suspended_window:
            continue

        list_date = list_dates.get(code)
        if list_date is None or pd.isna(list_date):
            continue

        trading_days_listed = int(full_history_counts.get(code, 0))
        if trading_days_listed < min_listing_days:
            continue

        eligible.append(code)

    return eligible


def filter_universe_topk(daily_df: pd.DataFrame, *,
                         on_date: pd.Timestamp,
                         topk: int | None = 500,
                         percentile: float | None = None,
                         liquidity_window: int = 20,
                         min_listing_days: int = UNIVERSE_MIN_LISTING_DAYS,
                         suspended_window: int = 5,
                         price_min: float | None = None,
                         price_max: float | None = None,
                         st_history: pd.DataFrame | None = None) -> list[str]:
    """B2: 基于历史成交额排序选 top K 股票池(去幸存者偏差)。

    流程:
    1. 主板过滤
    2. 历史 ST 过滤(B1)
    3. 上市天数、停牌过滤
    4. 价格过滤(price_min/price_max,默认从 default_config 读)
    5. 按过去 liquidity_window 日均成交额排序,取 top K / percentile

    与 filter_universe 的区别:不设最低成交额阈值,而是按 top K 截断。
    这样在回测期内,股票池会随市场变化(某月 top 500 与下月不同)。

    优先级(高 -> 低):
    - 显式传 percentile -> 用 percentile
    - 显式传 topk(非 None) -> 用 topk
    - 都不传 -> 从 default_config 读 quant_liquidity_percentile(默认 0.8)

    price_min/price_max:股价过滤边界。None 时从 default_config 读
    quant_price_min/quant_price_max(默认 3.0/120.0)。
    价格过滤在流动性排序之前应用,避免低价/高价股占用 top K 名额。
    """
    if price_min is None or price_max is None:
        from tradingagents.default_config import DEFAULT_CONFIG
        _cfg = DEFAULT_CONFIG
        if price_min is None:
            price_min = _cfg.get("quant_price_min")
        if price_max is None:
            price_max = _cfg.get("quant_price_max")

    on_date = pd.Timestamp(on_date).normalize()
    cache_key = (on_date, topk, percentile, liquidity_window, min_listing_days,
                 suspended_window, price_min, price_max)
    if cache_key in _universe_topk_cache:
        return _universe_topk_cache[cache_key]

    st_codes = get_st_codes_on_date(on_date, st_history)
    list_dates = get_list_dates()
    calendar = _get_calendar()

    window_start = on_date - pd.Timedelta(days=liquidity_window * 2)
    df_window = daily_df[(daily_df["trade_date"] <= on_date) &
                         (daily_df["trade_date"] >= window_start)].copy()
    # 显式排序,确保后续 grp.tail() / grp.iloc[-1] 拿到的是最近一天
    df_window = df_window.sort_values(["stock_code", "trade_date"]).reset_index(drop=True)

    first_dates_per_stock = _get_first_dates_per_stock(daily_df)
    on_date_idx = calendar.searchsorted(on_date, side="right")

    candidates: list[tuple[str, float]] = []
    for code, grp in df_window.groupby("stock_code", sort=False):
        if not is_main_board(code):
            continue
        if code in st_codes:
            continue

        if len(grp) == 0:
            continue

        if len(grp) < liquidity_window:
            continue
        recent = grp.tail(liquidity_window)

        last_date = grp["trade_date"].iloc[-1]
        days_since_last = (on_date - last_date).days
        if days_since_last > suspended_window:
            continue

        first_date = first_dates_per_stock.get(code)
        if first_date is None:
            list_date = list_dates.get(code)
            if list_date is None or pd.isna(list_date):
                continue
            first_date = pd.Timestamp(list_date).normalize()
        first_idx = calendar.searchsorted(first_date, side="left")
        trading_days_listed = on_date_idx - first_idx
        if trading_days_listed < min_listing_days:
            continue

        if price_min is not None or price_max is not None:
            last_close = float(recent["close"].iloc[-1])
            if price_min is not None and last_close < price_min:
                continue
            if price_max is not None and last_close > price_max:
                continue

        avg_amount = float(recent["amount"].mean())
        candidates.append((code, avg_amount))

    candidates.sort(key=lambda x: x[1], reverse=True)
    if percentile is not None:
        if not 0 < percentile <= 1:
            raise ValueError(f"percentile 必须在 (0, 1] 之间,收到 {percentile}")
        keep_n = max(1, int(len(candidates) * percentile))
        result = [c for c, _ in candidates[:keep_n]]
    elif topk is not None:
        keep_n = topk
        result = [c for c, _ in candidates[:keep_n]]
    else:
        from tradingagents.default_config import DEFAULT_CONFIG
        _cfg_pct = DEFAULT_CONFIG.get("quant_liquidity_percentile", 0.8)
        keep_n = max(1, int(len(candidates) * _cfg_pct))
        result = [c for c, _ in candidates[:keep_n]]
    _universe_topk_cache[cache_key] = result
    return result


_universe_topk_cache: dict = {}
_first_dates_cache: dict[tuple, pd.Series] = {}
_calendar_cache: pd.DatetimeIndex | None = None


def _get_calendar() -> pd.DatetimeIndex:
    global _calendar_cache
    if _calendar_cache is None:
        _calendar_cache = get_calendar()
    return _calendar_cache


def _get_first_dates_per_stock(daily_df: pd.DataFrame) -> dict:
    """获取每股在 daily_df 中的首个交易日(带缓存)。"""
    key = (id(daily_df), len(daily_df),
           daily_df["trade_date"].min(), daily_df["trade_date"].max())
    if key in _first_dates_cache:
        return _first_dates_cache[key]
    s = daily_df.groupby("stock_code")["trade_date"].min()
    result = {code: pd.Timestamp(d).normalize() for code, d in s.items()}
    _first_dates_cache[key] = result
    return result


def build_monthly_universe(daily_df: pd.DataFrame,
                           start_date: str, end_date: str) -> dict[pd.Timestamp, list[str]]:
    """构建月度股票池快照(每月首个交易日)。

    返回 {上月最后交易日: [股票代码列表]}。
    B4: 快照日取上月最后一个交易日,确保无前视。
    """
    st = pd.Timestamp(start_date)
    en = pd.Timestamp(end_date)
    daily_df = daily_df[(daily_df["trade_date"] >= st) & (daily_df["trade_date"] <= en)].copy()

    months = sorted({(d.year, d.month) for d in daily_df["trade_date"]})
    snapshots: dict[pd.Timestamp, list[str]] = {}
    for y, m in months:
        month_start = pd.Timestamp(year=y, month=m, day=1)
        month_data = daily_df[daily_df["trade_date"] < month_start]
        if len(month_data) == 0:
            continue
        snapshot_date = month_data["trade_date"].max()
        codes = filter_universe(daily_df, on_date=snapshot_date)
        snapshots[snapshot_date] = codes
        print(f"  {snapshot_date.date()}: {len(codes)} 只")

    return snapshots
