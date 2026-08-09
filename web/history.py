"""Manage completed and incomplete analysis history."""

from __future__ import annotations

import json
import logging
import re
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

import streamlit as st

from tradingagents.default_config import DEFAULT_CONFIG

logger = logging.getLogger(__name__)


_INCOMPLETE_TASKS_FILE = Path.home() / ".tradingagents" / "incomplete_tasks.json"
_INCOMPLETE_TASKS_LOCK = threading.Lock()


def _clear_history_cache() -> None:
    """Invalidate st.cache_data entries for the get_*_history functions.

    Safe to call from any thread (background runner or Streamlit main).
    try/except guards against no-ScriptRunContext (CLI / tests).
    """
    try:
        st.cache_data.clear()
    except Exception:
        pass


def _results_dir() -> Path:
    # 与写入方(cli/main.py、trading_graph.py、daily_pipeline)保持一致:
    # 尊重 TRADINGAGENTS_RESULTS_DIR 环境变量,否则 Web 历史面板会扫不到记录。
    return Path(DEFAULT_CONFIG["results_dir"])


@st.cache_data(ttl=30)
def get_history() -> list[dict[str, str]]:
    """Scan saved analysis logs and return a sorted list (newest first).

    Each entry: {"ticker": "300750", "date": "2026-05-12", "path": "/abs/path/...json"}

    Cached for 30s via st.cache_data - the rglob scan was showing up as
    noticeable lag on every Streamlit rerun (all 4 tabs render even when
    only one is visible). Cache is cleared by _clear_history_cache()
    after save_recommendation / save_quant_pick / clear_incomplete_task.
    """
    root = _results_dir()
    if not root.exists():
        return []

    entries: list[dict[str, str]] = []
    for log_file in root.rglob("full_states_log_*.json"):
        match = re.search(r"full_states_log_(\d{4}-\d{2}-\d{2})\.json$", log_file.name)
        if not match:
            continue
        date = match.group(1)
        ticker = log_file.parent.parent.name
        entries.append({"ticker": ticker, "date": date, "path": str(log_file)})

    entries.sort(key=lambda e: e["date"], reverse=True)
    return entries


def _completed_key(ticker: str, trade_date: str) -> tuple[str, str]:
    return ticker.upper(), trade_date


def _completed_keys() -> set[tuple[str, str]]:
    return {
        _completed_key(entry["ticker"], entry["date"])
        for entry in get_history()
    }


