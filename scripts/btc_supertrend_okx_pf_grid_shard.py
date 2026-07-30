#!/usr/bin/env python3
"""Parallel GitHub-runner orchestration for the BTC SuperTrend OKX grid.

The research protocol remains in ``btc_supertrend_okx_pf_grid``.  This wrapper
only splits its pre-declared cells across independent runners, verifies that the
shards form one exact, duplicate-free grid, and runs the same finalist checks.
"""
from __future__ import annotations

import argparse
import json
import os
from multiprocessing import Pool
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping

from scripts import btc_supertrend_okx_pf_grid as grid


def data_bundle(data_dir: Path) -> Dict[str, Any]:
    paths = {
        "native4": str((data_dir / "native4.csv").resolve()),
        "native1": str((data_dir / "native1.csv").resolve()),
    }
    missing = [path for path in paths.values() if not Path(path).is_file()]
    if missing:
        raise RuntimeError(f"missing prepared data: {missing}")
    import pandas as pd

    frames = {key: pd.read_csv(path) for key, path in paths.items()}
    return {
        "paths": paths,
        "ranges": {key: grid._date_range(frame) for key, frame in frames.items()},
    }


def shard_items(items: Iterable[Mapping[str, Any]], index: int, count: int):
    if count < 1:
        raise ValueError("shard count must be positive")
    if index < 0 or index >= count:
        raise ValueError(f"shard index {index} outside [0, {count})")
    return list(items)[index::count]


def verify_shard_rows(rows: Iterable[Mapping[str, Any]]) -> list[Dict[str, Any]]:
    materialized = [dict(row) for row in rows]
    expected = {item["id"] for item in grid.grid_items()}
    ids = [str(row.get("id") or "") for row in materialized]
    duplicates = sorted({combo_id for combo_id in ids if ids.count(combo_id) > 1})
    actual = set(ids)
    if duplicates or actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise RuntimeError(
            "invalid shard union: "
            f"duplicates={duplicates[:5]} missing={missing[:5]} extra={extra[:5]}"
        )
    materialized.sort(key=lambda row: row["id"])
    return materialized


