"""每日 cron pipeline:量化选股 -> (可选)逐只 LLM 深度分析 -> 综合推荐落盘。

默认模式(无 LLM,~3 分钟):
    py -3 scripts/daily_pipeline.py
    产出:outputs/daily/<YYYY-MM-DD>/
        ├── quant_picks.json         # 全量 Top N + 命中策略
        ├── quant_picks.md           # 可读摘要
        └── daily_report.md          # 综合报告(只含量化层)

LLM 模式(--with-llm,需 API key,每只 ~3-5 分钟):
    py -3 scripts/daily_pipeline.py --with-llm --top-n-for-llm 5
    产出额外:
        ├── llm/<ticker>/
        │   ├── complete_report.md
        │   └── final_state.json
        └── daily_report.md          # 综合报告含量化 + LLM + 冲突标签

注:本脚本独立运行,不依赖 cli.main.app。LLM 模式复用 TradingAgentsGraph。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import pandas as pd

# 允许从 scripts/ 目录运行,把项目根加进 sys.path
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.quant.quant_picker import format_top_picks_summary, pick


def _outputs_root() -> Path:
    """每日 pipeline 输出根目录。"""
    return _PROJECT_ROOT / "outputs" / "daily"


def _day_dir(today: pd.Timestamp) -> Path:
    p = _outputs_root() / today.strftime("%Y-%m-%d")
    p.mkdir(parents=True, exist_ok=True)
    return p


def _run_quant(today: pd.Timestamp, cache: str, top_n: int, top_k: int,
               workers: int, slice_days: int, progress: bool) -> dict:
    """跑量化层,返回 pick() 结果。"""
    cb = None
    if progress:
        def cb(completed: int, total: int, latest: dict) -> None:
            err = "ERR" if latest.get("error") else "OK"
            print(f"[{completed}/{total}] {latest['name']} ({latest['tier']}) "
                  f"hits={latest['n_hits']} {err} {latest['elapsed']:.1f}s", flush=True)

    print(f"[1/3] 启动量化选股: today={today.date()} cache={cache} top_n={top_n} workers={workers}")
    t0 = time.time()
    result = pick(
        today=today,
        daily_cache_name=cache,
        top_k=top_k,
        n_workers=workers,
        slice_days=slice_days,
        top_n=top_n,
        progress_callback=cb,
    )
    print(f"[1/3] 量化层完成: {result['n_strategies_run']} 策略 / {result['n_strategies_error']} 错误 / {time.time() - t0:.1f}s")
    return result


def _save_quant_outputs(result: dict, day_dir: Path) -> tuple[Path, Path]:
    """落盘量化层结果。返回 (json_path, md_path)。"""
    top = result.get("top_picks")
    payload = {
        "today": result["today"].strftime("%Y-%m-%d"),
        "elapsed": round(result["elapsed"], 2),
        "n_strategies_run": result["n_strategies_run"],
        "n_strategies_error": result["n_strategies_error"],
        "top_picks": top.to_dict(orient="records") if top is not None and len(top) > 0 else [],
        "per_strategy_stats": result["per_strategy_stats"],
    }
    json_path = day_dir / "quant_picks.json"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    md_path = day_dir / "quant_picks.md"
    md_path.write_text(format_top_picks_summary(result), encoding="utf-8")

    _save_standard_quant_pick(result)
    return json_path, md_path


def _save_standard_quant_pick(result: dict) -> Path:
    """按标准格式落盘到 ~/.tradingagents/quant_picks/{date}.json。

    quant_picker_node 只读该路径且要求 all_records(见
    tradingagents/agents/quant_picker_node.py);此前 daily_pipeline 只写
    outputs/daily/<date>/quant_picks.json 且缺 all_records,导致「复用已存
    结果跳过 pick()」的路径永不触发。与 web/history.save_quant_pick 同构,
    但不依赖 Streamlit(本脚本为 headless cron)。
    """
    out_dir = Path.home() / ".tradingagents" / "quant_picks"
    out_dir.mkdir(parents=True, exist_ok=True)
    top = result.get("top_picks")
    payload = {
        "today": result["today"].strftime("%Y-%m-%d"),
        "elapsed": round(result["elapsed"], 2),
        "n_strategies_run": result["n_strategies_run"],
        "n_strategies_error": result["n_strategies_error"],
        "top_picks": top.to_dict(orient="records") if top is not None and len(top) > 0 else [],
        "per_strategy_stats": result["per_strategy_stats"],
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
    out_file = out_dir / f"{result['today'].strftime('%Y-%m-%d')}.json"
    out_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return out_file


def _build_quant_contexts(quant_result: dict, tickers: list[str]) -> dict[str, str]:
    """从一次 pick() 结果为每个 ticker 提取 quant_context 字符串。

    这是 batch 优化:pick() 跑一次(~12 min),N 个 ticker 各自提取 context(O(1)),
    避免在 LangGraph 的 Quant Picker 节点里对每只股票重跑 pick()。
    """
    from tradingagents.agents.quant_picker_node import _extract_ticker_context

    contexts: dict[str, str] = {}
    for ticker in tickers:
        contexts[ticker] = _extract_ticker_context(quant_result, ticker)
    return contexts


def _run_llm_for_ticker(graph, ticker: str, trade_date: str,
                        quant_context: str) -> dict:
    """对单只股票跑完整 LangGraph,返回 final_state。

    使用 graph._run_graph(已支持 pre_quant_context 注入),走标准 finalize_graph_run
    路径,自动落盘 results_dir/<ticker>/TradingAgentsStrategy_logs/、memory log、
    process_signal。Quant Picker 节点 no-op(quant_pick_context 已预填)。
    """
    final_state, _signal = graph._run_graph(
        ticker, trade_date, pre_quant_context=quant_context,
    )
    return final_state


def _save_llm_output(ticker: str, final_state: dict, day_dir: Path) -> Path:
    """落盘单只 LLM 报告。"""
    llm_dir = day_dir / "llm" / ticker
    llm_dir.mkdir(parents=True, exist_ok=True)

    # final_state 可能含非 JSON 兼容类型,用 default=str 兜底
    json_path = llm_dir / "final_state.json"
    try:
        json_path.write_text(
            json.dumps(final_state, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
    except Exception as e:
        json_path.write_text(f"{{\"error\": \"serialize failed: {e}\"}}", encoding="utf-8")

    # 提取 Portfolio Manager 决策 + 最终交易决策
    pm_decision = (final_state.get("risk_debate_state") or {}).get("judge_decision", "")
    final_decision = final_state.get("final_trade_decision", "")
    quant_ctx = final_state.get("quant_pick_context", "")
    final_ranked = final_state.get("final_ranked_decision", "")

    md = [
        f"# LLM 深度分析报告: {ticker}",
        f"日期: {final_state.get('trade_date', '')}",
        "",
        "## 量化层上下文",
        "```",
        quant_ctx or "(无)",
        "```",
        "",
        "## Portfolio Manager 决策",
        pm_decision or "(无)",
        "",
        "## 最终交易决策",
        final_decision or "(无)",
        "",
        "## 综合推荐(冲突解决)",
        final_ranked or "(无)",
    ]
    (llm_dir / "complete_report.md").write_text("\n".join(md), encoding="utf-8")
    return llm_dir / "complete_report.md"


def _build_daily_report(today: pd.Timestamp, quant_result: dict,
                        llm_reports: dict[str, dict] | None, day_dir: Path) -> Path:
    """生成每日综合报告。"""
    top = quant_result.get("top_picks")
    lines = [
        f"# 每日选股综合报告 {today.strftime('%Y-%m-%d')}",
        "",
        f"- 量化层:{quant_result['n_strategies_run']} 策略 / {quant_result['n_strategies_error']} 错误 / {quant_result['elapsed']:.1f}s",
        f"- LLM 深度分析:{len(llm_reports or {})} 只",
        "",
        "## 一、量化层 Top N 候选",
        "",
    ]
    if top is None or len(top) == 0:
        lines.append("无候选股票(策略库均未生成信号)。")
    else:
        lines.append("| 排名 | 代码 | 命中数 | 加权分 | 加权胜率 | 持仓天 |")
        lines.append("|---|---|---|---|---|---|")
        for i, row in top.iterrows():
            lines.append(
                f"| {i + 1} | {row['stock_code']} | {int(row['n_strategies'])} | "
                f"{row['weighted_score']:.2f} | {row['avg_win_rate'] * 100:.1f}% | "
                f"{row['avg_holding_days']:.1f} |"
            )

    if llm_reports:
        lines += ["", "## 二、LLM 深度分析汇总", ""]
        lines.append("| 代码 | 综合推荐 | 最终决策 | 报告路径 |")
        lines.append("|---|---|---|---|")
        for ticker, state in llm_reports.items():
            final_ranked = (state.get("final_ranked_decision") or "").strip()
            final_decision = (state.get("final_trade_decision") or "").strip()[:60]
            report_path = f"llm/{ticker}/complete_report.md"
            lines.append(
                f"| {ticker} | {final_ranked or '(无)'} | {final_decision or '(无)'} | {report_path} |"
            )
    elif llm_reports is None:
        lines += ["", "## 二、LLM 深度分析", "", "(未启用 --with-llm)"]

    report_path = day_dir / "daily_report.md"
    report_path.write_text("\n".join(lines), encoding="utf-8")
    return report_path


def main() -> int:
    parser = argparse.ArgumentParser(description="每日 cron pipeline:量化选股 + (可选) LLM 深度分析")
    parser.add_argument("--today", default=None, help="选股日期 YYYY-MM-DD,默认今天")
    parser.add_argument("--cache", default="daily_main_board_liquid", help="日线缓存文件名")
    parser.add_argument("--top-k", type=int, default=2, help="每策略返回前 N 只")
    parser.add_argument("--top-n", type=int, default=20, help="量化层 Top N")
    parser.add_argument("--workers", type=int, default=8, help="量化层并行 worker 数")
    parser.add_argument("--slice-days", type=int, default=0, help="量化层切片天数")
    parser.add_argument("--no-progress", action="store_true", help="禁用进度回调")
    parser.add_argument("--with-llm", action="store_true", help="启用 LLM 深度分析(需 API key)")
    parser.add_argument("--top-n-for-llm", type=int, default=5,
                        help="对 Top N 中前几只跑 LLM(默认 5)")
    parser.add_argument("--tickers", default=None,
                        help="直接指定 LLM 分析的股票(逗号分隔,绕过量化层 Top N 选择;需配合 --with-llm)")
    parser.add_argument("--llm-provider", default=None,
                        help="LLM provider(默认读 DEFAULT_CONFIG)")
    args = parser.parse_args()

    # M11: 未来日期校验
    today = pd.Timestamp(args.today) if args.today else pd.Timestamp.now().normalize()
    if today > pd.Timestamp.now().normalize() + pd.Timedelta(days=1):
        print(f"[error] --today {today.strftime('%Y-%m-%d')} 是未来日期,数据不存在", file=sys.stderr)
        return 4

    # M12: --tickers 必须配合 --with-llm
    if args.tickers and not args.with_llm:
        print("[error] --tickers 必须配合 --with-llm 使用", file=sys.stderr)
        return 4

    day_dir = _day_dir(today)
    print(f"[setup] 输出目录: {day_dir}")

    # === 1. 量化层 ===
    try:
        quant_result = _run_quant(
            today, args.cache, args.top_n, args.top_k,
            args.workers, args.slice_days, progress=not args.no_progress,
        )
    except FileNotFoundError as e:
        print(f"[error] 量化层缓存文件未找到: {e}", file=sys.stderr)
        return 2
    except Exception as e:
        print(f"[error] 量化层失败: {type(e).__name__}: {e}", file=sys.stderr)
        return 2
    json_path, md_path = _save_quant_outputs(quant_result, day_dir)
    print(f"[2/3] 量化层落盘: {json_path.name} / {md_path.name}")

    # === 2. LLM 深度分析(可选) ===
    llm_reports: dict[str, dict] | None = None
    llm_failed_all = False
    if args.with_llm:
        llm_reports = {}
        if args.tickers:
            tickers = [t.strip() for t in args.tickers.split(",") if t.strip()]
        else:
            top = quant_result.get("top_picks")
            if top is None or len(top) == 0:
                print("[3/3] 无量化候选,跳过 LLM")
                tickers = []
            else:
                tickers = [row["stock_code"] for _, row in top.head(args.top_n_for_llm).iterrows()]

        if tickers:
            # S7: 校验 ticker 格式(防路径穿越,必须 6 位数字)
            import re as _re
            bad = [t for t in tickers if not _re.fullmatch(r"\d{6}", t)]
            if bad:
                print(f"[3/3] 非法 ticker(必须 6 位数字,跳过): {bad}")
                tickers = [t for t in tickers if t not in bad]
            if not tickers:
                print("[3/3] 校验后无合法 ticker,跳过 LLM")
            else:
                print(f"[3/3] LLM 深度分析 {len(tickers)} 只: {tickers}")
                cfg = dict(DEFAULT_CONFIG)
                cfg["quant_layer_enabled"] = True  # 节点会 no-op,因为 pre_quant_context 已注入
                if args.llm_provider:
                    cfg["llm_provider"] = args.llm_provider.lower()
                trade_date = today.strftime("%Y-%m-%d")

                # batch 优化:一次 pick() 结果 -> 每个 ticker 的 quant_context
                # 避免 Quant Picker 节点对每只股票重跑 pick()(~12 min/只)
                print(f"  [prep] 从量化层结果提取 {len(tickers)} 只 ticker 的 quant_context...")
                quant_contexts = _build_quant_contexts(quant_result, tickers)

                # 循环外初始化 graph 一次,复用 LLM client(避免每只股票重建)
                from tradingagents.graph.trading_graph import TradingAgentsGraph
                selected_analysts = ["market", "social", "news", "fundamentals"]
                print(f"  [prep] 初始化 LangGraph(analysts={selected_analysts})...")
                try:
                    graph = TradingAgentsGraph(selected_analysts, config=cfg, debug=False)
                except Exception as e:
                    print(f"[error] LangGraph 初始化失败: {type(e).__name__}: {e}", file=sys.stderr)
                    return 3

                n_ok = 0
                n_fail = 0
                for i, ticker in enumerate(tickers, 1):
                    print(f"  [{i}/{len(tickers)}] {ticker} ...", flush=True)
                    t0 = time.time()
                    try:
                        state = _run_llm_for_ticker(
                            graph, ticker, trade_date, quant_contexts.get(ticker, ""),
                        )
                        report_path = _save_llm_output(ticker, state, day_dir)
                        llm_reports[ticker] = state
                        n_ok += 1
                        print(f"      OK {time.time() - t0:.1f}s -> {report_path.relative_to(day_dir)}")
                    except Exception as e:
                        n_fail += 1
                        print(f"      FAIL {time.time() - t0:.1f}s: {type(e).__name__}: {e}")
                print(f"[3/3] LLM 完成: {n_ok} OK / {n_fail} FAIL")
                if n_ok == 0 and n_fail > 0:
                    llm_failed_all = True
    else:
        print("[3/3] 跳过 LLM(未启用 --with-llm)")

    # === 3. 综合报告 ===
    report_path = _build_daily_report(today, quant_result, llm_reports, day_dir)
    print(f"\n[done] 每日综合报告: {report_path}")

    if llm_failed_all:
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
