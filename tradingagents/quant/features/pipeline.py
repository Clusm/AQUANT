"""特征管道:统一计算指标 + 因子,供规则和 ML 策略复用。"""
from __future__ import annotations

import numpy as np
import pandas as pd

from tradingagents.quant.features.factors import amount_zscore, consecutive_limit_up, limit_distance, turnover_zscore
from tradingagents.quant.features.indicators import add_all_indicators

FEATURE_COLUMNS = [
    "ma5", "ma10", "ma20", "ma60",
    "ma_alignment", "above_ma20", "above_ma5",
    "macd_dif", "macd_dea", "macd_hist", "macd_golden_cross",
    "rsi_6", "rsi_14",
    "boll_pct_b",
    "atr_pct",
    "ret_1d", "ret_5d", "ret_10d", "ret_20d",
    "new_high_20", "new_high_60", "pullback_ma20",
    "gap", "volume_ratio_5", "volume_ratio_10",
    "consecutive_lu", "limit_dist",
    "turnover_zscore_20", "amount_zscore_20",
    "close_to_ma5", "close_to_ma20", "close_to_ma60",
    "circ_market_cap", "mcap_score",
]


WEEKLY_FEATURE_COLUMNS = [
    "week_key", "wma5", "wma10", "wma20",
    "weekly_above_ma5", "weekly_bullish", "weekly_ret_N", "weekly_up",
    "wema12", "wema26", "wmacd", "wsignal", "wmacd_hist",
    "wmacd_golden_cross", "wmacd_gc_recent",
    "wrsi_14", "wcci_20",
]


# 特征依赖:请求某列时,先计算其依赖列。
FEATURE_DEPENDENCIES: dict[str, set[str]] = {
    "ma_alignment": {"ma5", "ma10", "ma20"},
    "above_ma20": {"ma20"},
    "above_ma5": {"ma5"},
    "macd_golden_cross": {"macd_dif", "macd_dea"},
    "macd_hist": {"macd_dif", "macd_dea"},
    "boll_pct_b": {"boll_upper", "boll_mid", "boll_lower"},
    "atr_pct": {"atr_14"},
    "pullback_ma20": {"ma20"},
    "close_to_ma5": {"ma5"},
    "close_to_ma20": {"ma20"},
    "close_to_ma60": {"ma60"},
    "mcap_score": {"circ_market_cap"},
}


def resolve_feature_columns(requested: set[str]) -> set[str]:
    """递归展开特征依赖,返回实际需要计算的列集合。"""
    needed = set(requested)
    changed = True
    while changed:
        changed = False
        for col in list(needed):
            for dep in FEATURE_DEPENDENCIES.get(col, ()):
                if dep not in needed:
                    needed.add(dep)
                    changed = True
    return needed


MONTHLY_FEATURE_COLUMNS = [
    "month_key", "mma3", "mma6",
    "monthly_above_ma3", "monthly_bullish", "monthly_ret_N", "monthly_up",
    "mema12", "mema26", "mmacd", "msignal", "mmacd_hist",
    "mmacd_golden_cross", "mmacd_gc_recent",
    "mrsi_14", "mcci_20",
]


def build_features_for_stock(df: pd.DataFrame) -> pd.DataFrame:
    """对单只股票的日线 df 计算全部特征。返回含 stock_code, trade_date + 特征列。

    方案 B:优先查 _BFV_CACHE。如果传入的 df 有 stock_code 列,且缓存里有包含该 code
    的全市场特征矩阵,直接切片返回,避免重复计算。worker_init 预热 build_features_vectorized
    后,任何走单只循环路径的策略(fallback 或未来新策略)都能命中缓存。
    """
    if len(df) < 30:
        return pd.DataFrame()

    if "stock_code" in df.columns and _BFV_CACHE:
        codes_in_df = df["stock_code"].unique()
        if len(codes_in_df) == 1:
            target_code = codes_in_df[0]
            for cache_key, codes_set in _BFV_CACHE_CODES.items():
                if target_code in codes_set:
                    cached_df = _BFV_CACHE[cache_key]
                    return cached_df[cached_df["stock_code"] == target_code].copy()

    out = add_all_indicators(df)

    out["consecutive_lu"] = consecutive_limit_up(out["close"])
    out["limit_dist"] = limit_distance(out["close"])

    # B3: 流通市值从 amount / turnover_ratio 估算(不依赖 outstanding_share)
    # turnover_ratio 是小数(如 0.05 = 5%),circ_market_cap = amount / turnover_ratio
    if "turnover_ratio" in out.columns and "amount" in out.columns:
        tr = pd.to_numeric(out["turnover_ratio"], errors="coerce").replace(0, np.nan)
        out["turnover_zscore_20"] = turnover_zscore(tr, 20) if tr.notna().sum() > 20 else 0.0
        out["amount_zscore_20"] = amount_zscore(out["amount"], 20)
        out["circ_market_cap"] = out["amount"] / tr
        out["mcap_score"] = -out["circ_market_cap"]
    else:
        out["turnover_zscore_20"] = 0.0
        out["amount_zscore_20"] = amount_zscore(out["amount"], 20) if "amount" in out.columns else 0.0
        out["circ_market_cap"] = np.nan
        out["mcap_score"] = np.nan

    out["close_to_ma5"] = (out["close"] - out["ma5"]) / out["ma5"].replace(0, np.nan)
    out["close_to_ma20"] = (out["close"] - out["ma20"]) / out["ma20"].replace(0, np.nan)
    out["close_to_ma60"] = (out["close"] - out["ma60"]) / out["ma60"].replace(0, np.nan)

    return out


