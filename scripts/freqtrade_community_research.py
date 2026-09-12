"""Native Freqtrade research on GitHub-hosted runners; never start a trading bot.

All files, logs, cached public data, upstream hashes and results stay under the
explicit output directory. No xAuby runtime imports, account secrets or writes.
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
import urllib.request
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STUDY = ROOT / "docs/research/freqtrade_community_2026_09"
ALLOWED_COMMANDS = {"download-data", "backtesting", "lookahead-analysis", "recursive-analysis"}


def require_hosted_runner() -> None:
    if os.environ.get("GITHUB_ACTIONS") != "true" or os.environ.get("RUNNER_ENVIRONMENT") != "github-hosted":
        raise RuntimeError("Research execution is restricted to GitHub-hosted runners; never run on the trading VPS")


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, default=str, allow_nan=False) + "\n")


def make_config(pair: str) -> dict:
    return {
        "dry_run": True, "dry_run_wallet": 10000, "stake_currency": "USDT",
        "stake_amount": 2500, "max_open_trades": 1, "tradable_balance_ratio": 0.99,
        "trading_mode": "futures", "margin_mode": "isolated",
        "futures_funding_rate": 0.0,
        "exchange": {"name": "okx", "key": "", "secret": "", "password": "",
                     "ccxt_config": {"enableRateLimit": True},
                     "ccxt_async_config": {"enableRateLimit": True},
                     "pair_whitelist": [pair], "pair_blacklist": []},
        "pairlists": [{"method": "StaticPairList"}],
        "entry_pricing": {"price_side": "other", "use_order_book": False},
        "exit_pricing": {"price_side": "other", "use_order_book": False},
        "order_types": {"entry": "market", "exit": "market", "stoploss": "market", "stoploss_on_exchange": False},
        "order_time_in_force": {"entry": "GTC", "exit": "GTC"},
        "unfilledtimeout": {"entry": 10, "exit": 10, "unit": "minutes"},
        # Omit unused optional services: their schemas require credentials even
        # when enabled=False. Neither a backtest nor a download starts them.
        "dataformat_ohlcv": "feather", "dataformat_trades": "feather",
    }


def download_timerange(protocol: dict) -> str:
    # Some native exchange downloads discard their last returned candle as
    # potentially incomplete. Fetch a closed-day buffer, but neither audit nor
    # backtest extends beyond the frozen data_end_exclusive / window bounds.
    end = datetime.strptime(protocol["data_end_exclusive"], "%Y%m%d") + timedelta(days=1)
    return f"{protocol['data_start']}-{end:%Y%m%d}"


def compatible_upstream(name: str, source: bytes) -> tuple[bytes, list[str]]:
    """Annotate missing parameter categories, without changing any values.

    Native 2026.8 refuses the upstream's three unprefixed IntParameters. No
    hyperopt or parameter files are used here, so category metadata does not
    change evaluated defaults, indicator formulas, entry or exit rules.
    """
    if name != "FReinforcedStrategy.py":
        return source, []
    text = source.decode("utf-8")
    declarations = [
        'adx_period = IntParameter(4, 24, default=14)',
        'ema_short_period = IntParameter(4, 24, default=8)',
        'ema_long_period = IntParameter(12, 175, default=21)',
    ]
    patches = []
    for original in declarations:
        if text.count(original) != 1:
            raise ValueError(f"Pinned upstream compatibility declaration changed: {original}")
        replacement = original[:-1] + ', space="buy")'
        text = text.replace(original, replacement)
        patches.append(f"{original} -> {replacement}")
    return text.encode("utf-8"), patches


def recursive_startups(protocol: dict) -> list[int]:
    # Native OKX permits five 300-candle calls, minus the current candle.
    # This caps DIAGNOSTIC probes only; strategy startup counts are unchanged.
    return sorted({min(value, 1499) for value in protocol["validation"]["recursive_startups"]})


def fatal_log_errors(text: str) -> list[str]:
    # Native analysis commands can log a caught ConfigurationError and exit 0.
    return [line for line in text.splitlines() if " - ERROR - " in line or " - CRITICAL - " in line or line.startswith("Traceback (most recent call last)")]


def run_command(args: list[str], log: Path, commands: list[dict], timeout: int = 1200) -> bool:
    require_hosted_runner()
    if not args or args[0] not in ALLOWED_COMMANDS:
        raise ValueError("Only non-trading Freqtrade research commands are permitted")
    log.parent.mkdir(parents=True, exist_ok=True)
    # Use the installed console entry point, which propagates main()'s exit
    # status. The 2026.8 python -m entry point can return zero on config errors.
    command = [str(Path(sys.executable).with_name("freqtrade")), *args]
    print(f"START {log.stem}", flush=True)
    started = datetime.now(timezone.utc).isoformat()
    with log.open("w") as stream:
        try:
            result = subprocess.run(command, stdout=stream, stderr=subprocess.STDOUT, timeout=timeout, check=False)
            code = result.returncode
        except subprocess.TimeoutExpired:
            code = 124
    process_code = code
    errors = fatal_log_errors(log.read_text())
    if code == 0 and errors:
        code = 1
    commands.append({"argv": command, "started_at": started, "returncode": code,
                     "process_returncode": process_code, "fatal_log_errors": errors, "log": str(log)})
    write_json(log.parents[1] / "commands.json", commands)
    print(f"END {log.stem}: {code}", flush=True)
    if code:
        print(log.read_text()[-5000:], flush=True)
    return code == 0


def fetch_upstream(out: Path, protocol: dict) -> None:
    strategies = out / "strategies"
    strategies.mkdir(parents=True, exist_ok=True)
    pristine = out / "upstream_pristine"
    pristine.mkdir(parents=True, exist_ok=True)
    manifest = {}
    for name in ("VolatilitySystem.py", "FReinforcedStrategy.py", "LICENSE"):
        source_path = name if name == "LICENSE" else f"user_data/strategies/futures/{name}"
        url = ("https://raw.githubusercontent.com/freqtrade/freqtrade-strategies/"
               f"{protocol['upstream_commit']}/{source_path}")
        with urllib.request.urlopen(url, timeout=45) as response:
            content = response.read()
        if name.endswith(".py"):
            compile(content, name, "exec")
        (pristine / name).write_bytes(content)
        executable, patches = compatible_upstream(name, content)
        (strategies / name).write_bytes(executable)
        manifest[name] = {"url": url, "sha256": hashlib.sha256(content).hexdigest(),
                          "executed_sha256": hashlib.sha256(executable).hexdigest(),
                          "compatibility_patches": patches}
    shutil.copyfile(STUDY / "strategies.py", strategies / "CommunityStrategies.py")
    write_json(out / "upstream_sources.json", manifest)


def inspect_data(out: Path, pair: str, timeframes: list[str], protocol: dict) -> dict:
    import pandas as pd

    stem = pair.replace("/", "_").replace(":", "_")
    result = {"candles": {}, "funding": [], "certification_funding_complete": False}
    for timeframe in timeframes:
        matches = list((out / "data").rglob(f"{stem}-{timeframe}-futures.feather"))
        if len(matches) != 1:
            raise ValueError(f"Missing or ambiguous native data: {stem} {timeframe}")
        path = matches[0]
        df = pd.read_feather(path)
        dates = pd.to_datetime(df["date"], utc=True)
        minutes = {"1m": 1, "5m": 5, "1h": 60}[timeframe]
        start = pd.Timestamp(protocol["data_start"], tz="UTC")
        end = pd.Timestamp(protocol["data_end_exclusive"], tz="UTC")
        selected = df.loc[(dates >= start) & (dates < end)].copy()
        selected_dates = pd.to_datetime(selected["date"], utc=True)
        expected = pd.date_range(start, end, freq=f"{minutes}min", inclusive="left")
        missing = expected.difference(pd.DatetimeIndex(selected_dates))
        numeric = selected[["open", "high", "low", "close", "volume"]]
        invalid = (not numeric.map(math.isfinite).all().all()
                   or (numeric[["open", "high", "low", "close"]] <= 0).any().any()
                   or (numeric["volume"] < 0).any()
                   or (numeric["high"] < numeric[["open", "close", "low"]].max(axis=1)).any()
                   or (numeric["low"] > numeric[["open", "close", "high"]].min(axis=1)).any())
        entry = {"file": str(path.relative_to(out)), "bars": len(selected), "expected_bars": len(expected),
                 "first": str(selected_dates.min()), "last": str(selected_dates.max()),
                 "missing": len(missing), "duplicates": int(selected_dates.duplicated().sum()),
                 "invalid_ohlcv": bool(invalid), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
        result["candles"][timeframe] = entry
        if len(missing) or entry["duplicates"] or invalid:
            raise ValueError(f"Data integrity failed: {pair} {timeframe}: {entry}")
    for path in (out / "data").rglob(f"{stem}-*-funding_rate.feather"):
        df = pd.read_feather(path)
        result["funding"].append({"file": str(path.relative_to(out)), "rows": len(df),
                                  "first": str(df["date"].min()), "last": str(df["date"].max()),
                                  "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    result["funding_note"] = "Native available rates with zero fallback for gaps; coverage is not certified by first/last alone."
    return result


def load_result(directory: Path, strategy: str) -> dict:
    archives = sorted(directory.glob("*.zip"))
    for archive in reversed(archives):
        with zipfile.ZipFile(archive) as bundle:
            for name in bundle.namelist():
                if not name.endswith(".json") or name.endswith((".meta.json", "_config.json")):
                    continue
                data = json.loads(bundle.read(name))
                if strategy in data.get("strategy", {}):
                    return data["strategy"][strategy]
    for path in directory.glob("*.json"):
        data = json.loads(path.read_text())
        if isinstance(data, dict) and strategy in data.get("strategy", {}):
            return data["strategy"][strategy]
    raise ValueError(f"No native result for {strategy} in {directory}")


def summarize_result(data: dict) -> dict:
    trades = data.get("trades", [])
    pnl = [float(t["profit_abs"]) for t in trades]
    gains = sum(v for v in pnl if v > 0)
    losses = -sum(v for v in pnl if v < 0)
    pnl_sum = sum(pnl)
    reported = float(data["profit_total_abs"])
    if abs(pnl_sum - reported) > 0.02:
        raise ValueError(f"Native ledger does not reconcile: {pnl_sum} != {reported}")
    if int(data["total_trades"]) != len(trades):
        raise ValueError("Native trade count mismatch")
    if any(not math.isfinite(v) for v in pnl):
        raise ValueError("Non-finite trade result")
    sides = {}
    for short, label in ((False, "long"), (True, "short")):
        values = [float(t["profit_abs"]) for t in trades if bool(t.get("is_short")) == short]
        sides[label] = {"trades": len(values), "net_pnl_usdt": sum(values)}
    return {"net_return_pct": float(data["profit_total"]) * 100,
            "net_pnl_usdt": reported, "profit_factor": gains / losses if losses else None,
            "all_wins_no_losses": losses == 0 and gains > 0,
            "max_drawdown_pct": float(data["max_drawdown_account"]) * 100,
            "trades": len(trades), "unclosed_trades": sum(bool(t.get("is_open")) for t in trades),
            "forced_exit_trades": sum(t.get("exit_reason") == "force_exit" for t in trades),
            "funding_fees_sum": sum(float(t.get("funding_fees") or 0) for t in trades),
            "side_breakdown": sides,
            "backtest_start": data.get("backtest_start"), "backtest_end": data.get("backtest_end"),
            "ledger_reconciled": True}


def screening_reasons(results: dict, gate: dict) -> list[str]:
    reasons = []
    for window in ("early", "late"):
        row = results.get(window)
        if row is None:
            reasons.append(f"{window}: missing result")
            continue
        if row["net_return_pct"] <= 0:
            reasons.append(f"{window}: non-positive return")
        if row["max_drawdown_pct"] > gate["max_drawdown_fraction_max"] * 100:
            reasons.append(f"{window}: drawdown exceeds gate")
        if row["unclosed_trades"]:
            reasons.append(f"{window}: unclosed trades")
    late = results.get("late") or {}
    if late.get("trades", 0) < gate["late_trades_min"]:
        reasons.append("late: insufficient trades")
    if not late.get("all_wins_no_losses") and (late.get("profit_factor") or 0) < gate["late_profit_factor_min"]:
        reasons.append("late: profit factor below gate")
    return reasons


def regime_breakdown(data: dict, out: Path, pair: str) -> dict:
    """Descriptive prior-completed-hour ER20, not a tuned allocation model."""
    import pandas as pd

    stem = pair.replace("/", "_").replace(":", "_")
    path = next((out / "data").rglob(f"{stem}-1h-futures.feather"))
    candles = pd.read_feather(path).sort_values("date")
    candles["available_at"] = pd.to_datetime(candles["date"], utc=True) + pd.Timedelta(hours=1)
    denominator = candles["close"].diff().abs().rolling(20).sum()
    candles["er20"] = candles["close"].diff(20).abs() / denominator.replace(0, float("nan"))
    trades = pd.DataFrame(data.get("trades", []))
    if trades.empty:
        return {}
    trades["entry_at"] = pd.to_datetime(trades["open_date"], utc=True)
    merged = pd.merge_asof(trades.sort_values("entry_at"), candles[["available_at", "er20"]],
                          left_on="entry_at", right_on="available_at", direction="backward")
    merged["regime"] = "mixed"
    merged.loc[merged["er20"] <= .25, "regime"] = "range"
    merged.loc[merged["er20"] >= .30, "regime"] = "directional"
    merged.loc[merged["er20"].isna(), "regime"] = "unknown"
    return {name: {"trades": len(group), "net_pnl_usdt": float(group["profit_abs"].sum())}
            for name, group in merged.groupby("regime")}


def lookahead_status(path: Path) -> dict:
    if not path.exists():
        return {"status": "unverified", "reason": "No bias report; possibly insufficient triggered trades"}
    with path.open() as stream:
        rows = list(csv.DictReader(stream))
    if not rows:
        return {"status": "unverified", "reason": "Empty bias report"}
    if any(str(row.get("has_bias", "")).lower() == "true" for row in rows):
        return {"status": "failed", "rows": rows}
    if all(str(row.get("has_bias", "")).lower() == "false" for row in rows):
        return {"status": "no_bias_detected_in_tested_signals", "rows": rows}
    return {"status": "unverified", "rows": rows}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()
    require_hosted_runner()
    out = args.out_dir.resolve()
    if out.exists():
        raise RuntimeError("Output must be a new directory; never mix different runs")
    out.mkdir(parents=True)
    protocol = json.loads((STUDY / "protocol.json").read_text())
    write_json(out / "protocol.json", protocol)
    write_json(out / "environment.json", {"commit": os.environ.get("GITHUB_SHA"),
               "run_id": os.environ.get("GITHUB_RUN_ID"), "runner": os.environ.get("RUNNER_ENVIRONMENT"),
               "started_at": datetime.now(timezone.utc).isoformat(),
               "protocol_sha256": hashlib.sha256((STUDY / "protocol.json").read_bytes()).hexdigest()})
    freeze = subprocess.run([sys.executable, "-m", "pip", "freeze"], capture_output=True, text=True, check=True)
    (out / "dependencies.txt").write_text(freeze.stdout)
    fetch_upstream(out, protocol)
    commands: list[dict] = []
    summary = {"study": protocol["id"], "live_weight": 0, "certified": False,
               "window_warning": protocol["window_warning"], "candidates": [], "original_controls": [], "errors": []}
    configs = {}
    for pair in ("BTC/USDT:USDT", "XAU/USDT:USDT"):
        symbol = pair.split("/")[0]
        config = out / f"config_{symbol}.json"
        write_json(config, make_config(pair))
        configs[pair] = config
        timeframes = ["1m", "5m", "1h"] if symbol == "BTC" else ["5m", "1h"]
        download = ["download-data", "-c", str(config), "--userdir", str(out),
                    "--datadir", str(out / "data"), "--timeframes", *timeframes,
                    "--timerange", download_timerange(protocol)]
        if not run_command(download, out / "logs" / f"download_{symbol}.log", commands):
            summary["errors"].append(f"{symbol}: data download failed")
            continue
        try:
            audit = inspect_data(out, pair, timeframes, protocol)
            write_json(out / f"data_audit_{symbol}.json", audit)
        except ValueError as error:
            summary["errors"].append(str(error))
            print(f"DATA GATE: {error}", flush=True)
            continue
    if summary["errors"]:
        write_json(out / "summary.json", summary)
        return 1

    def common(candidate, command):
        return [command, "-c", str(configs[candidate["pair"]]), "--userdir", str(out),
                "--datadir", str(out / "data"), "--strategy-path", str(out / "strategies"),
                "-s", candidate["strategy"], "-i", candidate["timeframe"]]

    def backtest(candidate, window, fee, suffix):
        label = f"{candidate['id']}_{window}_{suffix}"
        directory = out / "results" / label
        directory.mkdir(parents=True)
        cmd = common(candidate, "backtesting") + [
            "--timerange", protocol["windows"][window], "--cache", "none", "--fee", str(fee),
            "--timeframe-detail", protocol["execution"]["detail_timeframes"][candidate["timeframe"]],
            "--export", "trades", "--backtest-directory", str(directory)]
        if not run_command(cmd, out / "logs" / f"{label}.log", commands):
            summary["errors"].append(f"{label}: backtest failed")
            return None
        try:
            data = load_result(directory, candidate["strategy"])
            metrics = summarize_result(data)
            metrics["entry_regime_breakdown"] = regime_breakdown(data, out, candidate["pair"])
            write_json(directory / "metrics.json", metrics)
            return metrics
        except (KeyError, ValueError) as error:
            summary["errors"].append(f"{label}: {error}")
            return None

    originals = [
        {"id": "btc-volatility-original", "pair": "BTC/USDT:USDT", "strategy": "VolatilitySystem", "timeframe": "1h"},
        {"id": "xau-volatility-original", "pair": "XAU/USDT:USDT", "strategy": "VolatilitySystem", "timeframe": "1h"},
        {"id": "btc-freinforced-original", "pair": "BTC/USDT:USDT", "strategy": "FReinforcedStrategy", "timeframe": "5m"},
    ]
    for candidate in [*originals, *protocol["candidates"]]:
        row = dict(candidate)
        row["results"] = {window: backtest(candidate, window, protocol["execution"]["base_cost_per_side"], "base") for window in protocol["windows"]}
        if candidate in originals:
            row["status"] = "reference_only_not_risk_matched"
            if candidate["strategy"] == "FReinforcedStrategy":
                row["source_note"] = "Compatibility control: three missing IntParameter space annotations; unchanged numeric defaults/signals, no hyperopt. See upstream_sources.json and upstream_pristine."
            summary["original_controls"].append(row)
        else:
            row["screen_reasons"] = screening_reasons(row["results"], protocol["screen_gate"])
            if any(value is None for value in row["results"].values()):
                row["status"] = "blocked_execution"
            else:
                row["status"] = "rejected_screen" if row["screen_reasons"] else "historical_metrics_pass_pending_validation"
            row["live_weight"] = 0
            # Bias checks are independent of profitability; failed strategies
            # still need a truthful explanation of their evidence quality.
            lookahead_csv = out / f"lookahead_{candidate['id']}.csv"
            lookahead = common(candidate, "lookahead-analysis") + [
                "--timerange", "20260312-20260908", "--fee", str(protocol["execution"]["base_cost_per_side"]),
                "--minimum-trade-amount", "10", "--targeted-trade-amount", "30",
                "--lookahead-analysis-exportfilename", str(lookahead_csv)]
            ok = run_command(lookahead, out / "logs" / f"lookahead_{candidate['id']}.log", commands)
            row["lookahead"] = lookahead_status(lookahead_csv) if ok else {"status": "command_failed"}
            recursive = common(candidate, "recursive-analysis") + [
                "--timerange", "20260312-20260908", "--startup-candle", *map(str, recursive_startups(protocol))]
            ok = run_command(recursive, out / "logs" / f"recursive_{candidate['id']}.log", commands)
            row["recursive"] = "completed_requires_table_review" if ok else "command_failed"
            row["recursive_probe_note"] = {"requested": protocol["validation"]["recursive_startups"],
                "effective": recursive_startups(protocol), "reason": "OKX native startup cap 1499; trading-strategy startup counts unchanged. Diagnostic starts after available warmup."}
            if not row["screen_reasons"]:
                row["stress"] = {window: backtest(candidate, window, protocol["execution"]["stress_cost_per_side"], "stress") for window in protocol["windows"]}
                row["status"] = "historical_metrics_pass_requires_manual_stress_bias_and_forward_review"
            summary["candidates"].append(row)
        write_json(out / "summary.json", summary)
    write_json(out / "summary.json", summary)
    print(json.dumps(summary, indent=2), flush=True)
    return 1 if summary["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
