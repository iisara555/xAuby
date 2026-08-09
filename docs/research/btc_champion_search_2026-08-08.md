# BTCUSDT — champion search and the Donchian walk-forward

- **Run date:** 2026-08-08/09
- **Venue/data:** OKX `BTC-USDT-SWAP` **native** 4h, 14,564 bars, 2019-12-16 →
  2026-08-08 (6.6 years). No proxy — selection and confirmation are both on the
  venue the pair trades, unlike the XAU studies.
- **Harnesses:** `scripts/btc_champion_search.py` (plugin sweep),
  `scripts/btc_donchian_wfa.py` (anchored rolling walk-forward),
  `scripts/rival_sweep.py` (shared sweep primitives).
- **Result JSON SHA-256:**
  - champion sweep `67ff879e42c15f9732fc0145330ec003b9ad99ec68d1e28f6c52e1ec5773bbf2`
  - walk-forward `2f8159e4d24e1638e1a53036ce7937d29364d37cf096037cefbd75e5571e423e`
  - archived under `docs/research/artifacts/btc_*_2026-08-08.results.json.gz`
- **Benchmark:** buy & hold **+820.70%**, max drawdown **-77.04%**.
- **No production config was changed.**

## Verdict

**Keep `supertrend_ema200` long+short.** `xauby_donchian_trend` beats it on
full-history profit factor, and that advantage is entirely historical: it comes
from 2020–2021 and 2023, has been negative for the last two years, and neither
monthly re-optimisation nor the short side this study added to the plugin
recovers it. Over the most recent 24 test months SuperTrend leads on every
measure.

A secondary result worth recording: **shorts are load-bearing on BTC**, unlike
on gold. `supertrend_ema200` long-only *fails* the validity gate (IS PF 0.896,
net -1.22%) where long+short reaches PF 1.513. The deployed BTC side policy is
correct.

## 1. Plugin sweep — every registered strategy on OKX BTC 4h

Each plugin runs on its **own** resolved default config, so it is judged on its
own stop, trailing and sizing model rather than wearing SuperTrend's. **Net is
therefore not comparable across rows; profit factor is** — exposure is printed
to make the sizing difference visible.

| strategy (best arm) | maturity | full PF | full net | MDD | exp | IS PF | OOS PF | gate |
|---|---|---:|---:|---:|---:|---:|---:|---|
| `xauby_donchian_trend` | undeclared | **1.658** | +35.58% | 6.74% | 16.6% | 1.765 | 1.326 | pass |
| **`supertrend_ema200` L+S (deployed)** | production | 1.513 | +19.24% | 9.81% | 9.5% | 1.494 | **1.549** | pass |
| `xauby_smc_pro` | undeclared | 1.269 | +13.53% | 6.48% | 16.0% | 1.323 | 1.160 | pass |
| `xauby_actionzone` | production | 1.267 | +124.57% | **23.68%** | 18.3% | 1.392 | **0.989** | fail |
| `supertrend_ema200` long-only | production | 1.242 | +4.17% | 5.65% | 5.2% | **0.896** | 1.986 | fail |
| `squeeze_momentum` | undeclared | 1.138 | +16.64% | 14.19% | 25.7% | 1.181 | 0.998 | fail |
| `bbkc_squeeze` | undeclared | 1.050 | +3.05% | 12.06% | 9.0% | 1.111 | 0.873 | fail |
| `vol_breakout` | undeclared | 1.032 | +0.92% | 6.03% | 5.4% | 1.088 | 0.922 | fail |
| `sol_ema_pullback` | undeclared | 0.999 | -0.01% | 3.67% | 2.5% | 1.007 | 0.981 | fail |
| `xauby_vwap_pullback` | undeclared | 0.995 | -0.25% | 6.96% | 3.8% | 1.128 | 0.736 | fail |
| `btc_ema_pullback` | paper | 0.975 | -1.84% | 7.67% | 7.2% | 0.993 | 0.907 | fail |
| `simple_scalp_plus` | undeclared | 0.917 | -31.17% | 43.52% | 59.8% | 0.947 | 0.812 | fail |
| `rsi2_meanrev` | undeclared | 0.867 | -5.90% | 9.73% | 4.4% | 0.944 | 0.634 | fail |
| `ict_lite_strategy` | undeclared | 0.722 | -0.54% | 1.47% | 0.2% | 0.722 | 0.000 | fail |
| `bbrsi_mean_reversion` | undeclared | 0.639 | -17.50% | 18.39% | 2.5% | 0.607 | 0.740 | fail |

Gate = the pre-declared validity test (>=30 IS trades, >=10 OOS trades, both
windows net positive). It is not a certification.

Five chronological folds:

| strategy | fold PFs | profitable | worst PF | compounded | trades |
|---|---|---:|---:|---:|---:|
| `xauby_donchian_trend` | 2.40, 1.13, 2.27, 2.09, 0.47 | 4/5 | 0.468 | **+35.59%** | 130 |
| `supertrend_ema200` L+S | 1.32, 1.81, 1.50, 2.57, 0.60 | 4/5 | **0.602** | +19.68% | 136 |
| `xauby_smc_pro` | 1.61, 0.98, 1.02, 2.16, 0.85 | 3/5 | 0.852 | +13.73% | 242 |
| `supertrend_ema200` long | 0.68, 1.18, 1.57, 2.13, 0.88 | 3/5 | 0.677 | +4.56% | 70 |

The contradiction that motivated the walk-forward is visible here: Donchian
wins full history and compounded folds, SuperTrend wins the out-of-sample
window (1.549 vs 1.326) and has the better worst fold (0.602 vs 0.468).

### Two findings that are not about the ranking

**`xauby_actionzone` does not transfer from gold to BTC.** Its +124.57% net
looks like a win and is not one: 23.68% max drawdown — beyond the 25%
`risk.drawdown_guard` kill-switch once costs move — and OOS PF 0.989 with a
losing OOS window. It banks an old bull run and stops working.

**Shorts earn their keep on BTC.** `supertrend_ema200` long-only posts IS PF
0.896 and -1.22% net; long+short posts 1.513 and +19.24%. This is the opposite
of the XAU result, where every short-bearing shape lost, and it means the two
live pairs' differing side policies are both correct rather than an oversight.

### A harness bug this run found

The first sweep reported `xauby_actionzone` with **zero trades**, which reads
as a verdict on the strategy and is not one. Its resolved config gates on the
D1 regime frame, no daily frame was attached, so the gate read `UNKNOWN` and
blocked every entry — the documented fail-safe behaving correctly on a missing
input. `rival_sweep.run_rival` now attaches the daily frame only to plugins
whose own config gates on it, and **raises** when such a plugin is handed none,
instead of silently returning a flat row. The XAU sweep had the same gap. The
numbers above are from the corrected re-run.

## 2. Walk-forward — can tuning repair the out-of-sample gap?

Anchored rolling: train on the trailing 6 months, pick the best cell of the
strategy's grid on that window only, freeze it, replay the next month, roll
forward. 74 test months, 2020-07 → 2026-07. Nothing in a test month informs the
config that trades it.

**The incumbent gets the same treatment.** Every arm runs `frozen` (one config
for the whole history) and `reopt` (re-tuned monthly) on identical windows.
Tuning the challenger while replaying the champion frozen would compare a tuned
strategy with an untuned one and read the difference as edge.

| arm | mode | +months | pooled PF | compounded | avg mo | worst mo | trades |
|---|---|---:|---:|---:|---:|---:|---:|
| supertrend L+S | frozen | 27/74 | 1.456 | +13.91% | +0.183% | **-1.68%** | 126 |
| supertrend L+S | reopt | 33/74 | 1.335 | +14.64% | +0.198% | -4.62% | 159 |
| donchian long | frozen | 25/74 | 1.802 | **+35.53%** | +0.433% | -2.82% | 124 |
| donchian long | reopt | 24/74 | **1.864** | +33.59% | +0.410% | -3.06% | 101 |
| donchian L+S | frozen | 32/74 | 1.231 | +19.03% | +0.265% | -5.02% | 228 |
| donchian L+S | reopt | 28/74 | 1.361 | +24.56% | +0.320% | -3.03% | 155 |

**Re-optimisation is a wash.** It improves pooled PF for donchian L+S
(1.231 → 1.361) and marginally for donchian long (1.802 → 1.864, while
*lowering* compounded return), and it makes SuperTrend worse (1.456 → 1.335).
No arm gains consistently on both measures. Monthly re-tuning is not what
separates these strategies.

### Where the Donchian advantage actually lives

Per calendar year, frozen arms, pooled PF / compounded:

| arm | 2020 | 2021 | 2022 | 2023 | 2024 | 2025 | 2026 |
|---|---|---|---|---|---|---|---|
| supertrend L+S | 0.625 / -1.0% | 0.968 / -0.3% | **2.727 / +6.8%** | 1.463 / +1.6% | 2.054 / +4.4% | **1.868 / +4.0%** | 0.434 / -2.0% |
| donchian long | **2.788 / +10.7%** | **2.358 / +9.6%** | 0.532 / -2.6% | **2.915 / +11.4%** | 2.209 / +8.2% | 0.787 / -1.0% | 0.045 / -4.0% |
| donchian L+S | 2.454 / +10.3% | 1.040 / +0.3% | 1.227 / +3.4% | 2.248 / +10.6% | 1.187 / +2.5% | 0.533 / -5.9% | 0.669 / -2.5% |