_BFV_CACHE: dict = {}
_BFV_CACHE_CODES: dict[tuple, set[str]] = {}


def _make_cache_key(df: pd.DataFrame, min_rows: int,
                    columns: set[str] | None = None) -> tuple:
    """内容指纹: 基于数据特征而非对象 id()。

    M6: 加 close 求和作为轻量内容指纹,避免相同行数/日期范围但内容不同的数据误命中缓存。
    列子集也进入 key,避免全量特征缓存被按需子集误命中。
    """
    cols_key = "ALL" if columns is None else tuple(sorted(columns))
    try:
        return (len(df), min_rows, df['stock_code'].nunique(),
                df['trade_date'].min(), df['trade_date'].max(),
                float(df['close'].sum()), cols_key)
    except Exception:
        return (id(df), min_rows, cols_key)


def build_features_vectorized(
    daily_df: pd.DataFrame,
    min_rows: int = 30,
    columns: list[str] | None = None,
) -> pd.DataFrame:
    """向量化计算全部股票的特征。

    columns=None 时计算全部 FEATURE_COLUMNS;传入子集时按依赖图只计算需要的列。
    基础行情列(stock_code/trade_date/OHLCV/amount 等)始终保留。

    要求:daily_df 已按 (stock_code, trade_date) 排序。
    返回:过滤 < min_rows 行股票后的特征 DataFrame。
    """
    requested = set(FEATURE_COLUMNS if columns is None else columns)
    needed = resolve_feature_columns(requested)

    def wants(*names: str) -> bool:
        return any(n in needed for n in names)

    cache_key = _make_cache_key(daily_df, min_rows, requested)
    if cache_key in _BFV_CACHE:
        return _BFV_CACHE[cache_key].copy()

    if len(daily_df) == 0:
        return pd.DataFrame()

    # 过滤行数不足的股票(与 build_features_for_stock 的 len < 30 检查等价)
    counts = daily_df.groupby("stock_code", observed=True).size()
    valid_codes = counts[counts >= min_rows].index
    df = daily_df[daily_df["stock_code"].isin(valid_codes)].copy()
    if len(df) == 0:
        return pd.DataFrame()

    out = df
    g = out.groupby("stock_code", observed=True)
    c = out["close"]

    # ---- MA(rolling mean)----
    if wants("ma5"):
        out["ma5"] = g["close"].transform(lambda s: s.rolling(5, min_periods=5).mean())
    if wants("ma10"):
        out["ma10"] = g["close"].transform(lambda s: s.rolling(10, min_periods=10).mean())
    if wants("ma20"):
        out["ma20"] = g["close"].transform(lambda s: s.rolling(20, min_periods=20).mean())
    if wants("ma60"):
        out["ma60"] = g["close"].transform(lambda s: s.rolling(60, min_periods=60).mean())

    # ---- MA 多头排列 / 均线上下方 ----
    if wants("ma_alignment"):
        out["ma_alignment"] = ((out["ma5"] > out["ma10"]) & (out["ma10"] > out["ma20"])).astype(float)
    if wants("above_ma20"):
        out["above_ma20"] = (c > out["ma20"]).astype(float)
    if wants("above_ma5"):
        out["above_ma5"] = (c > out["ma5"]).astype(float)

    # ---- MACD(ewm)----
    if wants("macd_dif", "macd_dea", "macd_hist", "macd_golden_cross"):
        ema_fast = g["close"].transform(lambda s: s.ewm(span=12, adjust=False).mean())
        ema_slow = g["close"].transform(lambda s: s.ewm(span=26, adjust=False).mean())
        dif = ema_fast - ema_slow
        dea = dif.groupby(out["stock_code"], observed=True).transform(
            lambda s: s.ewm(span=9, adjust=False).mean())
        if wants("macd_dif"):
            out["macd_dif"] = dif
        if wants("macd_dea"):
            out["macd_dea"] = dea
        if wants("macd_hist"):
            out["macd_hist"] = (dif - dea) * 2
        if wants("macd_golden_cross"):
            out["macd_golden_cross"] = (
                (dif > dea) & (dif.groupby(out["stock_code"], observed=True).shift(1)
                               <= dea.groupby(out["stock_code"], observed=True).shift(1))
            ).astype(float)

    # ---- RSI(Wilder ewm)----
    def _rsi_transform(s, n):
        delta = s.diff()
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)
        avg_gain = gain.ewm(alpha=1 / n, adjust=False).mean()
        avg_loss = loss.ewm(alpha=1 / n, adjust=False).mean()
        rs = avg_gain / avg_loss.replace(0, np.nan)
        return 100 - 100 / (1 + rs)

    if wants("rsi_14"):
        out["rsi_14"] = g["close"].transform(lambda s: _rsi_transform(s, 14))
    if wants("rsi_6"):
        out["rsi_6"] = g["close"].transform(lambda s: _rsi_transform(s, 6))

    # ---- Bollinger ----
    if wants("boll_upper", "boll_mid", "boll_lower", "boll_pct_b"):
        boll_mid = g["close"].transform(lambda s: s.rolling(20, min_periods=20).mean())
        boll_std = g["close"].transform(lambda s: s.rolling(20, min_periods=20).std())
        boll_upper = boll_mid + 2 * boll_std
        boll_lower = boll_mid - 2 * boll_std
        if wants("boll_upper"):
            out["boll_upper"] = boll_upper
        if wants("boll_mid"):
            out["boll_mid"] = boll_mid
        if wants("boll_lower"):
            out["boll_lower"] = boll_lower
        if wants("boll_pct_b"):
            out["boll_pct_b"] = (c - boll_lower) / (boll_upper - boll_lower).replace(0, np.nan)

    # ---- ATR(ewm on TR)----
    if wants("atr_14", "atr_pct"):
        prev_close = g["close"].shift(1)
        tr = pd.concat([
            (out["high"] - out["low"]).abs(),
            (out["high"] - prev_close).abs(),
            (out["low"] - prev_close).abs(),
        ], axis=1).max(axis=1)
        atr_14 = tr.groupby(out["stock_code"], observed=True).transform(
            lambda s: s.ewm(alpha=1 / 14, adjust=False).mean())
        if wants("atr_14"):
            out["atr_14"] = atr_14
        if wants("atr_pct"):
            out["atr_pct"] = atr_14 / c

    # ---- Returns ----
    if wants("ret_1d"):
        out["ret_1d"] = g["close"].transform(lambda s: s.pct_change(1))
    if wants("ret_5d"):
        out["ret_5d"] = g["close"].transform(lambda s: s.pct_change(5))
    if wants("ret_10d"):
        out["ret_10d"] = g["close"].transform(lambda s: s.pct_change(10))
    if wants("ret_20d"):
        out["ret_20d"] = g["close"].transform(lambda s: s.pct_change(20))

    # ---- N 日新高 ----
    if wants("new_high_20"):
        out["new_high_20"] = (
            c > g["close"].transform(lambda s: s.rolling(20, min_periods=20).max().shift(1))
        ).astype(float)
    if wants("new_high_60"):
        out["new_high_60"] = (
            c > g["close"].transform(lambda s: s.rolling(60, min_periods=60).max().shift(1))
        ).astype(float)

    # ---- 回踩 MA20 ----
    if wants("pullback_ma20"):
        out["pullback_ma20"] = ((c - out["ma20"]).abs() / out["ma20"].replace(0, np.nan) < 0.02).astype(float)

    # ---- 缺口 ----
    if wants("gap"):
        prev_close = g["close"].shift(1)
        out["gap"] = out["open"] / prev_close - 1

    # ---- 量比 ----
    if wants("volume_ratio_5"):
        out["volume_ratio_5"] = out["volume"] / g["volume"].transform(
            lambda s: s.rolling(5, min_periods=5).mean().shift(1)).replace(0, np.nan)
    if wants("volume_ratio_10"):
        out["volume_ratio_10"] = out["volume"] / g["volume"].transform(
            lambda s: s.rolling(10, min_periods=10).mean().shift(1)).replace(0, np.nan)

    # ---- 连板数 + 距涨停距离(groupby.transform 调原函数)----
    if wants("consecutive_lu"):
        out["consecutive_lu"] = g["close"].transform(consecutive_limit_up)
    if wants("limit_dist"):
        out["limit_dist"] = g["close"].transform(limit_distance)

    # ---- 换手率/成交额 z-score + 流通市值 ----
    if wants("turnover_zscore_20", "amount_zscore_20", "circ_market_cap", "mcap_score"):
        if "turnover_ratio" in out.columns and "amount" in out.columns:
            tr = pd.to_numeric(out["turnover_ratio"], errors="coerce").replace(0, np.nan)
            if wants("turnover_zscore_20"):
                tr_valid = tr.groupby(out["stock_code"], observed=True).transform(lambda s: s.notna().sum())
                out["turnover_zscore_20"] = tr.groupby(out["stock_code"], observed=True).transform(
                    lambda s: turnover_zscore(s, 20))
                out.loc[tr_valid <= 20, "turnover_zscore_20"] = 0.0
            if wants("amount_zscore_20"):
                out["amount_zscore_20"] = out.groupby("stock_code", observed=True)["amount"].transform(
                    lambda s: amount_zscore(s, 20))
            if wants("circ_market_cap", "mcap_score"):
                out["circ_market_cap"] = out["amount"] / tr
            if wants("mcap_score"):
                out["mcap_score"] = -out["circ_market_cap"]
        else:
            if wants("turnover_zscore_20"):
                out["turnover_zscore_20"] = 0.0
            if wants("amount_zscore_20"):
                out["amount_zscore_20"] = (
                    out.groupby("stock_code", observed=True)["amount"].transform(
                        lambda s: amount_zscore(s, 20))
                    if "amount" in out.columns else 0.0
                )
            if wants("circ_market_cap", "mcap_score"):
                out["circ_market_cap"] = np.nan
            if wants("mcap_score"):
                out["mcap_score"] = np.nan

    # ---- close 距 MA 偏离 ----
    if wants("close_to_ma5"):
        out["close_to_ma5"] = (c - out["ma5"]) / out["ma5"].replace(0, np.nan)
    if wants("close_to_ma20"):
        out["close_to_ma20"] = (c - out["ma20"]) / out["ma20"].replace(0, np.nan)
    if wants("close_to_ma60"):
        out["close_to_ma60"] = (c - out["ma60"]) / out["ma60"].replace(0, np.nan)

    _BFV_CACHE[cache_key] = out
    _BFV_CACHE_CODES[cache_key] = set(out["stock_code"].unique())
    return out.copy()


