#!/usr/bin/env python3
"""Grid-search BTC SuperTrend configurations on native OKX swap data.

The protocol mirrors ``scripts/xau_okx_pf_grid.py``:

* resolve the current live config through the production config resolver;
* retain live costs, sizing, and exit semantics;
* compare the six long/short and D1-gate shapes, including explicit live and
  long-only+D1 anchors;
* select on a pre-declared 70/30 IS/OOS split with a past-only warmup;
* rank both highest OOS PF and highest ``min(IS PF, OOS PF)``;
* replay finalists on the full native history and five chronological folds.

This harness writes research artifacts only.  It never edits a live config.

Usage::

    PYTHONPATH=. python3 scripts/btc_supertrend_okx_pf_grid.py \
        --out-dir core/btc_supertrend_okx_pf_grid --workers 4
"""
from __future__ import annotations

import argparse
import itertools
import json
import os
import re
import sys
from dataclasses import dataclass
from multiprocessing import Pool
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pandas as pd

from scripts.validate_on_venue_data import _bundle_for, _okx_frame
from xauby.backtest.service import (
    _prepare_backtest_config,
    resolve_strategy_name,
    run_replay_from_bundle,
)
from xauby.backtest.walkforward import resolve_variant
from xauby.observability.replay_validation import load_bot_config


SYMBOL = "BTCUSDT"
STRATEGY = "supertrend_ema200"
VENUE = "BTC-USDT-SWAP"
SPLIT_RATIO = 0.70
WARMUP_BARS = 300
MIN_IS_TRADES = 30
MIN_OOS_TRADES = 10
N_FOLDS = 5

D1_KEYS = (
    "use_d1_regime_filter",
    "use_d1_regime_filter_long",
    "use_d1_regime_filter_short",
)

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

_G: Dict[str, Any] = {}


def d1_gate(shared: bool, long_gated: bool, short_gated: bool) -> Dict[str, Any]:
    """Declare the complete D1 control group for one variant."""
    return dict(zip(D1_KEYS, (shared, long_gated, short_gated)))


VARIANTS: Dict[str, Dict[str, Any]] = {
    "long-only D1 off": {"enable_short": False, **d1_gate(False, False, False)},
    "long-only D1 on": {"enable_short": False, **d1_gate(True, True, True)},
    "long+short D1 off": {"enable_short": True, **d1_gate(False, False, False)},
    "long+short D1 on": {"enable_short": True, **d1_gate(True, True, True)},
    "L:D1on S:D1off": {"enable_short": True, **d1_gate(True, True, False)},
    "L:D1off S:D1on": {"enable_short": True, **d1_gate(True, False, True)},
}


def gated_sides(spec: Mapping[str, Any]) -> tuple[bool, bool]:
    """Return ``(long_gated, short_gated)`` for a complete strategy config."""
    shared = bool(spec.get("use_d1_regime_filter"))
    long_side = spec.get("use_d1_regime_filter_long")
    short_side = spec.get("use_d1_regime_filter_short")
    return (
        shared if long_side is None else bool(long_side),
        shared if short_side is None else bool(short_side),
    )


def _shape(spec: Mapping[str, Any]) -> tuple[bool, bool, bool | None]:
    shorts = bool(spec.get("enable_short"))
    long_gated, short_gated = gated_sides(spec)
    return shorts, long_gated, short_gated if shorts else None


def variant_name(spec: Mapping[str, Any]) -> str | None:
    shape = _shape(spec)
    for name, declared in VARIANTS.items():
        if _shape(declared) == shape:
            return name
    return None


@dataclass(frozen=True)
class Prepared:
    engine_config: Dict[str, Any]
    base_strategy_config: Dict[str, Any]
    primary_timeframe: str

    def variant_config(self, overrides: Mapping[str, Any]) -> Dict[str, Any]:
        return resolve_variant(self.base_strategy_config, overrides)


