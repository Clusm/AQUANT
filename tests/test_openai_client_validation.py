"""OpenAI-compatible client 参数校验测试(避免 httpx 抛 ascii codec 错误)。"""
from __future__ import annotations

import pytest


def test_non_ascii_api_key_raises_clear_error():
    from tradingagents.llm_clients.openai_client import OpenAIClient

    client = OpenAIClient(
        model="deepseek-v4-flash",
        provider="deepseek",
        api_key="sk-\u6d4b\u8bd5\u4e2d\u6587",
    )
    with pytest.raises(RuntimeError, match="API Key"):
        client.get_llm()


def test_non_ascii_base_url_raises_clear_error():
    from tradingagents.llm_clients.openai_client import OpenAIClient

    client = OpenAIClient(
        model="deepseek-v4-flash",
        provider="deepseek",
        base_url="https://example.com/\u4e2d\u6587",
        api_key="sk-test",
    )
    with pytest.raises(RuntimeError, match="网关地址"):
        client.get_llm()
