from datetime import datetime

import pytest

from app.services.etf_order_preview import preview_etf_order
from app.skills.runtime import _tool_allowed
from skills.mx_core.execution import MXExecutionService
from skills.mx_core.tool_specs import build_tools


class FakeEtfClient:
    def __init__(self, *, symbol="588000.SH", price=0.985,
                 timestamp="2026-09-24T13:59:00+08:00"):
        self.symbol = symbol
        self.price = price
        self.timestamp = timestamp
        self.queries = []
        self.trade_calls = 0

    def query_market(self, query):
        self.queries.append(query)
        return {"code": "200", "data": {"dataTableDTOList": [{
            "code": self.symbol, "dataTypeEnum": "HQ",
            "field": {"returnName": "最新价", "returnCode": "f2"},
            "rawTable": {"headName": [self.timestamp], "f2": [self.price]},
        }]}}

    def trade(self, **kwargs):
        self.trade_calls += 1
        raise AssertionError("ETF preview must never call vendor trade")


NOW = datetime.fromisoformat("2026-09-24T14:00:00+08:00")


def test_etf_preview_exact_units_tick_and_no_vendor_write():
    client = FakeEtfClient()
    result = preview_etf_order(client, symbol="588000", quantity=100, now=NOW)
    assert result["symbol"] == "588000.SH"
    assert result["quantity_unit"] == "份"
    assert result["limit_price_yuan"] == "0.985"
    assert result["max_notional_yuan"] == "98.500"
    assert result["market_quote"]["as_of"] == "2026-09-24T13:59:00+08:00"
    assert result["can_submit"] is False
    assert client.queries == ["588000.SH 最新价"]
    assert client.trade_calls == 0


def test_etf_preview_validates_user_limit_at_mill_precision():
    result = preview_etf_order(
        FakeEtfClient(symbol="510300.SH"), symbol="510300.SH", quantity=200,
        limit_price=0.986, now=NOW,
    )
    assert result["symbol"] == "510300.SH"
    assert result["limit_price_yuan"] == "0.986"
    assert result["max_notional_yuan"] == "197.200"


@pytest.mark.parametrize("symbol,quantity", [
    ("588000.SZ", 100), ("510301.SH", 100), ("600000.SH", 100),
    ("588000.SH", 0), ("588000.SH", 99), ("588000.SH", True),
])
def test_etf_preview_rejects_unsupported_symbol_and_lot_before_query(symbol, quantity):
    client = FakeEtfClient()
    with pytest.raises(RuntimeError):
        preview_etf_order(client, symbol=symbol, quantity=quantity, now=NOW)
    assert not client.queries and not client.trade_calls


@pytest.mark.parametrize("price", [0, -1, 0.9855, float("nan"), float("inf"), True])
def test_etf_preview_rejects_bad_limit(price):
    with pytest.raises(RuntimeError, match="limit price"):
        preview_etf_order(
            FakeEtfClient(), symbol="588000.SH", quantity=100,
            limit_price=price, now=NOW,
        )


@pytest.mark.parametrize("price,timestamp", [
    (0.9855, "2026-09-24T13:59:00+08:00"),
    (0.985, "2026-09-24T13:50:00+08:00"),
    (0.985, "2026-09-24T14:01:00+08:00"),
])
def test_etf_preview_rejects_inexact_quote_or_stale_time(price, timestamp):
    with pytest.raises(RuntimeError):
        preview_etf_order(
            FakeEtfClient(price=price, timestamp=timestamp),
            symbol="588000.SH", quantity=100, now=NOW,
        )


def test_etf_preview_tool_is_read_only_and_exposed_in_all_run_types(monkeypatch):
    client = FakeEtfClient()
    monkeypatch.setattr("skills.mx_core.execution.preview_etf_order", lambda client, **kwargs: {
        "can_submit": False, "symbol": "588000.SH",
    })
    service = MXExecutionService()
    for run_type in ("analysis", "chat", "trade"):
        assert _tool_allowed("mx_etf_order_preview", run_type)
        assert "mx_etf_order_preview" in {
            tool["function"]["name"] for tool in build_tools(run_type)
        }
    response = service.execute_tool(
        client=client, app_settings=None, tool_name="mx_etf_order_preview",
        arguments={"symbol": "588000.SH", "quantity": 100},
    )
    assert response["ok"] and response["result"]["can_submit"] is False
    assert client.trade_calls == 0
