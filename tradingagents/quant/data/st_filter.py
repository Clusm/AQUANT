"""ST 股票过滤:加载 st_history 数据,提供 is_st(code, date) 查询。"""
from __future__ import annotations

import pandas as pd

from tradingagents.quant.data import cache as cache_mod


_ST_CACHE: pd.DataFrame | None = None
_ST_BY_DATE: dict | None = None
_ST_BY_CODE: dict | None = None


def _find_st_history_name() -> str | None:
    """扫描缓存目录,自动找 st_history_*.parquet 文件。

    若多个文件存在,选名字字典序最大的(通常是日期范围最新的)。
    """
    names = cache_mod.list_cache()
    st_names = sorted(n for n in names if n.startswith("st_history_"))
    return st_names[-1] if st_names else None


def load_st_history() -> pd.DataFrame:
    global _ST_CACHE
    if _ST_CACHE is not None:
        return _ST_CACHE
    name = _find_st_history_name()
    if name is None:
        _ST_CACHE = pd.DataFrame(columns=["trade_date", "stock_code", "is_st"])
        return _ST_CACHE
    df = cache_mod.load(name)
    df = df.copy()
    df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.normalize()
    df["stock_code"] = df["stock_code"].astype(str)
    _ST_CACHE = df
    return df


def get_st_codes_on_date(date: pd.Timestamp) -> set[str]:
    """获取某日 ST 股票集合(date 当日为 ST 的股票)。"""
    global _ST_BY_DATE
    if _ST_BY_DATE is None:
        df = load_st_history()
        df_st = df[df["is_st"] == 1.0]
        _ST_BY_DATE = {d: set(g["stock_code"].tolist()) for d, g in df_st.groupby("trade_date", sort=False)}
    date = pd.Timestamp(date).normalize()
    return _ST_BY_DATE.get(date, set())


def is_st(code: str, date: pd.Timestamp) -> bool:
    """判断某股在某日是否为 ST。"""
    return code in get_st_codes_on_date(date)


def get_st_dates_for_code(code: str) -> set[pd.Timestamp]:
    """获取某股被标为 ST 的所有日期(用于检查整个持仓期)。"""
    global _ST_BY_CODE
    if _ST_BY_CODE is None:
        df = load_st_history()
        df_st = df[df["is_st"] == 1.0]
        _ST_BY_CODE = {c: set(g["trade_date"].tolist()) for c, g in df_st.groupby("stock_code", sort=False)}
    return _ST_BY_CODE.get(str(code), set())
