from langchain_core.messages import HumanMessage, RemoveMessage

# Import tools from separate utility files
from tradingagents.agents.utils.core_stock_tools import (
    get_stock_data
)
from tradingagents.agents.utils.technical_indicators_tools import (
    get_indicators
)
from tradingagents.agents.utils.fundamental_data_tools import (
    get_fundamentals,
    get_balance_sheet,
    get_cashflow,
    get_income_statement
)
from tradingagents.agents.utils.news_data_tools import (
    get_news,
    get_insider_transactions,
    get_global_news
)
from tradingagents.agents.utils.signal_data_tools import (
    get_profit_forecast,
    get_hot_stocks,
    get_northbound_flow,
    get_concept_blocks,
    get_fund_flow,
    get_dragon_tiger_board,
    get_lockup_expiry,
    get_industry_comparison,
)


def get_language_instruction() -> str:
    """Return a prompt instruction for the configured output language.

    Returns empty string when English (default), so no extra tokens are used.
    Applied to user-facing agents (analysts, portfolio manager) and internal
    debate agents (bull/bear researchers, aggressive/conservative/neutral
    debators). Debate agents keep English prompt bodies for reasoning quality,
    but append this instruction so their output reaches the user in Chinese.
    """
    from tradingagents.dataflows.config import get_config
    lang = get_config().get("output_language", "English")
    if lang.strip().lower() == "english":
        return ""
    return f" Write your entire response in {lang}."


def build_instrument_context(ticker: str) -> str:
    """Describe the exact instrument so agents preserve exchange-qualified tickers."""
    return (
        f"The instrument to analyze is `{ticker}`. "
        "Use this exact ticker in every tool call, report, and recommendation, "
        "preserving any exchange suffix (e.g. `.TO`, `.L`, `.HK`, `.T`). "
        "When a tool argument is named `ticker`, pass only this ticker value; "
        "do not pass company names, sectors, concepts, or search keywords."
    )


def build_quant_context(state) -> str:
    """Build quant pre-filter context string for prompt injection.

    Reads `quant_pick_context` from state. If empty (quant layer disabled or
    no signals), returns empty string so analysts run as before.

    The context is appended to analyst system messages so all 7 analysts
    see the same quant signals (hit strategies, weighted score, win rate,
    entry advice) for the ticker under analysis.
    """
    quant_ctx = state.get("quant_pick_context", "") if state else ""
    if not quant_ctx:
        return ""
    try:
        from tradingagents.quant.strategy.strategy_library_final import get_all_strategies_final
        _n_strats = len(get_all_strategies_final())
    except Exception:
        _n_strats = 0
    return (
        "\n\n--- Quant Pre-Filter Context ---\n"
        f"{quant_ctx}\n"
        "--- End Quant Context ---\n"
        f"Note: This ticker was selected by the quant pre-filter ({_n_strats} strategies). "
        "Use the hit strategies and win rates above as additional evidence. "
        "Quant signals are systematic; your job is to add LLM judgment on top, "
        "not to override them without reason."
    )

def extract_report_content(result) -> str:
    """Extract text content from an LLM response as the node report.

    Some models return content AND tool_calls in the final message (e.g. finish
    the report then emit one more tool call); the report must not be empty in
    that case. content may be a plain str or a list of blocks (text blocks carry
    a "text" key). Empty content returns "" to preserve the prior behavior.
    """
    if not result.content:
        return ""
    if isinstance(result.content, str):
        return result.content
    parts = []
    for block in result.content:
        if isinstance(block, dict):
            parts.append(block.get("text") or "")
        else:
            parts.append(str(block))
    return "".join(parts)


def create_msg_delete():
    def delete_messages(state):
        """Clear messages and add placeholder for Anthropic compatibility"""
        messages = state["messages"]

        # Remove all messages
        removal_operations = [RemoveMessage(id=m.id) for m in messages]

        # Add a minimal placeholder message
        placeholder = HumanMessage(content="Continue")

        return {"messages": removal_operations + [placeholder]}

    return delete_messages


        
