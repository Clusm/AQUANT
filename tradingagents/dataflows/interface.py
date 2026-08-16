# Import from vendor-specific modules
import hashlib
import threading
import time

from .a_stock import (
    get_balance_sheet as get_astock_balance_sheet,
)
from .a_stock import (
    get_cashflow as get_astock_cashflow,
)
from .a_stock import (
    get_concept_blocks as get_astock_concept_blocks,
)
from .a_stock import (
    get_dragon_tiger_board as get_astock_dragon_tiger_board,
)
from .a_stock import (
    get_fund_flow as get_astock_fund_flow,
)
from .a_stock import (
    get_fundamentals as get_astock_fundamentals,
)
from .a_stock import (
    get_global_news as get_astock_global_news,
)
from .a_stock import (
    get_hot_stocks as get_astock_hot_stocks,
)
from .a_stock import (
    get_income_statement as get_astock_income_statement,
)
from .a_stock import (
    get_indicators as get_astock_indicators,
)
from .a_stock import (
    get_industry_comparison as get_astock_industry_comparison,
)
from .a_stock import (
    get_insider_transactions as get_astock_insider_transactions,
)
from .a_stock import (
    get_lockup_expiry as get_astock_lockup_expiry,
)
from .a_stock import (
    get_news as get_astock_news,
)
from .a_stock import (
    get_northbound_flow as get_astock_northbound_flow,
)
from .a_stock import (
    get_profit_forecast as get_astock_profit_forecast,
)
from .a_stock import (
    get_stock_data as get_astock_stock_data,
)
from .alpha_vantage import (
    get_balance_sheet as get_alpha_vantage_balance_sheet,
)
from .alpha_vantage import (
    get_cashflow as get_alpha_vantage_cashflow,
)
from .alpha_vantage import (
    get_fundamentals as get_alpha_vantage_fundamentals,
)
from .alpha_vantage import (
    get_global_news as get_alpha_vantage_global_news,
)
from .alpha_vantage import (
    get_income_statement as get_alpha_vantage_income_statement,
)
from .alpha_vantage import (
    get_indicator as get_alpha_vantage_indicator,
)
from .alpha_vantage import (
    get_insider_transactions as get_alpha_vantage_insider_transactions,
)
from .alpha_vantage import (
    get_news as get_alpha_vantage_news,
)
from .alpha_vantage import (
    get_stock as get_alpha_vantage_stock,
)
from .alpha_vantage_common import AlphaVantageRateLimitError

# Configuration and routing logic
from .config import get_config
from .y_finance import (
    get_balance_sheet as get_yfinance_balance_sheet,
)
from .y_finance import (
    get_cashflow as get_yfinance_cashflow,
)
from .y_finance import (
    get_fundamentals as get_yfinance_fundamentals,
)
from .y_finance import (
    get_income_statement as get_yfinance_income_statement,
)
from .y_finance import (
    get_insider_transactions as get_yfinance_insider_transactions,
)
from .y_finance import (
    get_stock_stats_indicators_window,
    get_YFin_data_online,
)
from .yfinance_news import get_global_news_yfinance, get_news_yfinance

# Tools organized by category
TOOLS_CATEGORIES = {
    "core_stock_apis": {
        "description": "OHLCV stock price data",
        "tools": [
            "get_stock_data"
        ]
    },
    "technical_indicators": {
        "description": "Technical analysis indicators",
        "tools": [
            "get_indicators"
        ]
    },
    "fundamental_data": {
        "description": "Company fundamentals",
        "tools": [
            "get_fundamentals",
            "get_balance_sheet",
            "get_cashflow",
            "get_income_statement"
        ]
    },
    "news_data": {
        "description": "News and insider data",
        "tools": [
            "get_news",
            "get_global_news",
            "get_insider_transactions",
        ]
    },
    "signal_data": {
        "description": "A-stock signal layer (topic attribution, capital flow, consensus forecast)",
        "tools": [
            "get_profit_forecast",
            "get_hot_stocks",
            "get_northbound_flow",
            "get_concept_blocks",
            "get_fund_flow",
            "get_dragon_tiger_board",
            "get_lockup_expiry",
            "get_industry_comparison",
        ]
    }
}

VENDOR_LIST = [
    "a_stock",
    "yfinance",
    "alpha_vantage",
]

