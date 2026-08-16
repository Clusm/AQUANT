"""Evaluate the 4-tier recommendation mechanism against subsequent price moves.

输入(自动扫描):
  - ~/.tradingagents/recommendations/{date}_{ticker}.json   (web 保存的综合推荐)
  - ~/.tradingagents/logs/**/full_states_log_{date}.json    (完整分析日志,扩充样本)
行情:
  - daily_main_board.parquet(主板日线,含后续价格)
  - index_000001.parquet(上证指数,基准)

评估口径:
  - 信号在 trade_date 收盘后发出,实际买入点 = 该股下一交易日收盘(entry)。
  - 前向收益 = close[entry + k]/entry_close - 1,k ∈ {3, 5, 10, 20} 交易日。
  - 超额收益 = 个股前向收益 - 同期上证指数前向收益(同样 entry 口径)。
按 4 档标签(🟢强买/🟡关注/🟠冲突/🔴弃)与 (quant_state, llm_rating) 组合聚合。

用法: py -3 scripts/eval_recommendations.py --board <path> [--index <path>]
"""
from __future__ import annotations

import argparse
import json
import os
import re
from collections import defaultdict
from pathlib import Path

import pandas as pd

from tradingagents.agents.conflict_resolver import (
    _detect_llm_rating,
    _detect_quant_state,
    compute_conviction,
)

REC_DIR = Path.home() / ".tradingagents" / "recommendations"
LOG_ROOT = Path.home() / ".tradingagents" / "logs"

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_QUANT_CACHE = (
    Path(os.environ.get("QUANT_CACHE_DIR", _PROJECT_ROOT / "tradingagents" / "quant" / "outputs" / "cache"))
)
DEFAULT_BOARD = str(_QUANT_CACHE / "daily_main_board.parquet")
DEFAULT_INDEX = str(_QUANT_CACHE / "index_000001.parquet")

HORIZONS = (3, 5, 10, 20)

# 与 web/components/recommendation.parse_label 语义一致(本地轻量实现,
# 避免引入 streamlit / a_stock 重依赖)。
_LABEL_PREFIX_TO_BUCKET = {"🟢": "strong_buy", "🟡": "watch", "🟠": "conflict", "🔴": "discard"}


def _parse_label_line(line: str) -> str:
    line = line.strip()
    for emoji, bucket in _LABEL_PREFIX_TO_BUCKET.items():
        if line.startswith(emoji):
            return bucket
    low = line.lower()
    if "strong" in low or "强买" in line:
        return "strong_buy"
    if "watch" in low or "关注" in line:
        return "watch"
    if "conflict" in low or "冲突" in line:
        return "conflict"
    if low in ("buy", "overweight"):
        return "strong_buy"
    if low == "hold":
        return "watch"
    if low in ("sell", "underweight"):
        return "discard"
    return "discard"


def parse_label(text: str) -> str:
    if not text:
        return "discard"
    text = str(text).strip()
    if text in _LABEL_PREFIX_TO_BUCKET.values():
        return text
    m = re.search(r"标签[^\n:：]*[:：]\s*([^\n]+)", text)
    if m:
        return _parse_label_line(m.group(1))
    return _parse_label_line(text)


def _extract_conviction(d: dict) -> int | None:
    v = d.get("conviction_score")
    if isinstance(v, (int, float)):
        return int(v)
    m = re.search(r"置信分.*?(\d+)", str(d.get("final_ranked_decision", "")))
    return int(m.group(1)) if m else None


def _record_from(d: dict) -> dict:
    """从一份 JSON(推荐或完整日志)提取结构化信号;置信分优先存值,否则按规则重算。"""
    qs, qi = _detect_quant_state(d.get("quant_pick_context", ""))
    lr = _detect_llm_rating(d.get("final_trade_decision", ""))
    conviction = _extract_conviction(d)
    if conviction is None:
        conviction = compute_conviction(qs, qi, lr)
    return {
        "quant_state": qs,
        "llm_rating": lr,
        "conviction": conviction,
    }


