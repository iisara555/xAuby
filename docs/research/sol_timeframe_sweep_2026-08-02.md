# SOL — timeframe sweep, 2 August 2026

Long-only, `SOLUSDT`, 2021-01-01 → 2026-07-01, on Binance archive candles
replayed through the production plugin path (`run_plugin_replay`).

**Verdict: 4h is the timeframe that works, and `elliotv5_ewo` is the only
strategy that clears every pre-registered test. It is a capital-preservation
strategy, not a growth strategy — it does not beat buy-and-hold, and it is not
close.**

## Why this sweep exists

Two prior studies on 15m both failed:

| Study | Configs | Passed |
|---|---|---|
| 15m long+short | 126 | 0 |
| 15m long-only | 36 | 0 |

The zero-cost diagnostic showed why, and it was not "no signal". Both 15m
candidates had a real gross edge that their own turnover destroyed:

| Strategy | Trades | Gross PF (0 fees) | Gross net | Net after costs |
|---|---|---|---|---|
| `dual_thrust` | 516 | 1.045 | +3.53% | −14.94% |
| `simple_scalp_plus` | 14,796 | 1.019 | +62.08% | **−99.09%** |

Cost drag scales with trade count, so the experiment is: keep the strategies,
slow the sampling.

## Result: the hypothesis holds

| TF | Configs | Passed gate | Profitable in both windows | Best worst-window PF |
|---|---|---|---|---|
| 15m | 36 | 0 | 0 | — |
| **1h** | 90 | **14** | **17** | 1.235 |
| **4h** | 90 | **4** | **10** | 3.620 |
| 1d | 72 | 0 | 0 | 0.924 |

15m and 1d both fail; 1h and 4h both produce genuine passers. The failure at
each end has a different cause — 15m dies of costs, 1d of too few
opportunities and indicators that never warm (SOL's daily history is only 2,079
bars, so a 750-bar context window consumes a third of it).

## Full-period results (best config per strategy per TF)

| TF | Strategy | Trades | Gross PF | Net | PF | MDD | B&H net | B&H MDD |
|---|---|---|---|---|---|---|---|---|
| 1h | `elliotv5_ewo` | 617 | 1.679 | +28.3% | 1.387 | 8.2% | +4,575.7% | 96.8% |
| **4h** | **`elliotv5_ewo`** | **471** | **1.793** | **+17.9%** | **1.468** | **6.6%** | +4,442.3% | 96.6% |
| 4h | `dual_thrust` | 198 | 1.465 | +37.6% | 1.344 | 12.8% | +4,442.3% | 96.6% |
| 1d | `simple_scalp_plus` | 251 | 1.755 | +68.7% | 1.681 | 8.3% | +3,987.3% | 96.3% |

The 1d row looks like the winner and is not one — see *Configs that look good
and are not* below.

## The winner, tested against every pre-registered criterion

`elliotv5_ewo` on **4h**, `low_offset=0.975 high_offset=1.02 ewo_high=4.0
disable_stop_loss=False`:

| Test | Threshold | Result | |
|---|---|---|---|
| Tuning-window gate | IS ≥ 30, OOS ≥ 10 trades, both nets > 0 | passed | ✅ |
| Profitable in both full-span windows | > 0 | IS +16.3%, OOS +1.3% | ✅ |
| Profitable folds | ≥ 4 / 6 | **5 / 6** | ✅ |
| Survives 2.0× costs | PF > 1 | **+9.0%, PF 1.220** | ✅ |
| Post-2023 sub-period | > 0 | +9.7%, PF 1.597 | ✅ |

This is the first configuration in the whole investigation to clear all five.

## But it does not beat buy-and-hold, on return or risk-adjusted

| | Strategy | Buy-and-hold |
|---|---|---|
| Net (5.5y) | +17.9% | +4,442.3% |
| CAGR | **3.06%** | ~100% |
| Max drawdown | **6.6%** | 96.6% |
| Calmar | **0.46** | **1.04** |
| Sharpe / Sortino | 0.76 / 0.26 | — |
| Exposure | **8.15%** of bars | 100% |

Buy-and-hold wins on return by a factor of 248, and still wins on Calmar
(1.04 vs 0.46). Nobody should deploy this expecting to outperform holding SOL.

## What it is actually good at

The fold table is the interesting part — strategy net against B&H net, same
windows:

| Fold | Strategy | Buy-and-hold |
|---|---|---|
| 1 (2021 bull) | +9.2% | **+13,544.1%** |
| 2 (2022 bear) | −0.1% | **−85.6%** |
| 3 | +0.8% | −34.4% |
| 4 | +5.9% | +549.6% |
| 5 | 0.0% | +29.0% |
| 6 | +1.2% | −57.4% |

It is positive or flat in every fold, including the two where holding lost 86%
and 57%. It is in the market only 8% of the time, average hold 2.08 bars
(~8 hours). So it sidesteps the crashes and captures almost none of the
upside — a capital-preservation profile, and it should be described that way
rather than as an edge over the asset.

The 92% idle capital is the one genuine argument for it: on a single pair that
is dead weight, but the same signal across many pairs, or levered, is a
different proposition. This study does not measure either.

## Configs that look good and are not

**1d `simple_scalp_plus`** posts +68.7% (and a second config +167.0%) with PF
1.681 and only 251 trades, surviving 2.0× costs easily. It is not a candidate:
it **failed the tuning-window gate** (`passed_gate=false`) and was only
tabulated through the fallback that promotes the best of a failing slate so a
negative result carries evidence. Its IS/OOS split gives it away — IS +62.5%
against OOS +3.8%. The performance is front-loaded into the 2021 leg that
tuning deliberately excluded.