# Mapping of methods to their vendor-specific implementations
VENDOR_METHODS = {
    # core_stock_apis
    "get_stock_data": {
        "a_stock": get_astock_stock_data,
        "alpha_vantage": get_alpha_vantage_stock,
        "yfinance": get_YFin_data_online,
    },
    # technical_indicators
    "get_indicators": {
        "a_stock": get_astock_indicators,
        "alpha_vantage": get_alpha_vantage_indicator,
        "yfinance": get_stock_stats_indicators_window,
    },
    # fundamental_data
    "get_fundamentals": {
        "a_stock": get_astock_fundamentals,
        "alpha_vantage": get_alpha_vantage_fundamentals,
        "yfinance": get_yfinance_fundamentals,
    },
    "get_balance_sheet": {
        "a_stock": get_astock_balance_sheet,
        "alpha_vantage": get_alpha_vantage_balance_sheet,
        "yfinance": get_yfinance_balance_sheet,
    },
    "get_cashflow": {
        "a_stock": get_astock_cashflow,
        "alpha_vantage": get_alpha_vantage_cashflow,
        "yfinance": get_yfinance_cashflow,
    },
    "get_income_statement": {
        "a_stock": get_astock_income_statement,
        "alpha_vantage": get_alpha_vantage_income_statement,
        "yfinance": get_yfinance_income_statement,
    },
    # news_data
    "get_news": {
        "a_stock": get_astock_news,
        "alpha_vantage": get_alpha_vantage_news,
        "yfinance": get_news_yfinance,
    },
    "get_global_news": {
        "a_stock": get_astock_global_news,
        "yfinance": get_global_news_yfinance,
        "alpha_vantage": get_alpha_vantage_global_news,
    },
    "get_insider_transactions": {
        "a_stock": get_astock_insider_transactions,
        "alpha_vantage": get_alpha_vantage_insider_transactions,
        "yfinance": get_yfinance_insider_transactions,
    },
    # signal_data (A-stock only)
    "get_profit_forecast": {
        "a_stock": get_astock_profit_forecast,
    },
    "get_hot_stocks": {
        "a_stock": get_astock_hot_stocks,
    },
    "get_northbound_flow": {
        "a_stock": get_astock_northbound_flow,
    },
    "get_concept_blocks": {
        "a_stock": get_astock_concept_blocks,
    },
    "get_fund_flow": {
        "a_stock": get_astock_fund_flow,
    },
    "get_dragon_tiger_board": {
        "a_stock": get_astock_dragon_tiger_board,
    },
    "get_lockup_expiry": {
        "a_stock": get_astock_lockup_expiry,
    },
    "get_industry_comparison": {
        "a_stock": get_astock_industry_comparison,
    },
}

def get_category_for_method(method: str) -> str:
    """Get the category that contains the specified method."""
    for category, info in TOOLS_CATEGORIES.items():
        if method in info["tools"]:
            return category
    raise ValueError(f"Method '{method}' not found in any category")

def get_vendor(category: str, method: str = None) -> str:
    """Get the configured vendor for a data category or specific tool method.
    Tool-level configuration takes precedence over category-level.
    """
    config = get_config()

    # Check tool-level configuration first (if method provided)
    if method:
        tool_vendors = config.get("tool_vendors", {})
        if method in tool_vendors:
            return tool_vendors[method]

    # Fall back to category-level configuration
    return config.get("data_vendors", {}).get(category, "default")

_TOOL_CACHE: dict[str, tuple[float, str]] = {}
_TOOL_CACHE_TTL = 180.0
_TOOL_CACHE_LOCK = threading.Lock()


def _tool_cache_key(method: str, args: tuple, kwargs: dict) -> str:
    payload = repr((method, args, tuple(sorted(kwargs.items()))))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def clear_tool_result_cache() -> None:
    """Clear in-process vendor result cache (used before a fresh analysis run)."""
    with _TOOL_CACHE_LOCK:
        _TOOL_CACHE.clear()


def route_to_vendor(method: str, *args, **kwargs):
    """Route method calls to appropriate vendor implementation with fallback support.

    Seven analysts repeatedly request the same data; successful vendor results
    are cached for 180s by (method, args) so batch analysis does not re-hit the
    same endpoint dozens of times.
    """
    if method not in VENDOR_METHODS:
        raise ValueError(f"Method '{method}' not supported")

    cache_key = _tool_cache_key(method, args, kwargs)
    now = time.monotonic()
    with _TOOL_CACHE_LOCK:
        cached = _TOOL_CACHE.get(cache_key)
        if cached is not None and now - cached[0] < _TOOL_CACHE_TTL:
            return cached[1]

    category = get_category_for_method(method)
    vendor_config = get_vendor(category, method)
    primary_vendors = [v.strip() for v in vendor_config.split(',')]

    all_available_vendors = list(VENDOR_METHODS[method].keys())
    fallback_vendors = primary_vendors.copy()
    for vendor in all_available_vendors:
        if vendor not in fallback_vendors:
            fallback_vendors.append(vendor)

    result = None
    for vendor in fallback_vendors:
        if vendor not in VENDOR_METHODS[method]:
            continue
        vendor_impl = VENDOR_METHODS[method][vendor]
        try:
            result = vendor_impl(*args, **kwargs)
            break
        except AlphaVantageRateLimitError:
            continue  # Only rate limits trigger fallback

    if result is None:
        raise RuntimeError(f"No available vendor for '{method}'")

    if isinstance(result, str):
        with _TOOL_CACHE_LOCK:
            _TOOL_CACHE[cache_key] = (now, result)
    return result