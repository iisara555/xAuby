# SOL (15m) — 2-strategy long-only comparison, 2026-08-02

Symbol `SOLUSDT`, timeframe `15m`, long-only, 2021-01-01 to present. Ranked on profit factor with low drawdown, and judged against buy-and-hold.

> **Provenance note.** `simple_scalp_plus` is the 8-point confluence scalper ported from **Binance_Cryptonice**, not from freqtrade — see its module docstring. Same concept (HullMA, EMA9/21, RSI, ADX, MACD, Stochastic, rolling VWAP, volume, all genuinely computed), different lineage.

> **Gate change, declared before the run.** Trade floors are 100/30 here versus 200/60 in the long+short study. Long-only halves the opportunity set and `dual_thrust` is capped at one entry per session. This is a sample-adequacy threshold, not a performance one.

> **Inert knobs excluded from the grid.** `min_buy_confidence` is coupled to `min_confirmations_buy` via `conf = buy_count/7 + 0.22`, so it is pinned low and the count is the sole entry gate. `mtf_filter` and `require_macro_bull` are no-ops at `regime_timeframe=None`, and `risk_reward` is never read by the strategy. Gridding any of them would have duplicated rows.

## Protocol

| Element | Setting |
|---|---|
| Engine | `run_plugin_replay` (production StrategyRunner + PositionSimulator) |
| Data | Binance public archive, SOLUSDT 15m |
| Tuning window | 2022-01-01 -> 2023-10-01 (2021 melt-up held out) |
| Full span | 2021-01-01 -> present |
| Context window | 750 bars |
| IS/OOS split | 70% / 30% |
| Folds | 6 non-overlapping |
| Gates | IS trades >= 100, OOS trades >= 30, both nets > 0, MDD <= 25.0% |
| Configs searched | 36 |

Ranking score: `min(PF_is, PF_oos, PF_full) / (1 + MDD/10)`, PF clamped at 5.0 and the 99.9 no-losing-trade sentinel rejected outright. The MDD exchange rate is a chosen parameter, not a discovered one: a 10% drawdown halves the score.

## Full period

| Strategy | Combo | Net % | **B&H Net %** | PF | WR % | MDD % | **B&H MDD %** | Trades | Sharpe | Score |
|---|---|---|---|---|---|---|---|---|---|---|
| `dual_thrust` | lookback_days=2_k_upper=0.5_exit_at_session_end=False | -14.94 | 4802.83 | 0.821 | 31.78 | 19.83 | 96.84 | 516 | -0.61 | 0.232 |
| `simple_scalp_plus` | min_confirmations_buy=5_min_confirmations_sell=4_atr_multiplier=1.2 | -99.09 | 4802.83 | 0.897 | 31.30 | 99.45 | 96.84 | 14796 | -4.00 | 0.062 |

**B&H** is buy-and-hold over the same window, charged one entry and one exit at the same taker fee and slippage. On an asset that rose as far as SOL did, a long-only strategy can post a large positive net purely from beta — a config that trails B&H on both net and drawdown is an expensive index, not a strategy.

## Post-melt-up sub-period (2023-01-01 -> present)

| Strategy | Net % | **B&H Net %** | PF | MDD % | Trades |
|---|---|---|---|---|---|
| `dual_thrust` | -5.84 | 634.20 | 0.872 | 11.54 | 319 |
| `simple_scalp_plus` | -96.43 | 634.20 | 0.757 | 96.84 | 9454 |

## IS / OOS (70/30 on the full span)

| Strategy | IS Net % | IS PF | IS Trades | OOS Net % | OOS PF | OOS Trades |
|---|---|---|---|---|---|---|
| `dual_thrust` | -9.88 | 0.851 | 373 | -5.87 | 0.693 | 143 |
| `simple_scalp_plus` | -94.81 | 0.900 | 10378 | -82.42 | 0.673 | 4419 |

## 6-fold walk-forward (PF per fold)

| Strategy | F1 | F2 | F3 | F4 | F5 | F6 | Profitable folds |
|---|---|---|---|---|---|---|---|
| `dual_thrust` | 0.77 | 0.93 | 1.01 | 0.82 | 1.01 | 0.45 | 2/6 |
| `simple_scalp_plus` | 0.98 | 0.74 | 0.74 | 0.78 | 0.68 | 0.62 | 0/6 |

## Bootstrap CI and stress test

| Strategy | Trades | PF 90% CI (iid) | PF 90% CI (block=20) | P(PF<1) block | PF after top-5 cut |
|---|---|---|---|---|---|
| `dual_thrust` | 516 | 0.65–1.03 | 0.61–1.00 | 0.950 | 0.682 |
| `simple_scalp_plus` | 14796 | 0.75–0.82 | 0.75–0.81 | 1.000 | 0.769 |