def build_feature_matrix(daily_df: pd.DataFrame, lookback: int = 120) -> pd.DataFrame:
    """对全部股票计算特征矩阵。返回长表:stock_code, trade_date, *features。

    lookback: 每只股票保留最近 lookback 个交易日。
    """
    parts: list[pd.DataFrame] = []
    for _, grp in daily_df.groupby("stock_code"):
        grp = grp.sort_values("trade_date").tail(lookback).reset_index(drop=True)
        feats = build_features_for_stock(grp)
        if len(feats) > 0:
            parts.append(feats)

    if not parts:
        return pd.DataFrame()
    return pd.concat(parts, ignore_index=True)


# 模块级基础特征缓存:多策略共享 build_features_for_stock 输出
_base_feature_cache: dict[tuple, pd.DataFrame] = {}


# O1: 周线/月线指标共享缓存。所有 needs_full_data=True 的策略(weekly/monthly/quarterly)
# 调用 get_weekly_bars / get_monthly_bars / build_weekly_features / build_monthly_features
# 而不是各自 resample+算指标。缓存 key 用数据内容指纹(同 _BFV_CACHE)。
_WEEKLY_BARS_CACHE: dict = {}
_MONTHLY_BARS_CACHE: dict = {}
_WEEKLY_CACHE: dict = {}
_MONTHLY_CACHE: dict = {}