def _load_records() -> list[dict]:
    """合并 recommendations 与 full_states_log,按 (date, ticker) 去重,优先推荐记录。"""
    by_key: dict[tuple[str, str], dict] = {}

    def _add(date, ticker, rec, src):
        key = (date, ticker)
        # 已有推荐记录时优先保留(label 更干净),日志仅用于扩充缺失项
        if src == "log" and key in by_key:
            return
        by_key[key] = rec

    if REC_DIR.exists():
        for f in REC_DIR.glob("*.json"):
            m = re.match(r"^(\d{4}-\d{2}-\d{2})_(\d{6})\.json$", f.name)
            if not m:
                continue
            with open(f, encoding="utf-8") as fh:
                d = json.load(fh)
            date, ticker = m.group(1), m.group(2)
            _add(date, ticker, {
                "date": date,
                "ticker": ticker,
                "label": parse_label(d.get("label", "")),
                **_record_from(d),
            }, "rec")

    for f in sorted(LOG_ROOT.rglob("full_states_log_*.json")):
        m = re.search(r"full_states_log_(\d{4}-\d{2}-\d{2})\.json$", f.name)
        if not m:
            continue
        with open(f, encoding="utf-8") as fh:
            d = json.load(fh)
        date, ticker = m.group(1), str(d.get("company_of_interest", "")).strip()
        if not ticker:
            continue
        _add(date, ticker, {
            "date": date,
            "ticker": ticker,
            "label": parse_label(d.get("final_signal_label") or d.get("final_ranked_decision", "")),
            **_record_from(d),
        }, "log")

    return sorted(by_key.values(), key=lambda r: (r["date"], r["ticker"]))


def _forward_returns(dates: pd.Series, closes: pd.Series, rec_date: pd.Timestamp) -> dict[int, float]:
    """entry = 信号日期之后的第一个交易日收盘;返回 {horizon: 前向收益}。"""
    ts = pd.DatetimeIndex(pd.to_datetime(dates)).normalize().values
    entry_idx = None
    for i, d in enumerate(ts):
        if d > rec_date:
            entry_idx = i
            break
    if entry_idx is None or entry_idx + 1 >= len(ts):
        return {}
    entry_close = float(closes.iloc[entry_idx])
    if entry_close <= 0:
        return {}
    out = {}
    for k in HORIZONS:
        fwd = entry_idx + k
        if fwd < len(ts):
            fwd_close = float(closes.iloc[fwd])
            if fwd_close > 0:
                out[k] = fwd_close / entry_close - 1.0
    return out


