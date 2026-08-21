#!/usr/bin/env python3
"""Compare the deployed XAU CDC exit regime with ATR trailing stops.

This is a narrow A/B study, not an optimizer.  Entry logic, venue costs, D1
gate, ROI ladder, and candle timing are frozen to the effective owner-itsara
XAUUSDT live profile observed on 2026-08-21.  Three cases are reported:

* ``cdc_pure_no_stop`` -- the deployed CDC-pure exit, fixed 95% exposure;
* ``atr_trailing_equal_exposure`` -- 2 ATR initial SL and 1.8 ATR trailing,
  forced to the same 95% exposure to isolate the exit rule;
* ``atr_trailing_live_risk`` -- the same stop with the tenant's 1% risk-based
  sizing, which is the deployable capital-impact view.

The primary A/B is the first two cases.  The third is diagnostic because
changing ``disable_stop_loss`` also changes PositionSimulator/live sizing from
fixed-fraction to stop-distance risk sizing.
"""

from __future__ import annotations

import argparse
import copy
import csv
import json
import os
import sys
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.validate_on_venue_data import _bundle_for, _okx_frame  # noqa: E402
from scripts.xau_harness import SYMBOL, prepare  # noqa: E402
from xauby.backtest.service import run_replay_from_bundle  # noqa: E402
from xauby.observability.replay_validation import load_bot_config  # noqa: E402

PROXY = "XAUT-USDT"
NATIVE = "XAU-USDT-SWAP"
TIMEFRAME = "4h"
REGIME_TIMEFRAME = "1d"
WARMUP_BARS = 300
SPLIT_RATIO = 0.70
N_FOLDS = 5

# Explicit snapshot of the effective tenant profile.  Keeping every behavioral
# key here prevents repo fallback values from silently changing this study.
LIVE_STRATEGY: dict[str, Any] = {
    "enabled": True,
    "timeframe": TIMEFRAME,
    "primary_timeframe": TIMEFRAME,
    "confirm_timeframe": REGIME_TIMEFRAME,
    "use_d1_regime_filter": True,
    "use_d1_regime_filter_long": True,
    "use_d1_regime_filter_short": True,
    "ap_smoothing": 2,
    "enable_short": False,
    "require_fresh_zone": True,
    "fresh_zone_window": 3,
    "position_pct": 0.95,
    "rsi_min": 0.0,
    "rsi_max": 100.0,
    "vol_min_ratio": 0.0,
    "sl_atr_mult": 2.0,
    "trailing_atr_mult": 1.8,
    "fixed_tp_pct": 0.0,
    "breakeven_sl_enabled": False,
    "breakeven_activation_atr_mult": 1.5,
    "breakeven_buffer_atr_mult": 0.1,
    "cool_down_minutes": 240,
    "require_slow_slope": False,
    "slow_slope_bars": 3,
    "entry_thrust_min": 0.5,
    "exit_on_bear_cross": False,
    "minimal_roi": {"0": 8.0, "1440": 5.0, "4320": 3.0},
}

METRIC_KEYS = (
    "net_profit_pct",
    "profit_factor",
    "max_drawdown_pct",
    "win_rate",
    "total_trades",
    "sharpe",
    "sortino",
    "calmar",
    "cagr_pct",
    "exposure_pct",
    "avg_holding_bars",
    "max_consecutive_losses",
    "gross_profit",
    "gross_loss",
    "final_balance",
)


@dataclass(frozen=True)
class Case:
    name: str
    disable_stop_loss: bool
    risk_pct: float
    description: str


CASES = (
    Case(
        name="cdc_pure_no_stop",
        disable_stop_loss=True,
        risk_pct=0.01,
        description="Current live CDC-pure exit; fixed 95% position sizing",
    ),
    Case(
        name="atr_trailing_equal_exposure",
        disable_stop_loss=False,
        risk_pct=1.0,
        description="2 ATR initial SL + 1.8 ATR trail; 95% exposure control",
    ),
    Case(
        name="atr_trailing_live_risk",
        disable_stop_loss=False,
        risk_pct=0.01,
        description="2 ATR initial SL + 1.8 ATR trail; live 1% risk sizing",
    ),
)