def _resampled_cache_key(daily_df: pd.DataFrame) -> tuple:
    """周月线缓存的内容指纹(同 _make_cache_key 但 min_rows 固定 0)。"""
    try:
        return (len(daily_df),
                daily_df['stock_code'].nunique(),
                daily_df['trade_date'].min(),
                daily_df['trade_date'].max(),
                float(daily_df['close'].sum()))
    except Exception:
        return (id(daily_df),)


def _rsi_transform(s: pd.Series, n: int) -> pd.Series:
    """Wilder RSI(n)。s 为按股票分组的 close 序列。"""
    delta = s.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / n, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / n, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(50.0)


def _cci_transform(tp: pd.Series, n: int) -> pd.Series:
    """CCI(n)。tp 为 typical price = (high+low+close)/3。"""
    ma_s = tp.rolling(n, min_periods=n).mean()
    md_s = tp.rolling(n, min_periods=n).apply(
        lambda a: np.mean(np.abs(a - np.mean(a))), raw=True)
    md_safe = md_s.replace(0, np.nan)
    return ((tp - ma_s) / (0.015 * md_safe)).fillna(0.0)


def get_weekly_bars(daily_df: pd.DataFrame) -> pd.DataFrame:
    """resample daily -> weekly OHLCV bars + 共享周线指标。

    返回列:stock_code, week_key, week_close, week_high, week_low, week_date,
    wma5, wma10, wma20, weekly_above_ma5, weekly_bullish, weekly_ret_N, weekly_up,
    wema12, wema26, wmacd, wsignal, wmacd_hist, wmacd_golden_cross, wmacd_gc_recent,
    wrsi_14, wcci_20。

    模块级 _WEEKLY_BARS_CACHE 跨策略共享。需要自定义周线指标的策略调此函数
    拿到 weekly bars,再追加自己的指标。
    """
    if len(daily_df) == 0:
        return pd.DataFrame()

    cache_key = _resampled_cache_key(daily_df)
    if cache_key in _WEEKLY_BARS_CACHE:
        return _WEEKLY_BARS_CACHE[cache_key].copy()

    df = daily_df.copy()
    iso = df["trade_date"].dt.isocalendar()
    df["week_key"] = iso.week.astype(str) + "_" + iso.year.astype(str)

    weekly = df.groupby(["stock_code", "week_key"], observed=True).agg(
        week_close=("close", "last"),
        week_high=("high", "max"),
        week_low=("low", "min"),
        week_volume=("volume", "sum"),
        week_date=("trade_date", "last"),
    ).reset_index()
    weekly = weekly.sort_values(["stock_code", "week_date"]).reset_index(drop=True)

    wg = weekly.groupby("stock_code", observed=True)
    weekly["wma5"] = wg["week_close"].transform(lambda s: s.rolling(5, min_periods=3).mean())
    weekly["wma10"] = wg["week_close"].transform(lambda s: s.rolling(10, min_periods=5).mean())
    weekly["wma20"] = wg["week_close"].transform(lambda s: s.rolling(20, min_periods=10).mean())
    weekly["weekly_above_ma5"] = (weekly["week_close"] > weekly["wma5"]).astype(float)
    weekly["weekly_bullish"] = (
        (weekly["wma5"] > weekly["wma10"]) & (weekly["wma10"] > weekly["wma20"])
    ).astype(float)
    weekly["weekly_ret_N"] = wg["week_close"].pct_change(4)
    weekly["weekly_up"] = (weekly["weekly_ret_N"] >= 0).astype(float)

    wema12 = wg["week_close"].transform(lambda s: s.ewm(span=12, adjust=False).mean())
    wema26 = wg["week_close"].transform(lambda s: s.ewm(span=26, adjust=False).mean())
    wmacd = wema12 - wema26
    wsignal = wmacd.groupby(weekly["stock_code"], observed=True).transform(
        lambda s: s.ewm(span=9, adjust=False).mean())
    weekly["wema12"] = wema12
    weekly["wema26"] = wema26
    weekly["wmacd"] = wmacd
    weekly["wsignal"] = wsignal
    weekly["wmacd_hist"] = wmacd - wsignal

    wmacd_prev = wg["wmacd"].shift(1)
    wsignal_prev = wg["wsignal"].shift(1)
    weekly["wmacd_golden_cross"] = (
        (wmacd_prev <= wsignal_prev) & (weekly["wmacd"] > weekly["wsignal"])
    ).astype(float)
    weekly["wmacd_gc_recent"] = wg["wmacd_golden_cross"].transform(
        lambda s: s.rolling(2, min_periods=1).max()
    )

    # 周线 RSI(14) 与 CCI(20) - 多个 RSI/CCI 策略共用
    weekly["wrsi_14"] = wg["week_close"].transform(lambda s: _rsi_transform(s, 14))
    tp_w = (weekly["week_high"] + weekly["week_low"] + weekly["week_close"]) / 3.0
    weekly["wcci_20"] = wg.apply(
        lambda g: _cci_transform(tp_w.loc[g.index], 20),
        include_groups=False,
    ).reset_index(level=0, drop=True)

    _WEEKLY_BARS_CACHE[cache_key] = weekly
    return weekly.copy()


