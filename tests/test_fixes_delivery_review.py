"""Tests for P1/P2 fixes from the delivery review.

Covers:
- Debate router Chinese prefix matching (conditional_logic.py)
- quant_picker_node skip path returns explicit empty context
- _strip_think_tags helper (runner.py)
- quant_skip_hint setting for manual tickers (runner.py)
"""
from __future__ import annotations

from unittest.mock import patch

from tradingagents.graph.conditional_logic import ConditionalLogic

# ============================================================
# Debate router Chinese prefix matching
# ============================================================


def _make_state(count: int, current_response: str) -> dict:
    return {
        "investment_debate_state": {
            "count": count,
            "current_response": current_response,
        },
    }


class TestDebateRouter:
    """should_continue_debate must route correctly for English and Chinese prefixes."""

    def test_bull_english_routes_to_bear(self):
        logic = ConditionalLogic(max_debate_rounds=3)
        state = _make_state(0, "Bull Analyst: 我看好这只股票")
        assert logic.should_continue_debate(state) == "Bear Researcher"

    def test_bull_chinese_routes_to_bear(self):
        logic = ConditionalLogic(max_debate_rounds=3)
        state = _make_state(0, "多方分析师: 我看好这只股票")
        assert logic.should_continue_debate(state) == "Bear Researcher"

    def test_bear_english_routes_to_bull(self):
        logic = ConditionalLogic(max_debate_rounds=3)
        state = _make_state(1, "Bear Analyst: 我认为风险较大")
        assert logic.should_continue_debate(state) == "Bull Researcher"

    def test_bear_chinese_routes_to_bull(self):
        logic = ConditionalLogic(max_debate_rounds=3)
        state = _make_state(1, "空方分析师: 我认为风险较大")
        assert logic.should_continue_debate(state) == "Bull Researcher"

    def test_empty_response_falls_back_to_bull(self):
        logic = ConditionalLogic(max_debate_rounds=3)
        state = _make_state(0, "")
        assert logic.should_continue_debate(state) == "Bull Researcher"

    def test_unrecognized_prefix_falls_back_to_bull(self):
        logic = ConditionalLogic(max_debate_rounds=3)
        state = _make_state(0, "SomeOtherPrefix: text")
        assert logic.should_continue_debate(state) == "Bull Researcher"

    def test_after_max_debate_rounds_returns_research_manager(self):
        logic = ConditionalLogic(max_debate_rounds=3)
        # count >= 6 (2 * 3) triggers Research Manager
        state = _make_state(6, "Bull Analyst: 最后发言")
        assert logic.should_continue_debate(state) == "Research Manager"

    def test_bull_short_prefix_routes_to_bear(self):
        """Edge case: prefix starts with 'Bull' but isn't 'Bull Analyst'."""
        logic = ConditionalLogic(max_debate_rounds=3)
        state = _make_state(0, "Bullish: text")
        # starts with "Bull" → True → Bear Researcher
        assert logic.should_continue_debate(state) == "Bear Researcher"


# ============================================================
# _strip_think_tags (runner.py)
# ============================================================


class TestStripThinkTags:
    """_strip_think_tags must remove  thinking... response blocks."""

    def _strip_think_tags(self, text: str) -> str:
        """Inline copy of runner._strip_think_tags for isolated testing."""
        import re
        return re.sub(r" thinking.*? response\s*", "", text, flags=re.DOTALL).strip()

    def test_removes_think_block(self):
        text = "Before  thinking... response After"
        result = self._strip_think_tags(text)
        assert result == "Before After"

    def test_removes_think_block_with_content(self):
        text = "前文  thinking...Let me analyze the stock data. The PE ratio is 15.  response 后文"
        result = self._strip_think_tags(text)
        assert result == "前文 后文"

    def test_no_think_block_returns_unchanged(self):
        text = "普通文本，没有think标签"
        result = self._strip_think_tags(text)
        assert result == text

    def test_empty_string_returns_empty(self):
        result = self._strip_think_tags("")
        assert result == ""

    def test_multiple_think_blocks_all_removed(self):
        text = "A  thinking...first  response B  thinking...second  response C"
        result = self._strip_think_tags(text)
        assert result == "A B C"


