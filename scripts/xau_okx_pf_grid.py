#!/usr/bin/env python3
"""Grid-search XAU ActionZone configurations on OKX venue data.

The selection dataset is OKX XAUT-USDT spot because XAU-USDT-SWAP only starts
in April 2025.  The native swap is retained as a cross-check for the finalists.
The proxy relationship and its limitations are documented in
``docs/research/venue_data_revalidation_2026-07-26.md``.

This is deliberately a research harness, not a config writer.  It resolves the
repo's current live XAU config, preserves its costs, sizing, stop and ROI
settings, and varies only the six long/short + D1 shapes and the structural
entry/exit parameters listed by :func:`grid_items`.

Usage::

    PYTHONPATH=. python3 scripts/xau_okx_pf_grid.py \
        --out-dir core/xau_okx_pf_grid --workers 2
"""
from __future__ import annotations

import argparse
import itertools
import json
import os
import re
import sys
from multiprocessing import Pool
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pandas as pd

from scripts.validate_on_venue_data import _bundle_for, _okx_frame
from scripts.xau_harness import (
    PROXY,
    SYMBOL,
    VARIANTS,
    deployed_variant_name,
    gated_sides,
    prepare,
)
from xauby.backtest.service import run_replay_from_bundle
from xauby.observability.replay_validation import load_bot_config


SPLIT_RATIO = 0.70
WARMUP_BARS = 300
MIN_IS_TRADES = 40
MIN_OOS_TRADES = 18
N_FOLDS = 5

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


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def grid_items() -> List[Dict[str, Any]]:
    """Return the pre-declared 432-cell grid, including both required anchors."""
    slope_shapes = ((False, 3), (True, 3), (True, 5))
    items: List[Dict[str, Any]] = []
    for variant, shape in VARIANTS.items():
        for ap, fresh, slope, thrust, bear_cross in itertools.product(
            (1, 2),
            (1, 2, 3),
            slope_shapes,
            (0.0, 0.5),
            (False, True),
        ):
            require_slope, slope_bars = slope
            structural = {
                "ap_smoothing": ap,
                "require_fresh_zone": True,
                "fresh_zone_window": fresh,
                "require_slow_slope": require_slope,
                "slow_slope_bars": slope_bars,
                "entry_thrust_min": thrust,
                "exit_on_bear_cross": bear_cross,
            }
            override = {**shape, **structural}
            combo_id = (
                f"{_slug(variant)}__ap{ap}_fz{fresh}_"
                f"sl{(slope_bars if require_slope else 0)}_"
                f"th{thrust:g}_bx{int(bear_cross)}"
            )
            live_entry_shape = (
                ap == 2
                and fresh == 3
                and require_slope
                and slope_bars == 3
                and thrust == 0.0
                and not bear_cross
            )
            items.append(
                {
                    "id": combo_id,
                    "variant": variant,
                    "override": override,
                    "anchor_live": variant == "L:D1off S:D1on" and live_entry_shape,
                    "anchor_long_only_d1": variant == "long-only D1 on" and live_entry_shape,
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
        "proxy4": _okx_frame(PROXY, "4h"),
        "proxy1": _okx_frame(PROXY, "1d"),
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

    df = _G["proxy4"]
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
    label: str,
    skip_bars: int,
) -> Dict[str, Any]:
    prep = _G["prep"]
    strat = prep.variant_config(override)
    bundle = _bundle_for(SYMBOL, frame, engine_config=_G["cfg"], label=label)
    if any(gated_sides(strat)):
        bundle.df_regime = regime.reset_index(drop=True)
        bundle.regime_tf = "1d"
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
                _G["is_df"],
                _G["proxy1"],
                item["override"],
                label=PROXY,
                skip_bars=0,
            ),
            "oos": _run_frame(
                _G["oos_df"],
                _G["proxy1"],
                item["override"],
                label=PROXY,
                skip_bars=_G["oos_skip"],
            ),
        }
    except Exception as exc:  # keep the checkpoint useful when one cell fails
        return {**dict(item), "error": f"{type(exc).__name__}: {exc}"}


def _eval_crosscheck(item: Mapping[str, Any]) -> Dict[str, Any]:
    try:
        return {
            "id": item["id"],
            "proxy_full": _run_frame(
                _G["proxy4"],
                _G["proxy1"],
                item["override"],
                label=PROXY,
                skip_bars=0,
            ),
            "native_full": _run_frame(
                _G["native4"],
                _G["native1"],
                item["override"],
                label="XAU-USDT-SWAP",
                skip_bars=0,
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
            _run_frame(
                frame,
                _G["proxy1"],
                item["override"],
                label=PROXY,
                skip_bars=skip,
            )
            for frame, skip in _fold_slices(_G["proxy4"])
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="bot_config.yaml")
    parser.add_argument("--out-dir", default="core/xau_okx_pf_grid")
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--top-n", type=int, default=12)
    args = parser.parse_args()

    os.chdir(ROOT)
    out_dir = Path(args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    cfg = load_bot_config(args.config)
    prep = prepare(cfg)
    live_variant = deployed_variant_name(prep.base_strategy_config)
    if live_variant != "L:D1off S:D1on":
        raise SystemExit(
            "Live XAU shape changed; expected 'L:D1off S:D1on', "
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
        rows = list(pool.imap_unordered(_eval_grid, items, chunksize=1))
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

        crosschecks = list(pool.imap_unordered(_eval_crosscheck, finalists, chunksize=1))
        crosschecks.sort(key=lambda row: row["id"])

        fold_ids = list(
            dict.fromkeys(
                [row["id"] for row in ranked[:3]]
                + [balanced[0]["id"], live["id"], long_only["id"]]
            )
        )
        folds = list(
            pool.imap_unordered(_eval_folds, [by_id[combo_id] for combo_id in fold_ids])
        )
        folds.sort(key=lambda row: row["id"])

    split = int(data["ranges"]["proxy4"]["bars"] * SPLIT_RATIO)
    payload = {
        "protocol": {
            "selection_source": PROXY,
            "native_crosscheck": "XAU-USDT-SWAP",
            "split_ratio": SPLIT_RATIO,
            "split_bar": split,
            "warmup_bars": WARMUP_BARS,
            "warmup_traded": False,
            "minimum_trades": {"is": MIN_IS_TRADES, "oos": MIN_OOS_TRADES},
            "grid_cells": len(items),
            "variant_count": len(VARIANTS),
            "structural_cells_per_variant": len(items) // len(VARIANTS),
            "ranking": "OOS profit factor after IS/OOS positive-net and trade-count gates",
            "balanced_ranking": "maximize min(IS PF, OOS PF) under the same gates",
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
        "crosschecks": crosschecks,
        "folds": folds,
        "grid": rows,
    }
    result_path = out_dir / "results.json"
    result_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")

    print(f"OKX XAU PF grid: {len(items)} cells, {len(ranked)} passed gates")
    print(_summary_line("LIVE", live))
    print(_summary_line("LONG-ONLY + D1", long_only))
    print(_summary_line("HIGHEST OOS PF", ranked[0]))
    print(_summary_line("HIGHEST MIN(IS,OOS) PF", balanced[0]))
    print(f"Wrote {result_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