def _load_incomplete_index() -> list[dict[str, Any]]:
    if not _INCOMPLETE_TASKS_FILE.exists():
        return []

    try:
        with open(_INCOMPLETE_TASKS_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return []

    if not isinstance(data, list):
        return []

    entries: list[dict[str, Any]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        ticker = str(item.get("ticker", "")).strip().upper()
        trade_date = str(item.get("trade_date", "")).strip()
        if not ticker or not re.match(r"^\d{4}-\d{2}-\d{2}$", trade_date):
            continue
        item["ticker"] = ticker
        item["trade_date"] = trade_date
        entries.append(item)
    return entries


def _save_incomplete_index(entries: list[dict[str, Any]]) -> None:
    parent = _INCOMPLETE_TASKS_FILE.parent
    parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=parent,
        prefix=f"{_INCOMPLETE_TASKS_FILE.stem}.",
        suffix=".tmp",
        delete=False,
    ) as f:
        json.dump(entries, f, ensure_ascii=False, indent=2)
        tmp = Path(f.name)
    # Windows: os.replace can fail with WinError 5 when antivirus / file
    # indexer briefly holds the target. Retry a few times before giving up;
    # this file is just a cache of resumable tasks, not a critical artifact,
    # so a failed save must NOT crash the sidebar render (which leaves the
    # browser DOM half-rendered and surfaces as React NotFoundError).
    last_exc: Exception | None = None
    for attempt in range(4):
        try:
            tmp.replace(_INCOMPLETE_TASKS_FILE)
            return
        except PermissionError as exc:
            last_exc = exc
            time.sleep(0.05 * (attempt + 1))
        except OSError as exc:
            last_exc = exc
            time.sleep(0.05 * (attempt + 1))
    # Final fallback: try direct write to target (skipping atomicity).
    try:
        with open(_INCOMPLETE_TASKS_FILE, "w", encoding="utf-8") as f:
            json.dump(entries, f, ensure_ascii=False, indent=2)
    except OSError:
        pass
    # Clean up the orphaned tmp file if replace never succeeded.
    try:
        tmp.unlink(missing_ok=True)
    except OSError:
        pass
    if last_exc is not None:
        logger.warning("incomplete_tasks.json save failed after retries: %s", last_exc)


def _checkpoint_step(ticker: str, trade_date: str) -> int | None:
    try:
        from tradingagents.graph.checkpointer import checkpoint_step

        return checkpoint_step(DEFAULT_CONFIG["data_cache_dir"], ticker, trade_date)
    except Exception:
        return None


def record_incomplete_task(
    ticker: str,
    trade_date: str,
    *,
    status: str,
    error: str | None = None,
    completed_stages: list[str] | None = None,
) -> None:
    """Upsert a resumable task entry."""
    ticker = ticker.strip().upper()
    trade_date = trade_date.strip()
    if not ticker or not trade_date:
        return

    with _INCOMPLETE_TASKS_LOCK:
        entries = [
            entry
            for entry in _load_incomplete_index()
            if _completed_key(entry["ticker"], entry["trade_date"])
            != _completed_key(ticker, trade_date)
        ]
        now = time.time()
        entries.append(
            {
                "ticker": ticker,
                "trade_date": trade_date,
                "status": status,
                "error": error or "",
                "completed_stages": completed_stages or [],
                "updated_at": now,
            }
        )
        entries.sort(key=lambda e: float(e.get("updated_at", 0)), reverse=True)
        _save_incomplete_index(entries)

    _clear_history_cache()


def clear_incomplete_task(ticker: str, trade_date: str) -> None:
    """Remove an incomplete task once it completes successfully."""
    ticker = ticker.strip().upper()
    trade_date = trade_date.strip()
    with _INCOMPLETE_TASKS_LOCK:
        entries = [
            entry
            for entry in _load_incomplete_index()
            if _completed_key(entry["ticker"], entry["trade_date"])
            != _completed_key(ticker, trade_date)
        ]
        _save_incomplete_index(entries)

    _clear_history_cache()


def get_incomplete_history() -> list[dict[str, Any]]:
    """Return unfinished tasks that can be resumed from their checkpoint."""
    completed = _completed_keys()
    active_entries: list[dict[str, Any]] = []

    with _INCOMPLETE_TASKS_LOCK:
        entries = _load_incomplete_index()
        for entry in entries:
            key = _completed_key(entry["ticker"], entry["trade_date"])
            if key in completed:
                continue

            step = _checkpoint_step(entry["ticker"], entry["trade_date"])
            entry["checkpoint_step"] = step
            active_entries.append(entry)

        active_entries.sort(key=lambda e: float(e.get("updated_at", 0)), reverse=True)
        if len(active_entries) != len(entries):
            try:
                _save_incomplete_index(active_entries)
            except Exception as exc:
                # Never let cache pruning crash the sidebar render - that
                # leaves the browser DOM half-built and surfaces as React
                # NotFoundError. The next successful save will reconcile.
                logger.warning("incomplete_tasks.json pruning failed: %s", exc)
    return active_entries


def load_analysis(path: str) -> dict[str, Any]:
    """Load a saved analysis JSON file."""
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def extract_signal(state: dict[str, Any]) -> str:
    """Extract the short signal (Buy/Sell/Hold) from a final state dict."""
    import re

    for field in (
        "investment_plan",
        "trader_investment_decision",
        "final_trade_decision",
    ):
        text = state.get(field, "")
        if not text:
            continue
        cleaned = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
        for keyword in ("BUY", "SELL", "HOLD"):
            if keyword in cleaned.upper():
                return keyword.capitalize()
    return "N/A"


# ============================================================
# Quant pick history (~/.tradingagents/quant_picks/{date}.json)
# ============================================================

def _quant_picks_dir() -> Path:
    return Path.home() / ".tradingagents" / "quant_picks"


def _recommendations_dir() -> Path:
    return Path.home() / ".tradingagents" / "recommendations"


def _json_default(obj: Any) -> Any:
    """JSON fallback for pandas/numpy/Timestamp types."""
    if hasattr(obj, "isoformat"):
        return obj.isoformat()
    if hasattr(obj, "item"):
        try:
            return obj.item()
        except (ValueError, AttributeError):
            pass
    if hasattr(obj, "tolist"):
        try:
            return obj.tolist()
        except (ValueError, AttributeError):
            pass
    return str(obj)


def save_quant_pick(trade_date: str, result: dict[str, Any]) -> Path:
    """Persist a quant pick() result keyed by trade_date.

    Serializes:
      - top_picks: DataFrame -> list[dict] via to_dict(orient="records")
      - today: Timestamp -> ISO string
      - per_strategy_stats: dict[str, dict] (already JSON-safe primitives)
      - elapsed, n_strategies_run, n_strategies_error: scalar
      - all_records: list[dict] (date field -> ISO string)

    Returns the saved file path. Overwrites same-day picks.
    """
    out_dir = _quant_picks_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"{trade_date}.json"

    top = result.get("top_picks")
    today = result.get("today")

    payload = {
        "trade_date": trade_date,
        "today": today.isoformat() if hasattr(today, "isoformat") else str(today),
        "elapsed": float(result.get("elapsed", 0.0)),
        "n_strategies_run": int(result.get("n_strategies_run", 0)),
        "n_strategies_error": int(result.get("n_strategies_error", 0)),
        "top_picks": (
            top.to_dict(orient="records")
            if top is not None and hasattr(top, "to_dict")
            else []
        ),
        "per_strategy_stats": result.get("per_strategy_stats", {}),
        "all_records": [
            {
                **rec,
                "date": rec["date"].isoformat()
                if hasattr(rec.get("date"), "isoformat")
                else str(rec.get("date", "")),
            }
            for rec in result.get("all_records", [])
        ],
    }

    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2, default=_json_default)
    _clear_history_cache()
    return out_file