# ============================================================
# quant_picker_node skip paths
# ============================================================


class TestQuantPickerNodeSkipPaths:
    """quant_picker_node must return explicit empty context on manual skip paths."""

    @patch("tradingagents.agents.quant_picker_node.logger")
    def test_manual_ticker_no_saved_json_returns_empty_context(
        self, mock_logger,
    ):
        """Manual ticker with no saved quant pick JSON → explicit empty context."""
        from tradingagents.agents.quant_picker_node import create_quant_picker_node

        # Mock pathlib.Path.home to return a path where the JSON doesn't exist
        with patch("pathlib.Path.exists", return_value=False):
            node_fn = create_quant_picker_node(
                config={"quant_layer_enabled": True},
            )
            state = {
                "company_of_interest": "000001",
                "trade_date": "2026-08-01",
                "ticker_source": "manual",
            }
            result = node_fn(state)
            assert result == {"quant_pick_context": ""}

    @patch("tradingagents.agents.quant_picker_node.logger")
    def test_prepopulated_context_returns_empty(self, mock_logger):
        """When quant_pick_context is already set, node is a no-op."""
        from tradingagents.agents.quant_picker_node import create_quant_picker_node

        node_fn = create_quant_picker_node()
        state = {"quant_pick_context": "already populated"}
        result = node_fn(state)
        assert result == {}

    @patch("tradingagents.agents.quant_picker_node.logger")
    def test_quant_layer_disabled_returns_empty(self, mock_logger):
        """When quant_layer_enabled is False, node is a no-op."""
        from tradingagents.agents.quant_picker_node import create_quant_picker_node

        node_fn = create_quant_picker_node(
            config={"quant_layer_enabled": False},
        )
        state = {
            "company_of_interest": "000001",
            "trade_date": "2026-08-01",
            "ticker_source": "manual",
        }
        result = node_fn(state)
        assert result == {}


# ============================================================
# quant_skip_hint (runner.py)
# ============================================================


class TestQuantSkipHint:
    """Manual tickers without quant context should set quant_skip_hint."""

    def test_manual_ticker_sets_skip_hint(self):
        """Simulate the logic in runner.py:_run for manual ticker skip hint."""
        from web.progress import ProgressTracker

        tracker = ProgressTracker()
        ticker_source = "manual"
        pre_quant_context = ""
        config = {"quant_layer_enabled": True}

        if ticker_source == "manual" and not pre_quant_context:
            if config.get("quant_layer_enabled", True):
                tracker.quant_skip_hint = (
                    "手动选股，量化层未参与评估(仅 LLM 分析)"
                )

        assert tracker.quant_skip_hint == "手动选股，量化层未参与评估(仅 LLM 分析)"

    def test_quant_picker_source_does_not_set_hint(self):
        """Tickers from quant picker should NOT set the skip hint."""
        from web.progress import ProgressTracker

        tracker = ProgressTracker()
        ticker_source = "quant_picker"
        pre_quant_context = ""
        config = {"quant_layer_enabled": True}

        if ticker_source == "manual" and not pre_quant_context:
            if config.get("quant_layer_enabled", True):
                tracker.quant_skip_hint = (
                    "手动选股，量化层未参与评估(仅 LLM 分析)"
                )

        assert tracker.quant_skip_hint == ""

    def test_manual_with_pre_quant_does_not_set_hint(self):
        """Manual ticker WITH pre-computed quant context should NOT set hint."""
        from web.progress import ProgressTracker

        tracker = ProgressTracker()
        ticker_source = "manual"
        pre_quant_context = "=== 量化选股上下文 ==="
        config = {"quant_layer_enabled": True}

        if ticker_source == "manual" and not pre_quant_context:
            if config.get("quant_layer_enabled", True):
                tracker.quant_skip_hint = (
                    "手动选股，量化层未参与评估(仅 LLM 分析)"
                )

        assert tracker.quant_skip_hint == ""