#!/usr/bin/env python3
"""Search for the next XAU champion: config variants and rival strategies.

Two questions, one harness:

1. **Is the deployed `xauby_actionzone` config still the best shape?** Stage
   ``config`` re-runs the long-only D1-on / D1-off structural grid on fresh OKX
   data and adds the axis the 432-cell grid of 2026-07-29 never varied: the
   ``minimal_roi`` ladder. The ladder is part of what every XAU certificate
   measured, so it has never been searched — it was inherited.

2. **Could a different strategy take the slot?** Stage ``strategy`` replays every
   registered plugin over the same XAU frames on its own default config. A rival
   is not scored against ActionZone's exits: `_prepare_backtest_config` gives
   each plugin its own risk model, which is the honest question ("what does this
   strategy do on gold"), not a rigged one ("what does it do wearing CDC's
   stop-less 8% ladder").

Both stages reuse `scripts.xau_okx_pf_grid` for the IS/OOS split, warmup
accounting and validity gates, and `scripts.xau_harness` for the variant catalog
— re-declaring either is what produced the wrong figures this repo has already
had to retract.

This is a research harness. It writes JSON and prints tables; it never edits
`bot_config.yaml` or `coin_whitelist.json`.

Usage::

    PYTHONPATH=. python3 scripts/xau_champion_search.py --stage all \
        --out-dir core/xau_champion --workers 4
"""
from __future__ import annotations

import argparse
import itertools
import json
import os
import sys
from multiprocessing import Pool
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pandas as pd

from scripts.validate_on_venue_data import _bundle_for
from scripts.xau_harness import PROXY, SYMBOL, prepare
from scripts.xau_okx_pf_grid import (
    _G,
    _init_worker,
    _metrics,
    _run_frame,
    _slug,
    _valid,
    _write_frames,
)
from xauby.backtest.service import run_replay_from_bundle
from xauby.observability.replay_validation import load_bot_config
from xauby.strategies.registry import available_strategies, strategy_manifest


# --- stage 1: config variants -------------------------------------------------

# The deployed ladder, and the two questions worth asking about it: does taking
# profit earlier help, and does the ladder earn its keep at all? A stop-less
# config with no ladder exits only on the zone flip, which is the CDC-purest
# shape there is — worth measuring before assuming 8% was the right rung.
ROI_LADDERS: Dict[str, Optional[Dict[str, float]]] = {
    "live": {"0": 8.0, "1440": 5.0, "4320": 3.0},
    "tight": {"0": 5.0, "1440": 3.5, "4320": 2.0},
    "wide": {"0": 12.0, "1440": 8.0, "4320": 5.0},
    "none": None,
}

# Only the long-only shapes. The 2026-07-29 grid searched all six side/D1 shapes
# over 432 cells and every short-bearing one lost to long-only on PF, net and
# drawdown; the fresh six-variant run in this study reproduces that. Re-spending
# the compute on them here would buy nothing.
CONFIG_SHAPES: Dict[str, Dict[str, Any]] = {
    "long-only D1 on": {
        "enable_short": False,
        "use_d1_regime_filter": True,
        "use_d1_regime_filter_long": True,
        "use_d1_regime_filter_short": True,
    },
    "long-only D1 off": {
        "enable_short": False,
        "use_d1_regime_filter": False,
        "use_d1_regime_filter_long": False,
        "use_d1_regime_filter_short": False,
    },
}

# The live cell, restated so a run can prove it searched its own baseline.
LIVE_CELL = {
    "shape": "long-only D1 on",
    "ap_smoothing": 2,
    "fresh_zone_window": 3,
    "require_slow_slope": False,
    "slow_slope_bars": 3,
    "entry_thrust_min": 0.5,
    "exit_on_bear_cross": False,
    "roi": "live",
}


def config_items() -> List[Dict[str, Any]]:
    """The structural x ladder grid, with the deployed cell flagged as anchor."""
    items: List[Dict[str, Any]] = []
    for shape_name, shape in CONFIG_SHAPES.items():
        for ap, fresh, slope, thrust, roi_name in itertools.product(
            (1, 2),
            (2, 3, 4),
            ((False, 3), (True, 3)),
            (0.0, 0.5, 0.8),
            tuple(ROI_LADDERS),
        ):
            # `exit_on_bear_cross` is pinned to the deployed False. The 432-cell
            # grid of 2026-07-29 varied it across all six side/D1 shapes and it
            # was absent from every finalist; spending a 2x here would buy a
            # re-answer to a settled question at the cost of the ladder sweep.
            bear_cross = False
            require_slope, slope_bars = slope
            override = {
                **shape,
                "ap_smoothing": ap,
                "require_fresh_zone": True,
                "fresh_zone_window": fresh,
                "require_slow_slope": require_slope,
                "slow_slope_bars": slope_bars,
                "entry_thrust_min": thrust,
                "exit_on_bear_cross": bear_cross,
                "minimal_roi": ROI_LADDERS[roi_name],
            }
            cell = {
                "shape": shape_name,
                "ap_smoothing": ap,
                "fresh_zone_window": fresh,
                "require_slow_slope": require_slope,
                "slow_slope_bars": slope_bars,
                "entry_thrust_min": thrust,
                "exit_on_bear_cross": bear_cross,
                "roi": roi_name,
            }
            items.append(
                {
                    "id": (
                        f"{_slug(shape_name)}__ap{ap}_fz{fresh}_"
                        f"sl{(slope_bars if require_slope else 0)}_"
                        f"th{thrust:g}_bx{int(bear_cross)}_roi{roi_name}"
                    ),
                    "cell": cell,
                    "override": override,
                    "anchor_live": cell == LIVE_CELL,
                }
            )
    return items


