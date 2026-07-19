# BTC 4h — Squeeze Momentum (LazyBear), same method as the 4-strategy study

July 2026 run of `scripts/btc_wfa_multi_strategy.py` for the new
`squeeze_momentum` plugin (faithful port of the TradingView "Squeeze Momentum
Indicator [LazyBear]": BB-inside-KC squeeze + linreg momentum histogram),
BTCUSDT 4h, long + short. Same protocol as
`btc_wfa_4h_2026-07.md` / `btc_pf_grid_4h_2026-07.md`:

* Walk-forward: train 6 months → optimize grid on train only → test the next
  month out-of-sample → roll forward 1 month (66 test months).
* PF grid: single 70/30 IS/OOS split, non-traded warmup lead-in, ranked by OOS
  profit factor behind IS≥30 / OOS≥10 trade gates and IS net > 0.
* No look-ahead: decide on the last closed bar, fill next open; parameters from
  train only; phase labels from pre-month 1d data; production fee/slippage/funding.
* Sizing: risk-based, `risk_pct` = 2% per SL distance.

## 1. Walk-forward, out-of-sample by market phase

| phase | months | +months | avg mo % | compounded % | worst mo % | best mo % | trades |
|---|---|---|---|---|---|---|---|
| ALL | 66 | 31 | -0.10 | -7.0 | -3.65 | +4.38 | 405 |
| bull | 38 | 19 | +0.18 | +6.5 | -2.58 | +4.38 | 257 |
| bear | 23 | 11 | -0.29 | -6.7 | -3.17 | +3.33 | 118 |
| sideways | 5 | 1 | -1.31 | -6.4 | -3.65 | +0.90 | 30 |

Positive only in bull months (+6.5% compounded); gives it all back in bear and
sideways. Net −7.0% over the full walk-forward.

## 2. PF grid — best configs (54 combos, 21 pass gates, ranked by OOS PF)

| combo | IS pf | IS net% | IS trd | OOS pf | OOS net% | OOS wr% | OOS trd | OOS mdd% |
|---|---|---|---|---|---|---|---|---|
| kc_length=14, kc_mult=1.0, squeeze_release, sl=2.0×ATR | 1.84 | +14.2 | 85 | **1.73** | +1.6 | 44 | 18 | 1.8 |
| kc_length=14, kc_mult=1.0, squeeze_release, sl=2.5×ATR | 1.84 | +14.2 | 85 | 1.73 | +1.6 | 44 | 18 | 1.8 |
| kc_length=20, kc_mult=1.0, squeeze_release, sl=2.0×ATR | 1.07 | +2.8 | 156 | 1.22 | +1.3 | 39 | 38 | 4.1 |
| kc_length=20, kc_mult=1.0, squeeze_release, sl=2.5×ATR | 1.08 | +3.2 | 156 | 1.22 | +1.3 | 39 | 38 | 4.1 |

**Best config (highest OOS PF):**

```yaml
strategy: squeeze_momentum
primary_timeframe: 4h
enable_short: true
entry_trigger: squeeze_release   # enter when the squeeze fires, in momentum's direction
bb_length: 20
bb_mult: 2.0
kc_length: 14
kc_mult: 1.0
exit_on_zero_cross: true
sl_atr_mult: 2.0
trailing_atr_mult: 2.0
```

## 3. Reading

1. **`squeeze_release` beats `zero_cross` decisively.** Every combo that passes
   the gates uses squeeze-release entry — waiting for the BB-out-of-KC "fire"
   filters out the chop that a bare zero-cross trades. Zero-cross alone floods
   trades and loses.
2. **The strong cell is thin.** `kc_length=14, kc_mult=1.0` shows OOS pf 1.73 —
   but on only **18 OOS trades** in ~2 years and OOS net just +1.6%. `kc_mult=1.0`
   narrows the Keltner channel so the squeeze rarely arms; high PF, tiny sample,
   real overfitting risk. The `kc_length=20` cluster (38 trades, pf 1.22) is the
   more trustworthy — but only modestly profitable.
3. **Walk-forward disagrees with the grid.** Re-optimized every month, the
   strategy is net −7.0% and only works in bull phases. The grid's positive PF
   comes from a narrow parameter island that does not survive monthly
   re-selection — the classic gap between "best single split" and "robust across
   time."

## 4. Where it ranks vs the earlier four

| strategy | WFA compounded (66 mo) | best OOS PF (grid) | verdict |
|---|---|---|---|
| **supertrend_ema200** | **+12.4%** (all phases +) | **1.63** (52/96 pass) | robust — deployed |
| xauby_smc_pro | -0.7% | 1.60 (6/16 pass) | cost-neutral |
| **squeeze_momentum** | **-7.0%** (bull only) | 1.73 but 18 trades (21/54 pass) | bull-only, thin edge |
| bbrsi_mean_reversion | -15.7% | — (0 pass) | reject |
| xauby_actionzone | -62.8% | 0.86 (fails OOS) | reject on BTC |

**Verdict:** Squeeze Momentum with `squeeze_release` entry is real but weak on
BTC 4h — a bull-phase momentum filter, not a standalone all-weather system. Its
grid PF (1.73) edges SuperTrend's (1.63), but on a fraction of the trades and
with a walk-forward that goes negative, whereas SuperTrend stays positive in
every phase across all 66 months. **Not a live-deploy candidate as-is**; a
sensible next step is to route it as a bull-regime-only strategy behind the
regime router, or pair squeeze-release as an entry *filter* on top of the
SuperTrend trend model rather than trading it alone.

## Repro

```bash
python scripts/btc_wfa_multi_strategy.py run  --strategy squeeze_momentum
python scripts/btc_wfa_multi_strategy.py grid --strategy squeeze_momentum
python scripts/btc_wfa_multi_strategy.py gridreport   # includes all strategies
```
