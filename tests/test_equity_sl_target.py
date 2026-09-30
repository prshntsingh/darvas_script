"""Equity client: optional stop-loss + target via Dhan Super Order (EQUITY_SL_TARGET_ENABLED)."""

import asyncio

import pytest

from client_agent import client


@pytest.fixture
def broker(monkeypatch):
    b = client.DhanBroker("1100", "TOKEN", dry_run=False)
    monkeypatch.setattr(b._scrips, "get_equity", lambda s: {"id": "3045", "segment": "NSE_EQ"})
    b.sent = []
    monkeypatch.setattr(b, "_place_dhan_order",
                        lambda payload, is_retry=False, url=None: b.sent.append((url, payload)) or {"order_id": "1"})
    monkeypatch.setattr(b, "_fetch_ltp", lambda symbol: b.ltp)
    b.ltp = 200.0
    monkeypatch.setattr(client, "_is_amo_window", lambda: False)
    monkeypatch.setattr(client, "TRADE_AMOUNT_INR", 10000)
    monkeypatch.setattr(client, "EQUITY_SL_PCT", 2.0)
    monkeypatch.setattr(client, "EQUITY_TARGET_PCT", 1.0)
    return b


def enable(monkeypatch, on=True):
    monkeypatch.setattr(client, "EQUITY_SL_TARGET_ENABLED", on)


def test_flag_off_places_the_same_plain_order_as_before(broker, monkeypatch):
    enable(monkeypatch, False)
    broker.place_order({"stock_symbol": "SBIN", "order_type": "LIMIT", "entry_price": 100.0})
    (url, p), = broker.sent
    assert url is None  # default /v2/orders
    assert p["price"] == 101.0 and p["quantity"] == 100 and p["validity"] == "DAY"
    assert "stopLossPrice" not in p and "targetPrice" not in p


def test_limit_order_gets_sl_and_target_from_the_limit_price(broker, monkeypatch):
    enable(monkeypatch)
    broker.place_order({"stock_symbol": "SBIN", "order_type": "LIMIT", "entry_price": 100.0})
    (url, p), = broker.sent
    assert url == client.DhanBroker.SUPER_ORDERS_URL
    # limit 101.0 (1% buffer): target +1% = 102.01 -> 102.0; SL -2% = 98.98 -> 99.0 (0.05 ticks)
    assert (p["price"], p["targetPrice"], p["stopLossPrice"]) == (101.0, 102.0, 99.0)
    assert p["productType"] == "CNC" and p["orderType"] == "LIMIT" and p["quantity"] == 100


def test_market_order_uses_live_price_for_qty_sl_and_target(broker, monkeypatch):
    enable(monkeypatch)
    broker.place_order({"stock_symbol": "SBIN", "order_type": "MARKET", "entry_price": 0})
    (url, p), = broker.sent
    assert url == client.DhanBroker.SUPER_ORDERS_URL
    assert (p["orderType"], p["price"], p["quantity"]) == ("MARKET", 0.0, 50)
    assert (p["targetPrice"], p["stopLossPrice"]) == (202.0, 196.0)


def test_after_market_order_is_placed_without_sl_target(broker, monkeypatch, caplog):
    enable(monkeypatch)
    monkeypatch.setattr(client, "_is_amo_window", lambda: True)
    broker.place_order({"stock_symbol": "SBIN", "order_type": "LIMIT", "entry_price": 100.0})
    (url, p), = broker.sent
    assert url is None and p["afterMarketOrder"] is True and p["amoTime"] == "OPEN"
    assert "stopLossPrice" not in p
    assert "WITHOUT SL/target" in caplog.text


def test_market_order_without_live_price_is_placed_without_sl_target(broker, monkeypatch, caplog):
    enable(monkeypatch)
    broker.ltp = None
    broker.place_order({"stock_symbol": "SBIN", "order_type": "MARKET", "entry_price": 0})
    (url, p), = broker.sent
    assert url is None and p["quantity"] == client.DEFAULT_QUANTITY
    assert "WITHOUT SL/target" in caplog.text


def test_flag_on_without_sl_pct_refuses_to_start(monkeypatch):
    enable(monkeypatch)
    monkeypatch.setattr(client, "EQUITY_SL_PCT", 0.0)
    monkeypatch.setattr(client, "_create_broker", lambda dry_run: pytest.fail("must not start"))
    with pytest.raises(SystemExit):
        asyncio.run(client.connect_and_listen())