def get_monthly_bars(daily_df: pd.DataFrame) -> pd.DataFrame:
    """resample daily -> monthly OHLCV bars + 共享月线指标。

    返回列:stock_code, month_key, month_close, month_high, month_low, month_volume,
    month_date, mma3, mma6, monthly_above_ma3, monthly_bullish, monthly_ret_N,
    monthly_up, mema12, mema26, mmacd, msignal, mmacd_hist, mmacd_golden_cross,
    mmacd_gc_recent, mrsi_14, mcci_20。
    """
    if len(daily_df) == 0:
        return pd.DataFrame()

    cache_key = _resampled_cache_key(daily_df)
    if cache_key in _MONTHLY_BARS_CACHE:
        return _MONTHLY_BARS_CACHE[cache_key].copy()

    df = daily_df.copy()
    td = df["trade_date"].dt
    df["month_key"] = td.year.astype(str) + "_" + td.month.astype(str).str.zfill(2)

    monthly = df.groupby(["stock_code", "month_key"], observed=True).agg(
        month_close=("close", "last"),
        month_high=("high", "max"),
        month_low=("low", "min"),
        month_volume=("volume", "sum"),
        month_date=("trade_date", "last"),
    ).reset_index()
    monthly = monthly.sort_values(["stock_code", "month_date"]).reset_index(drop=True)

    mg = monthly.groupby("stock_code", observed=True)
    monthly["mma3"] = mg["month_close"].transform(lambda s: s.rolling(3, min_periods=2).mean())
    monthly["mma6"] = mg["month_close"].transform(lambda s: s.rolling(6, min_periods=3).mean())
    monthly["monthly_above_ma3"] = (monthly["month_close"] > monthly["mma3"]).astype(float)
    monthly["monthly_bullish"] = (
        (monthly["month_close"] > monthly["mma3"]) & (monthly["mma3"] > monthly["mma6"])
    ).astype(float)
    monthly["monthly_ret_N"] = mg["month_close"].pct_change(3)
    monthly["monthly_up"] = (monthly["monthly_ret_N"] >= 0).astype(float)

    mema12 = mg["month_close"].transform(lambda s: s.ewm(span=12, adjust=False).mean())
    mema26 = mg["month_close"].transform(lambda s: s.ewm(span=26, adjust=False).mean())
    mmacd = mema12 - mema26
    msignal = mmacd.groupby(monthly["stock_code"], observed=True).transform(
        lambda s: s.ewm(span=9, adjust=False).mean())
    monthly["mema12"] = mema12
    monthly["mema26"] = mema26
    monthly["mmacd"] = mmacd
    monthly["msignal"] = msignal
    monthly["mmacd_hist"] = mmacd - msignal

    mmacd_prev = mg["mmacd"].shift(1)
    msignal_prev = mg["msignal"].shift(1)
    monthly["mmacd_golden_cross"] = (
        (mmacd_prev <= msignal_prev) & (monthly["mmacd"] > monthly["msignal"])
    ).astype(float)
    monthly["mmacd_gc_recent"] = mg["mmacd_golden_cross"].transform(
        lambda s: s.rolling(2, min_periods=1).max()
    )

    # 月线 RSI(14) 与 CCI(20)
    monthly["mrsi_14"] = mg["month_close"].transform(lambda s: _rsi_transform(s, 14))
    tp_m = (monthly["month_high"] + monthly["month_low"] + monthly["month_close"]) / 3.0
    monthly["mcci_20"] = mg.apply(
        lambda g: _cci_transform(tp_m.loc[g.index], 20),
        include_groups=False,
    ).reset_index(level=0, drop=True)

    _MONTHLY_BARS_CACHE[cache_key] = monthly
    return monthly.copy()


