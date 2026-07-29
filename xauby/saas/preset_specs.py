"""Hand-authored preset specs — identity, configuration, and approval only.

Split out from `catalog.py` so the two halves cannot be confused: everything in
this file is written by a person, and everything derived from a certification
run lives in `certification.py`. A spec here that tries to state a verdict or a
backtest block is rejected when the catalog is built.
"""
from __future__ import annotations

# Three independent things kept in three fields, because collapsing them is how
# the July 2026 XAU preset ended up advertising a certificate it did not have:
#
#   backtest.status        EVIDENCE  — do we have a measurement, and is it any
#                                      good?  pending / insufficient / validated
#   certification_status   VERDICT   — did that measurement clear the
#                                      pre-registered backtest.acceptance gate?
#                                      certified / failed / not_assessed
#   live_certified         APPROVAL  — may an operator activate this live?  This
#                                      is a switch, not a research result: it
#                                      gates canGoLive in the SaaS UI.
#
# They genuinely can disagree in practice. The previous XAU profile had solid
# evidence, a FAILED verdict, and operator approval to run anyway. The current
# profile is long-only and has its own measured record; keeping the axes
# separate still matters because a future record may fail or become stale.
#
# EVIDENCE and VERDICT are no longer written here (P1.4). They are derived from
# xauby/saas/certificates/<preset_id>.json, which scripts/certify_preset.py
# emits from an actual protocol run, and a record stops applying the moment the
# preset's configuration stops matching the one it measured. Declaring either
# field in a spec below is a hard error — see xauby/saas/certification.py.
#
# APPROVAL stays here, because it is genuinely an operator's call. Approving a
# preset whose verdict is not "certified" is allowed and sometimes right (XAU),
# but it must carry operator_override naming who decided, when, and why.

