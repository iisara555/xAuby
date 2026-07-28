"""Issue a certificate record for a SaaS preset. The only way to a verdict.

Roadmap P1.4. `xauby/saas/catalog.py` used to state each preset's verdict and
backtest block as Python literals, so "certified" was a string an editor could
type — and in July 2026 one was, from a report that had measured a different
configuration. The catalog now *derives* those fields from
`xauby/saas/certificates/<preset_id>.json`, and this script is what writes them.

What it does:

1. Reads the preset spec (identity + `execution_profile`) from
   `xauby/saas/preset_specs.py` — the hand-authored half, which by construction
   contains no verdict.
2. Replays that exact configuration against venue data and applies
   `bot_config.yaml -> backtest.acceptance`, the pre-registered gate. For a
   long+short preset the gate needs a long-only baseline of the same
   configuration, which this builds by flipping `enable_short` off — so the
   measured edge is attributable to the short side and nothing else.
3. Fingerprints the configuration it measured and writes the record.

The fingerprint is the part that matters. If someone later edits the preset's
`execution_profile`, the fingerprint stops matching and the certificate stops
applying — the preset reverts to unassessed rather than keeping a verdict for a
configuration it no longer ships. Re-running this script is the only way back.

Usage:
    PYTHONPATH=. python3 scripts/certify_preset.py --preset okx-btc-supertrend-v1
    PYTHONPATH=. python3 scripts/certify_preset.py --preset okx-xau-actionzone-v1 \\
        --data-symbol XAUT-USDT --document docs/research/xau_certification_2026-07-26.md
    PYTHONPATH=. python3 scripts/certify_preset.py --list
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from typing import Any, Dict, List, Optional

import pandas as pd

from xauby.analytics.risk import equity_relative_returns
from xauby.backtest.service import _prepare_backtest_config, resolve_strategy_name
from xauby.backtest.significance import (
    OrderInvariantStatistic,
    bootstrap_returns,
    deflated_sharpe_ratio,
    longest_losing_streak,
    max_drawdown,
    shuffle_pvalue,
)
from xauby.backtest.walkforward import resolve_variant
from xauby.observability.replay_validation import load_bot_config
from xauby.saas.certification import (
    CERTIFICATE_DIR,
    RECORD_VERSION,
    config_fingerprint,
)
from xauby.saas.preset_specs import PRESET_SPECS

#: Venue series to certify each preset against. A preset with no entry here has
#: no data path yet, which is a reason to leave it unassessed — not to certify
#: it against whatever series happens to be handy.
DATA_SYMBOLS: Dict[str, str] = {
    "okx-xau-actionzone-v1": "XAUT-USDT",
    "okx-btc-supertrend-v1": "BTC-USDT-SWAP",
}


def spec_by_id(preset_id: str) -> Dict[str, Any]:
    for spec in PRESET_SPECS:
        if spec["id"] == preset_id:
            return dict(spec)
    raise SystemExit(f"unknown preset {preset_id!r}; try --list")


def _measure(spec, engine_config, df4, df1, *, overrides=None, label=""):
    """One continuous replay of the preset's own configuration."""
    from scripts.validate_on_venue_data import _bundle_for
    from xauby.backtest.service import run_replay_from_bundle

    symbol = str(spec["symbol"])
    strategy = str(spec.get("strategy") or resolve_strategy_name(symbol, engine_config, None))
    _merged, base_strat, _tf, _rtf, _u, _used = _prepare_backtest_config(
        symbol, strategy, engine_config, None
    )
    profile = dict(spec.get("execution_profile") or {})
    profile.update(overrides or {})
    strat = resolve_variant(base_strat, profile) if profile else dict(base_strat)

    bundle = _bundle_for(symbol, df4, engine_config=engine_config,
                         strategy_name=strategy, label=label)
    gates_any_side = bool(strat.get("use_d1_regime_filter")) or any(
        strat.get(key) for key in
        ("use_d1_regime_filter_long", "use_d1_regime_filter_short")
    )
    if gates_any_side and df1 is not None:
        bundle.df_regime = df1.reset_index(drop=True)
        bundle.regime_tf = "1d"
        bundle.use_d1 = True
    result = run_replay_from_bundle(bundle, strat_cfg_override=strat)
    stats = result.stats or {}
    if not stats:
        raise SystemExit(
            f"replay failed for {spec['id']}: "
            f"{getattr(result.meta, 'error', '') or 'unknown'}"
        )
    return stats


def _equity_relative_returns(stats: Dict[str, Any]) -> List[float]:
    """Each trade's PnL as a percent of account equity *before* that trade.

    Not ``pnl_pct``. That field is the return on the trade — on the position's
    own notional — and compounding a list of them treats every trade as if it
    had been the whole account. On this BTC preset that turned a real 9.8%
    max drawdown into a reported 35.4% and a bootstrap interval of +/-316%: all
    the right arithmetic on the wrong series.
    """
    return equity_relative_returns(
        [float(t.get("pnl") or 0.0) for t in (stats.get("trades") or [])],
        float(stats.get("initial_balance") or 0.0),
    )