def build_weekly_features(daily_df: pd.DataFrame) -> pd.DataFrame:
    """计算周线指标并合并回日线(无前视)。

    返回在 daily_df 基础上追加的列:
    - wma5, wma10, wma20: 周线 MA5/10/20(基于上一完整周)
    - weekly_above_ma5: 周线 close > wma5
    - weekly_bullish: wma5 > wma10 > wma20
    - weekly_ret_N: 周线 4 周涨幅
    - weekly_up: weekly_ret_N >= 0
    - wema12, wema26, wmacd, wsignal, wmacd_hist: 周线 MACD
    - wmacd_golden_cross: 当周金叉标志
    - wmacd_gc_recent: 最近 2 周内金叉
    - wrsi_14: 周线 RSI(14)
    - wcci_20: 周线 CCI(20)

    修复前视(2026-07):原实现把当周 week_close(last)merge 回当周所有日线行,
    导致周一/周二的日线行拿到了周五的收盘。现在用 merge_asof(direction='backward'),
    T 日只看到 week_date <= T 的最近一周线指标(即上一完整周)。

    模块级 _WEEKLY_CACHE 跨策略共享,基于数据内容指纹。要求 daily_df 含
    stock_code, trade_date, close, high, low 列并已排序。
    """
    if len(daily_df) == 0:
        return daily_df.copy()

    cache_key = _resampled_cache_key(daily_df)
    if cache_key in _WEEKLY_CACHE:
        return _WEEKLY_CACHE[cache_key].copy()

    df = daily_df.copy()

    weekly = get_weekly_bars(daily_df)
    df = merge_weekly_to_daily(df, weekly)

    _WEEKLY_CACHE[cache_key] = df
    return df.copy()


def merge_weekly_to_daily(daily_df: pd.DataFrame, weekly: pd.DataFrame | None = None) -> pd.DataFrame:
    """把周线指标无前视地合并回日线。

    用 merge_asof(direction='backward')让 T 日只看到 week_date <= T 的最近周线指标,
    等价于"上一完整周的周线"。当周(week_date > T)的指标不会出现在 T 日的行里。

    Args:
        daily_df: 日线 df,必须含 stock_code, trade_date 列
        weekly: 周线 bars(来自 get_weekly_bars)。None 时自动调

    Returns:
        daily_df 追加 wma5/wma10/.../wcci_20 等列(当周行这些列为 NaN,等下一周才有值)
    """
    if weekly is None:
        weekly = get_weekly_bars(daily_df)

    cols_to_merge = ["stock_code", "week_date", "wma5", "wma10", "wma20",
                     "weekly_above_ma5", "weekly_bullish", "weekly_ret_N", "weekly_up",
                     "wema12", "wema26", "wmacd", "wsignal", "wmacd_hist",
                     "wmacd_golden_cross", "wmacd_gc_recent",
                     "wrsi_14", "wcci_20"]
    # weekly 可能缺部分列(策略自定义的 weekly_bars 子集),只取交集
    available = [c for c in cols_to_merge if c in weekly.columns]
    weekly_last = weekly.groupby(["stock_code", "week_key"], observed=True).last().reset_index()
    weekly_last = weekly_last[available].sort_values("week_date")

    df = daily_df.sort_values("trade_date").copy()
    df = pd.merge_asof(
        df, weekly_last,
        left_on="trade_date", right_on="week_date",
        by="stock_code", direction="backward",
    )
    # merge_asof 后 week_date 列保留(用于诊断),不影响后续逻辑
    return df


