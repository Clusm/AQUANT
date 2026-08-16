"""实测 AI 分析全流程:用 600881 + 2026-07-17 跑完整 TradingAgentsGraph。

验证:
1. Streamlit button 点击修复是否有效(已通过 AppTest 验证,这里跳过 UI)
2. AI 分析流程能否跑通(LLM 调用、辩论、风险讨论、最终决策)
3. 每个阶段耗时
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

# Windows 控制台默认 GBK,print emoji (📊 等) 会触发 UnicodeEncodeError。
# 切到 utf-8 + errors=replace 保证 LLM 输出含任意 unicode 都能落地。
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from dotenv import load_dotenv

load_dotenv(_PROJECT_ROOT / ".env", override=True)

os.environ.setdefault(
    "QUANT_CACHE_DIR",
    r"C:\Users\Tao\Desktop\新建文件夹 (4)\stock_selector\outputs\cache",
)

from tradingagents.default_config import DEFAULT_CONFIG
from web.progress import PIPELINE_STAGES, ProgressTracker
from web.runner import run_analysis_in_thread

TICKER = "600881"
TRADE_DATE = "2026-07-17"


def _build_config() -> dict:
    config = DEFAULT_CONFIG.copy()
    config["llm_provider"] = "deepseek"
    config["deep_think_llm"] = "deepseek-v4-pro"
    config["quick_think_llm"] = "deepseek-v4-flash"
    config["backend_url"] = os.getenv("BACKEND_URL") or None
    config["max_debate_rounds"] = 1
    config["max_risk_discuss_rounds"] = 1
    config["checkpoint_enabled"] = True
    config["output_language"] = "Chinese"
    config["quant_layer_enabled"] = True
    config["quant_daily_cache_name"] = "daily_main_board_liquid"
    config["quant_top_n_default"] = 20
    config["quant_n_workers"] = 8
    config["quant_top_k_per_strategy"] = 2
    config["quant_slice_days"] = 0
    return config


def main() -> int:
    print("=== AI 分析实测 ===")
    print(f"ticker: {TICKER}, trade_date: {TRADE_DATE}")
    print(f"backend_url: {os.getenv('BACKEND_URL')}")
    print(f"DEEPSEEK_API_KEY last 4: {(os.getenv('DEEPSEEK_API_KEY') or '')[-4:]}")
    print()

    config = _build_config()
    tracker = ProgressTracker(ticker=TICKER, trade_date=TRADE_DATE)

    t0 = time.time()
    thread = run_analysis_in_thread(
        ticker=TICKER,
        trade_date=TRADE_DATE,
        config=config,
        tracker=tracker,
    )

    last_stage = None
    while thread.is_alive():
        time.sleep(3)
        completed = len(tracker.completed_stages)
        total = len(PIPELINE_STAGES)
        elapsed = time.time() - t0

        active = None
        for stage in PIPELINE_STAGES:
            if tracker.stage_status(stage["id"]) == "active":
                active = stage["name"]
                break

        if active != last_stage:
            print(f"[{elapsed:6.1f}s] {completed}/{total} 阶段完成 · 当前: {active or '-'}")
            last_stage = active

        if tracker.error:
            print(f"\n[ERROR] {tracker.error}")
            break

    thread.join(timeout=10)

    elapsed = time.time() - t0
    print(f"\n=== 完成 (总耗时 {elapsed:.1f}s) ===")
    print(f"completed_stages: {len(tracker.completed_stages)}/{len(PIPELINE_STAGES)}")
    print(f"llm_calls: {tracker.llm_calls}")
    print(f"tool_calls: {tracker.tool_calls}")
    print(f"tokens_in: {tracker.tokens_in:,}")
    print(f"tokens_out: {tracker.tokens_out:,}")

    if tracker.error:
        print(f"ERROR: {tracker.error}")
        return 1

    if tracker.is_complete and tracker.final_state:
        signal = tracker.final_state.get("final_trade_decision", "")
        print(f"\n最终决策:\n{signal[:1000]}")
        return 0

    print("\n未完成也未报错,可能超时")
    return 2


if __name__ == "__main__":
    sys.exit(main())