def _eval_config(item: Mapping[str, Any]) -> Dict[str, Any]:
    try:
        return {
            **dict(item),
            "is": _run_frame(_G["is_df"], _G["proxy1"], item["override"],
                             label=PROXY, skip_bars=0),
            "oos": _run_frame(_G["oos_df"], _G["proxy1"], item["override"],
                              label=PROXY, skip_bars=_G["oos_skip"]),
        }
    except Exception as exc:  # one bad cell must not lose the other 287
        return {**dict(item), "error": f"{type(exc).__name__}: {exc}"}


def _eval_config_full(item: Mapping[str, Any]) -> Dict[str, Any]:
    """Full-history proxy + native-swap confirmation for a finalist."""
    try:
        return {
            "id": item["id"],
            "proxy_full": _run_frame(_G["proxy4"], _G["proxy1"], item["override"],
                                     label=PROXY, skip_bars=0),
            "native_full": _run_frame(_G["native4"], _G["native1"], item["override"],
                                      label="XAU-USDT-SWAP", skip_bars=0),
        }
    except Exception as exc:
        return {"id": item["id"], "error": f"{type(exc).__name__}: {exc}"}


# --- stage 2: rival strategies ------------------------------------------------

# Every registered plugin except ActionZone itself (the incumbent, run separately
# as the benchmark) and the short-only research plugins, which cannot hold a
# long-biased slot on their own.
SKIP_STRATEGIES = {"xauby_actionzone", "donchian_short", "rsi2_short",
                   "supertrend_short"}


def strategy_items() -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for name in available_strategies():
        if name in SKIP_STRATEGIES:
            continue
        manifest = strategy_manifest(name)
        for shorts in (False, True):
            items.append(
                {
                    "id": f"{name}__{'ls' if shorts else 'long'}",
                    "strategy": name,
                    "enable_short": shorts,
                    "maturity": manifest.get("maturity"),
                    "native_tf": manifest.get("required_timeframes"),
                    "override": {"enable_short": shorts},
                }
            )
    return items


def _run_strategy_frame(
    frame: pd.DataFrame,
    item: Mapping[str, Any],
    *,
    skip_bars: int,
    label: str,
) -> Dict[str, Any]:
    """Replay one rival on its OWN default config, XAU costs and sizing.

    Deliberately not routed through `_run_frame`: that resolves the ActionZone
    variant. A rival gets its own `strat_cfg` from `_bundle_for`, so its stop,
    trailing and sizing are the plugin's, not gold's incumbent.
    """
    bundle = _bundle_for(
        SYMBOL, frame,
        engine_config=_G["cfg"],
        strategy_name=item["strategy"],
        label=label,
    )
    strat = {**dict(bundle.strat_cfg), **dict(item["override"])}
    result = run_replay_from_bundle(bundle, strat_cfg_override=strat,
                                    min_bars_override=skip_bars)
    if not result.meta.run_ok:
        raise RuntimeError(result.meta.error or "replay failed")
    return _metrics(result.stats or {})


def _eval_strategy(item: Mapping[str, Any]) -> Dict[str, Any]:
    try:
        return {
            **dict(item),
            "is": _run_strategy_frame(_G["is_df"], item, skip_bars=0, label=PROXY),
            "oos": _run_strategy_frame(_G["oos_df"], item,
                                       skip_bars=_G["oos_skip"], label=PROXY),
            "proxy_full": _run_strategy_frame(_G["proxy4"], item, skip_bars=0,
                                              label=PROXY),
            "native_full": _run_strategy_frame(_G["native4"], item, skip_bars=0,
                                               label="XAU-USDT-SWAP"),
        }
    except Exception as exc:
        return {**dict(item), "error": f"{type(exc).__name__}: {exc}"}


# --- ranking + reporting ------------------------------------------------------

def _pf(row: Mapping[str, Any], side: str) -> float:
    return float((row.get(side) or {}).get("profit_factor") or 0.0)


def _net(row: Mapping[str, Any], side: str) -> float:
    return float((row.get(side) or {}).get("net_profit_pct") or 0.0)