PRESET_SPECS = [
    {
        "id": "okx-xau-actionzone-v1",
        "target_id": "okx-swap",
        "label": "XAU ActionZone · CDC Pure",
        "exchange_id": "okx",
        "market_type": "swap",
        "symbol": "XAUUSDT",
        "asset": "XAU",
        "strategy": "xauby_actionzone",
        "primary_timeframe": "4h",
        "confirm_timeframe": "1d",
        "allowed_sides": ["long"],
        "max_leverage": 1,
        "allocation_pct": 65.0,
        "max_position_per_trade_pct": 25.0,
        # APPROVAL: independent from the venue-backed certificate record. The
        # exact long-only profile below must still carry a matching fingerprint
        # before this preset can build or be activated.
        "live_certified": True,
        "cdc_pure_certified": True,
        "stop_loss_required": False,
        "execution_profile": {
            "name": "cdc_pure",
            "backtest_data_proxy": "PAXGUSDT",
            "enable_short": False,
            # Balanced grid winner (2026-07-29): daily GREEN gates new LONG
            # entries. SHORT gating remains explicit even though new automated
            # shorts are disabled, so the measured profile cannot inherit it
            # from an unrelated bot_config.yaml.
            #
            # All three keys are stated even though `_short: True` is what the
            # shared flag already resolves to. A profile that sets part of the
            # group inherits the rest from whatever bot_config.yaml happens to
            # say, so what a certificate measured would depend on a file the
            # preset does not control — see CONTROL_GROUPS in
            # xauby/backtest/walkforward.py.
            "use_d1_regime_filter": True,
            "use_d1_regime_filter_long": True,
            "use_d1_regime_filter_short": True,
            "ap_smoothing": 2,
            "require_fresh_zone": True,
            "fresh_zone_window": 3,
            "require_slow_slope": False,
            "slow_slope_bars": 3,
            "entry_thrust_min": 0.5,
            "exit_on_bear_cross": False,
            "rsi_min": 0.0,
            "rsi_max": 100.0,
            "vol_min_ratio": 0.0,
            "sl_atr_mult": 2.0,
            "trailing_atr_mult": 1.8,
            "fixed_tp_pct": 0.0,
            "disable_stop_loss": True,
            "breakeven_sl_enabled": False,
            "breakeven_activation_atr_mult": 1.5,
            "breakeven_buffer_atr_mult": 0.1,
            "minimal_roi": {"0": 8.0, "1440": 5.0, "4320": 3.0},
            "cool_down_minutes": 240,
            "position_pct": 0.95,
        },
        "strategy_traits": [
            "Long only · automated short entries disabled",
            "D1 regime filter: long entries require a daily GREEN zone",
            "Fresh-zone window: 3 · thrust ≥ 0.5 · slope filter off",
            "Exit: CDC zone flip / ROI ladder 8→5→3% · no exchange stop",
        ],
    },
    {
        "id": "binance-btc-supertrend-v1",
        "target_id": "binance-global-futures",
        "label": "BTC Supertrend EMA200 · Binance 1H",
        "exchange_id": "binanceusdm",
        "market_type": "swap",
        "symbol": "BTCUSDT",
        "asset": "BTC",
        "strategy": "supertrend_ema200",
        "primary_timeframe": "1h",
        "confirm_timeframe": "4h",
        "allowed_sides": ["long"],
        "max_leverage": 1,
        # Available for simulation/research only until this exact Binance
        # futures profile has its own venue-backed certificate.  A certificate
        # for the same strategy on OKX is not portable evidence.
        "live_certified": False,
    },
    {
        "id": "binance-th-btc-supertrend-v1",
        "target_id": "binance-th-spot",
        "label": "BTC/THB Supertrend EMA200",
        "exchange_id": "binance_th",
        "market_type": "spot",
        "symbol": "BTCTHB",
        "asset": "BTC",
        "strategy": "supertrend_ema200",
        "primary_timeframe": "1h",
        "confirm_timeframe": "4h",
        "allowed_sides": ["long"],
        "max_leverage": 1,
        # BTCTHB has too little history for the protocol.  Keep the preset
        # selectable in sim, but never let "pending" cross the live gate.
        "live_certified": False,
    },
    {
        "id": "okx-btc-supertrend-v1",
        "target_id": "okx-swap",
        "label": "BTC Supertrend EMA200 · 4H",
        "exchange_id": "okx",
        "market_type": "swap",
        "symbol": "BTCUSDT",
        "asset": "BTC",
        "strategy": "supertrend_ema200",
        "primary_timeframe": "4h",
        "confirm_timeframe": "",
        "allowed_sides": ["long", "short"],
        "max_leverage": 1,
        "allocation_pct": 30.0,
        "risk_pct": 0.02,
        "max_position_per_trade_pct": 25.0,
        # The one preset whose certificate passes. No override needed: approval
        # and verdict agree here.
        "live_certified": True,
        "stop_loss_required": True,
        "execution_profile": {
            "enable_short": True,
            "ema_period": 200,
            "atr_period": 10,
            "supertrend_mult": 4.0,
            "confirm_bars": 1,
            "entry_on_flip_only": True,
            "rsi_period": 14,
            "rsi_min": 0.0,
            "rsi_max": 100.0,
            "volume_ma_period": 20,
            "vol_min_ratio": 0.0,
            "sl_atr_mult": 3.0,
            "trailing_atr_mult": 2.0,
            "fixed_tp_pct": 0.0,
            "breakeven_sl_enabled": True,
            "breakeven_activation_atr_mult": 1.2,
            "breakeven_buffer_atr_mult": 0.05,
            "exit_on_supertrend_flip": True,
            "exit_on_ema_loss": True,
            "max_calc_bars": 420,
        },
        "strategy_traits": [
            "Long + short · 1× isolated perpetual",
            "Single timeframe: 4H",
            "Risk sizing: 2% equity / stop distance",
            "Pair allocation cap: 30%",
            "Exit: SuperTrend flip / EMA200 loss · 3× ATR stop",
        ],
    },
    {
        "id": "binance-eth-supertrend-v1",
        "target_id": "binance-global-futures",
        "label": "ETH Supertrend EMA200",
        "exchange_id": "binanceusdm",
        "market_type": "swap",
        "symbol": "ETHUSDT",
        "asset": "ETH",
        "strategy": "supertrend_ema200",
        "primary_timeframe": "1h",
        "confirm_timeframe": "4h",
        "allowed_sides": ["long"],
        "max_leverage": 1,
        "live_certified": False,
        "stop_loss_required": True,
    },
    {
        "id": "binance-spot-btc-supertrend-v1",
        "target_id": "binance-global-spot",
        "label": "BTC Supertrend EMA200 (Spot)",
        "exchange_id": "binance",
        "market_type": "spot",
        "symbol": "BTCUSDT",
        "asset": "BTC",
        "strategy": "supertrend_ema200",
        "primary_timeframe": "1h",
        "confirm_timeframe": "4h",
        "allowed_sides": ["long"],
        "max_leverage": 1,
        "live_certified": False,
        "stop_loss_required": True,
    },
    {
        "id": "binance-spot-eth-supertrend-v1",
        "target_id": "binance-global-spot",
        "label": "ETH Supertrend EMA200 (Spot)",
        "exchange_id": "binance",
        "market_type": "spot",
        "symbol": "ETHUSDT",
        "asset": "ETH",
        "strategy": "supertrend_ema200",
        "primary_timeframe": "1h",
        "confirm_timeframe": "4h",
        "allowed_sides": ["long"],
        "max_leverage": 1,
        "live_certified": False,
        "stop_loss_required": True,
    },
    {
        "id": "binance-th-eth-supertrend-v1",
        "target_id": "binance-th-spot",
        "label": "ETH/THB Supertrend EMA200",
        "exchange_id": "binance_th",
        "market_type": "spot",
        "symbol": "ETHTHB",
        "asset": "ETH",
        "strategy": "supertrend_ema200",
        "primary_timeframe": "1h",
        "confirm_timeframe": "4h",
        "allowed_sides": ["long"],
        "max_leverage": 1,
        "live_certified": False,
        "stop_loss_required": True,
    },
]
