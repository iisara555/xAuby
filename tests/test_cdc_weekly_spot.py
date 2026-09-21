"""Weekly signal boundaries and isolation of the THB paper candidate."""

from dataclasses import replace
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml

from scripts.weekly_cdc_snapshot import WEEK_MS, snapshot
from xauby.engine.base import BaseEngine
from xauby.runtime.trading_config import canonical_runtime_config
from xauby.runtime.whitelist_validator import validate_whitelist_schema
from xauby.strategies.context import MarketContext
from xauby.strategies.registry import load_strategy

ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT / "configs/binance_th_weekly_cdc"


def candles(rising=True):
    prices = np.linspace(100, 200, 120)
    if not rising:
        prices = prices[::-1]
    start = 1_704_067_200_000  # Monday 2024-01-01 UTC
    return [[start + i * WEEK_MS, p, p + 2, p - 2, p, 10,
             start + (i + 1) * WEEK_MS - 1] for i, p in enumerate(prices)]


@pytest.mark.parametrize("rising,zone,flat,held", [
    (True, "GREEN", "BUY", "HOLD"), (False, "RED", "HOLD", "SELL"),
])
def test_closed_week_rules_and_no_short(rising, zone, flat, held):
    rows = candles(rising)
    result = snapshot("BTCTHB", rows, int(rows[-1][6]) + 1)
    assert result["zone"] == zone
    assert result["action_if_flat"] == flat
    assert result["action_if_holding_long"] == held
    assert result["closed_weeks"] == 120


def test_forming_week_cannot_flip_confirmed_signal():
    rows = candles()
    now = int(rows[-1][6]) + 1
    forming = [now, 200, 201, 1, 1, 1000, now + WEEK_MS - 1]
    before = snapshot("SOLTHB", rows, now)
    after = snapshot("SOLTHB", rows + [forming], now + 60_000)
    for key in ("zone", "ema12_thb", "ema26_thb", "action_if_flat", "close_thb"):
        assert after[key] == before[key]
    assert after["forming_rows_ignored"] == 1


@pytest.mark.parametrize("problem", ["duplicate", "gap", "stale", "short", "nan", "wrong_interval"])
def test_bad_or_incomplete_history_fails_closed(problem):
    rows = candles()
    now = int(rows[-1][6]) + 1
    if problem == "duplicate":
        rows.append(rows[-1])
    elif problem == "gap":
        del rows[50]
    elif problem == "stale":
        now += WEEK_MS
    elif problem == "short":
        rows = rows[-99:]
    elif problem == "nan":
        rows[-1][4] = float("nan")
    else:
        rows[-1][6] -= 1
    with pytest.raises(ValueError):
        snapshot("BTCTHB", rows, now)


def test_weekly_metadata_and_ambiguous_input_guards():
    strategy = load_strategy("cdc_weekly_spot", strict=True)
    assert strategy.get_meta().required_timeframes == ["1w"]
    assert strategy.get_meta().maturity == "paper"
    rows = candles()
    frame = pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume", "close_time"])
    frame["timestamp"] //= 1000
    ctx = MarketContext("BTCTHB", "1w", frame, 200,
                        extras={"last_bar_is_forming": False})
    signal = strategy.analyze(ctx)
    assert signal.action == "BUY"  # Old GREEN, no fresh-cross restriction.
    assert signal.timeframe == "1w"
    assert "4H" not in signal.reason + signal.status_summary
    assert strategy.analyze(replace(ctx, timeframe_primary="4h")).action == "HOLD"
    assert strategy.analyze(replace(ctx, extras={})).action == "HOLD"
    assert strategy.analyze(replace(ctx, position_side="SHORT")).action == "HOLD"
    assert strategy.analyze(replace(ctx, df_primary=frame.tail(99))).action == "HOLD"


def test_profile_resolves_to_thb_paper_without_old_overrides():
    config_path = PROFILE / "bot_config.yaml"
    cfg = yaml.safe_load(config_path.read_text())
    wl = json.loads((PROFILE / "coin_whitelist.json").read_text())
    assert validate_whitelist_schema(wl) == ["BTCTHB", "SOLTHB"]
    runtime = canonical_runtime_config(
        cfg, project_root=str(PROFILE), config_path=str(config_path),
        whitelist_path=str(PROFILE / "coin_whitelist.json"), for_live=True,
    )
    assert runtime.simulate_only and runtime.read_only
    assert cfg["exchange"]["quote_asset"] == cfg["portfolio"]["quote_asset"] == "THB"
    assert cfg["exchange"]["fee_pct"] == 0.0025
    assert set(runtime.symbols) == {"BTCTHB", "SOLTHB"}
    for symbol in runtime.symbols.values():
        assert symbol["execution_mode"] == "sim"
        rules = symbol["strategy"]
        assert rules["primary_timeframe"] == "1w"
        assert rules["confirm_timeframe"] == ""
        assert rules["disable_stop_loss"]
        assert not rules["enable_short"]
        assert not rules["require_fresh_zone"]
        assert not rules["use_d1_regime_filter"]
        assert rules["rsi_min"] == -1
        assert rules["rsi_max"] == 101
        assert rules["minimal_roi"] == {}
        assert rules["fixed_tp_pct"] == 0


def test_engine_treats_one_week_as_a_full_week():
    assert BaseEngine._timeframe_ms("1w") == WEEK_MS