def cmd_fetch(out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = grid._write_frames(out_dir)
    (out_dir / "data.json").write_text(
        json.dumps(payload["ranges"], indent=2), encoding="utf-8"
    )
    print(f"prepared OKX data in {out_dir}")


def cmd_grid(
    *,
    config: Path,
    data_dir: Path,
    out_dir: Path,
    workers: int,
    shard_index: int,
    shard_count: int,
) -> None:
    data = data_bundle(data_dir)
    selected = shard_items(grid.grid_items(), shard_index, shard_count)
    out_dir.mkdir(parents=True, exist_ok=True)
    with Pool(
        processes=max(1, workers),
        initializer=grid._init_worker,
        initargs=(str(config.resolve()), data["paths"]),
    ) as pool:
        rows = grid._collect_with_progress(
            pool, grid._eval_grid, selected, label=f"shard-{shard_index}"
        )
    rows.sort(key=lambda row: row["id"])
    payload = {
        "shard_index": shard_index,
        "shard_count": shard_count,
        "cells": len(rows),
        "rows": rows,
    }
    path = out_dir / f"grid-shard-{shard_index:02d}.json"
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    print(f"wrote {path} ({len(rows)} cells)")


def _load_shard_rows(shards_dir: Path) -> list[Dict[str, Any]]:
    paths = sorted(shards_dir.rglob("grid-shard-*.json"))
    if not paths:
        raise RuntimeError(f"no grid shard JSON found below {shards_dir}")
    rows = []
    for path in paths:
        payload = json.loads(path.read_text(encoding="utf-8"))
        rows.extend(payload.get("rows") or [])
    return verify_shard_rows(rows)


def cmd_finalize(
    *,
    config: Path,
    data_dir: Path,
    shards_dir: Path,
    out_dir: Path,
    workers: int,
    top_n: int,
) -> None:
    data = data_bundle(data_dir)
    rows = _load_shard_rows(shards_dir)
    ranked = grid._rank(rows)
    balanced = grid._balanced_rank(rows)
    if not ranked:
        raise RuntimeError("no grid cell passed the pre-declared validity gates")

    live = grid._anchor(rows, "anchor_live")
    long_only = grid._anchor(rows, "anchor_long_only_d1")
    items = grid.grid_items()
    by_id = {item["id"]: item for item in items}
    selected_ids = [row["id"] for row in ranked[:top_n]]
    selected_ids += [balanced[0]["id"], live["id"], long_only["id"]]
    selected_ids = list(dict.fromkeys(selected_ids))
    finalists = [by_id[combo_id] for combo_id in selected_ids]

    fold_ids = list(
        dict.fromkeys(
            [row["id"] for row in ranked[:3]]
            + [balanced[0]["id"], live["id"], long_only["id"]]
        )
    )
    with Pool(
        processes=max(1, workers),
        initializer=grid._init_worker,
        initargs=(str(config.resolve()), data["paths"]),
    ) as pool:
        full = grid._collect_with_progress(
            pool, grid._eval_full, finalists, label="full"
        )
        folds = grid._collect_with_progress(
            pool,
            grid._eval_folds,
            [by_id[combo_id] for combo_id in fold_ids],
            label="folds",
        )
    full.sort(key=lambda row: row["id"])
    folds.sort(key=lambda row: row["id"])

    cfg = grid.load_bot_config(str(config))
    live_variant = grid.variant_name(grid.prepare(cfg).base_strategy_config)
    split = int(data["ranges"]["native4"]["bars"] * grid.SPLIT_RATIO)
    payload = {
        "protocol": {
            "selection_source": grid.VENUE,
            "split_ratio": grid.SPLIT_RATIO,
            "split_bar": split,
            "warmup_bars": grid.WARMUP_BARS,
            "warmup_traded": False,
            "minimum_trades": {
                "is": grid.MIN_IS_TRADES,
                "oos": grid.MIN_OOS_TRADES,
            },
            "grid_cells": len(items),
            "variant_count": len(grid.VARIANTS),
            "structural_cells_per_variant": len(items) // len(grid.VARIANTS),
            "ranking": "OOS PF after positive-net and trade-count gates",
            "balanced_ranking": "maximize min(IS PF, OOS PF) under same gates",
            "workers": max(1, workers),
            "execution_sharded": True,
        },
        "data": data["ranges"],
        "live_variant": live_variant,
        "anchors": {"live": live, "long_only_d1": long_only},
        "winners": {
            "highest_oos_pf": ranked[0],
            "highest_min_is_oos_pf": balanced[0],
        },
        "top_oos_pf": ranked[: max(20, top_n)],
        "valid_cells": len(ranked),
        "failed_cells": sum(1 for row in rows if row.get("error")),
        "full_history": full,
        "folds": folds,
        "grid": rows,
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "results.json").write_text(
        json.dumps(payload, indent=2, default=str), encoding="utf-8"
    )
    (out_dir / "report.md").write_text(
        grid._render_report(payload) + "\n", encoding="utf-8"
    )
    print(grid._summary_line("LIVE", live))
    print(grid._summary_line("LONG-ONLY + D1", long_only))
    print(grid._summary_line("HIGHEST OOS PF", ranked[0]))
    print(grid._summary_line("HIGHEST MIN(IS,OOS) PF", balanced[0]))
    print(f"wrote final artifacts in {out_dir}")


def cmd_summarize(
    *,
    config: Path,
    data_dir: Path,
    shards_dir: Path,
    out_dir: Path,
    top_n: int,
) -> None:
    """Merge completed grid shards without executing another replay.

    This is also the recovery path when GitHub accepts every CPU-heavy shard
    but refuses to schedule the lightweight finalist job because of an account
    billing/spending-limit gate.  The grid evidence stays complete; the output
    marks the optional full-history/fold checks as unavailable rather than
    pretending they ran.
    """
    data = data_bundle(data_dir)
    rows = _load_shard_rows(shards_dir)
    ranked = grid._rank(rows)
    balanced = grid._balanced_rank(rows)
    if not ranked:
        raise RuntimeError("no grid cell passed the pre-declared validity gates")
    live = grid._anchor(rows, "anchor_live")
    long_only = grid._anchor(rows, "anchor_long_only_d1")
    items = grid.grid_items()
    cfg = grid.load_bot_config(str(config))
    live_variant = grid.variant_name(grid.prepare(cfg).base_strategy_config)
    split = int(data["ranges"]["native4"]["bars"] * grid.SPLIT_RATIO)
    payload = {
        "protocol": {
            "selection_source": grid.VENUE,
            "split_ratio": grid.SPLIT_RATIO,
            "split_bar": split,
            "warmup_bars": grid.WARMUP_BARS,
            "warmup_traded": False,
            "minimum_trades": {
                "is": grid.MIN_IS_TRADES,
                "oos": grid.MIN_OOS_TRADES,
            },
            "grid_cells": len(items),
            "variant_count": len(grid.VARIANTS),
            "structural_cells_per_variant": len(items) // len(grid.VARIANTS),
            "ranking": "OOS PF after positive-net and trade-count gates",
            "balanced_ranking": "maximize min(IS PF, OOS PF) under same gates",
            "execution_sharded": True,
        },
        "data": data["ranges"],
        "live_variant": live_variant,
        "anchors": {"live": live, "long_only_d1": long_only},
        "winners": {
            "highest_oos_pf": ranked[0],
            "highest_min_is_oos_pf": balanced[0],
        },
        "top_oos_pf": ranked[: max(20, top_n)],
        "valid_cells": len(ranked),
        "failed_cells": sum(1 for row in rows if row.get("error")),
        "full_history": [],
        "folds": [],
        "finalist_checks": {
            "status": "not_run",
            "reason": "GitHub finalize job rejected before runner assignment by account billing/spending-limit gate",
        },
        "grid": rows,
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "results.json").write_text(
        json.dumps(payload, indent=2, default=str), encoding="utf-8"
    )
    (out_dir / "report.md").write_text(
        grid._render_report(payload) + "\n", encoding="utf-8"
    )
    print(grid._summary_line("LIVE", live))
    print(grid._summary_line("LONG-ONLY + D1", long_only))
    print(grid._summary_line("HIGHEST OOS PF", ranked[0]))
    print(grid._summary_line("HIGHEST MIN(IS,OOS) PF", balanced[0]))
    print(f"wrote merged grid artifacts in {out_dir}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("fetch", "grid", "finalize", "summarize"))
    parser.add_argument("--config", default="bot_config.yaml")
    parser.add_argument("--data-dir", default="core/btc_supertrend_okx_grid_data")
    parser.add_argument("--out-dir", default="core/btc_supertrend_okx_pf_grid")
    parser.add_argument("--shards-dir", default="core/btc_supertrend_okx_grid_shards")
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--shard-index", type=int, default=0)
    parser.add_argument("--shard-count", type=int, default=8)
    parser.add_argument("--top-n", type=int, default=12)
    args = parser.parse_args()

    os.chdir(grid.ROOT)
    if args.command == "fetch":
        cmd_fetch(Path(args.data_dir).resolve())
    elif args.command == "grid":
        cmd_grid(
            config=Path(args.config),
            data_dir=Path(args.data_dir),
            out_dir=Path(args.out_dir),
            workers=args.workers,
            shard_index=args.shard_index,
            shard_count=args.shard_count,
        )
    elif args.command == "finalize":
        cmd_finalize(
            config=Path(args.config),
            data_dir=Path(args.data_dir),
            shards_dir=Path(args.shards_dir),
            out_dir=Path(args.out_dir),
            workers=args.workers,
            top_n=args.top_n,
        )
    else:
        cmd_summarize(
            config=Path(args.config),
            data_dir=Path(args.data_dir),
            shards_dir=Path(args.shards_dir),
            out_dir=Path(args.out_dir),
            top_n=args.top_n,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
