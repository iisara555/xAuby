#!/usr/bin/env python3
"""Run the locked BTC SMC structure-alpha protocol on native OKX data.

This is a two-arm side-policy test, not a parameter search. The primary
long-only candidate, the one-key long+short comparator, Champion, economics,
folds, and gates are frozen in
``docs/research/protocols/btc_smc_structure_challenger_v1.json``. Outputs are
research artifacts only; this script never writes to the certificate catalog,
tenant configuration, or runtime state.
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
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pandas as pd

from scripts.validate_on_venue_data import _okx_frame
from xauby.backtest.replay import run_plugin_replay
from xauby.observability.replay_validation import load_bot_config
from xauby.saas.certification import RECORD_VERSION, config_fingerprint
from xauby.saas.preset_specs import PRESET_SPECS

DEFAULT_PROTOCOL = (
    ROOT / "docs/research/protocols/btc_smc_structure_challenger_v1.json"
)
PROTOCOL_NAME = "btc-smc-structure-challenger"
PROTOCOL_VERSION = "3"
SYMBOL = "BTCUSDT"
VENUE = "BTC-USDT-SWAP"
METRIC_KEYS = (
    "net_profit_pct",
    "win_rate",
    "profit_factor",
    "max_drawdown_pct",
    "total_trades",
    "sharpe",
    "sortino",
    "calmar",
    "cagr_pct",
    "exposure_pct",
    "avg_holding_bars",
    "max_consecutive_losses",
)


def _preset(preset_id: str) -> dict[str, Any]:
    for spec in PRESET_SPECS:
        if str(spec.get("id")) == preset_id:
            return dict(spec)
    raise ValueError(f"unknown preset {preset_id!r}")


def _number(value: Any) -> float:
    result = float(value or 0.0)
    if not math.isfinite(result):
        raise ValueError("replay produced a non-finite metric")
    return result


def _positive_ratio(left: Any, right: Any) -> float | None:
    denominator = _number(right)
    if denominator <= 0.0:
        return None
    return _number(left) / denominator


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


def _side_profile(
    manifest: Mapping[str, Any], candidate: Mapping[str, Any]
) -> dict[str, Any]:
    comparator = manifest.get("side_policy_comparator") or {}
    if comparator.get("base_candidate_preset_id") != candidate.get("id"):
        raise ValueError("side-policy comparator is not based on the locked candidate")
    allowed = [str(key) for key in comparator.get("allowed_profile_changes") or []]
    override = dict(comparator.get("execution_profile_override") or {})
    if sorted(override) != sorted(allowed) or allowed != ["allow_short"]:
        raise ValueError("SMC side-policy arm may change only allow_short")
    primary = dict(candidate.get("execution_profile") or {})
    if primary.get("allow_short") is not False or override.get("allow_short") is not True:
        raise ValueError("locked arms must be long-only false versus long+short true")
    return {**primary, **override}


def _require_locked_identity(
    manifest: Mapping[str, Any],
    candidate: Mapping[str, Any],
    champion: Mapping[str, Any],
    economics: Mapping[str, Any],
) -> dict[str, Any]:
    expected = {
        str(manifest["candidate_preset_id"]): str(
            manifest["candidate_config_fingerprint"]
        ),
        str(manifest["champion_preset_id"]): str(
            manifest["champion_config_fingerprint"]
        ),
    }
    for preset in (candidate, champion):
        preset_id = str(preset["id"])
        actual = config_fingerprint(preset)
        if expected.get(preset_id) != actual:
            raise ValueError(
                f"{preset_id} fingerprint is {actual}, locked protocol expects "
                f"{expected.get(preset_id)}"
            )
    if str(candidate.get("strategy")) != "xauby_smc_pro":
        raise ValueError("locked SMC candidate changed strategy")
    if list(candidate.get("allowed_sides") or []) != ["long"]:
        raise ValueError("locked SMC candidate must remain long-only")
    if str(champion.get("strategy")) != "supertrend_ema200":
        raise ValueError("locked Champion changed strategy")
    locked_economics = manifest.get("execution_model") or {}
    for key, expected_value in locked_economics.items():
        actual = economics.get(key)
        if actual is None or abs(float(actual) - float(expected_value)) > 1e-12:
            raise ValueError(
                f"execution model changed for {key}: {actual!r}, "
                f"locked protocol expects {expected_value!r}"
            )
    return _side_profile(manifest, candidate)


def _fold_correlation(
    candidate_folds: list[Mapping[str, Any]],
    champion_folds: list[Mapping[str, Any]],
) -> float:
    left = pd.Series(
        [_number(row.get("net_profit_pct")) for row in candidate_folds],
        dtype="float64",
    )
    right = pd.Series(
        [_number(row.get("net_profit_pct")) for row in champion_folds],
        dtype="float64",
    )
    value = float(left.corr(right)) if len(left) >= 3 and len(left) == len(right) else math.nan
    # A constant or malformed series contains no evidence of differentiated
    # alpha. Fail closed by treating it as perfectly correlated.
    return value if math.isfinite(value) else 1.0


def _behavior_differs(
    long_only: Mapping[str, Any],
    long_short: Mapping[str, Any],
    long_only_folds: list[Mapping[str, Any]],
    long_short_folds: list[Mapping[str, Any]],
) -> bool:
    keys = ("total_trades", "net_profit_pct", "profit_factor", "max_drawdown_pct")
    rows = [(long_only, long_short), *zip(long_only_folds, long_short_folds)]
    return any(
        abs(_number(left.get(key)) - _number(right.get(key))) > 1e-9
        for left, right in rows
        for key in keys
    )


def evaluate_gate(
    candidate: Mapping[str, Any],
    champion: Mapping[str, Any],
    side_comparator: Mapping[str, Any],
    candidate_folds: list[Mapping[str, Any]],
    champion_folds: list[Mapping[str, Any]],
    side_comparator_folds: list[Mapping[str, Any]],
    *,
    history_days: int,
    manifest: Mapping[str, Any],
) -> dict[str, Any]:
    """Apply the absolute, robustness, orthogonality, and side-policy gates."""
    gates = manifest.get("gates") or {}
    source = manifest.get("data_source") or {}
    profitable_folds = sum(
        _number(row.get("net_profit_pct")) > 0.0 for row in candidate_folds
    )
    pf_one_folds = sum(
        _number(row.get("profit_factor")) >= 1.0 for row in candidate_folds
    )
    complementary_folds = sum(
        _number(left.get("net_profit_pct")) > 0.0
        and _number(right.get("net_profit_pct")) <= 0.0
        for left, right in zip(candidate_folds, champion_folds)
    )
    correlation = _fold_correlation(candidate_folds, champion_folds)
    pf_ratio = _positive_ratio(
        candidate.get("profit_factor"), champion.get("profit_factor")
    )
    drawdown_ratio = _positive_ratio(
        candidate.get("max_drawdown_pct"), champion.get("max_drawdown_pct")
    )
    side_pf_ratio = _positive_ratio(
        candidate.get("profit_factor"), side_comparator.get("profit_factor")
    )
    side_drawdown_ratio = _positive_ratio(
        candidate.get("max_drawdown_pct"), side_comparator.get("max_drawdown_pct")
    )
    behavior_differs = _behavior_differs(
        candidate,
        side_comparator,
        candidate_folds,
        side_comparator_folds,
    )
    latest_net = (
        _number(candidate_folds[-1].get("net_profit_pct"))
        if candidate_folds
        else float("-inf")
    )
    checks = {
        "native_history_sufficient": history_days >= int(source["minimum_history_days"]),
        "candidate_full_profit_factor": _number(candidate.get("profit_factor"))
        >= float(gates["candidate_full_profit_factor_min"]),
        "candidate_full_net_positive": _number(candidate.get("net_profit_pct"))
        > float(gates["candidate_full_net_profit_pct_min_exclusive"]),
        "candidate_full_drawdown": _number(candidate.get("max_drawdown_pct"))
        <= float(gates["candidate_full_max_drawdown_pct_max"]),
        "candidate_full_trades": int(candidate.get("total_trades") or 0)
        >= int(gates["candidate_full_trades_min"]),
        "profitable_folds": profitable_folds >= int(gates["profitable_folds_min"]),
        "folds_with_pf_at_least_one": pf_one_folds
        >= int(gates["folds_with_profit_factor_at_least_one_min"]),
        "latest_fold_net_positive": latest_net
        > float(gates["latest_fold_net_profit_pct_min_exclusive"]),
        "full_profit_factor_floor_vs_champion": pf_ratio is not None
        and pf_ratio >= float(
            gates["candidate_vs_champion_full_profit_factor_ratio_min"]
        ),
        "full_drawdown_noninferiority": drawdown_ratio is not None
        and drawdown_ratio <= float(
            gates["candidate_vs_champion_full_max_drawdown_ratio_max"]
        ),
        "fold_net_correlation": correlation
        <= float(gates["candidate_vs_champion_fold_net_correlation_max"]),
        "complementary_profitable_folds": complementary_folds
        >= int(gates["candidate_positive_when_champion_nonpositive_folds_min"]),
        "long_only_profit_factor_vs_long_short": side_pf_ratio is not None
        and side_pf_ratio
        >= float(gates["long_only_vs_long_short_full_profit_factor_ratio_min"]),
        "long_only_drawdown_vs_long_short": side_drawdown_ratio is not None
        and side_drawdown_ratio
        <= float(gates["long_only_vs_long_short_full_max_drawdown_ratio_max"]),
        "long_short_behavior_observed": behavior_differs
        == bool(gates["long_short_behavior_must_differ"]),
    }
    return {
        "passed": all(checks.values()),
        "checks": checks,
        "observed": {
            "history_days": history_days,
            "profitable_folds": profitable_folds,
            "folds_with_profit_factor_at_least_one": pf_one_folds,
            "latest_fold_net_profit_pct": round(latest_net, 6),
            "candidate_positive_when_champion_nonpositive_folds": complementary_folds,
            "candidate_vs_champion_fold_net_correlation": round(correlation, 6),
            "candidate_vs_champion_full_profit_factor_ratio": (
                round(pf_ratio, 6) if pf_ratio is not None else None
            ),
            "candidate_vs_champion_full_max_drawdown_ratio": (
                round(drawdown_ratio, 6) if drawdown_ratio is not None else None
            ),
            "long_only_vs_long_short_full_profit_factor_ratio": (
                round(side_pf_ratio, 6) if side_pf_ratio is not None else None
            ),
            "long_only_vs_long_short_full_max_drawdown_ratio": (
                round(side_drawdown_ratio, 6)
                if side_drawdown_ratio is not None
                else None
            ),
            "long_short_behavior_observed": behavior_differs,
        },
        "thresholds": dict(gates),
    }


def _period(frame: pd.DataFrame) -> tuple[str, str, int]:
    first = pd.to_datetime(int(frame["open_time"].iloc[0]), unit="ms", utc=True)
    last = pd.to_datetime(int(frame["open_time"].iloc[-1]), unit="ms", utc=True)
    days = int((last - first).total_seconds() // 86_400)
    return f"{first:%b %Y} – {last:%b %Y}", f"{days / 365.25:.1f} years", days


def _fold_slices(
    frame: pd.DataFrame, *, folds: int, warmup_bars: int
) -> list[tuple[pd.DataFrame, int]]:
    segment = len(frame) // folds
    if folds < 2 or segment <= 0:
        raise ValueError("locked fold count cannot be applied to this data")
    out: list[tuple[pd.DataFrame, int]] = []
    for fold in range(folds):
        traded_start = fold * segment
        start = max(0, traded_start - warmup_bars)
        end = len(frame) if fold == folds - 1 else (fold + 1) * segment
        out.append((frame.iloc[start:end].reset_index(drop=True), traded_start - start))
    return out


def _run_profile(
    preset: Mapping[str, Any],
    profile: Mapping[str, Any],
    frame: pd.DataFrame,
    config: Mapping[str, Any],
    economics: Mapping[str, Any],
    *,
    skip_bars: int,
) -> dict[str, Any]:
    stats = run_plugin_replay(
        frame,
        strategy_config=dict(profile),
        engine_config=dict(config),
        symbol=SYMBOL,
        strategy_name=str(preset["strategy"]),
        initial_balance=float(economics["initial_balance"]),
        primary_timeframe=str(preset["primary_timeframe"]),
        min_bars_override=skip_bars,
    )
    return {key: stats.get(key) for key in METRIC_KEYS}


def _run_arm(
    preset: Mapping[str, Any],
    profile: Mapping[str, Any],
    native: pd.DataFrame,
    config: Mapping[str, Any],
    economics: Mapping[str, Any],
    *,
    folds: int,
    warmup_bars: int,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    full = _run_profile(
        preset, profile, native, config, economics, skip_bars=0
    )
    fold_rows: list[dict[str, Any]] = []
    for index, (frame, skip) in enumerate(
        _fold_slices(native, folds=folds, warmup_bars=warmup_bars), start=1
    ):
        metrics = _run_profile(
            preset, profile, frame, config, economics, skip_bars=skip
        )
        metrics["fold"] = index
        metrics["window_start"] = str(
            pd.to_datetime(int(frame["open_time"].iloc[skip]), unit="ms", utc=True)
        )
        metrics["window_end"] = str(
            pd.to_datetime(int(frame["open_time"].iloc[-1]), unit="ms", utc=True)
        )
        fold_rows.append(metrics)
    return full, fold_rows


def _metric_row(label: str, metrics: Mapping[str, Any]) -> str:
    return (
        f"| {label} | {float(metrics['profit_factor']):.3f} | "
        f"{float(metrics['net_profit_pct']):+.2f} | "
        f"{float(metrics['max_drawdown_pct']):.2f} | "
        f"{int(metrics['total_trades'])} |"
    )


def _report(payload: Mapping[str, Any]) -> str:
    full = payload["full_history"]
    gate = payload["gate"]
    lines = [
        "# BTC SMC structure-alpha locked run",
        "",
        f"Verdict: **{'PASS — forward shadow eligible' if gate['passed'] else 'REJECT'}**",
        "",
        "| arm | PF | net% | MDD% | trades |",
        "|---|---:|---:|---:|---:|",
        _metric_row("Champion", full["champion"]),
        _metric_row("SMC long-only", full["candidate_long_only"]),
        _metric_row("SMC long+short research arm", full["side_comparator"]),
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
            payload["protocol"]["selection_disclosure"],
            "",
            "A pass proposes only a certificate for the frozen long-only candidate. Live approval remains false, and Strategy Arena forward-shadow gates still apply.",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="bot_config.yaml")
    parser.add_argument("--protocol", default=str(DEFAULT_PROTOCOL))
    parser.add_argument("--out-dir", default="core/btc_smc_structure_challenger")
    parser.add_argument("--fail-on-reject", action="store_true")
    args = parser.parse_args()

    os.chdir(ROOT)
    manifest_path = Path(args.protocol).resolve()
    manifest_bytes = manifest_path.read_bytes()
    manifest = json.loads(manifest_bytes)
    manifest_sha256 = hashlib.sha256(manifest_bytes).hexdigest()
    candidate = _preset(str(manifest["candidate_preset_id"]))
    champion = _preset(str(manifest["champion_preset_id"]))
    config = load_bot_config(args.config)
    economics = _economics(config)
    side_profile = _require_locked_identity(
        manifest, candidate, champion, economics
    )

    native = _okx_frame(SYMBOL, "4h")
    sampling = manifest.get("sampling") or {}
    folds = int(sampling["chronological_folds"])
    warmup = int(sampling["past_only_warmup_bars"])
    candidate_profile = dict(candidate.get("execution_profile") or {})
    champion_profile = dict(champion.get("execution_profile") or {})
    champion_full, champion_folds = _run_arm(
        champion,
        champion_profile,
        native,
        config,
        economics,
        folds=folds,
        warmup_bars=warmup,
    )
    candidate_full, candidate_folds = _run_arm(
        candidate,
        candidate_profile,
        native,
        config,
        economics,
        folds=folds,
        warmup_bars=warmup,
    )
    side_full, side_folds = _run_arm(
        candidate,
        side_profile,
        native,
        config,
        economics,
        folds=folds,
        warmup_bars=warmup,
    )
    period, duration, history_days = _period(native)
    gate = evaluate_gate(
        candidate_full,
        champion_full,
        side_full,
        candidate_folds,
        champion_folds,
        side_folds,
        history_days=history_days,
        manifest=manifest,
    )
    verdict = "certified" if gate["passed"] else "failed"
    measured_at = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()
    record = {
        "record_version": RECORD_VERSION,
        "preset_id": candidate["id"],
        "config_fingerprint": config_fingerprint(candidate),
        "measured_at": measured_at,
        "protocol": {
            "name": PROTOCOL_NAME,
            "version": PROTOCOL_VERSION,
            "script": "scripts/certify_btc_smc_challenger.py",
            "manifest": str(manifest_path.relative_to(ROOT)),
            "manifest_sha256": manifest_sha256,
        },
        "data_source": {
            "venue": "okx",
            "symbol": VENUE,
            "timeframe": "4h",
            "native": True,
        },
        "gate": gate,
        "verdict": verdict,
        "note": (
            "Clears the locked SMC structure-alpha protocol; eligible for forward shadow only."
            if gate["passed"]
            else "Fails one or more locked SMC gates; not eligible for Strategy Arena shadow."
        ),
        "document": "docs/research/btc_smc_structure_candidate_2026-08.md",
        "evidence": {
            "status": "validated",
            "score_label": f"PF {float(candidate_full['profit_factor']):.2f}",
            "period": period,
            "duration": duration,
            "win_rate_pct": round(float(candidate_full["win_rate"]), 1),
            "max_drawdown_pct": round(float(candidate_full["max_drawdown_pct"]), 1),
            "trades": int(candidate_full["total_trades"]),
            "source": f"OKX {VENUE} · locked full-history + 5-fold SMC side-policy run · measured {measured_at}",
        },
        "comparison": {
            "champion_preset_id": champion["id"],
            "champion_config_fingerprint": config_fingerprint(champion),
            "side_comparator_id": manifest["side_policy_comparator"]["id"],
            "champion_full": champion_full,
            "challenger_full": candidate_full,
            "side_comparator_full": side_full,
            "champion_folds": champion_folds,
            "challenger_folds": candidate_folds,
            "side_comparator_folds": side_folds,
        },
    }
    payload = {
        "protocol": {**manifest, "manifest_sha256": manifest_sha256},
        "measured_at": measured_at,
        "data": {"primary": {"bars": len(native), "period": period}},
        "execution_model": economics,
        "full_history": {
            "champion": champion_full,
            "candidate_long_only": candidate_full,
            "side_comparator": side_full,
        },
        "folds": {
            "champion": champion_folds,
            "candidate_long_only": candidate_folds,
            "side_comparator": side_folds,
        },
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
        f"{verdict.upper()} {candidate['id']} | "
        f"PF={float(candidate_full['profit_factor']):.3f} "
        f"net={float(candidate_full['net_profit_pct']):+.2f}% "
        f"MDD={float(candidate_full['max_drawdown_pct']):.2f}% "
        f"trades={int(candidate_full['total_trades'])} | "
        f"long+short PF={float(side_full['profit_factor']):.3f}"
    )
    print(f"wrote {out_dir}")
    return 2 if args.fail_on_reject and not gate["passed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