`P(PF<1)` is the share of block-bootstrap resamples whose profit factor falls below 1.0. The block variant keeps trade-outcome autocorrelation intact and is the one to read; the iid column is shown for comparison.

## Cost sensitivity

Round trip at 1.0x is taker fee + slippage on both sides. A strategy that cannot clear PF > 1 at 2.0x is disqualified regardless of score.

| Strategy | Trades | Net 0.0x | PF 0.0x | Net 1.0x | PF 1.0x | Net 2.0x | PF 2.0x |
|---|---|---|---|---|---|---|---|
| `dual_thrust` | 516 | 3.53 | 1.045 | -14.94 | 0.821 | -29.16 | 0.669 |
| `simple_scalp_plus` | 14796 | 62.08 | 1.019 | -99.09 | 0.897 | -100.00 | 0.826 |

## Cost decomposition — where the money goes

The 0.0x column removes fees and slippage entirely. It is not a tradeable scenario; it exists to separate two very different failures: *no edge at all* versus *a real edge too small to pay for its own turnover*. Only the second one points anywhere.

| Strategy | Trades | Gross PF (0.0x) | Gross net | Net after costs | Cost drag (pp) |
|---|---|---|---|---|---|
| `dual_thrust` | 516 | 1.045 | 3.53% | -14.94% | 18.46 |
| `simple_scalp_plus` | 14796 | 1.019 | 62.08% | -99.09% | 161.17 |

* `dual_thrust` **does have a gross edge** (PF 1.045 before costs) but spreads it across 516 trades. The edge per trade is far smaller than the ~14bp it costs to take, so the strategy is not broken — it is mis-sized for this timeframe. Any rescue would have to cut turnover hard, use maker fills, or trade a venue where the round trip is a fraction of this one.
* `simple_scalp_plus` **does have a gross edge** (PF 1.019 before costs) but spreads it across 14796 trades. The edge per trade is far smaller than the ~14bp it costs to take, so the strategy is not broken — it is mis-sized for this timeframe. Any rescue would have to cut turnover hard, use maker fills, or trade a venue where the round trip is a fraction of this one.

## Multiple-comparison correction

* Configurations evaluated across the whole study: **36** (36 completed without error).
* Deflated Sharpe uses that full count, not the shortlist — an under-reported `n_trials` deflates nothing.

## Verdict

**No configuration is recommended.** Of the 36 configurations searched, 0 passed the pre-registered gates and 0 were profitable in both the in-sample and out-of-sample windows.

The configs tabulated below are the best of a losing slate, promoted so the negative result carries full-span evidence rather than an empty table. They are **not** candidates.

Read this as a statement about SOL at 15m with `dual_thrust`, `simple_scalp_plus` and these cost assumptions — not as a claim that no 15m strategy can work. The dominant term is the fee bill: a round trip costs ~14bp, and configs here turn over hundreds to thousands of times per window.

Least-bad of the slate: `dual_thrust` (lookback_days=2_k_upper=0.5_exit_at_session_end=False) — PF 0.821, MDD 19.83%, 516 trades on the full span. It is reported for completeness only.

Highest score: **`dual_thrust`** (lookback_days=2_k_upper=0.5_exit_at_session_end=False) — PF 0.821, MDD 19.83%, 516 trades, 2/6 profitable folds.

**It does not survive the 2.0x cost test, so it is not recommended.** At 15m the fee bill is the dominant term; a config that only clears at modelled costs is not a strategy.

Versus buy-and-hold: net -14.94% vs 4802.83% (TRAILS B&H), drawdown 19.83% vs 96.84% (shallower than B&H).

It trails buy-and-hold on return but takes less drawdown. That is a risk-adjusted argument, not a return argument, and it should only be made explicitly.

### Best-scoring strategy_params

**This config did not pass the gates and is not a recommendation.** It is recorded so the run is reproducible and so a future study can start from what was already tried.

Research artifact. NOT applied to `coin_whitelist.json` or `bot_config.yaml`, and NOT certified for live.

```json
{
  "SOL": {
    "allowed_sides": [
      "long"
    ],
    "mode": "sim",
    "primary_timeframe": "15m",
    "strategy": "dual_thrust",
    "strategy_params": {
      "enable_short": false,
      "exit_at_session_end": false,
      "k_lower": 0.5,
      "k_upper": 0.5,
      "lookback_days": 2,
      "max_calc_bars": 600,
      "one_entry_per_session": true
    },
    "symbol": "SOLUSDT"
  }
}
```