def build_monthly_features(daily_df: pd.DataFrame) -> pd.DataFrame:
    """计算月线指标并合并回日线(无前视)。

    返回在 daily_df 基础上追加的列:
    - mma3, mma6: 月线 MA3/6(基于上一完整月)
    - monthly_above_ma3: 月线 close > mma3
    - monthly_bullish: close > mma3 > mma6
    - monthly_ret_N: 月线 3 月涨幅
    - monthly_up: monthly_ret_N >= 0
    - mema12, mema26, mmacd, msignal, mmacd_hist: 月线 MACD
    - mmacd_golden_cross: 当月金叉标志
    - mmacd_gc_recent: 最近 2 月内金叉
    - mrsi_14: 月线 RSI(14)
    - mcci_20: 月线 CCI(20)

    修复前视(2026-07):原实现把当月 month_close(last)merge 回当月所有日线行,
    导致月初的日线行拿到了月末收盘。现在用 merge_asof(direction='backward'),
    T 日只看到 month_date <= T 的最近月线指标(即上一完整月)。

    模块级 _MONTHLY_CACHE 跨策略共享。
    """
    if len(daily_df) == 0:
        return daily_df.copy()

    cache_key = _resampled_cache_key(daily_df)
    if cache_key in _MONTHLY_CACHE:
        return _MONTHLY_CACHE[cache_key].copy()

    df = daily_df.copy()

    monthly = get_monthly_bars(daily_df)
    df = merge_monthly_to_daily(df, monthly)

    _MONTHLY_CACHE[cache_key] = df
    return df.copy()


def merge_monthly_to_daily(daily_df: pd.DataFrame, monthly: pd.DataFrame | None = None) -> pd.DataFrame:
    """把月线指标无前视地合并回日线。

    用 merge_asof(direction='backward')让 T 日只看到 month_date <= T 的最近月线指标,
    等价于"上一完整月的月线"。当月(month_date > T)的指标不会出现在 T 日的行里。

    Args:
        daily_df: 日线 df,必须含 stock_code, trade_date 列
        monthly: 月线 bars(来自 get_monthly_bars)。None 时自动调

    Returns:
        daily_df 追加 mma3/mma6/.../mcci_20 等列(当月行这些列为 NaN,等下月才有值)
    """
    if monthly is None:
        monthly = get_monthly_bars(daily_df)

    cols_to_merge = ["stock_code", "month_date", "mma3", "mma6",
                     "monthly_above_ma3", "monthly_bullish", "monthly_ret_N", "monthly_up",
                     "mema12", "mema26", "mmacd", "msignal", "mmacd_hist",
                     "mmacd_golden_cross", "mmacd_gc_recent",
                     "mrsi_14", "mcci_20"]
    available = [c for c in cols_to_merge if c in monthly.columns]
    monthly_last = monthly.groupby(["stock_code", "month_key"], observed=True).last().reset_index()
    monthly_last = monthly_last[available].sort_values("month_date")

    df = daily_df.sort_values("trade_date").copy()
    df = pd.merge_asof(
        df, monthly_last,
        left_on="trade_date", right_on="month_date",
        by="stock_code", direction="backward",
    )
    return df


def merge_asof_weekly(daily_df: pd.DataFrame, weekly: pd.DataFrame) -> pd.DataFrame:
    """通用无前视周线合并:把 weekly bars 的全部列 attach 到 daily。

    用于策略自定义周线指标(如 wcci_cross_recent / weekly_above_ma10)。策略先在
    weekly bars 上算好指标,再调此函数 attach 回 daily。T 日只看到 week_date <= T
    的最近一周指标(即上一完整周)。

    与 merge_weekly_to_daily 的区别:后者只 merge 预定义列(wma5/wma10/...),
    本函数 merge weekly 的全部列(适合策略自定义场景)。

    Args:
        daily_df: 日线 df,必须含 stock_code, trade_date
        weekly: 周线 bars(来自 get_weekly_bars 或其上追加策略列),必须含
                stock_code, week_key, week_date

    Returns:
        daily_df 追加 weekly 的全部列(当周行 week_date > T 的列值为 NaN,
        等下一周才有值;week_date 列也保留供诊断)
    """
    if len(weekly) == 0:
        return daily_df.copy()
    weekly_last = weekly.groupby(["stock_code", "week_key"], observed=True).last().reset_index()
    # 丢弃 week_key 避免与 daily_df 已有的 week_key 列冲突(merge_asof 不在 by 里)
    drop_cols = [c for c in ["week_key"] if c in weekly_last.columns]
    if drop_cols:
        weekly_last = weekly_last.drop(columns=drop_cols)
    weekly_last = weekly_last.sort_values("week_date")
    df = daily_df.sort_values("trade_date").copy()
    df = pd.merge_asof(
        df, weekly_last,
        left_on="trade_date", right_on="week_date",
        by="stock_code", direction="backward",
    )
    return df


