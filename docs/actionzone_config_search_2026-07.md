# xAuby ActionZone (XAU) — institutional-grade config search, July 2026

Goal: find the best `xauby_actionzone` config for **win rate (WR)** and **profit
factor (PF)** on the XAU pair, using an evaluation protocol comparable to an
institutional strategy review — not a single lucky backtest window.

## Protocol

| Element | Setting |
|---|---|
| Engine | The repo's own `run_plugin_replay` (StrategyRunner + PositionSimulator) — same code path live parity is validated against |
| Data | PAXGUSDT 4h + 1d (the configured `backtest_data_proxy` for XAU), Global Binance archive (`data.binance.vision`), 2020-08-28 → 2026-07-09, 12,769 4h bars (~5.9 years) |
| Costs | Taker fee 0.05%/fill, slippage 2 bps/fill, funding 0.004 bps/8h (production `bot_config.yaml` values) |
| Split | In-sample (IS) = first 70% (2020-08 → 2024-06), out-of-sample (OOS) = last 30% (2024-06 → 2026-07) with a 300-bar warmup lead-in |
| Walk-forward | 5 contiguous ~14-month folds over the full history, 300-bar warmup each |
| Validity gates | ≥ 40 IS trades, ≥ 18 OOS trades, net profit > 0 in **both** IS and OOS |
| Search | Stage A: 144 structural entry combos · Stage B: exits/RSI on survivors · Stage C: focused robustness runs (IS/OOS + folds + full period) |
| Sizing | Live baseline: CDC-pure (`disable_stop_loss: true`, `position_pct: 0.95`) unless stated |

Context that matters for reading the numbers: **136/144 stage-A combos were
OOS-profitable but only 4/144 were IS-profitable.** The OOS window is gold's
2024-2025 mega-rally (any trend follower prints money there); the IS window
contains the 2021-2023 chop. Configs were therefore selected primarily on
surviving the chop, not on OOS peak numbers.

## Headline result

Recommended config — **long-only, D1 regime filter ON, fresh zone window 3,
partial TP 12% (bank half)**:

```yaml
# coin_whitelist.json -> assets[XAU].strategy_params (changes vs current live)
enable_short: false            # was true  (see phase-lock finding below)
use_d1_regime_filter: true     # was false
fresh_zone_window: 3           # was 1
# unchanged: ap_smoothing 2, require_fresh_zone true, require_slow_slope true,
#            slow_slope_bars 3, entry_thrust_min 0, disable_stop_loss true,
#            position_pct 0.95, partial_tp_pct 12.0, partial_tp_fraction 0.5,
#            rsi_min 0 / rsi_max 100, vol_min_ratio 0
# pair level: allowed_sides: ["long"], short_live_enabled: false
```

| Metric (5.9y full period) | Recommended | Current live config |
|---|---|---|
| Profit factor | **2.06** | 1.64 |
| Win rate | **41.3%** | 34.5% |
| Net profit | +68% | +72% * |
| Max drawdown | 15% | 18% |
| Sharpe (annualized, per-bar equity) | 0.90 | 0.85 |
| Trades | 104 | 174 |
| IS (2020-24 chop): PF / net | **1.50 / +19%** | 1.17 / +13% |
| OOS (2024-26): PF / WR / net | 2.90 / 48% / +46% | 2.65 / 49% / +57% |
| Walk-forward folds profitable | **3/5** (losers shallow: 0.62, 0.86) | 0/5 (see below) |

\* The live config's +72% full-period number is not robust — see the
phase-lock finding. Under walk-forward it lost money in every fold.

Runner-up (max OOS quality, thinner chop margin): same but
`fresh_zone_window: 1` — full PF 2.09 / WR 41%, OOS PF **3.74** / WR **52%**,
but IS only +4% and 79 trades. Prefer fz3 for robustness; fz1 if you want the
highest OOS WR/PF print.

## Key findings

1. **The current live combination `enable_short: true` + `fresh_zone_window: 1`
   never actually stop-and-reverses — it phase-locks into one side.** The exit
   fires on the zone-flip bar (streak = 1); on the next bar the streak (2) is
   already past the fresh window (1), so the opposite-side entry is always
   blocked. Whichever side the strategy enters first, it keeps trading only
   that side until a blocked entry re-randomizes the phase. In walk-forward
   fold 4 (Feb 2024 → May 2025 rally) the live config took **32 shorts and 0
   longs** (-10%), while long-only took +45% over the same window. The
   +72%/+57% headline numbers of the live config come from windows where it
   happened to phase-lock long. This is a structural hazard, not noise: with
   shorts on and fz1, live results are close to a coin flip on entry phase.
   With `fresh_zone_window >= 2` the reversal does execute (entry on streak 2),
   but every such long+short variant still underperformed long-only + D1 on
   IS, folds, and full period. Shorting gold's 4h chop simply hasn't paid:
   fold-4 short PnL was uniformly negative even with the D1 gate.
2. **The D1 regime filter is the single biggest quality lever.** Stage A
   average OOS PF 2.35 (D1 on) vs 1.74 (D1 off); it is what lifts IS from
   breakeven to PF 1.50 and full-period PF from ~1.5 to ~2.1. It cuts trade
   count ~30-40% — acceptable at 104 trades / 5.9y.
3. **`fresh_zone_window` interacts with the filters.** With D1 + slope filters
   on, window 3 recovers entries blocked on the exact cross bar (104 vs 79
   trades) and raises IS PF from 1.13 to 1.50. Without D1, window 1 was best
   (windows 2-3 admit stale-cross whipsaw).
4. **Partial TP 12% / 0.5 is confirmed** (already live): vs no partial TP it
   raises full PF 1.85 → 2.06 and cuts MDD; 8% and 15% are both slightly worse.
5. **ATR stops hurt this strategy** (`disable_stop_loss: false` variants: IS PF
   ~0.70): 4h gold noise stops trades out before the trend pays. CDC-pure exits
   confirmed.
6. **Dead parameters** on this data: `entry_thrust_min` (hurts),
   `exit_on_bear_cross` (zero effect — bear cross and RED flip coincide),
   RSI band and volume filter (never bind at fresh-cross entries),
   `ap_smoothing` 1 vs 2 (negligible; keep 2 = Piriya V3).

## Caveats (read before going live)

- Backtests run on **PAXGUSDT** (proxy), not XAUT-USDT-SWAP; results reflect
  PAXG price action per the standing proxy warning in `xauby/backtest/data.py`.
- Folds 1-2 (2020-2022) are negative for every config tested: CDC ActionZone
  has no edge in that regime even with filters; the D1 gate only makes the
  bleed shallow. If gold returns to a multi-year range, expect PF < 1 periods.
- WR ~41% full-period is the honest number; the ~50% WR prints are
  bull-window (OOS) figures.
- All metrics come from ~100-trade samples; treat second decimals as noise.
- Switching live requires operator action: this report does **not** change
  `coin_whitelist.json`. Apply the YAML above, run
  `python scripts/replay_validate.py <run_id> --symbol XAUUSDT` after restart,
  and note the D1 filter needs healthy 1d candle ingestion.

## Reproduction

```bash
python scripts/actionzone_wfa_sweep.py fetch      # PAXGUSDT 4h+1d from data.binance.vision
python scripts/actionzone_wfa_sweep.py stageA     # 144-combo structural grid, IS+OOS
python scripts/actionzone_wfa_sweep.py stageC     # focused candidates: IS/OOS + 5 folds + full
```

Raw per-stage results (JSONL) from this run are archived in
`docs/research/actionzone_sweep_2026-07/`.
