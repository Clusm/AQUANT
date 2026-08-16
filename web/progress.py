"""Thread-safe progress tracker shared between the background runner and Streamlit UI."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Optional

PIPELINE_STAGES: list[dict[str, str]] = [
    {"id": "market", "name": "技术分析", "icon": "", "report_key": "market_report"},
    {"id": "social", "name": "情绪分析", "icon": "", "report_key": "sentiment_report"},
    {"id": "news", "name": "新闻舆情", "icon": "", "report_key": "news_report"},
    {"id": "fundamentals", "name": "基本面", "icon": "", "report_key": "fundamentals_report"},
    {"id": "policy", "name": "政策分析", "icon": "", "report_key": "policy_report"},
    {"id": "hot_money", "name": "游资追踪", "icon": "", "report_key": "hot_money_report"},
    {"id": "lockup", "name": "解禁监控", "icon": "", "report_key": "lockup_report"},
    {"id": "quality_gate", "name": "质量门控", "icon": "", "report_key": "data_quality_summary"},
    {"id": "debate", "name": "多空辩论", "icon": "", "report_key": "investment_plan"},
    {"id": "trader", "name": "交易决策", "icon": "", "report_key": "trader_investment_plan"},
    {"id": "risk", "name": "风控评估", "icon": "", "report_key": "risk_debate_state"},
    {"id": "pm", "name": "最终决策", "icon": "", "report_key": "final_trade_decision"},
]

STAGE_IDS = [s["id"] for s in PIPELINE_STAGES]


@dataclass
class ProgressTracker:
    """Mutable state container updated by the runner thread, read by the UI."""

    ticker: str = ""
    trade_date: str = ""
    start_time: float = field(default_factory=time.time)

    is_running: bool = False
    is_complete: bool = False
    is_paused: bool = False
    stop_requested: bool = False
    error: Optional[str] = None

    current_stage: str = ""
    completed_stages: list[str] = field(default_factory=list)
    stage_reports: dict[str, str] = field(default_factory=dict)

    final_state: dict[str, Any] = field(default_factory=dict)
    signal: str = ""

    # Quant layer hint: when the quant layer is skipped for manual tickers,
    # this field is set so the UI can display a hint to the user.
    quant_skip_hint: str = ""

    llm_calls: int = 0
    tool_calls: int = 0
    tokens_in: int = 0
    tokens_out: int = 0

    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _pause_gate: threading.Event = field(
        default_factory=threading.Event,
        init=False,
        repr=False,
    )

    def __post_init__(self) -> None:
        self._pause_gate.set()

    def pause(self) -> bool:
        """Pause pipeline advancement after the current streamed step finishes."""
        with self._lock:
            if (
                not self.is_running
                or self.is_complete
                or self.error
                or self.is_paused
                or self.stop_requested
            ):
                return False
            self.is_paused = True
            self._pause_gate.clear()
            return True

    def resume(self) -> bool:
        """Allow the runner thread to continue to the next streamed step."""
        with self._lock:
            if not self.is_paused or self.stop_requested:
                return False
            self.is_paused = False
            self._pause_gate.set()
            return True

    def request_stop(self) -> bool:
        """Request cancellation and clear user-visible progress immediately."""
        with self._lock:
            if not self.is_running or self.is_complete or self.error or self.stop_requested:
                return False
            self.stop_requested = True
            self.is_paused = False
            self.current_stage = ""
            self.completed_stages.clear()
            self.stage_reports.clear()
            self.final_state = {}
            self.signal = ""
            self.llm_calls = 0
            self.tool_calls = 0
            self.tokens_in = 0
            self.tokens_out = 0
            self._pause_gate.set()
            return True

    def wait_if_paused(self) -> None:
        self._pause_gate.wait()

    def mark_stopped(self) -> None:
        with self._lock:
            self.is_running = False
            self.is_complete = False
            self.is_paused = False
            self.stop_requested = False
            self.error = None
            self.current_stage = ""
            self.completed_stages.clear()
            self.stage_reports.clear()
            self.final_state = {}
            self.signal = ""
            self.llm_calls = 0
            self.tool_calls = 0
            self.tokens_in = 0
            self.tokens_out = 0
            self._pause_gate.set()

    def mark_stage_active(self, stage_id: str) -> None:
        with self._lock:
            if self.stop_requested:
                return
            self.current_stage = stage_id

    def mark_stage_done(self, stage_id: str, report: str = "") -> None:
        with self._lock:
            if self.stop_requested:
                return
            if stage_id not in self.completed_stages:
                self.completed_stages.append(stage_id)
            if report:
                self.stage_reports[stage_id] = report
            self.current_stage = ""

    def mark_complete(self, final_state: dict, signal: str) -> None:
        with self._lock:
            self.final_state = final_state
            self.signal = signal
            self.is_running = False
            self.is_complete = True
            self.is_paused = False
            self.stop_requested = False
            self._pause_gate.set()

    def mark_error(self, err: str) -> None:
        with self._lock:
            self.error = err
            self.is_running = False
            self.is_paused = False
            self.stop_requested = False
            self._pause_gate.set()

    def update_stats(self, llm: int, tool: int, tok_in: int, tok_out: int) -> None:
        with self._lock:
            if self.stop_requested:
                return
            self.llm_calls = llm
            self.tool_calls = tool
            self.tokens_in = tok_in
            self.tokens_out = tok_out

    @property
    def elapsed(self) -> float:
        return time.time() - self.start_time

    def stage_status(self, stage_id: str) -> str:
        with self._lock:
            if stage_id in self.completed_stages:
                return "done"
            if stage_id == self.current_stage:
                return "active"
            return "pending"


@dataclass
class QuantProgressTracker(ProgressTracker):
    """Progress tracker for the quant pre-filter layer (strategies batch run).

    Independent of PIPELINE_STAGES - the quant layer runs *before* the LangGraph
    pipeline and reports progress per strategy via pick()'s progress_callback,
    not per LangGraph node. The parent's stage-related fields stay empty during
    a quant run.

    Lifecycle:
      0. (optional) data_update phase: mark_data_update_active -> mark_data_update_progress* -> mark_data_update_done
      1. is_running=True, total_strategies=N (set by run_quant_pick_in_thread from get_all_strategies_final())
      2. mark_strategy_done(name, stats) called N times by progress_callback
      3. mark_pick_complete(top_picks, elapsed) called once at the end
      4. UI reads top_picks via render_quant_progress
    """

    total_strategies: int = 0
    completed_strategies: int = 0
    latest_strategy: str = ""
    per_strategy_stats: dict[str, dict] = field(default_factory=dict)
    top_picks: Any = None  # pd.DataFrame
    all_records: list = field(default_factory=list)  # 每策略命中明细(供 UI 详情面板)
    pick_elapsed: float = 0.0
    n_strategies_error: int = 0

    # 数据增量更新阶段
    data_update_active: bool = False
    data_update_cache_name: str = ""
    data_update_last_date: Any = None  # pd.Timestamp
    data_update_days_behind: int = 0
    data_update_completed: int = 0
    data_update_total: int = 0
    data_update_failed: int = 0
    data_update_latest_code: str = ""

    def mark_data_update_active(self, cache_name: str, last_date: Any, days_behind: int) -> None:
        """Enter the data increment phase before pick() starts."""
        with self._lock:
            if self.stop_requested:
                return
            self.data_update_active = True
            self.data_update_cache_name = cache_name
            self.data_update_last_date = last_date
            self.data_update_days_behind = days_behind

    def mark_data_update_progress(self, completed: int, total: int, stats: dict) -> None:
        """Update progress during increment_data()."""
        with self._lock:
            if self.stop_requested:
                return
            self.data_update_completed = completed
            self.data_update_total = total
            self.data_update_failed = stats.get("failed", 0)
            self.data_update_latest_code = stats.get("latest_code", "")

    def mark_data_update_done(self) -> None:
        """Exit the data increment phase."""
        with self._lock:
            self.data_update_active = False

    def mark_strategy_done(self, name: str, stats: dict) -> None:
        """Record one strategy's result. Thread-safe; no-op if stop_requested."""
        with self._lock:
            if self.stop_requested:
                return
            self.latest_strategy = name
            self.per_strategy_stats[name] = {
                "tier": stats.get("tier", "?"),
                "comp": stats.get("comp", 0.0),
                "n_hits": stats.get("n_hits", 0),
                "elapsed": stats.get("elapsed", 0.0),
                "error": stats.get("error"),
                "needs_full": stats.get("needs_full", False),
            }
            self.completed_strategies = len(self.per_strategy_stats)
            if stats.get("error"):
                self.n_strategies_error = self.n_strategies_error + 1

    def mark_pick_complete(self, top_picks: Any, elapsed: float,
                           n_run: int = 0, n_error: int = 0,
                           all_records: list | None = None) -> None:
        """Finalize the pick() run. Thread-safe."""
        with self._lock:
            self.top_picks = top_picks
            self.pick_elapsed = elapsed
            if n_run:
                self.total_strategies = n_run
            if n_error:
                self.n_strategies_error = n_error
            if all_records is not None:
                self.all_records = all_records
            self.is_running = False
            self.is_complete = True
            self.is_paused = False
            self.stop_requested = False
            self._pause_gate.set()

    def request_stop(self) -> bool:
        """Override to also clear quant-specific fields."""
        with self._lock:
            if not self.is_running or self.is_complete or self.error or self.stop_requested:
                return False
            self.stop_requested = True
            self.is_paused = False
            self.latest_strategy = ""
            self.per_strategy_stats.clear()
            self.top_picks = None
            self.pick_elapsed = 0.0
            self._pause_gate.set()
            return True

    @property
    def strategy_progress_pct(self) -> float:
        """0.0 - 1.0, completed / total."""
        if self.total_strategies <= 0:
            return 0.0
        return min(1.0, self.completed_strategies / self.total_strategies)