Donchian's entire lead is 2020, 2021 and 2023. It has been **negative for two
consecutive years** (2025 -1.0%, 2026 -4.0%). SuperTrend survived the 2022 bear
(+6.8%, its best year) and 2025 (+4.0%), and both strategies are losing in 2026.

The most recent 24 test months (2024-08 → 2026-07), the era the champion
search's OOS window covers:

| arm | mode | pooled PF | compounded | +months | trades |
|---|---|---:|---:|---:|---:|
| **supertrend L+S** | frozen | **1.602** | **+6.45%** | 9/24 | 49 |
| supertrend L+S | reopt | 1.483 | +5.75% | 12/24 | 58 |
| donchian long | frozen | 1.419 | +3.57% | 8/24 | 39 |
| donchian long | reopt | 1.366 | +3.16% | 7/24 | 36 |
| donchian L+S | frozen | 0.906 | -2.75% | 9/24 | 79 |
| donchian L+S | reopt | 0.861 | -3.79% | 7/24 | 58 |

SuperTrend leads every recent slice, and `reopt` is worse than `frozen` for
**all three** arms here. This resolves the apparent contradiction in section 1:
Donchian wins the full history because of years that no longer resemble the
present, and the out-of-sample window was picking that up correctly.

### By market phase

| arm | bull | bear | sideways |
|---|---|---|---|
| supertrend L+S frozen | 1.568 / +9.9% | 1.439 / +4.2% | 1.293 / +0.5% |
| donchian long frozen | **2.223 / +30.4%** | 1.281 / +3.0% | 1.281 / +0.6% |
| donchian L+S frozen | 1.284 / +12.8% | 1.365 / +9.8% | **0.434 / -4.0%** |

SuperTrend is **positive in all three phases**. Donchian long is a bull-market
instrument: 30.4 of its 35.5 points of compounded return come from bull windows.
A strategy whose edge is that concentrated is one bad regime away from its
average, which is what 2025–2026 shows.

## 3. The Donchian short side

`xauby_donchian_trend` was long-only **in code**, not by configuration: the
plugin imported only `buy`/`sell`, had no short branch, and carried no
`enable_short` key. The identical `__long` and `__ls` rows in section 1 are the
evidence — the flag was inert, not declined.

This study added the mirror image (break the N-bar Donchian low below EMA200,
cover on reclaiming the exit midline or EMA200, same ATR stop and trailing),
behind `enable_short`, **default off** so every previously published long-only
Donchian figure still describes what it measured.

**It makes the strategy worse.** Against donchian long: pooled PF 1.231 vs
1.802 frozen, 1.361 vs 1.864 re-optimised, and -2.75% vs +3.57% over the last
24 months. The short side roughly doubles trade count (228 vs 124) while
lowering profit factor, and it is actively destructive in sideways markets
(PF 0.434, -4.0%).

So the answer to "give Donchian a short side and it can replace SuperTrend" is
no on both halves: the short side hurts, and the long-only version has been
losing for two years. The code is kept because the negative result is worth
being reproducible, and because the plugin is now symmetric if a future study
wants it.

## Limitations

- **Grids are small** (12 cells Donchian, 16 SuperTrend). A six-month train
  window of 4h bars offers few trades; a wider grid would pick winners from
  noise and answer the question with an artifact. A larger search might find
  tuning that helps where this one found none.
- **One venue, one asset, one timeframe.** Nothing here says how these plugins
  behave on 1h, or off OKX.
- **74 monthly windows are not 74 independent observations** — regimes persist
  across months, so per-phase and per-year splits carry fewer effective samples
  than their row counts suggest.
- **Compounded walk-forward totals are not a continuous equity curve.** Each
  window starts flat; use them for stability, not as a headline return.
- **Both strategies lose in 2026 so far.** SuperTrend -2.0%, Donchian -4.0%.
  This study concludes SuperTrend is the better of the two, not that it is
  currently working.
- **Buy & hold returned +820.70%** against SuperTrend's low-exposure walk-forward
  +13.91%. These strategies run 9–17% exposure and roughly one-eighth the
  drawdown; they are not competing for absolute return.

## Reproduction

```bash
PYTHONPATH=. python3 scripts/btc_champion_search.py --workers 4 --out-dir core/btc_champion
PYTHONPATH=. python3 scripts/btc_donchian_wfa.py   --workers 4 --out-dir core/btc_donchian_wfa
```

Roughly 40 and 90 minutes respectively on four cores.
