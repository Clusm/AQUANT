"""本地 parquet 缓存:按表名存储,支持日期分区。

v0.4.0 起 update() 具备进程内 + 跨进程文件锁:
- 读旧文件 -> concat -> drop_duplicates -> 原子写 整个流程串行化,
  避免两个更新进程同时读旧文件时后者覆盖前者(丢行/重复)。
- 写文件先写临时文件再 os.replace,读方不会看到半写文件。
"""
from __future__ import annotations

import json
import os
import threading
import time
from contextlib import contextmanager
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


def _file_lock_path(name: str) -> Path:
    return cache_path(name).with_suffix(cache_path(name).suffix + ".lock")


_LOCKS_GUARD = threading.Lock()
_LOCKS: dict[str, threading.Lock] = {}


def _thread_lock(name: str) -> threading.Lock:
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(name, threading.Lock())


if os.name == "nt":  # pragma: no cover - Windows 分支,Linux CI 不执行
    import msvcrt

    @contextmanager
    def _os_file_lock(lock_path: Path):
        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR)
        try:
            if os.fstat(fd).st_size == 0:
                os.write(fd, b"1")
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        finally:
            os.close(fd)
else:
    import fcntl

    @contextmanager
    def _os_file_lock(lock_path: Path):
        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


@contextmanager
def _locked(name: str):
    """同一张缓存表的所有写操作串行(进程内线程 + 跨进程文件锁)。"""
    lock_path = _file_lock_path(name)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with _thread_lock(name):
        with _os_file_lock(lock_path):
            yield


def _write_atomic(name: str, df: pd.DataFrame, meta: dict[str, Any] | None = None) -> None:
    """写临时 parquet 后原子替换,避免读方看到半写文件。"""
    target = cache_path(name)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp.parquet")
    df.to_parquet(tmp, engine="pyarrow")

    last_exc: Exception | None = None
    for attempt in range(5):
        try:
            os.replace(tmp, target)
            break
        except PermissionError as exc:
            # Windows 下杀毒/索引器可能短暂占用目标文件
            last_exc = exc
            time.sleep(0.05 * (attempt + 1))
        except OSError as exc:
            last_exc = exc
            time.sleep(0.05 * (attempt + 1))
    else:
        tmp.unlink(missing_ok=True)
        raise last_exc

    if meta is not None:
        with open(meta_path(name), "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, default=str, indent=2)


def save(name: str, df: pd.DataFrame, meta: dict[str, Any] | None = None) -> None:
    with _locked(name):
        _write_atomic(name, df, meta)


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

    整个 read-old -> merge -> write-new 流程持有同表写锁,避免两个进程
    基于同一份旧缓存并发更新时互相覆盖。合并后按 ``on`` 去重,重复 key
    只保留最后一行,因此重跑/并发拉取不会产生重复行。
    """
    with _locked(name):
        if not exists(name):
            _write_atomic(name, new_df)
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
        # Empty frames never change the merge result and pd.concat([])
        # emits a FutureWarning on pandas>=2.x; short-circuit instead.
        if new_df.empty:
            return old
        if old.empty:
            _write_atomic(name, new_df)
            return new_df
        merged = pd.concat([old, new_df], ignore_index=True)
        merged = merged.drop_duplicates(subset=on, keep="last").reset_index(drop=True)
        _write_atomic(name, merged)
        return merged


def list_cache() -> list[str]:
    return [p.stem for p in CACHE_DIR.glob("*.parquet")]