def _significance(
    stats: Dict[str, Any],
    *,
    n_trials: int,
    sharpe_variance: Optional[float],
) -> Dict[str, Any]:
    """Bootstrap, shuffle, and deflated Sharpe over the run's own returns.

    Roadmap P1.3. A profit factor reported without these is a point estimate
    from one path of one search; the certificate should carry the caveats that
    make it readable.

    Neither ``n_trials`` nor ``sharpe_variance`` can be recovered from a single
    replay — they describe the *search* that selected this configuration — so
    both are recorded verbatim and the deflated Sharpe is simply not computed
    when the variance is unknown. A placeholder there produces a confident,
    meaningless number: the default 0.25 against a per-trade Sharpe of 0.115
    yields an expected-max of 1.11 and a deflated Sharpe of exactly 0.0, which
    looks like a damning result and is really a units mismatch.
    """
    returns = _equity_relative_returns(stats)
    out: Dict[str, Any] = {
        "observations": len(returns),
        "basis": "per-trade PnL as a percent of equity before the trade",
    }

    if len(returns) < 5:
        out["note"] = "too few trades for any of these tests to mean anything"
        return out

    out["bootstrap"] = bootstrap_returns(returns).to_dict()
    for name, statistic in (("max_drawdown", max_drawdown),
                            ("longest_losing_streak", longest_losing_streak)):
        try:
            out[f"shuffle_{name}"] = shuffle_pvalue(returns, statistic).to_dict()
        except OrderInvariantStatistic as exc:
            # Recorded rather than swallowed: "this sample could not test it" is
            # a finding, and dropping it would leave a gap that reads as a pass.
            out[f"shuffle_{name}"] = {"error": str(exc)}

    if sharpe_variance is None:
        out["deflated_sharpe"] = {
            "computed": False,
            "n_trials": n_trials,
            "reason": (
                "--sharpe-variance was not supplied. The deflated Sharpe needs "
                "the spread of Sharpe ratios across the configurations that "
                "were searched, in the same per-trade units as this run. "
                "Guessing it produces a confident number about nothing."
            ),
        }
    else:
        out["deflated_sharpe"] = deflated_sharpe_ratio(
            returns, n_trials=n_trials, sharpe_variance=sharpe_variance
        ).to_dict()
    return out


def _fmt_period(df: pd.DataFrame) -> tuple[str, str]:
    first = pd.to_datetime(int(df["open_time"].iloc[0]), unit="ms")
    last = pd.to_datetime(int(df["open_time"].iloc[-1]), unit="ms")
    years = (last - first).days / 365.25
    return (f"{first:%b %Y} – {last:%b %Y}", f"{years:.1f} years")


