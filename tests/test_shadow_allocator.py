import pytest

from xauby.engine.shadow_allocator import (
    ShadowAllocationInput,
    ShadowAllocatorConfig,
    allocate_shadow_risk,
    regime_risk_multiplier,
)


def _candidate(candidate_id: str, symbol: str, side: str, **overrides):
    values = {
        "candidate_id": candidate_id,
        "symbol": symbol,
        "side": side,
        "base_risk_budget": 0.01,
        "realized_vol": 0.4,
        "target_vol": 0.4,
        "regime": "BULL_TREND_WEAK",
    }
    values.update(overrides)
    return ShadowAllocationInput(**values)


def test_regime_changes_risk_but_not_direction():
    assert regime_risk_multiplier("BULL_TREND_STRONG") == 1.0
    assert regime_risk_multiplier("BEAR_TREND_STRONG") == 1.0
    assert regime_risk_multiplier("HIGH_VOL_TRANSITION") == 0.5
    assert regime_risk_multiplier("NO_TRADE") == 0.0


def test_inverse_volatility_weights_two_symbols_without_forcing_full_heat():
    decision = allocate_shadow_risk(
        [
            _candidate("btc-trend", "BTCUSDT", "LONG", realized_vol=0.8),
            _candidate("xau-trend", "XAUUSDT", "LONG", realized_vol=0.2),
        ],
        config=ShadowAllocatorConfig(
            max_portfolio_heat=0.10,
            max_symbol_heat=0.05,
        ),
    )
    budgets = {item.candidate_id: item.risk_budget for item in decision.candidates}

    assert budgets["btc-trend"] == pytest.approx(0.005)
    assert budgets["xau-trend"] == pytest.approx(0.015)
    assert decision.total_gross_heat == pytest.approx(0.02)
    assert decision.research_only is True
    assert decision.executable is False


def test_same_symbol_conflict_abstains_when_neither_side_dominates():
    decision = allocate_shadow_risk(
        [
            _candidate("btc-long", "BTCUSDT", "LONG"),
            _candidate("btc-short", "BTCUSDT", "SHORT"),
        ],
        config=ShadowAllocatorConfig(
            max_portfolio_heat=0.10,
            max_symbol_heat=0.10,
            min_conflict_dominance=0.25,
        ),
    )

    btc = decision.symbols[0]
    assert btc.conflict is True
    assert btc.decision == "FLAT"
    assert btc.net_risk_budget == 0.0


def test_dominant_side_is_netted_and_global_and_symbol_caps_hold():
    decision = allocate_shadow_risk(
        [
            _candidate("btc-long-a", "BTCUSDT", "LONG", base_risk_budget=0.08),
            _candidate("btc-long-b", "BTCUSDT", "LONG", base_risk_budget=0.04),
            _candidate("btc-short", "BTCUSDT", "SHORT", base_risk_budget=0.02),
            _candidate("xau-long", "XAUUSDT", "LONG", base_risk_budget=0.08),
        ],
        config=ShadowAllocatorConfig(
            max_portfolio_heat=0.06,
            max_symbol_heat=0.04,
            min_conflict_dominance=0.20,
        ),
    )

    by_symbol = {item.symbol: item for item in decision.symbols}
    assert decision.total_gross_heat <= 0.06 + 1e-12
    assert all(item.gross_risk_budget <= 0.04 + 1e-12 for item in decision.symbols)
    assert by_symbol["BTCUSDT"].decision == "LONG"
    assert by_symbol["BTCUSDT"].net_risk_budget == pytest.approx(
        by_symbol["BTCUSDT"].long_risk_budget
        - by_symbol["BTCUSDT"].short_risk_budget
    )


def test_unhealthy_and_no_trade_candidates_receive_zero_budget():
    decision = allocate_shadow_risk(
        [
            _candidate("unhealthy", "BTCUSDT", "LONG", healthy=False),
            _candidate("blocked", "XAUUSDT", "SHORT", regime="NO_TRADE"),
        ]
    )
    rows = {item.candidate_id: item for item in decision.candidates}

    assert rows["unhealthy"].risk_budget == 0
    assert rows["unhealthy"].blocked_reason == "candidate_unhealthy"
    assert rows["blocked"].risk_budget == 0
    assert rows["blocked"].blocked_reason == "regime_blocks_risk"