def _engine_config(base: Mapping[str, Any], case: Case) -> dict[str, Any]:
    cfg = copy.deepcopy(dict(base))
    cfg.setdefault("exchange", {})["fee_pct"] = 0.0005
    cfg.setdefault("trading", {}).update(
        {
            "risk_pct": case.risk_pct,
            "max_position_per_trade_pct": 95.0,
            "sl_confirm_ticks": 3,
        }
    )
    cfg.setdefault("backtest", {}).update(
        {
            "slippage_bps": 2.0,
            "funding_rate_8h": 0.00004,
        }
    )
    profile = (
        cfg.setdefault("strategy", {}).setdefault("config", {}).setdefault("xauby_actionzone", {})
    )
    profile.update(LIVE_STRATEGY)
    profile["disable_stop_loss"] = case.disable_stop_loss
    return cfg


def _compact_stats(stats: Mapping[str, Any]) -> dict[str, Any]:
    result = {key: stats.get(key) for key in METRIC_KEYS}
    trades = stats.get("trades") or []
    result["exit_triggers"] = dict(
        sorted(Counter(str(t.get("trigger") or "UNKNOWN") for t in trades).items())
    )
    return result


def _run(
    base_config: Mapping[str, Any],
    case: Case,
    frame: pd.DataFrame,
    regime: pd.DataFrame,
    *,
    label: str,
    skip_bars: int = 0,
) -> dict[str, Any]:
    cfg = _engine_config(base_config, case)
    prep = prepare(cfg)
    strategy = prep.variant_config(
        {
            **LIVE_STRATEGY,
            "disable_stop_loss": case.disable_stop_loss,
        }
    )
    bundle = _bundle_for(SYMBOL, frame, engine_config=cfg, label=label)
    bundle.df_regime = regime.reset_index(drop=True)
    bundle.regime_tf = REGIME_TIMEFRAME
    bundle.use_d1 = True
    # The runtime merge may supply portfolio defaults.  Re-pin the effective
    # tenant sizing so this branch cannot drift away from the declared case.
    bundle.merged_cfg.setdefault("trading", {}).update(
        {
            "risk_pct": case.risk_pct,
            "max_position_per_trade_pct": 95.0,
            "sl_confirm_ticks": 3,
        }
    )
    bundle.merged_cfg.setdefault("backtest", {}).update(
        {"slippage_bps": 2.0, "funding_rate_8h": 0.00004}
    )
    result = run_replay_from_bundle(
        bundle,
        strat_cfg_override=strategy,
        min_bars_override=skip_bars,
    )
    if not result.meta.run_ok:
        raise RuntimeError(result.meta.error or f"replay failed for {case.name}")
    return _compact_stats(result.stats or {})


def _range(frame: pd.DataFrame) -> dict[str, Any]:
    stamps = frame["open_time"] if "open_time" in frame.columns else frame["timestamp"]
    return {
        "bars": int(len(frame)),
        "start": pd.to_datetime(int(stamps.iloc[0]), unit="ms", utc=True).isoformat(),
        "end": pd.to_datetime(int(stamps.iloc[-1]), unit="ms", utc=True).isoformat(),
    }


def _folds(frame: pd.DataFrame) -> Iterable[tuple[pd.DataFrame, int]]:
    segment = len(frame) // N_FOLDS
    for index in range(N_FOLDS):
        traded_start = index * segment
        start = max(0, traded_start - WARMUP_BARS)
        end = len(frame) if index == N_FOLDS - 1 else (index + 1) * segment
        yield frame.iloc[start:end].reset_index(drop=True), traded_start - start


def _fold_summary(rows: list[Mapping[str, Any]]) -> dict[str, Any]:
    nets = [float(row.get("net_profit_pct") or 0.0) for row in rows]
    pfs = [float(row.get("profit_factor") or 0.0) for row in rows]
    compounded = 1.0
    for net in nets:
        compounded *= 1.0 + net / 100.0
    return {
        "fold_profit_factors": pfs,
        "fold_net_profit_pct": nets,
        "profitable_folds": sum(1 for net in nets if net > 0),
        "worst_profit_factor": min(pfs) if pfs else 0.0,
        "compounded_net_profit_pct": (compounded - 1.0) * 100.0,
        "total_trades": sum(int(row.get("total_trades") or 0) for row in rows),
    }