def _balanced_rank(rows: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    """Worst-of-IS/OOS first: consistency over a single lucky window.

    The 2026-07-29 grid chose its winner this way and said why — the top-OOS cell
    there had a losing fold and a weaker native check. Same rule here so the two
    studies are comparable.
    """
    valid = [dict(row) for row in rows if _valid(row)]
    valid.sort(
        key=lambda row: (min(_pf(row, "is"), _pf(row, "oos")),
                         _pf(row, "oos"), _net(row, "oos")),
        reverse=True,
    )
    return valid


def _fmt(row: Mapping[str, Any], side: str) -> str:
    block = row.get(side) or {}
    if not block:
        return "—"
    return (f"PF {float(block.get('profit_factor') or 0):5.3f} "
            f"net {float(block.get('net_profit_pct') or 0):+7.2f}% "
            f"MDD {float(block.get('max_drawdown_pct') or 0):5.2f}% "
            f"n={int(block.get('total_trades') or 0):3d}")


def _collect(pool: Optional[Pool], fn: Any, items: Sequence[Mapping[str, Any]],
             *, label: str) -> List[Dict[str, Any]]:
    total = len(items)
    rows: List[Dict[str, Any]] = []
    stream = (pool.imap_unordered(fn, items, chunksize=1) if pool
              else (fn(item) for item in items))
    interval = max(1, min(20, total // 10 or 1))
    for done, row in enumerate(stream, start=1):
        rows.append(row)
        if done == total or done % interval == 0:
            print(f"{label}: {done}/{total}", flush=True)
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="bot_config.yaml")
    parser.add_argument("--out-dir", default="core/xau_champion")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--top-n", type=int, default=10)
    parser.add_argument("--stage", default="all",
                        choices=("all", "config", "strategy"))
    args = parser.parse_args()

    os.chdir(ROOT)
    out_dir = Path(args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    cfg = load_bot_config(args.config)
    prep = prepare(cfg)
    print(f"live XAU strategy: {prep.strategy_name}  tf={prep.primary_timeframe}")

    data = _write_frames(out_dir)
    for key, meta in data["ranges"].items():
        print(f"  {key}: {meta['bars']} bars  {meta['start']} -> {meta['end']}")

    config_path = str(Path(args.config).resolve())
    report: Dict[str, Any] = {"ranges": data["ranges"]}

    with Pool(processes=max(1, args.workers), initializer=_init_worker,
              initargs=(config_path, data["paths"])) as pool:
        if args.stage in ("all", "config"):
            items = config_items()
            print(f"\n=== stage config: {len(items)} cells ===", flush=True)
            rows = _collect(pool, _eval_config, items, label="config")
            rows.sort(key=lambda row: row["id"])
            ranked = _balanced_rank(rows)
            if not ranked:
                raise SystemExit("no config cell passed the validity gates")

            anchor = next(r for r in rows if r.get("anchor_live"))
            by_id = {item["id"]: item for item in items}
            finalist_ids = list(dict.fromkeys(
                [r["id"] for r in ranked[: args.top_n]] + [anchor["id"]]))
            fulls = _collect(pool, _eval_config_full,
                             [by_id[i] for i in finalist_ids], label="config-full")
            full_by_id = {r["id"]: r for r in fulls}

            print("\n-- config finalists (balanced rank) --")
            for rank, row in enumerate(ranked[: args.top_n], start=1):
                full = full_by_id.get(row["id"], {})
                mark = " <-- LIVE" if row.get("anchor_live") else ""
                print(f"{rank:2d}. {row['id']}{mark}")
                print(f"    IS   {_fmt(row, 'is')}")
                print(f"    OOS  {_fmt(row, 'oos')}")
                print(f"    full {_fmt(full, 'proxy_full')}")
                print(f"    nativ{_fmt(full, 'native_full')}")
            live_full = full_by_id.get(anchor["id"], {})
            print(f"\n-- deployed cell: {anchor['id']} --")
            print(f"    IS   {_fmt(anchor, 'is')}")
            print(f"    OOS  {_fmt(anchor, 'oos')}")
            print(f"    full {_fmt(live_full, 'proxy_full')}")
            print(f"    nativ{_fmt(live_full, 'native_full')}")
            report["config"] = {"rows": rows, "ranked_ids": [r["id"] for r in ranked],
                                "full_checks": fulls, "live_id": anchor["id"]}

        if args.stage in ("all", "strategy"):
            items = strategy_items()
            print(f"\n=== stage strategy: {len(items)} runs ===", flush=True)
            rows = _collect(pool, _eval_strategy, items, label="strategy")
            rows.sort(key=lambda row: row["id"])
            print("\n-- rival strategies on XAU 4h (own default configs) --")
            for row in sorted(rows, key=lambda r: -_pf(r, "proxy_full")):
                if row.get("error"):
                    print(f"{row['id']:38s} ERROR {row['error'][:70]}")
                    continue
                gate = "pass" if _valid(row) else "fail"
                print(f"{row['id']:38s} [{row.get('maturity')}] gate={gate}")
                print(f"    full {_fmt(row, 'proxy_full')}   "
                      f"nativ {_fmt(row, 'native_full')}")
                print(f"    IS   {_fmt(row, 'is')}   OOS {_fmt(row, 'oos')}")
            report["strategy"] = {"rows": rows}

    out = out_dir / "champion_search.json"
    out.write_text(json.dumps(report, indent=2, default=str))
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
