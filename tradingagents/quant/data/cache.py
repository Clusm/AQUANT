"""本地 parquet 缓存:按表名存储,支持日期分区。"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

from tradingagents.quant.config import CACHE_DIR


def cache_path(name: str) -> Path:
    return CACHE_DIR / f"{name}.parquet"


def meta_path(name: str) -> Path:
    return CACHE_DIR / f"{name}.meta.json"


def exists(name: str) -> bool:
    return cache_path(name).exists()


def save(name: str, df: pd.DataFrame, meta: dict[str, Any] | None = None) -> None:
    df.to_parquet(cache_path(name), engine="pyarrow")
    if meta is not None:
        with open(meta_path(name), "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, default=str, indent=2)


def load(name: str) -> pd.DataFrame:
    return pd.read_parquet(cache_path(name))


def load_meta(name: str) -> dict[str, Any] | None:
    p = meta_path(name)
    if not p.exists():
        return None
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)


def update(name: str, new_df: pd.DataFrame, *, on: list[str] | None = None) -> pd.DataFrame:
    """Upsert new_df into cache; dedup keeping the latest row per key.

    Args:
        name: Cache name.
        new_df: New rows to merge.
        on: Merge/dedup key columns. If None, auto-detect: prefer
            ["trade_date", "stock_code"] if both present, else fall back to
            ["stock_code"] if present, else raise. Previous behaviour silently
            picked the first intersecting column, which could pick trade_date
            alone and merge rows across different stocks.
    """
    if not exists(name):
        save(name, new_df)
        return new_df
    old = load(name)
    if on is None:
        common = list(old.columns.intersection(new_df.columns))
        if {"trade_date", "stock_code"}.issubset(common):
            on = ["trade_date", "stock_code"]
        elif "stock_code" in common:
            on = ["stock_code"]
        else:
            raise ValueError(
                f"cache.update('{name}'): cannot infer safe merge key "
                f"(common cols={common}). Pass on=[...] explicitly."
            )
    merged = pd.concat([old, new_df], ignore_index=True)
    merged = merged.drop_duplicates(subset=on, keep="last").reset_index(drop=True)
    save(name, merged)
    return merged


def list_cache() -> list[str]:
    return [p.stem for p in CACHE_DIR.glob("*.parquet")]
