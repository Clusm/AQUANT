"""DeepSeek API 端到端冒烟测试:只跑 market analyst,验证 LLM 调用链通。

用法: py -3 tests/smoke_llm_e2e.py
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

# 确保项目根在 sys.path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# 加载 .env
from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

# 在 import graph 之前禁用 quant 层(冒烟测试不需要预过滤)
os.environ["TRADINGAGENTS_QUANT_LAYER_ENABLED"] = "false"

from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.graph.trading_graph import TradingAgentsGraph


def main():
    config = DEFAULT_CONFIG.copy()
    config["llm_provider"] = "deepseek"
    config["deep_think_llm"] = "deepseek-v4-pro"
    config["quick_think_llm"] = "deepseek-v4-flash"
    # opencode go 套餐:显式覆盖 base_url,SDK 自动追加 /chat/completions
    config["backend_url"] = "https://opencode.ai/zen/go/v1"
    config["max_debate_rounds"] = 1
    config["max_risk_discuss_rounds"] = 1
    config["output_language"] = "Chinese"
    config["quant_layer_enabled"] = False

    print(f"[smoke] DEEPSEEK_API_KEY 设置: {'是' if os.getenv('DEEPSEEK_API_KEY') else '否'}")
    print(f"[smoke] provider={config['llm_provider']} deep={config['deep_think_llm']} quick={config['quick_think_llm']}")
    print(f"[smoke] backend_url={config['backend_url']}")

    # 只用 market analyst,最小测试
    analysts = ["market"]
    print(f"[smoke] analysts={analysts}")

    graph = TradingAgentsGraph(analysts, config=config, debug=False)
    init_state = graph.propagator.create_initial_state("600519", "2026-07-18")
    args = graph.propagator.get_graph_args()

    print("[smoke] 开始 stream...")
    t0 = time.time()
    final_state = None
    n_chunks = 0
    try:
        for chunk in graph.graph.stream(init_state, **args):
            n_chunks += 1
            elapsed = time.time() - t0
            keys = list(chunk.keys()) if isinstance(chunk, dict) else type(chunk).__name__
            print(f"  [chunk {n_chunks}] t={elapsed:.1f}s keys={keys}")
            if isinstance(chunk, dict) and "market_report" in chunk:
                final_state = chunk
    except Exception as e:
        print(f"[smoke] ERROR: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

    elapsed = time.time() - t0
    print(f"\n[smoke] 完成: {n_chunks} chunks, {elapsed:.1f}s")

    if final_state and final_state.get("market_report"):
        report = final_state["market_report"]
        print(f"[smoke] market_report 长度: {len(report)} 字符")
        print(f"[smoke] 前 500 字符预览:")
        print("-" * 60)
        print(report[:500])
        print("-" * 60)
        print("[smoke] PASS: DeepSeek API 端到端 OK")
        sys.exit(0)
    else:
        print("[smoke] FAIL: 未生成 market_report")
        print(f"[smoke] final_state keys: {list(final_state.keys()) if final_state else 'None'}")
        sys.exit(2)


if __name__ == "__main__":
    main()
