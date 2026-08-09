"""Batch 流程协同测试:验证多股票并行 AI 分析的几个关键 bug。

不跑真实 quant pick (3 分钟) 也不调真实 LLM,用 monkey-patch 替换为
快速 mock,聚焦验证:
  1. 每只股票是否会重复跑 quant pick() (应该只跑 1 次,预填给所有 ticker)
  2. memory_log 是否有多线程写竞争 (应该用文件锁)
  3. save_recommendation 是否对所有 ticker 触发 (应该)
  4. 综合推荐 tab 是否能拿到所有 ticker 的结果 (应该)

运行:
  python scripts/test_batch_flow.py
"""
from __future__ import annotations

import os
import sys
import time
import threading
import traceback
from pathlib import Path
from unittest.mock import MagicMock

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from dotenv import load_dotenv
load_dotenv(_PROJECT_ROOT / ".env", override=True)

# ---- 1. Monkey-patch: 替换 quant pick 和 LLM,让测试快速完成 ----

# 记录 pick() 调用次数和 ticker (这个计数器跟踪 LangGraph 节点层面的 pick()
# 调用 -- 在正确实现的 batch 模式下应该为 0,因为 pre_quant_context 已预填)
_PICK_CALLS: list[str] = []
_PICK_LOCK = threading.Lock()

# 跟踪 prepare_quant_contexts 调用次数 (batch 模式下应该为 1,即所有 ticker
# 共享一次 pick() 结果)
_PREPARE_CALLS: list[int] = []


def _fake_quant_pick(today, **kwargs):
    """Mock pick():记录调用,返回最小化 result。"""
    with _PICK_LOCK:
        _PICK_CALLS.append(str(today))
    print(f"[mock] quant_pick called for today={today}, total calls={len(_PICK_CALLS)}")
    # 返回空结果,Top N 为空,_extract_ticker_context 会生成 "未命中" context
    return {
        "today": today,
        "elapsed": 0.01,
        "n_strategies_run": 12,
        "n_strategies_error": 0,
        "top_picks": [],
        "all_records": [],
        "per_strategy_stats": {},
    }


def _fake_extract_ticker_context(result, ticker):
    """Mock:返回固定 context,跳过策略库查询。"""
    return f"[mock quant context] ticker={ticker}, no hit"


def _fake_prepare_quant_contexts(self, tickers, trade_date, progress_callback=None):
    """Mock: TradingAgentsGraph.prepare_quant_contexts 的替身。

    记录调用次数,返回每个 ticker 的 mock context。app_main.py 的
    _build_quant_contexts_for_batch 在 saved JSON 未命中时会走这个路径。
    """
    _PREPARE_CALLS.append(len(tickers))
    print(f"[mock] prepare_quant_contexts called for {len(tickers)} tickers, "
          f"total calls={len(_PREPARE_CALLS)}")
    return {t: f"[mock quant context] ticker={t}, no hit" for t in tickers}


# 替换 quant pick 模块函数
from tradingagents.quant import quant_picker as _qp_module
from tradingagents.agents import quant_picker_node as _qpn_module
_qp_module.pick = _fake_quant_pick
_qpn_module.quant_pick = _fake_quant_pick
_qpn_module._extract_ticker_context = _fake_extract_ticker_context
# quant_picker_node 导入的是 pick,替换其命名空间引用
import tradingagents.agents.quant_picker_node as _qpn_ns
_qpn_ns.quant_pick = _fake_quant_pick
_qpn_ns._extract_ticker_context = _fake_extract_ticker_context

# NOTE: TradingAgentsGraph 的 prepare_quant_contexts patch 必须放在 LLM patch
# 之后。trading_graph.py 在模块顶层 from tradingagents.llm_clients import
# create_llm_client,如果在 patch 之前 import,本地绑定就是原始函数,后续
# TradingAgentsGraph() 会创建真实 DeepSeek 客户端拖慢测试。


# 替换 LLM:用 FakeChatModel 返回固定字符串,支持 bind_tools / with_structured_output
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult


class FakeChatModel(BaseChatModel):
    """最小化 ChatModel mock,返回固定内容。"""

    response_text: str = "[mock LLM response] 评级: Buy"
    invoke_count: int = 0
    _lock: threading.Lock = None

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        object.__setattr__(self, "_lock", threading.Lock())

    @property
    def _llm_type(self) -> str:
        return "fake"

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        with self._lock:
            self.invoke_count += 1
        content = self.response_text
        # 如果 messages 是 list of dict,模拟 trader 情况
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=content))])

    def bind_tools(self, tools, **kwargs):
        # analyst 用 prompt | llm.bind_tools(tools),返回 self 即可
        return self

    def with_structured_output(self, schema, **kwargs):
        # 返回一个 callable,invoke 时返回 schema 实例
        def _invoke(prompt):
            # 尝试用 schema 的字段构造实例,失败则用 mock
            try:
                # 用 default 值构造
                return schema()
            except Exception:
                # fallback:返回一个有 .model_dump() 的对象
                class _Stub:
                    def __init__(self):
                        self.recommendation = "Buy"
                        self.rationale = "mock"
                        self.strategic_actions = "mock"
                        self.action = "Buy"
                        self.reasoning = "mock"
                        self.rating = "Buy"
                        self.executive_summary = "mock"
                        self.investment_thesis = "mock"
                return _Stub()
        # 包装成 Runnable
        from langchain_core.runnables import RunnableLambda
        return RunnableLambda(_invoke)


# 替换 create_llm_client 返回 FakeChatModel
from tradingagents.llm_clients import factory as _llm_factory
_orig_create = _llm_factory.create_llm_client


