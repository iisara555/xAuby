#!/usr/bin/env python3
"""Run the locked BTC SuperTrend + Donchian 50/50 shadow protocol.

The exploratory branch measured the two strategies separately and also reset
capital at calendar-month boundaries for its correlation study.  This harness
answers the portfolio question directly: both frozen strategies run on one
native OKX 4H clock, their virtual sleeves compound continuously, and their
mark-to-market curves are combined without granting either strategy broker
access.  It writes proposed evidence only; it never edits the catalog, tenant
configuration, or runtime state.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import os
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pandas as pd

from scripts.validate_on_venue_data import _okx_frame
from xauby.analytics import risk
from xauby.backtest.replay import run_plugin_replay
from xauby.observability.replay_validation import load_bot_config
from xauby.saas.certification import config_fingerprint
from xauby.saas.preset_specs import PRESET_SPECS

DEFAULT_PROTOCOL = (
    ROOT / "docs/research/protocols/btc_supertrend_donchian_ensemble_v1.json"
)
PROTOCOL_NAME = "btc-supertrend-donchian-50-50-shadow"
PROTOCOL_VERSION = "1"
VENUE_SYMBOL = "BTC-USDT-SWAP"
ENGINE_SYMBOL = "BTCUSDT"
PERIODS_PER_YEAR_4H = 365.0 * 6.0


def _number(value: Any) -> float:
    result = float(value or 0.0)
    if not math.isfinite(result):
        raise ValueError("replay produced a non-finite number")
    return result


def _ratio(left: Any, right: Any) -> float:
    denominator = _number(right)
    return _number(left) / denominator if denominator > 0 else 0.0


def _preset(preset_id: str) -> dict[str, Any]:
    for preset in PRESET_SPECS:
        if str(preset.get("id")) == preset_id:
            return dict(preset)
    raise ValueError(f"unknown preset {preset_id!r}")


def canonical_sha256(value: Mapping[str, Any]) -> str:
    payload = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def frame_sha256(frame: pd.DataFrame) -> str:
    """Hash the exact native OHLCV rows without locale-dependent formatting."""
    digest = hashlib.sha256()
    columns = ("open_time", "open", "high", "low", "close", "volume")
    for row in frame.loc[:, columns].itertuples(index=False, name=None):
        encoded = [str(int(row[0]))]
        encoded.extend(format(float(value), ".17g") for value in row[1:])
        digest.update((",".join(encoded) + "\n").encode("ascii"))
    return digest.hexdigest()


def workflow_provenance(*, required: bool) -> dict[str, Any]:
    provenance = {
        "repository": os.getenv("GITHUB_REPOSITORY", ""),
        "commit": os.getenv("GITHUB_SHA", ""),
        "workflow": os.getenv("GITHUB_WORKFLOW", ""),
        "run_id": os.getenv("GITHUB_RUN_ID", ""),
        "run_attempt": os.getenv("GITHUB_RUN_ATTEMPT", ""),
    }
    complete = (
        os.getenv("GITHUB_ACTIONS") == "true"
        and len(provenance["commit"]) == 40
        and all(provenance[key] for key in ("repository", "workflow", "run_id"))
    )
    provenance["complete"] = complete
    if required and not complete:
        raise ValueError("locked certification requires complete GitHub workflow provenance")
    return provenance


def ensemble_fingerprint(manifest: Mapping[str, Any]) -> str:
    identity = {
        "ensemble_id": manifest.get("ensemble_id"),
        "members": [
            {
                "id": member.get("preset_id"),
                "config_fingerprint": member.get("config_fingerprint"),
                "weight": member.get("weight"),
            }
            for member in manifest.get("members") or []
        ],
        "execution_model": manifest.get("execution_model"),
        "fill_model": manifest.get("fill_model"),
        "primary_timeframe": (manifest.get("data_source") or {}).get(
            "primary_timeframe"
        ),
    }
    return canonical_sha256(identity)[:16]


def _economics(config: Mapping[str, Any]) -> dict[str, Any]:
    exchange = config.get("exchange") or {}
    trading = config.get("trading") or {}
    backtest = config.get("backtest") or {}
    portfolio = config.get("portfolio") or {}
    sizing = portfolio.get("position_sizing") or {}
    return {
        "fee_pct": _number(exchange.get("fee_pct")),
        "slippage_bps": _number(backtest.get("slippage_bps")),
        "funding_rate_8h": _number(backtest.get("funding_rate_8h")),
        "initial_balance": _number(portfolio.get("initial_balance")),
        "risk_pct": _number(trading.get("risk_pct", sizing.get("risk_pct"))),
        "max_position_per_trade_pct": _number(
            trading.get(
                "max_position_per_trade_pct",
                sizing.get("max_position_per_trade_pct"),
            )
        ),
        "sl_confirm_ticks": int(trading.get("sl_confirm_ticks") or 0),
    }


def _research_member(manifest: Mapping[str, Any]) -> dict[str, Any]:
    members = list(manifest.get("members") or [])
    research = [member for member in members if member.get("role") == "donchian"]
    if len(research) != 1 or not isinstance(research[0].get("preset"), Mapping):
        raise ValueError("protocol requires one embedded Donchian research preset")
    return dict(research[0]["preset"])


def require_locked_identity(
    manifest: Mapping[str, Any],
    champion: Mapping[str, Any],
    donchian: Mapping[str, Any],
    economics: Mapping[str, Any],
) -> None:
    members = list(manifest.get("members") or [])
    if len(members) != 2:
        raise ValueError("locked ensemble requires exactly two members")
    weights = [float(member.get("weight") or 0.0) for member in members]
    if any(weight <= 0 for weight in weights) or abs(sum(weights) - 1.0) > 1e-12:
        raise ValueError("locked member weights must be positive and total one")
    if weights != [0.5, 0.5]:
        raise ValueError("primary ensemble allocation must remain fixed at 50/50")

    by_role = {str(member.get("role")): member for member in members}
    if set(by_role) != {"champion", "donchian"}:
        raise ValueError("locked ensemble roles changed")
    expected = {
        "champion": (champion, by_role["champion"]),
        "donchian": (donchian, by_role["donchian"]),
    }
    for role, (preset, member) in expected.items():
        if str(member.get("preset_id")) != str(preset.get("id")):
            raise ValueError(f"{role} preset id changed")
        actual = config_fingerprint(preset)
        if str(member.get("config_fingerprint")) != actual:
            raise ValueError(
                f"{role} fingerprint is {actual}, protocol expects "
                f"{member.get('config_fingerprint')}"
            )
    if str(champion.get("strategy")) != "supertrend_ema200":
        raise ValueError("Champion strategy changed")
    if str(donchian.get("strategy")) != "xauby_donchian_trend":
        raise ValueError("Donchian research strategy changed")
    if str(donchian.get("primary_timeframe")) != "4h":
        raise ValueError("Donchian research member must remain explicitly 4H")
    if list(donchian.get("allowed_sides") or []) != ["long"]:
        raise ValueError("Donchian research member must remain long-only")
    if manifest.get("fill_model") != "isolated_virtual_sleeves_close_fill_v1":
        raise ValueError("unsupported locked fill model")
    locked = manifest.get("execution_model") or {}
    for key, expected_value in locked.items():
        if key in {"primary_weights", "sensitivity_weights"}:
            continue
        actual = economics.get(key)
        if actual is None or abs(float(actual) - float(expected_value)) > 1e-12:
            raise ValueError(
                f"execution model changed for {key}: {actual!r}, "
                f"expected {expected_value!r}"
            )
    if str(manifest.get("ensemble_fingerprint")) != ensemble_fingerprint(manifest):
        raise ValueError("ensemble fingerprint does not match the locked identity")


def _clip_native(frame: pd.DataFrame, source: Mapping[str, Any]) -> pd.DataFrame:
    start = int(pd.Timestamp(str(source["window_start"])).timestamp() * 1000)
    end = int(pd.Timestamp(str(source["window_end"])).timestamp() * 1000)
    clipped = frame[
        (frame["open_time"].astype("int64") >= start)
        & (frame["open_time"].astype("int64") <= end)
    ].copy()
    clipped.reset_index(drop=True, inplace=True)
    if clipped.empty:
        raise ValueError("locked native data window is empty")
    first = int(clipped["open_time"].iloc[0])
    last = int(clipped["open_time"].iloc[-1])
    tolerance_ms = 4 * 60 * 60 * 1000
    if first - start > tolerance_ms or end - last > 24 * 60 * 60 * 1000:
        raise ValueError("native data does not cover the locked date window")
    return clipped


def _run_arm(
    frame: pd.DataFrame,
    *,
    strategy_name: str,
    profile: Mapping[str, Any],
    config: Mapping[str, Any],
    warmup_bars: int,
) -> dict[str, Any]:
    stats = run_plugin_replay(
        frame,
        strategy_config=dict(profile),
        engine_config=dict(config),
        symbol=ENGINE_SYMBOL,
        strategy_name=strategy_name,
        initial_balance=1000.0,
        primary_timeframe="4h",
        regime_timeframe=None,
        min_bars_override=warmup_bars,
        include_trace=True,
    )
    trace = stats.get("trace") or {}
    lengths = {
        len(trace.get("timestamps") or []),
        len(trace.get("equity_curve") or []),
        len(trace.get("position_side_curve") or []),
    }
    if len(lengths) != 1 or not next(iter(lengths), 0):
        raise ValueError(f"{strategy_name} replay trace is missing or misaligned")
    return stats


def _monthly_returns(
    timestamps: Sequence[int], equity: Sequence[float], initial: float
) -> dict[str, float]:
    index = pd.to_datetime(list(timestamps), unit="s", utc=True)
    series = pd.Series([float(value) for value in equity], index=index)
    month_labels = series.index.tz_localize(None).to_period("M")
    month_end = series.groupby(month_labels).last()
    out: dict[str, float] = {}
    previous = float(initial)
    for period, value in month_end.items():
        current = float(value)
        out[str(period)] = (current / previous - 1.0) if previous > 0 else 0.0
        previous = current
    return out


def combine_arms(
    champion: Mapping[str, Any],
    donchian: Mapping[str, Any],
    *,
    champion_weight: float,
) -> dict[str, Any]:
    donchian_weight = 1.0 - float(champion_weight)
    if not 0.0 < champion_weight < 1.0:
        raise ValueError("portfolio weights must be inside (0, 1)")
    left_trace = champion.get("trace") or {}
    right_trace = donchian.get("trace") or {}
    timestamps = [int(value) for value in left_trace.get("timestamps") or []]
    if timestamps != [int(value) for value in right_trace.get("timestamps") or []]:
        raise ValueError("portfolio members do not share one continuous clock")
    left_equity = [float(value) for value in left_trace.get("equity_curve") or []]
    right_equity = [float(value) for value in right_trace.get("equity_curve") or []]
    if len(left_equity) != len(right_equity) or not left_equity:
        raise ValueError("portfolio member equity curves are not aligned")
    equity = [
        champion_weight * left + donchian_weight * right
        for left, right in zip(left_equity, right_equity)
    ]
    returns = [
        (current / previous - 1.0)
        for previous, current in zip(equity, equity[1:])
        if previous > 0
    ]
    peak = 1000.0
    max_drawdown = 0.0
    for value in equity:
        peak = max(peak, value)
        if peak > 0:
            max_drawdown = max(max_drawdown, (peak - value) / peak * 100.0)
    weighted_trades: list[float] = []
    wins = 0
    losses = 0
    for stats, weight in (
        (champion, champion_weight),
        (donchian, donchian_weight),
    ):
        for trade in stats.get("trades") or []:
            pnl = float(trade.get("pnl") or 0.0) * weight
            weighted_trades.append(pnl)
            if pnl > 0:
                wins += 1
            else:
                losses += 1
    gross_profit = sum(value for value in weighted_trades if value > 0)
    gross_loss = abs(sum(value for value in weighted_trades if value < 0))
    profit_factor = (
        gross_profit / gross_loss if gross_loss > 0 else 1000.0 if gross_profit else 0.0
    )
    left_sides = list(left_trace.get("position_side_curve") or [])
    right_sides = list(right_trace.get("position_side_curve") or [])
    conflict_bars = sum(
        left is not None and right is not None and left != right
        for left, right in zip(left_sides, right_sides)
    )
    exposed_bars = sum(
        left is not None or right is not None
        for left, right in zip(left_sides, right_sides)
    )
    monthly = _monthly_returns(timestamps, equity, 1000.0)
    years = len(equity) / PERIODS_PER_YEAR_4H
    final = float(equity[-1])
    cagr = (
        ((final / 1000.0) ** (1.0 / years) - 1.0) * 100.0
        if years > 0 and final > 0
        else 0.0
    )
    return {
        "weights": {
            "champion": round(champion_weight, 6),
            "donchian": round(donchian_weight, 6),
        },
        "initial_balance": 1000.0,
        "final_balance": round(final, 8),
        "net_profit_pct": round((final / 1000.0 - 1.0) * 100.0, 6),
        "profit_factor": round(min(1000.0, profit_factor), 6),
        "max_drawdown_pct": round(max_drawdown, 6),
        "sharpe": round(
            risk.sharpe(returns, periods_per_year=PERIODS_PER_YEAR_4H)
            if len(returns) > 1
            else 0.0,
            6,
        ),
        "cagr_pct": round(cagr, 6),
        "total_trades": len(weighted_trades),
        "wins": wins,
        "losses": losses,
        "win_rate": round(wins / len(weighted_trades) * 100.0, 6)
        if weighted_trades
        else 0.0,
        "gross_profit": round(gross_profit, 8),
        "gross_loss": round(gross_loss, 8),
        "positive_months": sum(value > 0 for value in monthly.values()),
        "months": len(monthly),
        "monthly_returns": {key: round(value, 10) for key, value in monthly.items()},
        "exposure_pct": round(exposed_bars / len(equity) * 100.0, 6),
        "conflict_bars": conflict_bars,
        "conflict_rate_pct": round(conflict_bars / len(equity) * 100.0, 6),
    }


def _arm_summary(stats: Mapping[str, Any]) -> dict[str, Any]:
    keys = (
        "net_profit_pct",
        "profit_factor",
        "max_drawdown_pct",
        "sharpe",
        "total_trades",
        "wins",
        "losses",
        "win_rate",
        "gross_profit",
        "gross_loss",
        "initial_balance",
        "final_balance",
        "exposure_pct",
    )
    trace = stats.get("trace") or {}
    monthly = _monthly_returns(
        trace.get("timestamps") or [], trace.get("equity_curve") or [], 1000.0
    )
    result = {key: stats.get(key) for key in keys}
    result.update(
        {
            "positive_months": sum(value > 0 for value in monthly.values()),
            "months": len(monthly),
            "monthly_returns": {
                key: round(value, 10) for key, value in monthly.items()
            },
        }
    )
    return result


def _member_monthly_correlation(
    champion: Mapping[str, Any], donchian: Mapping[str, Any]
) -> float:
    left = _arm_summary(champion)["monthly_returns"]
    right = _arm_summary(donchian)["monthly_returns"]
    labels = sorted(set(left) & set(right))
    if len(labels) < 2:
        return 1.0
    value = pd.Series([left[label] for label in labels]).corr(
        pd.Series([right[label] for label in labels])
    )
    return round(float(value) if pd.notna(value) else 1.0, 6)


def _fold_slices(frame: pd.DataFrame, folds: int, warmup: int):
    traded = len(frame) - warmup
    if traded < folds:
        raise ValueError("not enough bars for locked chronological folds")
    width = traded // folds
    for index in range(folds):
        trade_start = warmup + index * width
        trade_end = len(frame) if index == folds - 1 else warmup + (index + 1) * width
        slice_start = max(0, trade_start - warmup)
        sliced = frame.iloc[slice_start:trade_end].reset_index(drop=True)
        yield index + 1, sliced, trade_start - slice_start


def _window_slice(
    frame: pd.DataFrame, *, start: str, end: str, warmup: int
) -> tuple[pd.DataFrame, int]:
    start_ms = int(pd.Timestamp(start).timestamp() * 1000)
    end_ms = int(pd.Timestamp(end).timestamp() * 1000)
    matches = frame.index[
        (frame["open_time"].astype("int64") >= start_ms)
        & (frame["open_time"].astype("int64") <= end_ms)
    ].tolist()
    if not matches:
        raise ValueError("locked recent window is absent from native data")
    trade_start = int(matches[0])
    trade_end = int(matches[-1]) + 1
    slice_start = max(0, trade_start - warmup)
    return frame.iloc[slice_start:trade_end].reset_index(drop=True), trade_start - slice_start


def evaluate_gate(
    champion: Mapping[str, Any],
    donchian: Mapping[str, Any],
    ensemble: Mapping[str, Any],
    folds: Sequence[Mapping[str, Any]],
    recent: Mapping[str, Any],
    sensitivity: Mapping[str, Mapping[str, Any]],
    *,
    member_correlation: float,
    history_days: int,
    manifest: Mapping[str, Any],
) -> dict[str, Any]:
    gates = manifest.get("gates") or {}
    profitable_folds = sum(
        _number(row["ensemble"].get("net_profit_pct")) > 0 for row in folds
    )
    mdd_folds = sum(
        _number(row["ensemble"].get("max_drawdown_pct"))
        <= _number(row["champion"].get("max_drawdown_pct"))
        for row in folds
    )
    net_ratio = _ratio(ensemble.get("net_profit_pct"), champion.get("net_profit_pct"))
    pf_ratio = _ratio(ensemble.get("profit_factor"), champion.get("profit_factor"))
    mdd_ratio = _ratio(
        ensemble.get("max_drawdown_pct"), champion.get("max_drawdown_pct")
    )
    sharpe_edge = _number(ensemble.get("sharpe")) - _number(champion.get("sharpe"))
    positive_month_edge = int(ensemble.get("positive_months") or 0) - int(
        champion.get("positive_months") or 0
    )
    sensitivity_pass = all(
        _number(row.get("net_profit_pct"))
        > float(gates["sensitivity_net_profit_pct_min_exclusive"])
        and _ratio(
            row.get("max_drawdown_pct"), champion.get("max_drawdown_pct")
        )
        <= float(gates["sensitivity_max_drawdown_ratio_max"])
        for row in sensitivity.values()
    )
    parity = manifest.get("donchian_4h_exploratory_parity") or {}
    parity_baseline = parity.get("baseline") or {}
    parity_tolerance = parity.get("absolute_tolerance") or {}
    parity_pass = bool(parity_baseline) and all(
        abs(_number(donchian.get(key)) - _number(expected))
        <= _number(parity_tolerance.get(key))
        for key, expected in parity_baseline.items()
    )
    checks = {
        "native_history_sufficient": history_days
        >= int((manifest.get("data_source") or {})["minimum_history_days"]),
        "full_net_uplift": net_ratio
        >= float(gates["ensemble_vs_champion_net_profit_ratio_min"]),
        "full_profit_factor_noninferiority": pf_ratio
        >= float(gates["ensemble_vs_champion_profit_factor_ratio_min"]),
        "full_drawdown_reduction": mdd_ratio
        <= float(gates["ensemble_vs_champion_max_drawdown_ratio_max"]),
        "full_sharpe_edge": sharpe_edge
        >= float(gates["ensemble_vs_champion_sharpe_edge_min"]),
        "positive_month_edge": positive_month_edge
        >= int(gates["ensemble_vs_champion_positive_months_min_additional"]),
        "profitable_folds": profitable_folds >= int(gates["profitable_folds_min"]),
        "drawdown_noninferior_folds": mdd_folds
        >= int(gates["drawdown_noninferior_folds_min"]),
        "recent_net_positive": _number(recent.get("net_profit_pct"))
        > float(gates["recent_net_profit_pct_min_exclusive"]),
        "recent_profit_factor": _number(recent.get("profit_factor"))
        >= float(gates["recent_profit_factor_min"]),
        "member_monthly_correlation": member_correlation
        <= float(gates["member_monthly_return_correlation_max"]),
        "weight_sensitivity": sensitivity_pass,
        "donchian_4h_exploratory_parity": parity_pass,
    }
    return {
        "passed": all(checks.values()),
        "checks": checks,
        "observed": {
            "history_days": history_days,
            "ensemble_vs_champion_net_profit_ratio": round(net_ratio, 6),
            "ensemble_vs_champion_profit_factor_ratio": round(pf_ratio, 6),
            "ensemble_vs_champion_max_drawdown_ratio": round(mdd_ratio, 6),
            "ensemble_vs_champion_sharpe_edge": round(sharpe_edge, 6),
            "ensemble_vs_champion_positive_months_additional": positive_month_edge,
            "profitable_folds": profitable_folds,
            "drawdown_noninferior_folds": mdd_folds,
            "recent_net_profit_pct": round(_number(recent.get("net_profit_pct")), 6),
            "recent_profit_factor": round(_number(recent.get("profit_factor")), 6),
            "member_monthly_return_correlation": member_correlation,
            "weight_sensitivity_passed": sensitivity_pass,
            "donchian_4h_parity_deltas": {
                key: round(_number(donchian.get(key)) - _number(expected), 8)
                for key, expected in parity_baseline.items()
            },
            "virtual_sleeve_conflict_bars": int(ensemble.get("conflict_bars") or 0),
            "virtual_sleeve_conflict_rate_pct": _number(
                ensemble.get("conflict_rate_pct")
            ),
        },
        "thresholds": dict(gates),
    }


def _report(payload: Mapping[str, Any]) -> str:
    champion = payload["full_history"]["champion"]
    ensemble = payload["full_history"]["ensemble"]
    gate = payload["gate"]
    lines = [
        "# BTC SuperTrend + Donchian 50/50 locked shadow run",
        "",
        f"Verdict: **{'PASS — forward shadow eligible' if gate['passed'] else 'REJECT'}**",
        "",
        "| book | net | PF | MDD | Sharpe | +months | trades |",
        "|---|---:|---:|---:|---:|---:|---:|",
        f"| Champion | {float(champion['net_profit_pct']):+.2f}% | {float(champion['profit_factor']):.3f} | {float(champion['max_drawdown_pct']):.2f}% | {float(champion['sharpe']):.3f} | {int(champion['positive_months'])}/{int(champion['months'])} | {int(champion['total_trades'])} |",
        f"| 50/50 ensemble | {float(ensemble['net_profit_pct']):+.2f}% | {float(ensemble['profit_factor']):.3f} | {float(ensemble['max_drawdown_pct']):.2f}% | {float(ensemble['sharpe']):.3f} | {int(ensemble['positive_months'])}/{int(ensemble['months'])} | {int(ensemble['total_trades'])} |",
        "",
        "## Pre-registered gates",
        "",
    ]
    lines.extend(
        f"- {'PASS' if passed else 'FAIL'} `{name}`"
        for name, passed in gate["checks"].items()
    )
    lines.extend(
        [
            "",
            f"Virtual sleeve conflict: {int(ensemble['conflict_bars'])} bars ({float(ensemble['conflict_rate_pct']):.2f}%). This is diagnostic: the record is shadow-only and is not executable in the current one-way live account.",
            "",
            str(payload["protocol"]["selection_disclosure"]),
            "",
            "Passing proposes an ensemble certificate for forward shadow only. It does not approve live trading, account netting, allocation changes, or tenant configuration changes.",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="bot_config.yaml")
    parser.add_argument("--protocol", default=str(DEFAULT_PROTOCOL))
    parser.add_argument("--out-dir", default="core/btc_supertrend_donchian_ensemble")
    parser.add_argument("--fail-on-reject", action="store_true")
    parser.add_argument("--require-workflow-provenance", action="store_true")
    args = parser.parse_args()

    os.chdir(ROOT)
    manifest_path = Path(args.protocol).resolve()
    manifest_bytes = manifest_path.read_bytes()
    manifest = json.loads(manifest_bytes)
    manifest_sha256 = hashlib.sha256(manifest_bytes).hexdigest()
    champion = _preset(str(manifest["champion_preset_id"]))
    donchian = _research_member(manifest)
    config = load_bot_config(args.config)
    economics = _economics(config)
    require_locked_identity(manifest, champion, donchian, economics)
    provenance = workflow_provenance(required=args.require_workflow_provenance)

    native = _clip_native(
        _okx_frame(ENGINE_SYMBOL, "4h"), manifest.get("data_source") or {}
    )
    warmup = int((manifest.get("sampling") or {})["past_only_warmup_bars"])
    champion_profile = dict(champion.get("execution_profile") or {})
    donchian_profile = dict(donchian.get("execution_profile") or {})

    champion_run = _run_arm(
        native,
        strategy_name=str(champion["strategy"]),
        profile=champion_profile,
        config=config,
        warmup_bars=warmup,
    )
    donchian_run = _run_arm(
        native,
        strategy_name=str(donchian["strategy"]),
        profile=donchian_profile,
        config=config,
        warmup_bars=warmup,
    )
    champion_full = _arm_summary(champion_run)
    donchian_full = _arm_summary(donchian_run)
    ensemble_full = combine_arms(champion_run, donchian_run, champion_weight=0.5)
    member_correlation = _member_monthly_correlation(champion_run, donchian_run)

    fold_rows: list[dict[str, Any]] = []
    fold_count = int((manifest.get("sampling") or {})["chronological_folds"])
    for fold, frame, skip in _fold_slices(native, fold_count, warmup):
        left = _run_arm(
            frame,
            strategy_name=str(champion["strategy"]),
            profile=champion_profile,
            config=config,
            warmup_bars=skip,
        )
        right = _run_arm(
            frame,
            strategy_name=str(donchian["strategy"]),
            profile=donchian_profile,
            config=config,
            warmup_bars=skip,
        )
        fold_rows.append(
            {
                "fold": fold,
                "window_start": str(
                    pd.to_datetime(int(frame["open_time"].iloc[skip]), unit="ms", utc=True)
                ),
                "window_end": str(
                    pd.to_datetime(int(frame["open_time"].iloc[-1]), unit="ms", utc=True)
                ),
                "champion": _arm_summary(left),
                "donchian": _arm_summary(right),
                "ensemble": combine_arms(left, right, champion_weight=0.5),
            }
        )

    recent_spec = (manifest.get("sampling") or {})["recent_complete_months"]
    recent_frame, recent_skip = _window_slice(
        native,
        start=str(recent_spec["window_start"]),
        end=str(recent_spec["window_end"]),
        warmup=warmup,
    )
    recent_champion = _run_arm(
        recent_frame,
        strategy_name=str(champion["strategy"]),
        profile=champion_profile,
        config=config,
        warmup_bars=recent_skip,
    )
    recent_donchian = _run_arm(
        recent_frame,
        strategy_name=str(donchian["strategy"]),
        profile=donchian_profile,
        config=config,
        warmup_bars=recent_skip,
    )
    recent = combine_arms(recent_champion, recent_donchian, champion_weight=0.5)

    sensitivity = {
        "40_60": combine_arms(champion_run, donchian_run, champion_weight=0.4),
        "60_40": combine_arms(champion_run, donchian_run, champion_weight=0.6),
    }
    first = pd.to_datetime(int(native["open_time"].iloc[0]), unit="ms", utc=True)
    last = pd.to_datetime(int(native["open_time"].iloc[-1]), unit="ms", utc=True)
    history_days = int((last - first).total_seconds() // 86_400)
    gate = evaluate_gate(
        champion_full,
        donchian_full,
        ensemble_full,
        fold_rows,
        recent,
        sensitivity,
        member_correlation=member_correlation,
        history_days=history_days,
        manifest=manifest,
    )
    verdict = "certified" if gate["passed"] else "failed"
    measured_at = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()
    record = {
        "record_version": 1,
        "record_kind": "ensemble",
        "ensemble_id": manifest["ensemble_id"],
        "ensemble_fingerprint": manifest["ensemble_fingerprint"],
        "measured_at": measured_at,
        "protocol": {
            "name": PROTOCOL_NAME,
            "version": PROTOCOL_VERSION,
            "script": "scripts/certify_btc_ensemble.py",
            "manifest": str(manifest_path.relative_to(ROOT)),
            "manifest_sha256": manifest_sha256,
            "workflow_provenance": provenance,
        },
        "data_source": {
            **dict(manifest.get("data_source") or {}),
            "bars": len(native),
            "observed_start": str(first),
            "observed_end": str(last),
            "ohlcv_sha256": frame_sha256(native),
        },
        "members": [
            {
                "preset_id": member["preset_id"],
                "config_fingerprint": member["config_fingerprint"],
                "weight": member["weight"],
                "role": member["role"],
            }
            for member in manifest["members"]
        ],
        "fill_model": manifest["fill_model"],
        "execution_model": dict(manifest["execution_model"]),
        "gate": gate,
        "verdict": verdict,
        "shadow_only": True,
        "live_certified": False,
        "note": (
            "Clears the locked 50/50 ensemble acceptance protocol; eligible for forward shadow only."
            if gate["passed"]
            else "Fails one or more locked 50/50 ensemble acceptance gates; not eligible for forward shadow."
        ),
        "document": "docs/research/btc_supertrend_donchian_ensemble_2026-08.md",
        "evidence": {
            "status": "validated",
            "score_label": f"PF {float(ensemble_full['profit_factor']):.2f}",
            "period": f"{first:%b %Y} – {last:%b %Y}",
            "duration": f"{history_days / 365.25:.1f} years",
            "max_drawdown_pct": round(float(ensemble_full["max_drawdown_pct"]), 1),
            "trades": int(ensemble_full["total_trades"]),
            "source": f"OKX {VENUE_SYMBOL} · continuous 50/50 virtual sleeves · measured {measured_at}",
        },
    }
    payload = {
        "protocol": {**manifest, "manifest_sha256": manifest_sha256},
        "measured_at": measured_at,
        "execution_model": economics,
        "full_history": {
            "champion": champion_full,
            "donchian": donchian_full,
            "ensemble": ensemble_full,
            "member_monthly_return_correlation": member_correlation,
        },
        "folds": fold_rows,
        "recent_complete_months": recent,
        "weight_sensitivity": sensitivity,
        "gate": gate,
        "proposed_certificate": record,
    }

    out_dir = Path(args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "results.json").write_text(
        json.dumps(payload, indent=2, default=str, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    (out_dir / "proposed_certificate.json").write_text(
        json.dumps(record, indent=2, default=str, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    (out_dir / "report.md").write_text(_report(payload), encoding="utf-8")
    print(
        f"{verdict.upper()} {manifest['ensemble_id']} | "
        f"net={float(ensemble_full['net_profit_pct']):+.2f}% "
        f"PF={float(ensemble_full['profit_factor']):.3f} "
        f"MDD={float(ensemble_full['max_drawdown_pct']):.2f}% "
        f"Sharpe={float(ensemble_full['sharpe']):.3f}"
    )
    print(f"wrote {out_dir}")
    return 2 if args.fail_on_reject and not gate["passed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
