from dataclasses import dataclass

from scripts import controlled_restart_preflight as preflight


@dataclass
class Pair:
    symbol: str


class FakeRegistry:
    def __init__(self, cfg):
        self.cfg = cfg

    def load(self, client):
        return [Pair("BTCUSDT")]


class FakePosition:
    def __init__(self, state="idle", quantity=0.0, stop_loss_order_id=None):
        self._data = {
            "state": state,
            "quantity": quantity,
            "stop_loss_order_id": stop_loss_order_id,
        }

    def get(self, key, default=None):
        return self._data.get(key, default)


class FakeDB:
    position = FakePosition()

    def __init__(self, *args, **kwargs):
        pass

    def get_trade_state(self, symbol):
        return self.position


class FakeClient:
    balances = {}
    orders = {}

    def __init__(self, *args, **kwargs):
        pass

    def get_balances(self):
        return self.balances

    def get_open_orders(self, symbol):
        return self.orders.get(symbol, [])

    def get_symbol_filters(self, symbol):
        return {"minQty": "0.000001", "stepSize": "0.000001"}

    def close(self):
        pass


def _patch_common(monkeypatch, state):
    monkeypatch.setenv("BINANCE_API_KEY", "key")
    monkeypatch.setenv("BINANCE_API_SECRET", "secret")
    monkeypatch.setattr(preflight, "load_bot_config", lambda: {"portfolio": {"quote_asset": "USDT"}})
    monkeypatch.setattr(preflight, "PairRegistry", FakeRegistry)
    monkeypatch.setattr(preflight, "LiteDB", FakeDB)
    monkeypatch.setattr(preflight, "create_exchange_client", lambda *args, **kwargs: FakeClient())
    monkeypatch.setattr(preflight, "_load_state", lambda: state)


def test_preflight_allows_tracked_position_and_stop_order(monkeypatch):
    FakeDB.position = FakePosition("bought", quantity=0.01, stop_loss_order_id="sl-123")
    FakeClient.balances = {"BTC": {"available": 0.01, "reserved": 0.0}}
    FakeClient.orders = {"BTCUSDT": [{"orderId": "sl-123"}]}
    _patch_common(monkeypatch, {"aggregate": {"open_positions": 1}})

    report = preflight.run_preflight()

    assert report["safe_to_restart"] is True
    assert report["tracked_positions"]["BTCUSDT"]["quantity"] == 0.01
    assert report["reasons"] == []


def test_preflight_blocks_untracked_open_order(monkeypatch):
    FakeDB.position = FakePosition("bought", quantity=0.01, stop_loss_order_id="sl-123")
    FakeClient.balances = {"BTC": {"available": 0.01, "reserved": 0.0}}
    FakeClient.orders = {"BTCUSDT": [{"orderId": "sl-123"}, {"orderId": "extra-1"}]}
    _patch_common(monkeypatch, {"aggregate": {"open_positions": 1}})

    report = preflight.run_preflight()

    assert report["safe_to_restart"] is False
    assert "BTCUSDT has 1 untracked open orders" in report["reasons"]


def test_preflight_blocks_balance_without_tracked_position(monkeypatch):
    FakeDB.position = FakePosition("idle", quantity=0.0)
    FakeClient.balances = {"BTC": {"available": 0.01, "reserved": 0.0}}
    FakeClient.orders = {}
    _patch_common(monkeypatch, {"aggregate": {"open_positions": 0}})

    report = preflight.run_preflight()

    assert report["safe_to_restart"] is False
    assert "BTC balance is non-zero (0.01) with no tracked position" in report["reasons"]


def test_preflight_strict_mode_keeps_old_zero_position_policy(monkeypatch):
    FakeDB.position = FakePosition("bought", quantity=0.01, stop_loss_order_id="sl-123")
    FakeClient.balances = {"BTC": {"available": 0.01, "reserved": 0.0}}
    FakeClient.orders = {"BTCUSDT": [{"orderId": "sl-123"}]}
    _patch_common(monkeypatch, {"aggregate": {"open_positions": 1}})

    report = preflight.run_preflight(allow_tracked_positions=False)

    assert report["safe_to_restart"] is False
    assert "state reports open_positions=1" in report["reasons"]