@st.cache_data(ttl=30)
def get_quant_history() -> list[dict[str, Any]]:
    """Scan saved quant pick JSONs, return newest-first summary list.

    Each entry: {"trade_date": "2026-07-19", "path": "...", "n_picks": 20,
                  "elapsed": 180.0, "n_strategies_run": 12, "n_strategies_error": 0}

    Cached for 30s; see get_history() for rationale.
    """
    root = _quant_picks_dir()
    if not root.exists():
        return []

    entries: list[dict[str, Any]] = []
    for f in root.glob("*.json"):
        match = re.match(r"^(\d{4}-\d{2}-\d{2})\.json$", f.name)
        if not match:
            continue
        try:
            with open(f, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, json.JSONDecodeError):
            continue
        entries.append(
            {
                "trade_date": match.group(1),
                "path": str(f),
                "n_picks": len(data.get("top_picks", [])),
                "elapsed": float(data.get("elapsed", 0.0)),
                "n_strategies_run": int(data.get("n_strategies_run", 0)),
                "n_strategies_error": int(data.get("n_strategies_error", 0)),
            }
        )
    entries.sort(key=lambda e: e["trade_date"], reverse=True)
    return entries


def load_quant_pick(trade_date: str) -> dict[str, Any] | None:
    """Load a saved quant pick by trade_date. Returns None if not found."""
    f = _quant_picks_dir() / f"{trade_date}.json"
    if not f.exists():
        return None
    with open(f, encoding="utf-8") as fh:
        return json.load(fh)


# ============================================================
# Recommendation history (~/.tradingagents/recommendations/{date}_{ticker}.json)
# ============================================================

def save_recommendation(
    trade_date: str,
    ticker: str,
    label: str,
    final_state_summary: dict[str, Any],
) -> Path:
    """Persist a single-ticker recommendation (label + key state fields).

    Args:
        trade_date: YYYY-MM-DD
        ticker: 6-digit code, normalized to upper-case
        label: 🟢强买 / 🟡关注 / 🟠冲突 / 🔴弃 (one of RECOMMENDATION_LABELS)
        final_state_summary: dict with keys like final_trade_decision,
                             final_ranked_decision, signal, quant_pick_context, etc.

    Returns the saved file path. Overwrites same-day same-ticker.
    """
    out_dir = _recommendations_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    ticker = ticker.strip().upper()
    out_file = out_dir / f"{trade_date}_{ticker}.json"

    payload = {
        "trade_date": trade_date,
        "ticker": ticker,
        "label": label,
        "saved_at": time.time(),
        **final_state_summary,
    }

    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2, default=_json_default)
    _clear_history_cache()
    return out_file


@st.cache_data(ttl=30)
def get_recommendation_history() -> list[dict[str, Any]]:
    """Scan saved recommendations, return newest-first summary list.

    Cached for 30s; see get_history() for rationale.
    """
    root = _recommendations_dir()
    if not root.exists():
        return []

    entries: list[dict[str, Any]] = []
    for f in root.glob("*.json"):
        match = re.match(r"^(\d{4}-\d{2}-\d{2})_(\d{6})\.json$", f.name)
        if not match:
            continue
        try:
            with open(f, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, json.JSONDecodeError):
            continue
        entries.append(
            {
                "trade_date": match.group(1),
                "ticker": match.group(2),
                "label": data.get("label", ""),
                "path": str(f),
            }
        )
    entries.sort(key=lambda e: (e["trade_date"], e["ticker"]), reverse=True)
    return entries


def load_recommendation(trade_date: str, ticker: str) -> dict[str, Any] | None:
    """Load a saved recommendation by (trade_date, ticker). None if not found."""
    ticker = ticker.strip().upper()
    f = _recommendations_dir() / f"{trade_date}_{ticker}.json"
    if not f.exists():
        return None
    with open(f, encoding="utf-8") as fh:
        return json.load(fh)
