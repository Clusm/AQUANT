"""A 股数据源兜底逻辑测试:insider dict 归一化 + Sina 备用源。"""
from __future__ import annotations


class _FakeResponse:
    def __init__(self, payload, encoding="utf-8"):
        self._payload = payload
        self.encoding = encoding
        self.text = payload if isinstance(payload, str) else ""

    def json(self):
        if isinstance(self._payload, (list, dict)):
            return self._payload
        raise TypeError


def test_insider_transactions_handles_dict_f10(monkeypatch):
    from tradingagents.dataflows import a_stock

    class _DictF10Client:
        def F10(self, symbol, name):
            return {
                "最新提示": "【1.最新提示】测试内容\r\n【4.股东变化】股东变化测试内容",
                "股东研究": "股东研究文本",
            }

    monkeypatch.setattr(a_stock, "_get_mootdx_client", lambda: _DictF10Client())
    result = a_stock.get_insider_transactions("600602")
    assert result.startswith("# Shareholder Research")
    assert "【4.股东变化】" in result
    assert not result.startswith("Error")


def test_sina_fund_flow_fallback_parses_rows(monkeypatch):
    from tradingagents.dataflows import a_stock

    payload = [
        {"opendate": "2026-08-14", "netamount": "191881911.22", "ratioamount": "0.0855514"},
        {"opendate": "2026-08-13", "netamount": "-339564325.43", "ratioamount": "-0.118748"},
    ]
    monkeypatch.setattr(a_stock._requests, "get", lambda *args, **kwargs: _FakeResponse(payload))
    lines = a_stock._get_fund_flow_sina("600602", 5)
    assert any("19188" in line for line in lines)
    assert any("-33956" in line for line in lines)


def test_sina_industry_fallback_parses_board_json(monkeypatch):
    from tradingagents.dataflows import a_stock

    payload = (
        "var S_Finance_bankuai_sinaindustry = "
        '{"new_blhy":"new_blhy,玻璃行业,19,17.41,0.28,1.66,603570618,17507420617,'
        'sh600176,5.943,44.390,2.490,中国巨石"}'
    )
    monkeypatch.setattr(a_stock._requests, "get", lambda *args, **kwargs: _FakeResponse(payload))
    lines = a_stock._get_industry_comparison_sina(20)
    assert lines[0] == "## Sina Industry Board Ranking (fallback)"
    assert any("玻璃行业" in line for line in lines)


def test_eastmoney_financial_fallback_builds_df(monkeypatch):
    from tradingagents.dataflows import a_stock

    payload = {
        "result": {
            "data": [
                {"REPORT_DATE": "2026-03-31 00:00:00", "NOTICE_DATE": "2026-04-25 00:00:00",
                 "TOTAL_ASSETS": 100, "TOTAL_LIABILITIES": 60},
            ]
        }
    }
    monkeypatch.setattr(a_stock, "_em_get", lambda *args, **kwargs: _FakeResponse(payload))
    df = a_stock._get_financial_report_em("600602", "资产负债表", "quarterly", "2026-08-14")
    assert len(df) == 1
    assert df.iloc[0]["TOTAL_ASSETS"] == 100