**1h `elliotv5_ewo`** passed the tuning-window gate and posts a larger net
(+28.3%) than the 4h winner, but its full-span OOS is **−5.5% at PF 0.490** and
only 3/6 folds are profitable. It is the same config family degrading as
turnover rises — consistent with the cost story, and not selectable.

## Protocol

| Element | Setting |
|---|---|
| Engine | `run_plugin_replay` — production StrategyRunner + PositionSimulator |
| Data | Binance public archive, `SOLUSDT` |
| Span | 2021-01-01 → **2026-07-01 (pinned)** |
| Tuning window | 2022-01-01 → 2023-10-01, 70/30 IS/OOS |
| Costs | 5bp taker + 2bp slippage per side (~14bp round trip), funding 4e-05/8h |
| Context window | 750 bars |
| Folds | 6 non-overlapping |
| Gates | 1h 60/20 · 4h 30/10 · 1d 15/5 |

**The end date is pinned deliberately.** The monthly archives land at different
times per timeframe — 15m and 1d ended 2026-06-30 while 1h and 4h already had
July. Without the pin the sweep would have compared different periods and
favoured whichever TF happened to include the most recent leg.

**Gates scale with timeframe** because trade counts do; a fixed floor would
reject slower books purely for being slower. 4h's 30/10 matches the existing
BTC harness precedent. All thresholds were set before the run.

**B&H differs slightly per TF** (+4,802% at 15m → +3,987% at 1d) purely because
the first *closed* bar after the start is later and higher on a coarser
timeframe ($1.500 at 15m vs $1.800 at 1d) during a fast rally. All timeframes
exit at the same price on the same day. Each TF's B&H is the correct benchmark
for the bars that TF trades.

**`dual_thrust` is excluded from 1d.** It is an intraday range-breakout system;
at 1d one bar is one session, `bars_per_session` collapses to 1, and the
plugin's own `validate_config` rejects the config.

## On the freqtrade port

`elliotv5_ewo` ports the freqtrade **ElliotV5 / SMAOffset** family: mean
reversion against an SMA offset, gated by the Elliott Wave Oscillator
`(EMA(50) − EMA(200)) / close × 100`.

**NostalgiaForInfinity is the stronger public freqtrade strategy and was not
attempted.** It is 10,000+ lines bound to `custom_exit`, `custom_stoploss`,
protections and multiple informative timeframes; any port would be a different
strategy wearing its name.

Four fidelity gaps in this port, stated so the numbers are not over-read:

1. **`minimal_roi` units differ.** freqtrade uses fractions (`0.099` = 9.9%);
   `resolve_minimal_roi` requires percent. Values are ×100. A verbatim copy
   would set a 0.099% take-profit and exit on noise.
2. **freqtrade's terminal `"119": 0` rung is dropped** by the `roi <= 0` filter;
   expressed as `0.01`, the workaround the resolver's docstring prescribes.
3. **freqtrade's fixed-percent `stoploss = −0.189` has no equivalent** — this
   engine sizes stops from ATR. `disable_stop_loss` is a grid axis so both
   readings were measured; the winner uses the ATR stop.
4. **Single-pair removes freqtrade's pair-selection alpha.** These strategies
   normally scan ~50 pairs and trade whichever fires. This measures the signal,
   not the deployed system — and is the most likely reason the returns here are
   far below what the strategy is reported to do live.

## Best config

Research artifact. **Not** applied to `coin_whitelist.json` or `bot_config.yaml`,
and **not** certified for live. `elliotv5_ewo` is `maturity: research`, so the
engine refuses it on a `mode: live` pair.

```json
{
  "SOL": {
    "symbol": "SOLUSDT",
    "strategy": "elliotv5_ewo",
    "primary_timeframe": "4h",
    "mode": "sim",
    "allowed_sides": ["long"],
    "strategy_params": {
      "disable_stop_loss": false,
      "ewo_high": 4.0,
      "high_offset": 1.02,
      "low_offset": 0.975,
      "max_calc_bars": 600
    }
  }
}
```

Measured: PF 1.468, net +17.9%, MDD 6.6%, 471 trades, 5/6 profitable folds,
PF 1.220 at 2× costs, IS +16.3% / OOS +1.3%.

## Open items

1. **Multi-pair is untested and is the obvious next step.** 8% exposure on one
   pair is the strategy's main limitation, and the freqtrade original is
   designed around pair selection.
2. **No bootstrap significance on the winner yet** — 471 trades is enough to run
   it; the multiple-comparison count for this sweep is 252 configs.
3. **`cool_down_minutes` is not simulated** in replay, a live/replay divergence
   that grows with timeframe.
4. **1d is under-powered**, not merely unprofitable: 2,079 daily bars against a
   750-bar context window. A longer history or a shorter window would test it
   properly.

## Reproducing

```bash
PYTHONPATH=. python3 scripts/sol_15m_multi_strategy.py fetch     --slate tf-4h
PYTHONPATH=. python3 scripts/sol_15m_multi_strategy.py grid      --slate tf-4h --jobs 4
PYTHONPATH=. python3 scripts/sol_15m_multi_strategy.py finalists --slate tf-4h --jobs 4 --top 2
PYTHONPATH=. python3 scripts/sol_15m_multi_strategy.py report    --slate tf-4h --md <out>
```
