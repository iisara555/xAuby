from types import SimpleNamespace

import pytest

from xauby.engine.shadow import ShadowVirtualLedger


def test_shadow_ledger_is_candidate_scoped_and_never_uses_live_broker():
    ledger = ShadowVirtualLedger("challenger-a", initial_cash=1000)
    opened = ledger.apply(
        SimpleNamespace(action="BUY", intent="OPEN", position_side="LONG"),
        symbol="BTCUSDT",
        price=100,
        notional=100,
        fee_pct=0.1,
        timestamp=1,
    )
    assert opened["candidate_id"] == "challenger-a"
    assert opened["position"]["quantity"] == pytest.approx(1)
    assert opened["equity"] == pytest.approx(999.9)

    closed = ledger.apply(
        SimpleNamespace(action="SELL", intent="CLOSE", position_side="LONG"),
        symbol="BTCUSDT",
        price=110,
        notional=0,
        fee_pct=0.1,
        timestamp=2,
    )
    assert closed["position"] is None
    assert closed["realized_pnl"] == pytest.approx(9.79)
    assert closed["equity"] == pytest.approx(1009.79)


def test_shadow_ledger_rejects_invalid_mark():
    ledger = ShadowVirtualLedger("challenger-a")
    with pytest.raises(ValueError):
        ledger.apply(
            SimpleNamespace(action="HOLD", intent="HOLD", position_side=None),
            symbol="BTCUSDT",
            price=0,
            notional=0,
        )