def prepare(engine_config: Mapping[str, Any]) -> Prepared:
    cfg = dict(engine_config)
    name = resolve_strategy_name(SYMBOL, cfg, None)
    if name != STRATEGY:
        raise RuntimeError(f"{SYMBOL} resolves to {name!r}, expected {STRATEGY!r}")
    merged, strat, primary_tf, _regime_tf, _use_d1, _used = _prepare_backtest_config(
        SYMBOL, name, cfg, None
    )
    return Prepared(
        engine_config=dict(merged),
        base_strategy_config=dict(strat),
        primary_timeframe=primary_tf,
    )


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def grid_items() -> List[Dict[str, Any]]:
    """Return the pre-declared 576-cell grid and required anchors."""
    items: List[Dict[str, Any]] = []
    for variant, shape in VARIANTS.items():
        for mult, atr, stop, trail, ema_exit in itertools.product(
            (2.5, 3.0, 3.5, 4.0),
            (10, 14),
            (2.0, 2.5, 3.0),
            (1.5, 2.0),
            (True, False),
        ):
            structural = {
                "supertrend_mult": mult,
                "atr_period": atr,
                "sl_atr_mult": stop,
                "trailing_atr_mult": trail,
                "exit_on_ema_loss": ema_exit,
                # Fixed at the certified/live values, not optimized.
                "ema_period": 200,
                "confirm_bars": 1,
                "entry_on_flip_only": True,
                "exit_on_supertrend_flip": True,
            }
            override = {**shape, **structural}
            combo_id = (
                f"{_slug(variant)}__m{mult:g}_a{atr}_sl{stop:g}_"
                f"tr{trail:g}_ex{int(ema_exit)}"
            )
            live_structure = (
                mult == 4.0
                and atr == 10
                and stop == 3.0
                and trail == 2.0
                and ema_exit
            )
            items.append(
                {
                    "id": combo_id,
                    "variant": variant,
                    "override": override,
                    "anchor_live": variant == "long+short D1 off" and live_structure,
                    "anchor_long_only_d1": variant == "long-only D1 on" and live_structure,
                }
            )
    return items


def _date_range(df: pd.DataFrame) -> Dict[str, Any]:
    return {
        "bars": len(df),
        "start": str(pd.to_datetime(int(df["open_time"].iloc[0]), unit="ms")),
        "end": str(pd.to_datetime(int(df["open_time"].iloc[-1]), unit="ms")),
    }


def _write_frames(out_dir: Path) -> Dict[str, Any]:
    frames = {
        "native4": _okx_frame(SYMBOL, "4h"),
        "native1": _okx_frame(SYMBOL, "1d"),
    }
    paths: Dict[str, str] = {}
    for key, frame in frames.items():
        path = out_dir / f"{key}.csv"
        frame.to_csv(path, index=False)
        paths[key] = str(path)
    return {
        "paths": paths,
        "ranges": {key: _date_range(frame) for key, frame in frames.items()},
    }


def _init_worker(config_path: str, paths: Mapping[str, str]) -> None:
    os.chdir(ROOT)
    cfg = load_bot_config(config_path)
    prep = prepare(cfg)
    _G.clear()
    _G.update(cfg=cfg, prep=prep)
    for key, path in paths.items():
        _G[key] = pd.read_csv(path)

    df = _G["native4"]
    split = int(len(df) * SPLIT_RATIO)
    oos_lo = max(0, split - WARMUP_BARS)
    _G.update(
        split=split,
        is_df=df.iloc[:split].reset_index(drop=True),
        oos_df=df.iloc[oos_lo:].reset_index(drop=True),
        oos_skip=split - oos_lo,
    )


def _metrics(stats: Mapping[str, Any]) -> Dict[str, Any]:
    return {key: stats.get(key) for key in METRIC_KEYS}


def _run_frame(
    frame: pd.DataFrame,
    regime: pd.DataFrame,
    override: Mapping[str, Any],
    *,
    skip_bars: int,
) -> Dict[str, Any]:
    prep: Prepared = _G["prep"]
    strat = prep.variant_config(override)
    bundle = _bundle_for(
        SYMBOL,
        frame,
        engine_config=_G["cfg"],
        strategy_name=STRATEGY,
        label=VENUE,
    )
    if any(gated_sides(strat)):
        bundle.df_regime = regime.reset_index(drop=True)
        bundle.regime_tf = "1d"
        bundle.regime_bars = len(regime)
        bundle.use_d1 = True
    result = run_replay_from_bundle(
        bundle,
        strat_cfg_override=strat,
        min_bars_override=skip_bars,
    )
    if not result.meta.run_ok:
        raise RuntimeError(result.meta.error or "replay failed")
    return _metrics(result.stats or {})


def _eval_grid(item: Mapping[str, Any]) -> Dict[str, Any]:
    try:
        return {
            **dict(item),
            "is": _run_frame(
                _G["is_df"], _G["native1"], item["override"], skip_bars=0
            ),
            "oos": _run_frame(
                _G["oos_df"],
                _G["native1"],
                item["override"],
                skip_bars=_G["oos_skip"],
            ),
        }
    except Exception as exc:
        return {**dict(item), "error": f"{type(exc).__name__}: {exc}"}


def _eval_full(item: Mapping[str, Any]) -> Dict[str, Any]:
    try:
        return {
            "id": item["id"],
            "native_full": _run_frame(
                _G["native4"], _G["native1"], item["override"], skip_bars=0
            ),
        }
    except Exception as exc:
        return {"id": item["id"], "error": f"{type(exc).__name__}: {exc}"}


