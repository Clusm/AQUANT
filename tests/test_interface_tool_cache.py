"""vendor 工具结果缓存测试(批量分析减少重复数据请求)。"""
from __future__ import annotations

from tradingagents.dataflows import interface


def test_route_to_vendor_caches_repeat_calls(monkeypatch):
    calls = []

    def fake_vendor(ticker):
        calls.append(ticker)
        return f"data:{ticker}"

    monkeypatch.setitem(interface.VENDOR_METHODS, "test_cache_method", {"a_stock": fake_vendor})
    monkeypatch.setattr(interface, "get_category_for_method", lambda method: "signal_data")
    monkeypatch.setattr(interface, "get_vendor", lambda category, method: "a_stock")
    interface.clear_tool_result_cache()

    assert interface.route_to_vendor("test_cache_method", "600602") == "data:600602"
    assert interface.route_to_vendor("test_cache_method", "600602") == "data:600602"
    assert calls == ["600602"]


def test_clear_tool_result_cache_forces_refetch(monkeypatch):
    calls = []

    def fake_vendor(ticker):
        calls.append(ticker)
        return f"data:{ticker}"

    monkeypatch.setitem(interface.VENDOR_METHODS, "test_cache_method2", {"a_stock": fake_vendor})
    monkeypatch.setattr(interface, "get_category_for_method", lambda method: "signal_data")
    monkeypatch.setattr(interface, "get_vendor", lambda category, method: "a_stock")
    interface.clear_tool_result_cache()

    interface.route_to_vendor("test_cache_method2", "000001")
    interface.clear_tool_result_cache()
    interface.route_to_vendor("test_cache_method2", "000001")
    assert calls == ["000001", "000001"]