def _f(value: Any, digits: int = 2) -> str:
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return "n/a"


def _metric_table(full: Mapping[str, Mapping[str, Mapping[str, Any]]]) -> str:
    lines = [
        "| Dataset | Case | PF | Net % | MDD % | Win % | Trades | Sharpe | Exposure % |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for dataset in ("proxy", "native"):
        for case in CASES:
            row = full[dataset][case.name]
            lines.append(
                f"| {dataset} | `{case.name}` | {_f(row['profit_factor'])} | "
                f"{_f(row['net_profit_pct'])} | {_f(row['max_drawdown_pct'])} | "
                f"{_f(row['win_rate'])} | {int(row['total_trades'] or 0)} | "
                f"{_f(row['sharpe'])} | {_f(row['exposure_pct'])} |"
            )
    return "\n".join(lines)


def _delta(a: Mapping[str, Any], b: Mapping[str, Any], key: str) -> float:
    return float(b.get(key) or 0.0) - float(a.get(key) or 0.0)


def _build_report(payload: Mapping[str, Any]) -> str:
    full = payload["full_history"]
    proxy_a = full["proxy"]["cdc_pure_no_stop"]
    proxy_b = full["proxy"]["atr_trailing_equal_exposure"]
    native_a = full["native"]["cdc_pure_no_stop"]
    native_b = full["native"]["atr_trailing_equal_exposure"]
    oos_a = payload["split_70_30"]["oos"]["cdc_pure_no_stop"]
    oos_b = payload["split_70_30"]["oos"]["atr_trailing_equal_exposure"]
    folds_b = payload["five_folds"]["atr_trailing_equal_exposure"]["summary"]

    enough_native = int(native_b.get("total_trades") or 0) >= 18
    checks = {
        "proxy_pf_improves": _delta(proxy_a, proxy_b, "profit_factor") > 0,
        "proxy_net_improves": _delta(proxy_a, proxy_b, "net_profit_pct") > 0,
        "proxy_mdd_not_worse": float(proxy_b["max_drawdown_pct"] or 0)
        <= float(proxy_a["max_drawdown_pct"] or 0),
        "native_pf_improves": _delta(native_a, native_b, "profit_factor") > 0,
        "native_net_improves": _delta(native_a, native_b, "net_profit_pct") > 0,
        "oos_pf_above_one": float(oos_b.get("profit_factor") or 0.0) > 1.0,
        "at_least_four_profitable_folds": int(folds_b["profitable_folds"]) >= 4,
        "native_sample_at_least_18_trades": enough_native,
    }
    passed = sum(bool(value) for value in checks.values())
    if all(checks.values()):
        verdict = "PASS — ATR trailing cleared every pre-declared comparison gate."
    elif not enough_native:
        verdict = "INCONCLUSIVE — the native-contract trade sample is below 18."
    else:
        verdict = "FAIL — keep CDC-pure; ATR trailing did not clear every comparison gate."

    trigger_lines = []
    for dataset in ("proxy", "native"):
        for name in ("cdc_pure_no_stop", "atr_trailing_equal_exposure"):
            trigger_lines.append(
                f"- {dataset} `{name}`: "
                f"{json.dumps(full[dataset][name]['exit_triggers'], sort_keys=True)}"
            )

    fold_lines = [
        "| Case | Fold PFs | Profitable | Worst PF | Compounded net % | Trades |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for name in ("cdc_pure_no_stop", "atr_trailing_equal_exposure"):
        row = payload["five_folds"][name]["summary"]
        pf_text = ", ".join(_f(value) for value in row["fold_profit_factors"])
        fold_lines.append(
            f"| `{name}` | {pf_text} | {row['profitable_folds']}/5 | "
            f"{_f(row['worst_profit_factor'])} | "
            f"{_f(row['compounded_net_profit_pct'])} | {row['total_trades']} |"
        )

    check_lines = [f"- [{'x' if ok else ' '}] `{name}`" for name, ok in checks.items()]
    generated = payload["generated_at_utc"]
    proxy_range = payload["datasets"]["proxy_4h"]
    native_range = payload["datasets"]["native_4h"]

    return f"""# XAUUSDT CDC ActionZone: ATR trailing stop A/B

- Generated: {generated}
- Commit: `{payload["git_commit"]}`
- Strategy: current Live `xauby_actionzone`, long-only, 4h + D1 gate
- Proxy: OKX `XAUT-USDT`, {proxy_range["bars"]} bars, {proxy_range["start"]} to {proxy_range["end"]}
- Native: OKX `XAU-USDT-SWAP`, {native_range["bars"]} bars, {native_range["start"]} to {native_range["end"]}

## Verdict

**{verdict}** Gate score: **{passed}/{len(checks)}**.

The primary A/B holds exposure at 95% in both cases so the comparison isolates
the exit regime. `atr_trailing_live_risk` is shown separately: it uses the
tenant's actual 1% stop-distance risk sizing and therefore measures capital
impact, not only exit quality.

## Frozen inputs

- Entry/strategy: AP smoothing 2, fresh-zone window 3, D1 filter on, long-only,
  no slow-slope requirement, thrust minimum 0.5, RED-zone strategy exit.
- Current exit: `disable_stop_loss=true`; CDC/ROI exit only.
- ATR exit: initial stop 2.0 ATR, trailing 1.8 ATR, breakeven disabled.
- ROI ladder: 8% at entry, 5% after 1 day, 3% after 3 days.
- Economics: 0.05% taker fee per fill, 2 bps slippage per fill, flat 0.004%
  funding per 8h, 95% maximum exposure.

## Full-history results

{_metric_table(full)}

Equal-exposure A/B deltas (trailing minus CDC-pure):

- Proxy: PF {_delta(proxy_a, proxy_b, "profit_factor"):+.2f}, net
  {_delta(proxy_a, proxy_b, "net_profit_pct"):+.2f} pp, MDD
  {_delta(proxy_a, proxy_b, "max_drawdown_pct"):+.2f} pp.
- Native: PF {_delta(native_a, native_b, "profit_factor"):+.2f}, net
  {_delta(native_a, native_b, "net_profit_pct"):+.2f} pp, MDD
  {_delta(native_a, native_b, "max_drawdown_pct"):+.2f} pp.

## 70/30 chronological holdout (proxy)

| Window | Case | PF | Net % | MDD % | Trades |
|---|---|---:|---:|---:|---:|
| IS | `cdc_pure_no_stop` | {_f(payload["split_70_30"]["is"]["cdc_pure_no_stop"]["profit_factor"])} | {_f(payload["split_70_30"]["is"]["cdc_pure_no_stop"]["net_profit_pct"])} | {_f(payload["split_70_30"]["is"]["cdc_pure_no_stop"]["max_drawdown_pct"])} | {int(payload["split_70_30"]["is"]["cdc_pure_no_stop"]["total_trades"] or 0)} |
| IS | `atr_trailing_equal_exposure` | {_f(payload["split_70_30"]["is"]["atr_trailing_equal_exposure"]["profit_factor"])} | {_f(payload["split_70_30"]["is"]["atr_trailing_equal_exposure"]["net_profit_pct"])} | {_f(payload["split_70_30"]["is"]["atr_trailing_equal_exposure"]["max_drawdown_pct"])} | {int(payload["split_70_30"]["is"]["atr_trailing_equal_exposure"]["total_trades"] or 0)} |
| OOS | `cdc_pure_no_stop` | {_f(oos_a["profit_factor"])} | {_f(oos_a["net_profit_pct"])} | {_f(oos_a["max_drawdown_pct"])} | {int(oos_a["total_trades"] or 0)} |
| OOS | `atr_trailing_equal_exposure` | {_f(oos_b["profit_factor"])} | {_f(oos_b["net_profit_pct"])} | {_f(oos_b["max_drawdown_pct"])} | {int(oos_b["total_trades"] or 0)} |

## Five chronological folds (proxy)

{chr(10).join(fold_lines)}

## Exit attribution

{chr(10).join(trigger_lines)}

## Decision gates

{chr(10).join(check_lines)}

## Limitations

- The long history uses tokenized-gold spot as a proxy. The native perpetual
  starts only in April 2025 and is a confirmation sample, not a selection set.
- Stops are simulated from 4h OHLC bars through the repository's live-parity
  replay. Intrabar tick order is unknowable, so exact stop fills can differ
  from production even though signals use only closed bars.
- Funding is a flat configured approximation, not historical OKX funding.
- The comparison is historical evidence, not a guarantee. No production
  config was changed by this run.
"""


def _csv_rows(payload: Mapping[str, Any]) -> Iterable[dict[str, Any]]:
    for dataset, cases in payload["full_history"].items():
        for name, stats in cases.items():
            yield {"section": "full_history", "window": dataset, "case": name, **stats}
    for window, cases in payload["split_70_30"].items():
        for name, stats in cases.items():
            yield {"section": "split_70_30", "window": window, "case": name, **stats}


def main() -> None:
    parser = argparse.ArgumentParser(description="XAU CDC no-stop vs ATR trailing A/B")
    parser.add_argument("--config", default=str(ROOT / "bot_config.yaml"))
    parser.add_argument("--out-dir", default=str(ROOT / "core/xau_cdc_trailing_ab"))
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    base_config = load_bot_config(args.config)

    frames = {
        "proxy_4h": _okx_frame(PROXY, TIMEFRAME),
        "proxy_1d": _okx_frame(PROXY, REGIME_TIMEFRAME),
        "native_4h": _okx_frame(NATIVE, TIMEFRAME),
        "native_1d": _okx_frame(NATIVE, REGIME_TIMEFRAME),
    }

    full_history: dict[str, dict[str, Any]] = {"proxy": {}, "native": {}}
    for case in CASES:
        full_history["proxy"][case.name] = _run(
            base_config, case, frames["proxy_4h"], frames["proxy_1d"], label=PROXY
        )
        full_history["native"][case.name] = _run(
            base_config, case, frames["native_4h"], frames["native_1d"], label=NATIVE
        )

    proxy = frames["proxy_4h"]
    split = int(len(proxy) * SPLIT_RATIO)
    oos_start = max(0, split - WARMUP_BARS)
    split_results: dict[str, dict[str, Any]] = {"is": {}, "oos": {}}
    primary_cases = CASES[:2]
    for case in primary_cases:
        split_results["is"][case.name] = _run(
            base_config,
            case,
            proxy.iloc[:split].reset_index(drop=True),
            frames["proxy_1d"],
            label=PROXY,
        )
        split_results["oos"][case.name] = _run(
            base_config,
            case,
            proxy.iloc[oos_start:].reset_index(drop=True),
            frames["proxy_1d"],
            label=PROXY,
            skip_bars=split - oos_start,
        )

    fold_results: dict[str, Any] = {}
    fold_slices = list(_folds(proxy))
    for case in primary_cases:
        rows = [
            _run(
                base_config,
                case,
                frame,
                frames["proxy_1d"],
                label=PROXY,
                skip_bars=skip,
            )
            for frame, skip in fold_slices
        ]
        fold_results[case.name] = {"folds": rows, "summary": _fold_summary(rows)}

    payload: dict[str, Any] = {
        "schema_version": 1,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "git_commit": os.environ.get("GITHUB_SHA", "local"),
        "protocol": {
            "strategy": LIVE_STRATEGY,
            "cases": [case.__dict__ for case in CASES],
            "split_ratio": SPLIT_RATIO,
            "warmup_bars": WARMUP_BARS,
            "folds": N_FOLDS,
            "fee_pct": 0.0005,
            "slippage_bps": 2.0,
            "funding_rate_8h": 0.00004,
        },
        "datasets": {name: _range(frame) for name, frame in frames.items()},
        "full_history": full_history,
        "split_70_30": split_results,
        "five_folds": fold_results,
    }

    json_path = out_dir / "results.json"
    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    report_path = out_dir / "report.md"
    report_path.write_text(_build_report(payload), encoding="utf-8")

    rows = list(_csv_rows(payload))
    csv_path = out_dir / "summary.csv"
    fieldnames = ["section", "window", "case", *METRIC_KEYS, "exit_triggers"]
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    print(report_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