def build_record(
    spec: Dict[str, Any],
    engine_config: Dict[str, Any],
    data_symbol: str,
    *,
    document: str = "",
    note: str = "",
    n_trials: int = 1,
    sharpe_variance: Optional[float] = None,
) -> Dict[str, Any]:
    from scripts.validate_on_venue_data import _okx_frame

    df4 = _okx_frame(data_symbol, str(spec.get("primary_timeframe") or "4h"))
    df1 = _okx_frame(data_symbol, "1d")

    stats = _measure(spec, engine_config, df4, df1, label=data_symbol)
    net = float(stats.get("net_profit_pct", 0.0) or 0.0)

    acceptance = ((engine_config.get("backtest") or {}).get("acceptance") or {})
    min_edge = float(acceptance.get("min_profit_edge_pp", 5.0))
    shorts_on = "short" in [str(s).lower() for s in (spec.get("allowed_sides") or [])]

    gate: Dict[str, Any] = {
        "criterion": (
            f"backtest.acceptance: long+short net > 0 AND beats the matching "
            f"long-only config by >= {min_edge:.2f}pp"
        ),
        "min_profit_edge_pp": min_edge,
    }
    if shorts_on:
        # Same configuration with the short side off: the only difference is the
        # thing being judged.
        baseline = _measure(spec, engine_config, df4, df1,
                            overrides={"enable_short": False}, label=data_symbol)
        baseline_net = float(baseline.get("net_profit_pct", 0.0) or 0.0)
        edge = net - baseline_net
        gate.update({
            "baseline": "same configuration, enable_short=False",
            "baseline_net_pct": round(baseline_net, 2),
            "candidate_net_pct": round(net, 2),
            "edge_pp": round(edge, 2),
            "passed": bool(net > 0.0 and edge >= min_edge),
        })
        verdict = "certified" if gate["passed"] else "failed"
    else:
        # A long-only preset has no short side to justify, so the edge test does
        # not apply. Net-positive over the sample is the whole bar it can clear,
        # and calling that "certified" would overstate it — say so in the note.
        gate.update({"applies": False,
                     "reason": "long-only preset: the acceptance edge test "
                               "measures the short side, which this preset "
                               "does not trade",
                     "candidate_net_pct": round(net, 2),
                     "passed": bool(net > 0.0)})
        verdict = "certified" if net > 0.0 else "failed"

    period, duration = _fmt_period(df4)
    profit_factor = float(stats.get("profit_factor", 0.0) or 0.0)
    win_rate = stats.get("win_rate")
    max_dd = stats.get("max_drawdown_pct")

    if not note:
        if verdict == "certified":
            note = (
                f"Clears the pre-registered backtest.acceptance gate "
                f"({gate.get('edge_pp', net):+.2f}"
                f"{'pp edge' if shorts_on else '% net'}) on {data_symbol} over "
                f"{duration}."
            )
        else:
            note = (
                f"Fails the pre-registered backtest.acceptance gate by "
                f"{gate.get('edge_pp', net):+.2f}pp: long+short must beat the "
                f"matching long-only config by >= {min_edge:.1f}pp and instead "
                f"loses to it."
            )
        if document:
            note = f"{note} See {document}."

    return {
        "record_version": RECORD_VERSION,
        "preset_id": spec["id"],
        "config_fingerprint": config_fingerprint(spec),
        "measured_at": dt.date.today().isoformat(),
        "protocol": {
            "name": "saas-preset-acceptance",
            "version": "1",
            "script": "scripts/certify_preset.py",
        },
        "data_source": f"OKX {data_symbol} {spec.get('primary_timeframe') or '4h'}",
        "gate": gate,
        "verdict": verdict,
        "note": note,
        "document": document,
        "significance": _significance(stats, n_trials=n_trials,
                                      sharpe_variance=sharpe_variance),
        "evidence": {
            "status": "validated",
            "score_label": f"PF {profit_factor:.2f}",
            "period": period,
            "duration": duration,
            "win_rate_pct": round(float(win_rate), 1) if win_rate is not None else None,
            "max_drawdown_pct": round(float(max_dd), 1) if max_dd is not None else None,
            "trades": int(stats.get("total_trades", 0) or 0),
            "source": (
                f"OKX {data_symbol} · this preset's execution_profile · "
                f"net of fee/slippage/funding · measured "
                f"{dt.date.today().isoformat()}"
            ),
        },
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--preset", default=None)
    ap.add_argument("--config", default="bot_config.yaml")
    ap.add_argument("--data-symbol", default=None,
                    help="venue series to certify against (default: DATA_SYMBOLS)")
    ap.add_argument("--document", default="",
                    help="path to the human-readable write-up this record points at")
    ap.add_argument("--note", default="", help="override the generated note")
    ap.add_argument("--trials", type=int, default=1,
                    help="how many configurations were evaluated before this "
                         "one was chosen — the whole grid, not the shortlist. "
                         "Deflates the Sharpe for selection bias; leaving it at "
                         "1 deflates nothing and the record says so.")
    ap.add_argument("--sharpe-variance", type=float, default=None,
                    help="variance of the per-trade Sharpe ratios across those "
                         "trials, measured from the sweep. Omitted, the "
                         "deflated Sharpe is recorded as not computed rather "
                         "than guessed — there is no safe default here.")
    ap.add_argument("--list", action="store_true", help="show presets and their data paths")
    ap.add_argument("--dry-run", action="store_true", help="print the record, do not write it")
    args = ap.parse_args()

    if args.list or not args.preset:
        print(f"{'preset':34s} {'data series':16s} fingerprint")
        for spec in PRESET_SPECS:
            data = DATA_SYMBOLS.get(spec["id"], "—")
            print(f"{spec['id']:34s} {data:16s} {config_fingerprint(spec)}")
        return 0

    spec = spec_by_id(args.preset)
    data_symbol: Optional[str] = args.data_symbol or DATA_SYMBOLS.get(spec["id"])
    if not data_symbol:
        raise SystemExit(
            f"no venue data path for {spec['id']!r}. Add one to DATA_SYMBOLS or "
            "pass --data-symbol; leaving it unassessed is the honest default."
        )

    engine_config = load_bot_config(args.config)
    record = build_record(spec, engine_config, data_symbol,
                          document=args.document, note=args.note,
                          n_trials=args.trials,
                          sharpe_variance=args.sharpe_variance)

    text = json.dumps(record, indent=2) + "\n"
    if args.dry_run:
        print(text)
        return 0

    os.makedirs(CERTIFICATE_DIR, exist_ok=True)
    path = os.path.join(CERTIFICATE_DIR, f"{spec['id']}.json")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    print(f"{record['verdict'].upper()}  {spec['id']}  "
          f"fingerprint {record['config_fingerprint']}")
    print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
