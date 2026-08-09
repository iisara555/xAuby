#!/usr/bin/env python3
"""Run the locked BTC LONG-D1 Challenger finalist protocol on native OKX data.

This is deliberately a finalist-only replay, not another parameter search.  Its
candidate, Champion, economics, folds, and gates are frozen in
``docs/research/protocols/btc_long_d1_challenger_v1.json``.  The output is a
*proposed* certificate artifact; this script never writes into the catalog's
certificate directory and never changes runtime or tenant configuration.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pandas as pd

from scripts import btc_supertrend_okx_pf_grid as grid
from scripts.validate_on_venue_data import _okx_frame
from xauby.observability.replay_validation import load_bot_config
from xauby.saas.certification import RECORD_VERSION, config_fingerprint
from xauby.saas.preset_specs import PRESET_SPECS

DEFAULT_PROTOCOL = ROOT / "docs/research/protocols/btc_long_d1_challenger_v1.json"
PROTOCOL_NAME = "btc-long-d1-challenger-finalist"
PROTOCOL_VERSION = "3"


def _preset(preset_id: str) -> dict[str, Any]:
    for spec in PRESET_SPECS:
        if str(spec.get("id")) == preset_id:
            return dict(spec)
    raise ValueError(f"unknown preset {preset_id!r}")


def _number(value: Any) -> float:
    result = float(value or 0.0)
    if not pd.notna(result):
        raise ValueError("replay produced a non-finite metric")
    return result


def _ratio(left: Any, right: Any) -> float:
    denominator = _number(right)
    if denominator <= 0:
        return 0.0
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


def _require_locked_identity(
    manifest: Mapping[str, Any],
    candidate: Mapping[str, Any],
    champion: Mapping[str, Any],
    economics: Mapping[str, Any],
) -> None:
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
    locked_economics = manifest.get("execution_model") or {}
    for key, expected_value in locked_economics.items():
        actual = economics.get(key)
        if actual is None or abs(float(actual) - float(expected_value)) > 1e-12:
            raise ValueError(
                f"execution model changed for {key}: {actual!r}, "
                f"locked protocol expects {expected_value!r}"
            )


def evaluate_gate(
    candidate: Mapping[str, Any],
    champion: Mapping[str, Any],
    candidate_folds: list[Mapping[str, Any]],
    champion_folds: list[Mapping[str, Any]],
    *,
    history_days: int,
    manifest: Mapping[str, Any],
) -> dict[str, Any]:
    """Apply every pre-registered absolute and comparative criterion."""
    gates = manifest.get("gates") or {}
    source = manifest.get("data_source") or {}
    profitable_folds = sum(_number(row.get("net_profit_pct")) > 0 for row in candidate_folds)
    pf_one_folds = sum(_number(row.get("profit_factor")) >= 1 for row in candidate_folds)
    pf_wins = sum(
        _number(left.get("profit_factor")) >= _number(right.get("profit_factor"))
        for left, right in zip(candidate_folds, champion_folds)
    )
    pf_ratio = _ratio(candidate.get("profit_factor"), champion.get("profit_factor"))
    drawdown_ratio = _ratio(
        candidate.get("max_drawdown_pct"), champion.get("max_drawdown_pct")
    )
    net_retention = _ratio(candidate.get("net_profit_pct"), champion.get("net_profit_pct"))
    trade_retention = _ratio(candidate.get("total_trades"), champion.get("total_trades"))
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
        "full_profit_factor_noninferiority": pf_ratio
        >= float(gates["candidate_vs_champion_full_profit_factor_ratio_min"]),
        "full_drawdown_noninferiority": drawdown_ratio
        <= float(gates["candidate_vs_champion_full_max_drawdown_ratio_max"]),
        "full_net_retention": net_retention
        >= float(gates["candidate_vs_champion_full_net_profit_retention_min"]),
        "full_trade_retention": trade_retention
        >= float(gates["candidate_vs_champion_full_trade_retention_min"]),
        "fold_profit_factor_wins": pf_wins
        >= int(gates["candidate_vs_champion_fold_profit_factor_wins_min"]),
    }
    return {
        "passed": all(checks.values()),
        "checks": checks,
        "observed": {
            "history_days": history_days,
            "profitable_folds": profitable_folds,
            "folds_with_profit_factor_at_least_one": pf_one_folds,
            "candidate_vs_champion_fold_profit_factor_wins": pf_wins,
            "candidate_vs_champion_full_profit_factor_ratio": round(pf_ratio, 6),
            "candidate_vs_champion_full_max_drawdown_ratio": round(drawdown_ratio, 6),
            "candidate_vs_champion_full_net_profit_retention": round(net_retention, 6),
            "candidate_vs_champion_full_trade_retention": round(trade_retention, 6),
        },
        "thresholds": dict(gates),
    }


def _period(frame: pd.DataFrame) -> tuple[str, str, int]:
    first = pd.to_datetime(int(frame["open_time"].iloc[0]), unit="ms", utc=True)
    last = pd.to_datetime(int(frame["open_time"].iloc[-1]), unit="ms", utc=True)
    days = int((last - first).total_seconds() // 86_400)
    return f"{first:%b %Y} – {last:%b %Y}", f"{days / 365.25:.1f} years", days


def _run_candidate(
    preset: Mapping[str, Any],
    native4: pd.DataFrame,
    native1: pd.DataFrame,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    profile = dict(preset.get("execution_profile") or {})
    full = grid._run_frame(native4, native1, profile, skip_bars=0)
    folds: list[dict[str, Any]] = []
    for index, (frame, skip) in enumerate(grid._fold_slices(native4), start=1):
        metrics = grid._run_frame(frame, native1, profile, skip_bars=skip)
        metrics["fold"] = index
        metrics["window_start"] = str(
            pd.to_datetime(int(frame["open_time"].iloc[skip]), unit="ms", utc=True)
        )
        metrics["window_end"] = str(
            pd.to_datetime(int(frame["open_time"].iloc[-1]), unit="ms", utc=True)
        )
        folds.append(metrics)
    return full, folds


def _report(payload: Mapping[str, Any]) -> str:
    candidate = payload["full_history"]["challenger"]
    champion = payload["full_history"]["champion"]
    gate = payload["gate"]
    lines = [
        "# BTC LONG-D1 Challenger locked finalist run",
        "",
        f"Verdict: **{'PASS — forward shadow eligible' if gate['passed'] else 'REJECT'}**",
        "",
        "| role | PF | net% | MDD% | trades |",
        "|---|---:|---:|---:|---:|",
        f"| Champion | {float(champion['profit_factor']):.3f} | {float(champion['net_profit_pct']):+.2f} | {float(champion['max_drawdown_pct']):.2f} | {int(champion['total_trades'])} |",
        f"| Challenger | {float(candidate['profit_factor']):.3f} | {float(candidate['net_profit_pct']):+.2f} | {float(candidate['max_drawdown_pct']):.2f} | {int(candidate['total_trades'])} |",
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
            "Passing this run issues only a proposed catalog certificate. Live approval and promotion remain false, and the 30-day / 20-trade forward-shadow gate still applies.",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="bot_config.yaml")
    parser.add_argument("--protocol", default=str(DEFAULT_PROTOCOL))
    parser.add_argument("--out-dir", default="core/btc_long_d1_challenger")
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
    _require_locked_identity(manifest, candidate, champion, economics)

    grid._G.clear()
    grid._G.update(cfg=config, prep=grid.prepare(config))
    native4 = _okx_frame(grid.SYMBOL, "4h")
    native1 = _okx_frame(grid.SYMBOL, "1d")
    grid._G.update(native4=native4, native1=native1)

    champion_full, champion_folds = _run_candidate(champion, native4, native1)
    candidate_full, candidate_folds = _run_candidate(candidate, native4, native1)
    period, duration, history_days = _period(native4)
    gate = evaluate_gate(
        candidate_full,
        champion_full,
        candidate_folds,
        champion_folds,
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
            "script": "scripts/certify_btc_d1_challenger.py",
            "manifest": str(manifest_path.relative_to(ROOT)),
            "manifest_sha256": manifest_sha256,
        },
        "data_source": {
            "venue": "okx",
            "symbol": "BTC-USDT-SWAP",
            "timeframe": "4h",
            "regime_timeframe": "1d",
            "native": True,
        },
        "gate": gate,
        "verdict": verdict,
        "note": (
            "Clears the locked finalist protocol; eligible for forward shadow only."
            if gate["passed"]
            else "Fails one or more locked finalist gates; not eligible for Strategy Arena shadow."
        ),
        "document": "docs/research/btc_supertrend_okx_pf_grid_2026-07-29.md",
        "evidence": {
            "status": "validated",
            "score_label": f"PF {float(candidate_full['profit_factor']):.2f}",
            "period": period,
            "duration": duration,
            "win_rate_pct": round(float(candidate_full["win_rate"]), 1),
            "max_drawdown_pct": round(float(candidate_full["max_drawdown_pct"]), 1),
            "trades": int(candidate_full["total_trades"]),
            "source": f"OKX BTC-USDT-SWAP · locked full-history + 5-fold finalist run · measured {measured_at}",
        },
        "comparison": {
            "champion_preset_id": champion["id"],
            "champion_config_fingerprint": config_fingerprint(champion),
            "champion_full": champion_full,
            "challenger_full": candidate_full,
            "champion_folds": champion_folds,
            "challenger_folds": candidate_folds,
        },
    }
    payload = {
        "protocol": {**manifest, "manifest_sha256": manifest_sha256},
        "measured_at": measured_at,
        "data": {
            "primary": grid._date_range(native4),
            "regime": grid._date_range(native1),
        },
        "execution_model": economics,
        "full_history": {"champion": champion_full, "challenger": candidate_full},
        "folds": {"champion": champion_folds, "challenger": candidate_folds},
        "gate": gate,
        "proposed_certificate": record,
    }

    out_dir = Path(args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "results.json").write_text(
        json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8"
    )
    (out_dir / "proposed_certificate.json").write_text(
        json.dumps(record, indent=2, default=str) + "\n", encoding="utf-8"
    )
    (out_dir / "report.md").write_text(_report(payload), encoding="utf-8")
    print(
        f"{verdict.upper()} {candidate['id']} | "
        f"PF={float(candidate_full['profit_factor']):.3f} "
        f"net={float(candidate_full['net_profit_pct']):+.2f}% "
        f"MDD={float(candidate_full['max_drawdown_pct']):.2f}% "
        f"trades={int(candidate_full['total_trades'])}"
    )
    print(f"wrote {out_dir}")
    return 2 if args.fail_on_reject and not gate["passed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
