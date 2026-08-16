"""Aquant 投研工具 - 最简快速启动示例。

先创建 .env 并填入 DeepSeek key(或改用其他 provider),然后运行:
    python main.py

默认手动输入股票代码,量化层不会启动全市场扫描(与 Web UI 的"手动选股"
策略一致);如需量化前置筛选请使用 Web UI 的「开始选股」或 CLI:
    tradingagents quant-pick
"""

from datetime import date, timedelta

from dotenv import load_dotenv

from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.graph.trading_graph import TradingAgentsGraph

load_dotenv()

config = DEFAULT_CONFIG.copy()
config["llm_provider"] = "deepseek"
config["deep_think_llm"] = "deepseek-v4-pro"
config["quick_think_llm"] = "deepseek-v4-flash"
config["checkpoint_enabled"] = True
config["max_debate_rounds"] = 1
config["max_risk_discuss_rounds"] = 1
config["output_language"] = "Chinese"

# A 股默认数据源已在 DEFAULT_CONFIG 中配置为 a_stock,无需再改 data_vendors。
ta = TradingAgentsGraph(debug=True, config=config)

# 使用最近一个工作日(简单跳过周末,法定假期可手工改日期)。
trade_date = date.today()
while trade_date.weekday() >= 5:
    trade_date -= timedelta(days=1)
trade_date = trade_date.strftime("%Y-%m-%d")
final_state, decision = ta.propagate("600519", trade_date)
print(decision)
