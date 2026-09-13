"""Isolated Community 15m research. Run ONLY on GitHub-hosted runners.

Reuse the reviewed non-trading I/O and metric helpers; never mutate the earlier
study's protocol or strategies. No application runtime or account credentials.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from scripts import freqtrade_community_research as base

STUDY = base.ROOT / "docs/research/freqtrade_community_15m_2026_09"
INTERVAL_PATCHES = {
    "VolatilitySystem.py": ("resample_int = 60 * 3", "resample_int = 60"),
    "FReinforcedStrategy.py": (
        "self.resample_interval = timeframe_to_minutes(self.timeframe) * 12",
        "self.resample_interval = 60",
    ),
}


def adapt_interval(name: str, source: bytes) -> tuple[bytes, str]:
    """Exactly one disclosed MODEL change per pinned source; not compatibility."""
    original, replacement = INTERVAL_PATCHES[name]
    text = source.decode("utf-8")
    if text.count(original) != 1:
        raise ValueError(f"Pinned informative interval changed: {name}")
    return text.replace(original, replacement).encode("utf-8"), f"{original} -> {replacement}"


def prepare_sources(out: Path, protocol: dict) -> None:
    base.fetch_upstream(out, protocol)
    manifest = json.loads((out / "upstream_sources.json").read_text())
    for name in INTERVAL_PATCHES:
        if manifest[name]["sha256"] != protocol["upstream_sha256"][name]:
            raise ValueError(f"Pinned source hash mismatch: {name}")
        path = out / "strategies" / name
        source, patch = adapt_interval(name, path.read_bytes())
        compile(source, name, "exec")
        path.write_bytes(source)
        manifest[name]["research_model_patches"] = [patch]
        manifest[name]["executed_sha256"] = hashlib.sha256(source).hexdigest()
    # Replace only the output copy, not the previous frozen study on disk.
    shutil.copyfile(STUDY / "strategies.py", out / "strategies/CommunityStrategies.py")
    base.write_json(out / "upstream_sources.json", manifest)


def audit_15m_frame(df, protocol: dict) -> dict:
    import pandas as pd

    dates = pd.to_datetime(df["date"], utc=True)
    start = pd.Timestamp(protocol["data_start"], tz="UTC")
    end = pd.Timestamp(protocol["data_end_exclusive"], tz="UTC")
    selected = df.loc[(dates >= start) & (dates < end)]
    selected_dates = dates.loc[selected.index]
    expected = pd.date_range(start, end, freq="15min", inclusive="left")
    missing = expected.difference(pd.DatetimeIndex(selected_dates))
    extra = pd.DatetimeIndex(selected_dates).difference(expected)
    numeric = selected[["open", "high", "low", "close", "volume"]]
    invalid = (not numeric.map(math.isfinite).all().all()
               or (numeric[["open", "high", "low", "close"]] <= 0).any().any()
               or (numeric["volume"] < 0).any()
               or (numeric["high"] < numeric[["open", "close", "low"]].max(axis=1)).any()
               or (numeric["low"] > numeric[["open", "close", "high"]].min(axis=1)).any())
    result = {"bars": len(selected), "expected_bars": len(expected),
              "first": str(selected_dates.min()), "last": str(selected_dates.max()),
              "missing": len(missing), "off_grid": len(extra),
              "duplicates": int(selected_dates.duplicated().sum()), "invalid_ohlcv": bool(invalid)}
    if len(missing) or len(extra) or result["duplicates"] or invalid:
        raise ValueError(f"15m data integrity failed: {result}")
    return result


def inspect_data(out: Path, pair: str, protocol: dict) -> dict:
    import pandas as pd

    audit = base.inspect_data(out, pair, ["1m", "1h"], protocol)
    stem = pair.replace("/", "_").replace(":", "_")
    paths = list((out / "data").rglob(f"{stem}-15m-futures.feather"))
    if len(paths) != 1:
        raise ValueError(f"Missing or ambiguous native 15m data: {pair}")
    path = paths[0]
    audit["candles"]["15m"] = {
        **audit_15m_frame(pd.read_feather(path), protocol),
        "file": str(path.relative_to(out)), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }
    return audit


def stress_reasons(results: dict, gate: dict) -> list[str]:
    # Keep sample size, drawdown and flat-at-boundary requirements in stress.
    stress_gate = {**gate, "late_profit_factor_min": gate["stress_late_profit_factor_min"]}
    return base.screening_reasons(results, stress_gate)


LEDGER_FIELDS = ["candidate", "window", "cost", "pair", "open_date", "close_date", "is_short",
                 "stake_amount", "leverage", "open_rate", "close_rate", "profit_abs", "profit_ratio",
                 "fee_open", "fee_close", "funding_fees", "exit_reason", "trade_duration",
                 "initial_stop_loss_ratio", "is_open", "entry_orders"]


def export_trades(data: dict, candidate: dict, window: str, cost: str) -> list[dict]:
    rows = []
    for trade in data.get("trades", []):
        row = {key: trade.get(key) for key in LEDGER_FIELDS}
        row.update(candidate=candidate["id"], window=window, cost=cost,
                   entry_orders=sum(bool(order.get("ft_is_entry")) for order in trade.get("orders", [])))
        rows.append(row)
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()
    base.require_hosted_runner()
    out = args.out_dir.resolve()
    if out.exists():
        raise RuntimeError("Output must be new; never mix runs")
    out.mkdir(parents=True)
    protocol = json.loads((STUDY / "protocol.json").read_text())
    shutil.copyfile(STUDY / "protocol.json", out / "protocol.json")
    base.write_json(out / "environment.json", {
        "commit": os.environ.get("GITHUB_SHA"), "run_id": os.environ.get("GITHUB_RUN_ID"),
        "runner": os.environ.get("RUNNER_ENVIRONMENT"), "started_at": datetime.now(timezone.utc).isoformat(),
        "protocol_sha256": hashlib.sha256((STUDY / "protocol.json").read_bytes()).hexdigest(),
        "harness_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "shared_helpers_sha256": hashlib.sha256(Path(base.__file__).read_bytes()).hexdigest(),
    })
    freeze = subprocess.run([sys.executable, "-m", "pip", "freeze"], capture_output=True, text=True, check=True)
    (out / "dependencies.txt").write_text(freeze.stdout)
    prepare_sources(out, protocol)
    summary = {"study": protocol["id"], "live_weight": 0, "certified": False, "arena_admitted": False,
               "window_warning": protocol["window_warning"], "candidates": [], "errors": []}
    commands: list[dict] = []
    ledger: list[dict] = []
    configs = {}
    for pair in dict.fromkeys(row["pair"] for row in protocol["candidates"]):
        symbol = pair.split("/")[0]
        config = out / f"config_{symbol}.json"
        base.write_json(config, base.make_config(pair))
        configs[pair] = config
        cmd = ["download-data", "-c", str(config), "--userdir", str(out),
               "--datadir", str(out / "data"), "--timeframes", *protocol["download_timeframes"],
               "--timerange", base.download_timerange(protocol)]
        if not base.run_command(cmd, out / "logs" / f"download_{symbol}.log", commands):
            summary["errors"].append(f"{symbol}: download failed")
            continue
        try:
            base.write_json(out / f"data_audit_{symbol}.json", inspect_data(out, pair, protocol))
        except ValueError as error:
            summary["errors"].append(str(error))
    if summary["errors"]:
        base.write_json(out / "summary.json", summary)
        return 1

    def common(candidate, command):
        return [command, "-c", str(configs[candidate["pair"]]), "--userdir", str(out),
                "--datadir", str(out / "data"), "--strategy-path", str(out / "strategies"),
                "-s", candidate["strategy"], "-i", candidate["timeframe"]]

    def backtest(candidate, window, cost):
        label = f"{candidate['id']}_{window}_{cost}"
        directory = out / "results" / label
        directory.mkdir(parents=True)
        fee = protocol["execution"][f"{cost}_cost_per_side"]
        cmd = common(candidate, "backtesting") + [
            "--timerange", protocol["windows"][window], "--cache", "none", "--fee", str(fee),
            "--timeframe-detail", "1m", "--export", "trades", "--backtest-directory", str(directory)]
        if not base.run_command(cmd, out / "logs" / f"{label}.log", commands):
            summary["errors"].append(f"{label}: backtest failed")
            return None
        try:
            data = base.load_result(directory, candidate["strategy"])
            metrics = base.summarize_result(data)
            metrics["entry_regime_breakdown"] = base.regime_breakdown(data, out, candidate["pair"])
            metrics["side_entry_regime_breakdown"] = {
                side: base.regime_breakdown({"trades": [t for t in data.get("trades", [])
                                                       if bool(t.get("is_short")) == short]}, out, candidate["pair"])
                for side, short in (("long", False), ("short", True))
            }
            ledger.extend(export_trades(data, candidate, window, cost))
            base.write_json(directory / "metrics.json", metrics)
            return metrics
        except (KeyError, ValueError) as error:
            summary["errors"].append(f"{label}: {error}")
            return None

    for candidate in protocol["candidates"]:
        row = {**candidate, "live_weight": 0, "certified": False, "arena_admitted": False}
        row["results"] = {window: backtest(candidate, window, "base") for window in protocol["windows"]}
        row["screen_reasons"] = base.screening_reasons(row["results"], protocol["screen_gate"])
        row["status"] = "rejected_screen" if row["screen_reasons"] else "historical_metrics_pass_pending_validation"
        if any(value is None for value in row["results"].values()):
            row["status"] = "blocked_execution"
        csv_path = out / f"lookahead_{candidate['id']}.csv"
        validation = protocol["validation"]
        lookahead = common(candidate, "lookahead-analysis") + [
            "--timerange", "20260312-20260908", "--fee", str(protocol["execution"]["base_cost_per_side"]),
            "--timeframe-detail", validation["lookahead_detail_timeframe"],
            "--minimum-trade-amount", str(validation["lookahead_minimum_trades"]),
            "--targeted-trade-amount", str(validation["lookahead_target_trades"]),
            "--lookahead-analysis-exportfilename", str(csv_path)]
        ok = base.run_command(lookahead, out / "logs" / f"lookahead_{candidate['id']}.log", commands)
        row["lookahead"] = base.lookahead_status(csv_path) if ok else {"status": "command_failed"}
        recursive = common(candidate, "recursive-analysis") + [
            "--timerange", "20260312-20260908", "--startup-candle", *map(str, base.recursive_startups(protocol))]
        ok = base.run_command(recursive, out / "logs" / f"recursive_{candidate['id']}.log", commands)
        row["recursive"] = "completed_requires_table_review" if ok else "command_failed"
        if not row["screen_reasons"]:
            row["stress"] = {window: backtest(candidate, window, "stress") for window in protocol["windows"]}
            row["stress_reasons"] = stress_reasons(row["stress"], protocol["screen_gate"])
            row["status"] = ("rejected_stress" if row["stress_reasons"] else
                             "historical_metrics_pass_requires_bias_funding_and_forward_review")
        summary["candidates"].append(row)
        base.write_json(out / "summary.json", summary)
    with (out / "trade_ledger.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=LEDGER_FIELDS)
        writer.writeheader()
        writer.writerows(ledger)
    # Analysis failures are operational failures, never silently green validation.
    failed = any(command["returncode"] for command in commands)
    base.write_json(out / "summary.json", summary)
    print(json.dumps(summary, indent=2), flush=True)
    return 1 if failed or summary["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