def _fold_slices(df: pd.DataFrame) -> Iterable[tuple[pd.DataFrame, int]]:
    segment = len(df) // N_FOLDS
    for fold in range(N_FOLDS):
        traded_start = fold * segment
        start = max(0, traded_start - WARMUP_BARS)
        end = len(df) if fold == N_FOLDS - 1 else (fold + 1) * segment
        yield df.iloc[start:end].reset_index(drop=True), traded_start - start


def _eval_folds(item: Mapping[str, Any]) -> Dict[str, Any]:
    try:
        folds = [
            _run_frame(frame, _G["native1"], item["override"], skip_bars=skip)
            for frame, skip in _fold_slices(_G["native4"])
        ]
        return {"id": item["id"], "folds": folds}
    except Exception as exc:
        return {"id": item["id"], "error": f"{type(exc).__name__}: {exc}"}


def _valid(row: Mapping[str, Any]) -> bool:
    if row.get("error"):
        return False
    ins = row["is"]
    oos = row["oos"]
    return bool(
        int(ins.get("total_trades") or 0) >= MIN_IS_TRADES
        and int(oos.get("total_trades") or 0) >= MIN_OOS_TRADES
        and float(ins.get("net_profit_pct") or 0.0) > 0.0
        and float(oos.get("net_profit_pct") or 0.0) > 0.0
    )


def _pf(row: Mapping[str, Any], side: str) -> float:
    return float((row.get(side) or {}).get("profit_factor") or 0.0)


