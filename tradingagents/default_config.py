import os

_TRADINGAGENTS_HOME = os.path.join(os.path.expanduser("~"), ".tradingagents")

DEFAULT_CONFIG = {
    "project_dir": os.path.abspath(os.path.join(os.path.dirname(__file__), ".")),
    "results_dir": os.getenv("TRADINGAGENTS_RESULTS_DIR", os.path.join(_TRADINGAGENTS_HOME, "logs")),
    "data_cache_dir": os.getenv("TRADINGAGENTS_CACHE_DIR", os.path.join(_TRADINGAGENTS_HOME, "cache")),
    "memory_log_path": os.getenv("TRADINGAGENTS_MEMORY_LOG_PATH", os.path.join(_TRADINGAGENTS_HOME, "memory", "trading_memory.md")),
    # Optional cap on the number of resolved memory log entries. When set,
    # the oldest resolved entries are pruned once this limit is exceeded.
    # Pending entries are never pruned. None disables rotation entirely.
    "memory_log_max_entries": None,
    # LLM settings
    # Defaults align with Web UI (DeepSeek-only provider list, Flash=quick / Pro=deep).
    # Override via .env (DEEPSEEK_API_KEY) or Web UI sidebar.
    "llm_provider": "deepseek",
    "deep_think_llm": "deepseek-v4-pro",
    "quick_think_llm": "deepseek-v4-flash",
    # When None, each provider's client falls back to its own default endpoint
    # (api.openai.com for OpenAI, generativelanguage.googleapis.com for Gemini, ...).
    # The CLI overrides this per provider when the user picks one. Keeping a
    # provider-specific URL here would leak (e.g. OpenAI's /v1 was previously
    # being forwarded to Gemini, producing malformed request URLs).
    "backend_url": None,
    # Provider-specific thinking configuration
    "google_thinking_level": None,      # "high", "minimal", etc.
    "openai_reasoning_effort": None,    # "medium", "high", "low"
    "anthropic_effort": None,           # "high", "medium", "low"
    # Checkpoint/resume: when True, LangGraph saves state after each node
    # so a crashed run can resume from the last successful step.
    "checkpoint_enabled": False,
    # Output language for analyst reports and final decision
    # Internal agent debate stays in English for reasoning quality
    "output_language": "Chinese",
    # Force all decision agents (Research Manager / Trader / Portfolio Manager)
    # to skip with_structured_output and use free-text generation. Set true
    # when the LLM gateway rejects tool_choice / function_calling with HTTP
    # 400 (e.g. opencode.ai Console Go proxy). Auto-downgrade still fires on
    # first failure; this flag avoids the wasted probe call entirely.
    "force_free_text_llm": os.getenv("FORCE_FREE_TEXT_LLM", "").lower() in ("1", "true", "yes"),
    # Debate and discussion settings
    "max_debate_rounds": 1,
    "max_risk_discuss_rounds": 1,
    "max_recur_limit": 100,
    # Data vendor configuration
    # Category-level configuration (default for all tools in category)
    "data_vendors": {
        "core_stock_apis": "a_stock",        # Options: a_stock, alpha_vantage, yfinance
        "technical_indicators": "a_stock",   # Options: a_stock, alpha_vantage, yfinance
        "fundamental_data": "a_stock",       # Options: a_stock, alpha_vantage, yfinance
        "news_data": "a_stock",              # Options: a_stock, alpha_vantage, yfinance
        "signal_data": "a_stock",            # A-stock only: topic attribution, capital flow, consensus
    },
    # Tool-level configuration (takes precedence over category-level)
    "tool_vendors": {
        # Example: "get_stock_data": "alpha_vantage",  # Override category default
    },
    # Quant pre-filter layer (P2 integration)
    # When enabled, runs the active strategy library (see strategy_library_final.py)
    # before LLM analysts and injects Top N context
    "quant_layer_enabled": True,
    # Cache file name for daily OHLCV data (sina_fetcher format)
    # daily_main_board = 全量主板(~3042 股,慢)
    # daily_main_board_liquid = 流动性前 80%(~2433 股,数据采集层已按成交额截断,推荐)
    "quant_daily_cache_name": "daily_main_board_liquid",
    # Universe 过滤参数(v0.3.0 新增):
    # 价格过滤(在 ST/上市天数/停牌过滤之后,流动性排序之前应用)
    # - quant_price_min: 最低股价,过滤低价股(ST/退市风险集中区)
    # - quant_price_max: 最高股价,过滤高价股(实盘参与门槛高)
    # 流动性过滤:按 20 日均成交额排序,保留前 quant_liquidity_percentile
    # - 1.0 = 不做 percentile 截断(数据采集层已按流动性前 80% 截断,策略层无需再砍)
    # - 0.8 = 砍掉流动性最差 20%(仅在用 daily_main_board 全量 cache 时有意义)
    "quant_price_min": 3.0,
    "quant_price_max": 80.0,
    "quant_liquidity_percentile": 1.0,
    # Default number of Top N candidates to deep-analyze.
    # Options: 5, 10, 20. Default 10 (balance of coverage vs LLM API cost;
    # 20 tends to drain DeepSeek quota for new users).
    "quant_top_n_default": 10,
    # Number of parallel workers for strategy execution (multiprocessing spawn)
    "quant_n_workers": 8,
    # Slice days for strategy data (0 = full history, 350 = recommended mixed slice)
    "quant_slice_days": 0,
    # Top-K per strategy (each strategy returns at most top_k stocks)
    "quant_top_k_per_strategy": 2,
    # Compare LLM (defer - not implemented, kept for future)
    "quant_compare_llm_enabled": False,
}
