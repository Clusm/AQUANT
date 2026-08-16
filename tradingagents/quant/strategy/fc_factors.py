"""FC(因子组合)策略工程因子列的自包含计算。

TradingAgents 日线缓存不含 stock_selector llm_loop 产出的工程因子列, 本模块把
FC 策略引用的因子按原公式向量化重算, 使 FC 策略在本地 daily_df 上自包含。

公式出处 (stock_selector 源仓库):
- 因子表达式: factor_research/outputs/llm_loop_state.json (accepted.name/expr),
  经 factor_research/strategy_loop/column_store.py ensure_columns() 计算成列。
- 算子语义: factor_research/ic_alpha101_gtja191.py _eval_qlib (L52-L203):
  Rank(x,1)=按 trade_date 截面 pct-rank; Ref(x,n)=groupby(stock).shift(n);
  Corr/Min/Max=groupby(stock).rolling(n, min_periods=min(3,max(1,n-1)));
  结果 clip(-1e6,1e6) (column_store._compute_one 同口径)。
- neg_volume = -volume (column_store._base3_from, 不 clip)。

表达式原文:
- GAP_AVOLC60_PVC10:
  Rank($open/Ref($close,1)-1,1)*Rank(-1*Corr(Abs($change_pct),$volume,60),1)*Rank(-1*Corr($high,$amount,10),1)
- OVNSHARE_AVC5_PVC10:
  Rank(($open-Ref($close,1))/($high-$low+1e-9),1)*Rank(-1*Corr($amount,$volume,5),1)*Rank(-1*Corr($high,$amount,10),1)
- DIST_HIGH3_AMTVC10:
  Rank($close/Max($high,3)-1,1)*Rank(-1*Corr($high,$amount,10),1)
- RANGE_POS3:
  ($close-Min($low,3))/(Max($high,3)-Min($low,3)+1e-9)
- RANGE_POS_20D:
  ($close-Min($low,20))/(Max($high,20)-Min($low,20)+1e-9)
- neg_volume: -volume
"""
from __future__ import annotations

import pandas as pd

FC_FACTORS = [
    "GAP_AVOLC60_PVC10",
    "OVNSHARE_AVC5_PVC10",
    "DIST_HIGH3_AMTVC10",
    "RANGE_POS3",
    "RANGE_POS_20D",
    "neg_volume",
]


def _min_periods(n: int) -> int:
    return min(3, max(1, n - 1))


def _ref(df: pd.DataFrame, col: str, n: int) -> pd.Series:
    return df.groupby("stock_code", observed=True)[col].shift(int(n))


def _roll_max(df: pd.DataFrame, col: str, n: int) -> pd.Series:
    n = int(n)
    return df.groupby("stock_code", observed=True)[col].transform(
        lambda s: s.rolling(n, min_periods=_min_periods(n)).max())


def _roll_min(df: pd.DataFrame, col: str, n: int) -> pd.Series:
    n = int(n)
    return df.groupby("stock_code", observed=True)[col].transform(
        lambda s: s.rolling(n, min_periods=_min_periods(n)).min())


def _corr(df: pd.DataFrame, a: str, b: str, n: int) -> pd.Series:
    n = int(n)
    y = df[b]
    return df[a].groupby(df["stock_code"], observed=True).transform(
        lambda s: s.rolling(n, min_periods=_min_periods(n)).corr(y.loc[s.index]))


def _cs_rank(df: pd.DataFrame, col: str) -> pd.Series:
    return df[col].groupby(df["trade_date"], observed=True).rank(pct=True)


def ensure_fc_factor_columns(daily_df: pd.DataFrame,
                             factors: list[str] | None = None) -> pd.DataFrame:
    """若 factors 中有列不在 daily_df.columns, 按原公式补齐并返回追加了这些列的 DataFrame。

    只计算缺失的列; 全部存在时原样返回 daily_df。不修改输入。
    """
    wanted = factors if factors is not None else FC_FACTORS
    missing = [f for f in dict.fromkeys(wanted) if f not in daily_df.columns]
    if not missing:
        return daily_df

    df = daily_df
    if "amount" not in df.columns:
        df = df.copy()
        df["amount"] = df["close"] * df["volume"]
    if "change_pct" not in df.columns:
        df = df.copy()
        df["change_pct"] = df.groupby("stock_code", observed=True)["close"].pct_change() * 100

    cols: dict[str, pd.Series] = {}
    neg_corr_high_amount_10 = None
    if any(f in missing for f in ("GAP_AVOLC60_PVC10", "OVNSHARE_AVC5_PVC10", "DIST_HIGH3_AMTVC10")):
        neg_corr_high_amount_10 = -_corr(df, "high", "amount", 10)

    if "GAP_AVOLC60_PVC10" in missing:
        gap = df["open"] / _ref(df, "close", 1) - 1
        avolc60 = -_corr(df.assign(_abschg=df["change_pct"].abs()), "_abschg", "volume", 60)
        cols["GAP_AVOLC60_PVC10"] = (
            _cs_rank(df.assign(_g=gap), "_g").mul(
                _cs_rank(df.assign(_a=avolc60), "_a"), axis=0).mul(
                _cs_rank(df.assign(_p=neg_corr_high_amount_10), "_p"), axis=0))

    if "OVNSHARE_AVC5_PVC10" in missing:
        ovn = (df["open"] - _ref(df, "close", 1)) / (df["high"] - df["low"] + 1e-9)
        avc5 = -_corr(df, "amount", "volume", 5)
        cols["OVNSHARE_AVC5_PVC10"] = (
            _cs_rank(df.assign(_o=ovn), "_o").mul(
                _cs_rank(df.assign(_a=avc5), "_a"), axis=0).mul(
                _cs_rank(df.assign(_p=neg_corr_high_amount_10), "_p"), axis=0))

    if "DIST_HIGH3_AMTVC10" in missing:
        dist = df["close"] / _roll_max(df, "high", 3) - 1
        cols["DIST_HIGH3_AMTVC10"] = (
            _cs_rank(df.assign(_d=dist), "_d").mul(
                _cs_rank(df.assign(_p=neg_corr_high_amount_10), "_p"), axis=0))

    if "RANGE_POS3" in missing:
        lo3 = _roll_min(df, "low", 3)
        hi3 = _roll_max(df, "high", 3)
        cols["RANGE_POS3"] = (df["close"] - lo3) / (hi3 - lo3 + 1e-9)

    if "RANGE_POS_20D" in missing:
        lo20 = _roll_min(df, "low", 20)
        hi20 = _roll_max(df, "high", 20)
        cols["RANGE_POS_20D"] = (df["close"] - lo20) / (hi20 - lo20 + 1e-9)

    if "neg_volume" in missing:
        cols["neg_volume"] = -df["volume"]

    out = df.copy()
    for name, col in cols.items():
        if name == "neg_volume":
            out[name] = col.values
        else:
            out[name] = col.clip(-1e6, 1e6).values
    return out
