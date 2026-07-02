"""A/B comparison of CDC Action Zone variants on one candle set.

Runs the baseline (runtime-merged config, exactly what live/replay uses) and a
set of variants — minimal-ROI ladders, the slow-slope entry filter, and their
combos — over the SAME candles, then prints a ranked table so profit / win
rate / drawdown deltas are attributable to the config change alone.

Usage (on a machine that can reach the backtest data source, e.g. the VPS):

    python scripts/compare_cdc_ab.py --symbol XAUUSDT
    python scripts/compare_cdc_ab.py --symbol XAUUSDT --roi "0:8,1440:5,2880:3"
    python scripts/compare_cdc_ab.py --symbol XAUUSDT --csv core/backtest_cache/PAXGUSDT_4h.csv

`--csv` replays offline from a CSV with columns
open_time/open/high/low/close/volume (or timestamp/...), skipping the network.
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import yaml

from xauby.backtest.service import (
    BacktestReplayBundle,
    load_backtest_replay_bundle,
    run_replay_from_bundle,
)


# Candidate minimal-ROI ladders for a 4h trend strategy on gold.
# Keys are position age in MINUTES, values required ROI in PERCENT.
ROI_PRESETS: Dict[str, Dict[str, float]] = {
    "roi_tight": {"0": 5.0, "1440": 3.0, "2880": 1.5},
    "roi_mid": {"0": 8.0, "1440": 5.0, "2880": 3.0, "5760": 1.5},
    "roi_wide": {"0": 12.0, "2880": 8.0, "5760": 4.0, "11520": 2.0},
}


def parse_roi(spec: str) -> Dict[str, float]:
    """Parse "0:8,1440:5,2880:3" into a minimal_roi dict."""
    table: Dict[str, float] = {}
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        age, _, roi = part.partition(":")
        table[str(float(age))] = float(roi)
    if not table:
        raise ValueError(f"empty minimal_roi spec: {spec!r}")
    return table


def build_variants(custom_roi: Optional[Dict[str, float]]) -> List[Tuple[str, Dict[str, Any]]]:
    variants: List[Tuple[str, Dict[str, Any]]] = [("baseline", {})]
    roi_tables = (
        {"roi_custom": custom_roi} if custom_roi else dict(ROI_PRESETS)
    )
    variants.append(("slope", {"require_slow_slope": True}))
    for name, table in roi_tables.items():
        variants.append((name, {"minimal_roi": table}))
        variants.append((f"{name}+slope", {"minimal_roi": table, "require_slow_slope": True}))
    return variants


def bundle_from_csv(
    csv_path: str,
    symbol: str,
    *,
    strategy_name: Optional[str],
    engine_config: Dict[str, Any],
    primary_timeframe: Optional[str],
) -> BacktestReplayBundle:
    """Build a replay bundle from a local CSV instead of the data service."""
    from xauby.backtest.data import normalize_ohlcv_df, prepare_raw_dataframe
    from xauby.backtest.service import (
        _prepare_backtest_config,
        _resolve_fee_pct,
        resolve_strategy_name,
    )

    sym = symbol.upper().replace("_", "")
    strat_name = strategy_name or resolve_strategy_name(sym, engine_config, None)
    bot_state = {"primary_timeframe": primary_timeframe or ""} if primary_timeframe else None
    merged_cfg, strat_cfg, primary_tf, regime_tf, use_d1, config_used = _prepare_backtest_config(
        sym, strat_name, engine_config, bot_state
    )
    raw = pd.read_csv(csv_path)
    df = prepare_raw_dataframe(normalize_ohlcv_df(raw))
    if df.empty:
        raise SystemExit(f"No usable candles in {csv_path}")
    return BacktestReplayBundle(
        symbol=sym,
        strategy_name=strat_name,
        merged_cfg=merged_cfg,
        strat_cfg=dict(strat_cfg),
        primary_tf=primary_tf,
        regime_tf=None,  # CSV mode: no regime frame; D1 filter needs live data
        use_d1=False,
        fee_pct=_resolve_fee_pct(engine_config, merged_cfg.get("trading") or {}),
        config_used=dict(config_used),
        df=df,
        df_regime=None,
        period_start=str(df.iloc[0].get("timestamp", "")),
        period_end=str(df.iloc[-1].get("timestamp", "")),
        bars=len(df),
        regime_bars=0,
        data_symbol=sym,
        used_data_proxy=False,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="A/B compare CDC Action Zone variants")
    parser.add_argument("--symbol", default="XAUUSDT")
    parser.add_argument("--strategy", default=None)
    parser.add_argument("--config", default="bot_config.yaml")
    parser.add_argument("--timeframe", dest="primary_timeframe", default=None)
    parser.add_argument("--roi", default=None, help='Custom ladder "age_min:roi_pct,..." (replaces presets)')
    parser.add_argument("--csv", default=None, help="Offline OHLCV CSV instead of the data service")
    parser.add_argument("--force-download", action="store_true")
    parser.add_argument("--only", default=None, help="Comma-separated variant names to run (default: all)")
    parser.add_argument("--json-out", default=None, help="Also write rows as JSON to this path")
    args = parser.parse_args()

    with open(args.config, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}

    if args.csv:
        bundle = bundle_from_csv(
            args.csv,
            args.symbol,
            strategy_name=args.strategy,
            engine_config=cfg,
            primary_timeframe=args.primary_timeframe,
        )
    else:
        bundle = load_backtest_replay_bundle(
            args.symbol,
            strategy_name=args.strategy,
            engine_config=cfg,
            bot_state=(
                {"primary_timeframe": args.primary_timeframe} if args.primary_timeframe else None
            ),
            force_download=args.force_download,
        )

    print(
        f"Symbol {bundle.symbol} ({bundle.data_symbol}) | strategy {bundle.strategy_name} | "
        f"TF {bundle.primary_tf} | bars {bundle.bars} | {bundle.period_start} -> {bundle.period_end}"
    )

    custom_roi = parse_roi(args.roi) if args.roi else None
    variants = build_variants(custom_roi)
    if args.only:
        wanted = {v.strip() for v in args.only.split(",") if v.strip()}
        variants = [(n, o) for n, o in variants if n in wanted]
        if not variants:
            raise SystemExit(f"--only matched no variants: {args.only}")
    rows: List[Dict[str, Any]] = []
    for name, override in variants:
        result = run_replay_from_bundle(bundle, strat_cfg_override=override)
        if not result.meta.run_ok:
            print(f"  {name}: FAILED ({result.meta.error})")
            continue
        s = result.stats
        rows.append(
            {
                "variant": name,
                "profit%": float(s.get("net_profit_pct", 0.0) or 0.0),
                "winrate%": float(s.get("win_rate", 0.0) or 0.0),
                "trades": int(s.get("total_trades", 0) or 0),
                "PF": float(s.get("profit_factor", 0.0) or 0.0),
                "maxDD%": float(s.get("max_drawdown_pct", 0.0) or 0.0),
            }
        )

    if not rows:
        raise SystemExit("No variant produced results")

    if args.json_out:
        import json
        with open(args.json_out, "w", encoding="utf-8") as f:
            json.dump(rows, f)

    baseline = next((r for r in rows if r["variant"] == "baseline"), rows[0])
    rows.sort(key=lambda r: r["profit%"], reverse=True)

    header = f"{'variant':18s} {'profit%':>9s} {'winrate%':>9s} {'trades':>7s} {'PF':>6s} {'maxDD%':>8s}  vs baseline"
    print(header)
    print("-" * len(header))
    for r in rows:
        d_profit = r["profit%"] - baseline["profit%"]
        d_wr = r["winrate%"] - baseline["winrate%"]
        marker = " (baseline)" if r["variant"] == "baseline" else f"  {d_profit:+.2f}pp / {d_wr:+.2f}pp"
        print(
            f"{r['variant']:18s} {r['profit%']:>9.2f} {r['winrate%']:>9.2f} "
            f"{r['trades']:>7d} {r['PF']:>6.2f} {r['maxDD%']:>8.2f}{marker}"
        )

    best = rows[0]
    if best["variant"] != "baseline":
        print(
            f"\nBest: {best['variant']} "
            f"(profit {baseline['profit%']:.2f}% -> {best['profit%']:.2f}%, "
            f"win rate {baseline['winrate%']:.2f}% -> {best['winrate%']:.2f}%)"
        )
        print("Apply by adding the variant's keys under strategy.config.cdc_action_zone in bot_config.yaml.")
    else:
        print("\nBaseline wins on this data — keep the current config.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