def _summarize(rows: list[dict], horizons: tuple[int, ...]) -> dict:
    if not rows:
        return {"n": 0}
    agg: dict[int, list[float]] = defaultdict(list)
    hit: dict[int, int] = defaultdict(int)
    for r in rows:
        for k, ret in r["fwd"].items():
            agg[k].append(ret)
            if ret > 0:
                hit[k] += 1
    out = {"n": len(rows)}
    for k in horizons:
        vals = agg.get(k)
        if not vals:
            out[f"n{k}"] = 0
            continue
        out[f"n{k}"] = len(vals)
        out[f"mean{k}"] = sum(vals) / len(vals)
        out[f"med{k}"] = sorted(vals)[len(vals) // 2]
        out[f"hit{k}"] = hit[k] / len(vals)
    return out


def _fmt_pct(x):
    return f"{x * 100:+.1f}%"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--board", default=DEFAULT_BOARD)
    ap.add_argument("--index", default=DEFAULT_INDEX)
    args = ap.parse_args()

    board = pd.read_parquet(args.board)
    board["trade_date"] = pd.to_datetime(board["trade_date"]).dt.normalize()
    idx = pd.read_parquet(args.index)
    idx["trade_date"] = pd.to_datetime(idx["trade_date"]).dt.normalize()
    idx = idx.sort_values("trade_date").reset_index(drop=True)
    idx_dates = idx["trade_date"].values
    idx_closes = idx["close"]

    recs = _load_records()
    print(f"记录: {len(recs)} (推荐 + 日志去重)")

    # 逐条计算前向收益与指数基准
    enriched: list[dict] = []
    for r in recs:
        sub = board[board["stock_code"] == r["ticker"]].sort_values("trade_date").reset_index(drop=True)
        if len(sub) == 0:
            continue
        rd = pd.Timestamp(r["date"]).normalize()
        r["fwd"] = _forward_returns(sub["trade_date"], sub["close"], rd)
        r["idx"] = _forward_returns(idx_dates, idx_closes, rd)
        r["excess"] = {k: r["fwd"].get(k, float("nan")) - r["idx"].get(k, float("nan"))
                       for k in HORIZONS}
        enriched.append(r)

    print(f"能取到行情的: {len(enriched)}\n")

    # ── 1) 按 4 档标签 ──
    print("=" * 72)
    print("按 4 档标签(信号下一交易日收盘买入, 前向收益 / 超额 vs 上证)")
    print("=" * 72)
    by_bucket: dict[str, list[dict]] = defaultdict(list)
    for r in enriched:
        by_bucket[r["label"]].append(r)
    order = ["strong_buy", "watch", "conflict", "discard"]
    header = f"{'bucket':<10} {'n':>3} " + " ".join(
        f"{k}d{'mean':>8}{'hit':>6}" for k in HORIZONS)
    print(header)
    for b in order:
        rows = by_bucket[b]
        if not rows:
            print(f"{b:<10} {0:>3} (无样本)")
            continue
        s = _summarize(rows, HORIZONS)
        line = f"{b:<10} {s['n']:>3} "
        for k in HORIZONS:
            if s.get(f"n{k}"):
                line += f"{_fmt_pct(s[f'mean{k}']):>8}{s[f'hit{k}'] * 100:>5.0f}%"
            else:
                line += f"{'--':>8}{'--':>6}"
        print(line)
        # 超额
        ex = {k: [r["excess"][k] for r in rows if k in r["fwd"] and not pd.isna(r["excess"][k])] for k in HORIZONS}
        exline = f"{'  excess':<10} {'':>3} "
        for k in HORIZONS:
            v = ex.get(k) or []
            exline += f"{(_fmt_pct(sum(v)/len(v)) if v else '--'):>8}{'':>6}"
        print(exline)

    # ── 2) 按 (quant_state, llm_rating) 组合 ──
    print("\n" + "=" * 72)
    print("按 (quant_state, llm_rating) 组合(诊断 watch 大桶内部)"
          "——均值前向收益")
    print("=" * 72)
    combos: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for r in enriched:
        combos[(r["quant_state"], r["llm_rating"])].append(r)
    print(f"{'quant':<8} {'llm':<11} {'n':>3} " + " ".join(f"{k}d{'mean':>9}" for k in (5, 10)))
    for (qs, lr), rows in sorted(combos.items(), key=lambda kv: -len(kv[1])):
        s = _summarize(rows, (5, 10))
        line = f"{qs:<8} {lr:<11} {s['n']:>3} "
        for k in (5, 10):
            line += f"{(_fmt_pct(s[f'mean{k}']) if s.get(f'n{k}') else '--'):>9}"
        print(line)

    # ── 3) 置信分 vs 前向收益(验证连续分可用性)──
    with_score = [r for r in enriched if r.get("conviction") is not None]
    if with_score:
        print("\n" + "=" * 72)
        print("按置信分档(前向收益均值; 检验连续分能否排序)")
        print("=" * 72)
        print(f"{'band':<8} {'n':>3} " + " ".join(f"{k}d{'mean':>9}" for k in (5, 10)))
        for name, (lo, hi) in (("0-30", (0, 30)), ("31-60", (31, 60)), ("61-100", (61, 100))):
            rows = [r for r in with_score if lo <= r["conviction"] <= hi]
            if not rows:
                continue
            s = _summarize(rows, (5, 10))
            line = f"{name:<8} {s['n']:>3} "
            for k in (5, 10):
                line += f"{(_fmt_pct(s[f'mean{k}']) if s.get(f'n{k}') else '--'):>9}"
            print(line)

    # ── 4) 明细 ──
    print("\n" + "=" * 72)
    print("逐条明细(前向收益, %; -- = 尚无足够后续行情)")
    print("=" * 72)
    print(f"{'date':<10} {'ticker':<7} {'bucket':<11} {'q':<7} {'llm':<10} {'置信':>3} " +
          " ".join(f"{k}d{'ret':>8}" for k in HORIZONS) + "  5d超额")
    for r in enriched:
        c = r.get("conviction")
        line = (f"{r['date']:<10} {r['ticker']:<7} {r['label']:<11} "
                f"{r['quant_state']:<7} {r['llm_rating']:<10} {c if c is not None else '--':>3} ")
        for k in HORIZONS:
            v = r["fwd"].get(k)
            line += f"{(_fmt_pct(v) if v is not None else '--'):>8}"
        e5 = r["excess"].get(5)
        line += f"  {(_fmt_pct(e5) if not pd.isna(e5) else '--')}"
        print(line)

    print("\n注: 样本小(强买 2/关注 16),结论仅作机制诊断,不构成投资建议。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
