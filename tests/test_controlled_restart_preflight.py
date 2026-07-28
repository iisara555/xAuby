from dataclasses import dataclass

from scripts import controlled_restart_preflight as preflight


@dataclass
class Pair:
    symbol: str


class FakeRegistry:
    def __init__(self, cfg, **kwargs):
        self.cfg = cfg
        self.kwargs = kwargs

    def load(self, client):
        return [Pair("BTCUSDT")]


class FakePosition:
    def __init__(self, state="idle", quantity=0.0, stop_loss_order_id=None,
                 position_side="LONG", entry_price=0.0, exchange_position_id=None):
        self._data = {
            "state": state,
            "quantity": quantity,
            "stop_loss_order_id": stop_loss_order_id,
            "position_side": position_side,
            "entry_price": entry_price,
            "exchange_position_id": exchange_position_id,
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
    positions = []

    def __init__(self, *args, **kwargs):
        pass

    def get_balances(self):
        return self.balances

    def get_open_orders(self, symbol=None):
        if symbol is None:
            return [order for rows in self.orders.values() for order in rows]
        return self.orders.get(symbol, [])

    def get_positions(self, symbols=None):
        return list(self.positions)

    def get_symbol_filters(self, symbol):
        return {"minQty": "0.000001", "stepSize": "0.000001"}

    def close(self):
        pass


def _patch_common(monkeypatch, state, *, market_type="spot"):
    monkeypatch.setenv("BINANCE_API_KEY", "key")
    monkeypatch.setenv("BINANCE_API_SECRET", "secret")
    monkeypatch.setattr(preflight, "load_bot_config", lambda _path: {
        "portfolio": {"quote_asset": "USDT"},
        "exchange": {"market_type": market_type},
    })
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


def test_preflight_registry_is_read_only_and_uses_tenant_config(monkeypatch, tmp_path):
    captured = {}

    class CaptureRegistry(FakeRegistry):
        def __init__(self, cfg, **kwargs):
            super().__init__(cfg, **kwargs)
            captured.update(kwargs)

    monkeypatch.setenv("XAUBY_CONFIG_DIR", str(tmp_path))
    _patch_common(monkeypatch, {"aggregate": {"open_positions": 0}})
    monkeypatch.setattr(preflight, "PairRegistry", CaptureRegistry)
    FakeDB.position = FakePosition("idle")
    FakeClient.balances = {}
    FakeClient.orders = {}

    report = preflight.run_preflight()

    assert report["safe_to_restart"] is True
    assert captured["read_only"] is True
    assert captured["project_root"] == str(tmp_path)
    assert captured["config_path"] == str(tmp_path / "bot_config.yaml")


def test_default_credentials_prefer_materialized_tenant_env(monkeypatch, tmp_path):
    runtime_dir = tmp_path / "run"
    runtime_dir.mkdir()
    materialized = runtime_dir / "owner-itsara.env"
    materialized.write_text("TEST_PREFLIGHT_SOURCE=materialized\n", encoding="utf-8")
    tenant = tmp_path / "tenant"
    tenant.mkdir()
    (tenant / "secrets.env").write_text(
        "TEST_PREFLIGHT_SOURCE=tenant\n", encoding="utf-8"
    )
    monkeypatch.delenv("TEST_PREFLIGHT_SOURCE", raising=False)
    monkeypatch.setenv("XAUBY_INSTANCE_ID", "owner-itsara")
    monkeypatch.setattr(preflight, "MATERIALIZED_CREDENTIAL_ROOT", str(runtime_dir))
    monkeypatch.setattr(preflight, "config_file", lambda name: str(tenant / name))

    preflight._load_dotenv()

    assert preflight.os.environ["TEST_PREFLIGHT_SOURCE"] == "materialized"


def test_derivative_preflight_matches_side_quantity_and_entry(monkeypatch):
    FakeDB.position = FakePosition(
        "bought", quantity=0.0008, position_side="SHORT", entry_price=63733.9,
        exchange_position_id="pos-1",
    )
    FakeClient.balances = {}
    FakeClient.orders = {}
    FakeClient.positions = [{
        "symbol": "BTC/USDT:USDT", "position_side": "SHORT",
        "quantity": 0.0008, "entry_price": 63733.9,
        "exchange_position_id": "pos-1",
    }]
    _patch_common(
        monkeypatch, {"aggregate": {"open_positions": 1}}, market_type="swap"
    )

    report = preflight.run_preflight()

    assert report["safe_to_restart"] is True
    assert report["exchange_positions"]["BTCUSDT"]["position_side"] == "SHORT"


def test_derivative_preflight_blocks_side_quantity_and_entry_drift(monkeypatch):
    FakeDB.position = FakePosition(
        "bought", quantity=0.0008, position_side="SHORT", entry_price=63733.9,
    )
    FakeClient.balances = {}
    FakeClient.orders = {}
    FakeClient.positions = [{
        "symbol": "BTCUSDT", "position_side": "LONG",
        "quantity": 0.0010, "entry_price": 63000.0,
    }]
    _patch_common(
        monkeypatch, {"aggregate": {"open_positions": 1}}, market_type="swap"
    )

    report = preflight.run_preflight()

    assert report["safe_to_restart"] is False
    assert any("side mismatch" in reason for reason in report["reasons"])
    assert any("quantity mismatch" in reason for reason in report["reasons"])
    assert any("entry mismatch" in reason for reason in report["reasons"])


def test_derivative_preflight_blocks_position_id_drift(monkeypatch):
    FakeDB.position = FakePosition(
        "bought", quantity=0.0008, position_side="SHORT", entry_price=63733.9,
        exchange_position_id="tracked-position",
    )
    FakeClient.balances = {}
    FakeClient.orders = {}
    FakeClient.positions = [{
        "symbol": "BTCUSDT", "position_side": "SHORT",
        "quantity": 0.0008, "entry_price": 63733.9,
        "exchange_position_id": "different-position",
    }]
    _patch_common(
        monkeypatch, {"aggregate": {"open_positions": 1}}, market_type="swap"
    )

    report = preflight.run_preflight()

    assert report["safe_to_restart"] is False
    assert any("position id mismatch" in reason for reason in report["reasons"])


def test_derivative_preflight_blocks_missing_or_untracked_position(monkeypatch):
    FakeDB.position = FakePosition(
        "bought", quantity=0.0008, position_side="SHORT", entry_price=63733.9,
    )
    FakeClient.balances = {}
    FakeClient.orders = {}
    FakeClient.positions = []
    _patch_common(
        monkeypatch, {"aggregate": {"open_positions": 1}}, market_type="swap"
    )
    report = preflight.run_preflight()
    assert report["safe_to_restart"] is False
    assert "BTCUSDT is tracked locally but missing on exchange" in report["reasons"]

    FakeDB.position = FakePosition("idle")
    FakeClient.positions = [{
        "symbol": "BTCUSDT", "position_side": "SHORT",
        "quantity": 0.0008, "entry_price": 63733.9,
    }]
    report = preflight.run_preflight()
    assert report["safe_to_restart"] is False
    assert "BTCUSDT exists on exchange but is not tracked locally" in report["reasons"]


def test_preflight_blocks_account_exposure_outside_configured_pairs(monkeypatch):
    FakeDB.position = FakePosition("idle")
    FakeClient.balances = {}
    FakeClient.orders = {
        "ETHUSDT": [{"symbol": "ETH/USDT:USDT", "orderId": "orphan-order"}]
    }
    FakeClient.positions = [{
        "symbol": "ETH/USDT:USDT", "position_side": "LONG",
        "quantity": 0.01, "entry_price": 3000.0,
    }]
    _patch_common(
        monkeypatch, {"aggregate": {"open_positions": 0}}, market_type="swap"
    )

    report = preflight.run_preflight()

    assert report["safe_to_restart"] is False
    assert "exchange has untracked open order on ETHUSDT" in report["reasons"]
    assert any(
        "untracked derivative position ETHUSDT" in reason
        for reason in report["reasons"]
    )
