"""`tradingagents quant-pick` 子命令:纯量化选股,不依赖 LLM。

跑策略库(multiprocessing.Pool)产出 Top N 候选 + 命中策略 + 加权分 + 入场建议。
策略数由 get_all_strategies_final() 动态决定(弃用的不参与)。
支持输出到终端(rich table)/ JSON / CSV / Markdown。

使用:
    tradingagents quant-pick --today 2026-07-17 --top-n 20
    tradingagents quant-pick --output-format json --output-path picks.json
    tradingagents quant-pick --output-format csv --output-path picks.csv
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import pandas as pd
import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from tradingagents.quant.quant_picker import (
    format_top_picks_summary,
    pick,
)

console = Console()


def _parse_today(today: Optional[str]) -> pd.Timestamp:
    if today:
        return pd.Timestamp(today)
    return pd.Timestamp.now().normalize()


def _resolve_output_path(path: Optional[str], fmt: str, today: pd.Timestamp) -> Optional[Path]:
    if not path:
        return None
    p = Path(path)
    if p.is_dir() or (not p.suffix and path.endswith(("/", "\\"))):
        # 用户给的是目录,生成默认文件名
        ext = {"json": "json", "csv": "csv", "markdown": "md", "terminal": "txt"}.get(fmt, "txt")
        p = p / f"quant_pick_{today.strftime('%Y%m%d')}.{ext}"
    return p


def _print_terminal(result: dict) -> None:
    """rich table 展示 Top N + 命中策略详情。"""
    top = result.get("top_picks")
    today = result["today"]
    n_run = result["n_strategies_run"]
    n_err = result["n_strategies_error"]
    elapsed = result["elapsed"]

    console.print(Panel(
        f"[bold green]量化选股 Top {len(top) if top is not None else 0}[/bold green]  "
        f"日期: {today.date()}  策略: {n_run} (错误 {n_err})  耗时: {elapsed:.1f}s",
        border_style="green",
    ))

    if top is None or len(top) == 0:
        console.print("[yellow]无候选股票(策略库均未生成信号)[/yellow]")
        return

    table = Table(show_header=True, header_style="bold magenta", show_lines=True, expand=True)
    table.add_column("排名", justify="center", width=4)
    table.add_column("代码", style="cyan", width=10)
    table.add_column("命中数", justify="right", width=6)
    table.add_column("加权分", justify="right", style="bold yellow", width=10)
    table.add_column("加权胜率", justify="right", width=10)
    table.add_column("持仓天", justify="right", width=8)
    table.add_column("命中策略(tier)", no_wrap=False)

    by_code: dict[str, list[dict]] = {}
    for rec in result["all_records"]:
        by_code.setdefault(rec["stock_code"], []).append(rec)

    for i, row in top.iterrows():
        code = row["stock_code"]
        recs = by_code.get(code, [])
        recs_sorted = sorted(recs, key=lambda r: r["strategy_comp"], reverse=True)
        strat_str = "\n".join(
            f"[{r['tier']}] {r['strategy']} (comp={r['strategy_comp']:.2f})"
            for r in recs_sorted[:5]
        )
        if len(recs_sorted) > 5:
            strat_str += f"\n... 还有 {len(recs_sorted) - 5} 个"
        # 老缓存无 win_rate 数据时 avg_win_rate 可能是 NaN,避免显示 "nan%"
        wr = row["avg_win_rate"]
        wr_str = f"{wr * 100:.1f}%" if pd.notna(wr) else "-"
        table.add_row(
            str(i + 1),
            code,
            str(int(row["n_strategies"])),
            f"{row['weighted_score']:.2f}",
            wr_str,
            f"{row['avg_holding_days']:.1f}",
            strat_str,
        )

    console.print(table)

    # 入场建议(对前 5 只展示)
    if top is not None and len(top) > 0:
        advice_table = Table(show_header=True, header_style="bold blue", show_lines=True, expand=True)
        advice_table.add_column("代码", style="cyan", width=10)
        advice_table.add_column("策略", style="green", no_wrap=False)
        advice_table.add_column("持仓天", justify="right", width=8)
        advice_table.add_column("入场建议", no_wrap=False)

        shown_codes = set()
        for _, row in top.head(5).iterrows():
            code = row["stock_code"]
            if code in shown_codes:
                continue
            shown_codes.add(code)
            recs = by_code.get(code, [])
            if not recs:
                continue
            top_rec = max(recs, key=lambda r: r["strategy_comp"])
            advice_table.add_row(
                code,
                f"[{top_rec['tier']}] {top_rec['strategy']}",
                str(top_rec["holding_days"]),
                top_rec["entry_advice"],
            )
        console.print(Panel(advice_table, title="入场建议(Top 5)", border_style="blue"))


def _serialize_result(result: dict) -> dict:
    """把 pick() 返回的 result 序列化为 JSON 兼容格式。"""
    top = result.get("top_picks")
    return {
        "today": result["today"].strftime("%Y-%m-%d"),
        "elapsed": round(result["elapsed"], 2),
        "n_strategies_run": result["n_strategies_run"],
        "n_strategies_error": result["n_strategies_error"],
        "top_picks": top.to_dict(orient="records") if top is not None and len(top) > 0 else [],
        "all_records": [
            {
                **rec,
                "date": rec["date"].strftime("%Y-%m-%d") if hasattr(rec.get("date"), "strftime") else str(rec.get("date", "")),
            }
            for rec in result["all_records"]
        ],
        "per_strategy_stats": result["per_strategy_stats"],
    }


def register_quant_pick(app: typer.Typer) -> None:
    """把 quant-pick 子命令注册到主 app。"""

    @app.command(name="quant-pick", help="跑策略库量化选股(不依赖 LLM),产出 Top N 候选 + 命中策略 + 加权分 + 入场建议。")
    def quant_pick(
        today: Optional[str] = typer.Option(
            None, "--today", "-t", help="选股日期 YYYY-MM-DD(默认今天)。",
        ),
        cache: str = typer.Option(
            "daily_main_board", "--cache",
            help="日线数据缓存文件名(不含 .parquet 后缀)。daily_main_board=全量主板(~3042股,默认,流动性/价格筛选在选股层执行);daily_main_board_liquid=流动性前80%(~2433股,数据采集层已截断)。",
        ),
        top_k: int = typer.Option(
            2, "--top-k", help="每个策略返回的前 N 只股票。",
        ),
        top_n: int = typer.Option(
            20, "--top-n", help="最终返回 Top N 候选数(5/10/20)。",
        ),
        workers: int = typer.Option(
            8, "--workers", "-w", help="并行 worker 数(Windows 必须 spawn,启动较慢)。",
        ),
        slice_days: int = typer.Option(
            0, "--slice-days", help="切片天数(0=全量;短线策略可用切片加速,周月季策略自动用全量)。",
        ),
        output_format: str = typer.Option(
            "terminal", "--output-format", "-f",
            help="输出格式:terminal / json / csv / markdown(默认 terminal,rich 表格)。",
        ),
        output_path: Optional[str] = typer.Option(
            None, "--output-path", "-o",
            help="输出文件路径(不指定则只打印到 stdout)。目录则自动命名 quant_pick_YYYYMMDD.<ext>。",
        ),
        no_progress: bool = typer.Option(
            False, "--no-progress", help="禁用进度回调(安静模式)。",
        ),
    ) -> None:
        """跑策略库批量选股,产出 Top N 候选。"""
        today_ts = _parse_today(today)
        fmt = output_format.lower()
        if fmt not in {"terminal", "json", "csv", "markdown"}:
            console.print(f"[red]无效的 output-format: {output_format}[/red]")
            raise typer.Exit(code=2) from None

        if not no_progress:
            def progress(completed: int, total: int, latest: dict) -> None:
                err = "ERR" if latest.get("error") else "OK"
                console.log(
                    f"[{completed}/{total}] {latest['name']} ({latest['tier']}) "
                    f"hits={latest['n_hits']} {err} {latest['elapsed']:.1f}s"
                )
        else:
            progress = None  # type: ignore[assignment]

        console.print(f"[bold cyan]启动量化选股...[/bold cyan] 日期={today_ts.date()} cache={cache} top_n={top_n} workers={workers}")

        try:
            result = pick(
                today=today_ts,
                daily_cache_name=cache,
                top_k=top_k,
                n_workers=workers,
                slice_days=slice_days,
                top_n=top_n,
                progress_callback=progress,
            )
        except FileNotFoundError as e:
            console.print(f"[red]缓存文件未找到: {e}[/red]")
            console.print(f"[dim]检查 {cache}.parquet 是否在 tradingagents/quant/outputs/cache/ 下[/dim]")
            raise typer.Exit(code=3) from e
        except Exception as e:
            console.print(f"[red]量化选股失败: {type(e).__name__}: {e}[/red]")
            raise typer.Exit(code=1) from e

        # 输出
        out_file = _resolve_output_path(output_path, fmt, today_ts)

        if fmt == "terminal":
            _print_terminal(result)
            if out_file:
                # terminal 模式 + 指定路径 = 把 markdown 摘要写到文件
                out_file.parent.mkdir(parents=True, exist_ok=True)
                out_file.write_text(format_top_picks_summary(result), encoding="utf-8")
                console.print(f"\n[green]摘要已写入:[/green] {out_file}")
        elif fmt == "json":
            payload = _serialize_result(result)
            text = json.dumps(payload, ensure_ascii=False, indent=2)
            if out_file:
                out_file.parent.mkdir(parents=True, exist_ok=True)
                out_file.write_text(text, encoding="utf-8")
                console.print(f"[green]JSON 已写入:[/green] {out_file}")
            else:
                console.print_json(text)
        elif fmt == "csv":
            top = result.get("top_picks")
            if top is None or len(top) == 0:
                console.print("[yellow]无候选股票,CSV 不输出[/yellow]")
                return
            if out_file:
                out_file.parent.mkdir(parents=True, exist_ok=True)
                top.to_csv(out_file, index=False, encoding="utf-8-sig")
                console.print(f"[green]CSV 已写入:[/green] {out_file}")
            else:
                console.print(top.to_csv(index=False))
        elif fmt == "markdown":
            text = format_top_picks_summary(result)
            if out_file:
                out_file.parent.mkdir(parents=True, exist_ok=True)
                out_file.write_text(text, encoding="utf-8")
                console.print(f"[green]Markdown 已写入:[/green] {out_file}")
            else:
                console.print(text)