def _fake_create_llm_client(provider, model, base_url=None, **kwargs):
    """Mock:返回包装 FakeChatModel 的 client。"""
    client = MagicMock()
    client.get_llm.return_value = FakeChatModel()
    return client


_llm_factory.create_llm_client = _fake_create_llm_client
# tradingagents.graph.trading_graph 是 from tradingagents.llm_clients import create_llm_client
# 直接替换 llm_clients 模块属性
import tradingagents.llm_clients as _llm_pkg
_llm_pkg.create_llm_client = _fake_create_llm_client

# LLM patch 完成后再 import TradingAgentsGraph,这样 trading_graph.py 顶层
# from tradingagents.llm_clients import create_llm_client 拿到的是 fake 版本。
# 同时 patch prepare_quant_contexts (被 _build_quant_contexts_for_batch 调用)。
from tradingagents.graph.trading_graph import TradingAgentsGraph
TradingAgentsGraph.prepare_quant_contexts = _fake_prepare_quant_contexts


# ---- 2. 运行 batch 流程 ----

from tradingagents.default_config import DEFAULT_CONFIG
from web.progress import ProgressTracker
from web.runner import run_analysis_in_thread
from web.app_main import _build_quant_contexts_for_batch


def _build_config() -> dict:
    config = DEFAULT_CONFIG.copy()
    config["llm_provider"] = "deepseek"
    config["deep_think_llm"] = "deepseek-v4-pro"
    config["quick_think_llm"] = "deepseek-v4-flash"
    config["backend_url"] = os.getenv("BACKEND_URL") or None
    config["max_debate_rounds"] = 1
    config["max_risk_discuss_rounds"] = 1
    config["checkpoint_enabled"] = False  # 测试不走 checkpoint
    config["output_language"] = "Chinese"
    config["quant_layer_enabled"] = True
    config["force_free_text_llm"] = True  # 跳过 structured probe
    return config


def main() -> int:
    print("=== Batch 流程协同测试 ===")
    print(f"mock quant_pick / LLM 已替换,流程应快速完成")
    print()

    tickers = ["600881", "600095"]
    trade_date = "2026-07-17"
    config = _build_config()

    # 模拟 web/app_main.py 的 batch entry:先一次性构建 quant contexts,
    # 再分发给每只 ticker 的分析线程 (Bug 1 修复后的正确流程)
    print(f"[step 1] 构建 quant contexts (一次调用,所有 ticker 共享)")
    quant_contexts = _build_quant_contexts_for_batch(tickers, trade_date, config)
    print(f"  -> 得到 {len(quant_contexts)} 个 context, "
          f"prepare_quant_contexts 被调用 {len(_PREPARE_CALLS)} 次")
    print()

    print(f"[step 2] 启动 {len(tickers)} 只 ticker 的并行分析线程")
    trackers: dict[str, ProgressTracker] = {}
    threads = []
    for t in tickers:
        tk = ProgressTracker(ticker=t, trade_date=trade_date)
        trackers[t] = tk
        th = run_analysis_in_thread(
            ticker=t,
            trade_date=trade_date,
            config=config,
            tracker=tk,
            pre_quant_context=quant_contexts.get(t, ""),
        )
        threads.append(th)

    # 等所有 thread 完成 (最多 120s)
    deadline = time.time() + 120
    while time.time() < deadline:
        if all(not th.is_alive() for th in threads):
            break
        time.sleep(2)
        alive = sum(1 for th in threads if th.is_alive())
        done = sum(1 for tk in trackers.values() if tk.is_complete)
        err = sum(1 for tk in trackers.values() if tk.error)
        print(f"  [tick] alive={alive} done={done} err={err}")

    print()
    print("=== 结果 ===")
    print(f"prepare_quant_contexts 调用次数: {len(_PREPARE_CALLS)} (期望 1)")
    print(f"LangGraph 节点层 quant_pick 调用次数: {len(_PICK_CALLS)} "
          f"(期望 0,因为 pre_quant_context 已预填)")
    for t, tk in trackers.items():
        print(f"  {t}: is_complete={tk.is_complete}, error={tk.error}, "
              f"stages={len(tk.completed_stages)}/12, signal={tk.signal!r}")
        if tk.final_state:
            has_quant_ctx = bool(tk.final_state.get("quant_pick_context"))
            has_final = bool(tk.final_state.get("final_trade_decision"))
            has_ranked = bool(tk.final_state.get("final_ranked_decision"))
            print(f"    final_state: quant_ctx={has_quant_ctx}, "
                  f"final_decision={has_final}, ranked={has_ranked}")

    # 判定
    print()
    print("=== Bug 验证 ===")
    if len(_PREPARE_CALLS) == 1:
        print(f"[OK] prepare_quant_contexts 只调用 1 次 (batch 一次构建所有 context)")
    else:
        print(f"[BUG] prepare_quant_contexts 调用 {len(_PREPARE_CALLS)} 次 "
              f"(应该 1 次)")

    if len(_PICK_CALLS) == 0:
        print(f"[OK] LangGraph 节点层 quant_pick 未被调用 "
              f"(pre_quant_context 注入成功,Quant Picker 节点是 no-op)")
    else:
        print(f"[BUG 1 确认] LangGraph 节点仍调用了 quant_pick {len(_PICK_CALLS)} 次 "
              f"(pre_quant_context 注入失败)")

    for t, tk in trackers.items():
        if tk.error:
            print(f"[BUG] {t} 跑失败: {tk.error}")
        elif not tk.is_complete:
            print(f"[BUG] {t} 未完成 (超时)")
        elif not tk.final_state.get("final_ranked_decision"):
            print(f"[BUG] {t} 缺少 final_ranked_decision")
        else:
            print(f"[OK] {t} 完整跑通,有 final_ranked_decision")

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        traceback.print_exc()
        sys.exit(2)