def _rank(rows: Iterable[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    valid = [dict(row) for row in rows if _valid(row)]
    valid.sort(
        key=lambda row: (
            _pf(row, "oos"),
            min(_pf(row, "is"), _pf(row, "oos")),
            float(row["oos"].get("net_profit_pct") or 0.0),
        ),
        reverse=True,
    )
    return valid


def _balanced_rank(rows: Iterable[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    valid = [dict(row) for row in rows if _valid(row)]
    valid.sort(
        key=lambda row: (
            min(_pf(row, "is"), _pf(row, "oos")),
            _pf(row, "oos"),
            float(row["oos"].get("net_profit_pct") or 0.0),
        ),
        reverse=True,
    )
    return valid


def _anchor(rows: Iterable[Mapping[str, Any]], key: str) -> Dict[str, Any]:
    return next(dict(row) for row in rows if row.get(key))


def _summary_line(label: str, row: Mapping[str, Any]) -> str:
    if row.get("error"):
        return f"{label}: ERROR {row['error']}"
    ins, oos = row["is"], row["oos"]
    return (
        f"{label}: {row['id']} | "
        f"IS PF={float(ins['profit_factor']):.3f} net={float(ins['net_profit_pct']):+.2f}% "
        f"n={int(ins['total_trades'])} | "
        f"OOS PF={float(oos['profit_factor']):.3f} "
        f"net={float(oos['net_profit_pct']):+.2f}% n={int(oos['total_trades'])}"
    )


def _collect_with_progress(
    pool: Pool,
    fn: Any,
    items: List[Mapping[str, Any]],
    *,
    label: str,
) -> List[Dict[str, Any]]:
    total = len(items)
    rows: List[Dict[str, Any]] = []
    interval = max(1, min(48, total // 12 or 1))
    for completed, row in enumerate(
        pool.imap_unordered(fn, items, chunksize=1), start=1
    ):
        rows.append(row)
        if completed == total or completed % interval == 0:
            print(f"{label}: {completed}/{total} complete", flush=True)
    return rows


def _render_report(payload: Mapping[str, Any]) -> str:
    lines = [
        "# BTC SuperTrend OKX PF grid",
        "",
        f"Venue: `{VENUE}` 4h + 1d. Grid: {payload['protocol']['grid_cells']} cells.",
        "Selection: 70/30 chronological IS/OOS, ranked after positive-net and trade-count gates.",
        "",
    ]
    rows = [
        ("Live anchor", payload["anchors"]["live"]),
        ("Long-only + D1 anchor", payload["anchors"]["long_only_d1"]),
        ("Highest OOS PF", payload["winners"]["highest_oos_pf"]),
        ("Highest min(IS,OOS) PF", payload["winners"]["highest_min_is_oos_pf"]),
    ]
    lines.extend(
        [
            "| candidate | variant | IS PF | IS net% | IS trades | OOS PF | OOS net% | OOS trades | OOS MDD% |",
            "|---|---|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for label, row in rows:
        ins, oos = row.get("is") or {}, row.get("oos") or {}
        lines.append(
            f"| {label} | {row.get('variant', '')} | "
            f"{float(ins.get('profit_factor') or 0):.3f} | "
            f"{float(ins.get('net_profit_pct') or 0):+.2f} | "
            f"{int(ins.get('total_trades') or 0)} | "
            f"{float(oos.get('profit_factor') or 0):.3f} | "
            f"{float(oos.get('net_profit_pct') or 0):+.2f} | "
            f"{int(oos.get('total_trades') or 0)} | "
            f"{float(oos.get('max_drawdown_pct') or 0):.2f} |"
        )
    lines.extend(
        [
            "",
            "The highest-OOS row is selection-biased. The balanced row and five-fold results are the safer recommendation inputs.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="bot_config.yaml")
    parser.add_argument("--out-dir", default="core/btc_supertrend_okx_pf_grid")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--top-n", type=int, default=12)
    args = parser.parse_args()

    os.chdir(ROOT)
    out_dir = Path(args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    cfg = load_bot_config(args.config)
    prep = prepare(cfg)
    live_variant = variant_name(prep.base_strategy_config)
    if live_variant != "long+short D1 off":
        raise SystemExit(
            "Live BTC shape changed; expected 'long+short D1 off', "
            f"resolved {live_variant!r}. Update the declared anchors before running."
        )

    data = _write_frames(out_dir)
    items = grid_items()
    workers = max(1, int(args.workers))
    with Pool(
        processes=workers,
        initializer=_init_worker,
        initargs=(str(Path(args.config).resolve()), data["paths"]),
    ) as pool:
        rows = _collect_with_progress(pool, _eval_grid, items, label="grid")
        rows.sort(key=lambda row: row["id"])
        ranked = _rank(rows)
        balanced = _balanced_rank(rows)
        if not ranked:
            raise SystemExit("No grid cell passed the pre-declared validity gates")

        live = _anchor(rows, "anchor_live")
        long_only = _anchor(rows, "anchor_long_only_d1")
        selected_ids = [row["id"] for row in ranked[: args.top_n]]
        selected_ids += [balanced[0]["id"], live["id"], long_only["id"]]
        selected_ids = list(dict.fromkeys(selected_ids))
        by_id = {item["id"]: item for item in items}
        finalists = [by_id[combo_id] for combo_id in selected_ids]

        full = _collect_with_progress(pool, _eval_full, finalists, label="full")
        full.sort(key=lambda row: row["id"])

        fold_ids = list(
            dict.fromkeys(
                [row["id"] for row in ranked[:3]]
                + [balanced[0]["id"], live["id"], long_only["id"]]
            )
        )
        folds = _collect_with_progress(
            pool,
            _eval_folds,
            [by_id[combo_id] for combo_id in fold_ids],
            label="folds",
        )
        folds.sort(key=lambda row: row["id"])

    split = int(data["ranges"]["native4"]["bars"] * SPLIT_RATIO)
    payload = {
        "protocol": {
            "selection_source": VENUE,
            "split_ratio": SPLIT_RATIO,
            "split_bar": split,
            "warmup_bars": WARMUP_BARS,
            "warmup_traded": False,
            "minimum_trades": {"is": MIN_IS_TRADES, "oos": MIN_OOS_TRADES},
            "grid_cells": len(items),
            "variant_count": len(VARIANTS),
            "structural_cells_per_variant": len(items) // len(VARIANTS),
            "ranking": "OOS PF after positive-net and trade-count gates",
            "balanced_ranking": "maximize min(IS PF, OOS PF) under same gates",
            "workers": workers,
        },
        "data": data["ranges"],
        "live_variant": live_variant,
        "anchors": {"live": live, "long_only_d1": long_only},
        "winners": {
            "highest_oos_pf": ranked[0],
            "highest_min_is_oos_pf": balanced[0],
        },
        "top_oos_pf": ranked[: max(20, args.top_n)],
        "valid_cells": len(ranked),
        "failed_cells": sum(1 for row in rows if row.get("error")),
        "full_history": full,
        "folds": folds,
        "grid": rows,
    }
    result_path = out_dir / "results.json"
    result_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    report_path = out_dir / "report.md"
    report_path.write_text(_render_report(payload) + "\n", encoding="utf-8")

    print(f"OKX BTC SuperTrend PF grid: {len(items)} cells, {len(ranked)} passed gates")
    print(_summary_line("LIVE", live))
    print(_summary_line("LONG-ONLY + D1", long_only))
    print(_summary_line("HIGHEST OOS PF", ranked[0]))
    print(_summary_line("HIGHEST MIN(IS,OOS) PF", balanced[0]))
    print(f"Wrote {result_path}")
    print(f"Wrote {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