def merge_asof_monthly(daily_df: pd.DataFrame, monthly: pd.DataFrame) -> pd.DataFrame:
    """通用无前视月线合并:把 monthly bars 的全部列 attach 到 daily。

    用于策略自定义月线指标(如 monthly_new_high / mma3_up)。用法同 merge_asof_weekly。
    """
    if len(monthly) == 0:
        return daily_df.copy()
    monthly_last = monthly.groupby(["stock_code", "month_key"], observed=True).last().reset_index()
    drop_cols = [c for c in ["month_key"] if c in monthly_last.columns]
    if drop_cols:
        monthly_last = monthly_last.drop(columns=drop_cols)
    monthly_last = monthly_last.sort_values("month_date")
    df = daily_df.sort_values("trade_date").copy()
    df = pd.merge_asof(
        df, monthly_last,
        left_on="trade_date", right_on="month_date",
        by="stock_code", direction="backward",
    )
    return df


def merge_asof_quarterly(daily_df: pd.DataFrame, quarterly: pd.DataFrame) -> pd.DataFrame:
    """通用无前视季线合并:把 quarterly bars 的全部列 attach 到 daily。

    用于策略自定义季线指标(如 quarterly_breakout / qrsi_cross_recent)。
    T 日只看到 quarter_date <= T 的最近一季指标(即上一完整季)。
    """
    if len(quarterly) == 0:
        return daily_df.copy()
    quarterly_last = quarterly.groupby(["stock_code", "quarter_key"], observed=True).last().reset_index()
    drop_cols = [c for c in ["quarter_key"] if c in quarterly_last.columns]
    if drop_cols:
        quarterly_last = quarterly_last.drop(columns=drop_cols)
    quarterly_last = quarterly_last.sort_values("quarter_date")
    df = daily_df.sort_values("trade_date").copy()
    df = pd.merge_asof(
        df, quarterly_last,
        left_on="trade_date", right_on="quarter_date",
        by="stock_code", direction="backward",
    )
    return df


def clear_resampled_caches() -> None:
    """清空周月线缓存(测试或内存压力大时调用)。"""
    _WEEKLY_BARS_CACHE.clear()
    _MONTHLY_BARS_CACHE.clear()
    _WEEKLY_CACHE.clear()
    _MONTHLY_CACHE.clear()


def get_or_build_base_features(daily_df: pd.DataFrame) -> pd.DataFrame:
    """获取或构建基础特征矩阵(多策略共享)。

    缓存 key 用数据内容指纹避免 id() 陷阱。
    每个策略的 _precompute_features 调用此函数获取基础矩阵,
    然后在上面追加策略特有列,避免子策略各算一遍。
    """
    key = _make_cache_key(daily_df, 0)
    if key in _base_feature_cache:
        return _base_feature_cache[key].copy()

    parts: list[pd.DataFrame] = []
    codes = daily_df["stock_code"].unique()
    for code in codes:
        grp = daily_df[daily_df["stock_code"] == code].sort_values("trade_date").reset_index(drop=True)
        if len(grp) < 30:
            continue
        feats = build_features_for_stock(grp)
        if len(feats) > 0:
            parts.append(feats)

    if not parts:
        result = pd.DataFrame()
    else:
        result = pd.concat(parts, ignore_index=True)
        result = result.sort_values(["stock_code", "trade_date"]).reset_index(drop=True)

    _base_feature_cache[key] = result
    return result.copy()


def get_snapshot(features: pd.DataFrame, on_date: pd.Timestamp) -> pd.DataFrame:
    """取某日全部股票的特征快照。"""
    return features[features["trade_date"] == on_date].copy()


def cross_sectional_zscore(series: pd.Series, clip: float = 3.0) -> pd.Series:
    """横截面 z-score:对当日全部股票标准化。

    必须传入当日切片(不能是全样本),否则有前视。
    """
    s = series.copy()
    mask = s.notna()
    if mask.sum() < 2:
        return pd.Series(0.0, index=s.index)
    mean = s[mask].mean()
    std = s[mask].std()
    if std == 0 or pd.isna(std):
        return pd.Series(0.0, index=s.index)
    z = (s - mean) / std
    return z.clip(-clip, clip).fillna(0)
