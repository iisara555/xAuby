# BTC BTCUSDT 4h PF grid search (IS 70% / OOS 30%)

Validity gates: IS trades >= 30, OOS trades >= 10, IS net > 0. Ranked by OOS profit factor.

## bbrsi_mean_reversion — 54 combos, 0 pass gates

| combo | IS pf | IS net% | IS wr% | IS trd | IS mdd% | OOS pf | OOS net% | OOS wr% | OOS trd | OOS mdd% |
|---|---|---|---|---|---|---|---|---|---|---|
| (no combo passes; best raw OOS pf) bb_std=3.0_rsi_oversold=20.0_rsi_exit=50.0_sl_atr_mult=1.8 | — | — | — | — | — | 1.23 | +0.3 | 62 | 8 | 1.1 |

## supertrend_ema200 — 96 combos, 52 pass gates

| combo | IS pf | IS net% | IS wr% | IS trd | IS mdd% | OOS pf | OOS net% | OOS wr% | OOS trd | OOS mdd% |
|---|---|---|---|---|---|---|---|---|---|---|
| supertrend_mult=4.0_atr_period=10_sl_atr_mult=3.0_trailing_atr_mult=2.0_exit_on_ema_loss=True | 1.16 | +4.0 | 35 | 83 | 10.5 | 1.63 | +7.1 | 42 | 48 | 3.7 |
| supertrend_mult=4.0_atr_period=10_sl_atr_mult=2.5_trailing_atr_mult=2.0_exit_on_ema_loss=True | 1.15 | +3.8 | 35 | 83 | 11.0 | 1.62 | +7.0 | 42 | 48 | 3.7 |
| supertrend_mult=4.0_atr_period=10_sl_atr_mult=2.0_trailing_atr_mult=2.0_exit_on_ema_loss=True | 1.13 | +3.4 | 35 | 83 | 11.4 | 1.60 | +6.9 | 42 | 48 | 3.7 |
| supertrend_mult=4.0_atr_period=10_sl_atr_mult=3.0_trailing_atr_mult=2.0_exit_on_ema_loss=False | 1.18 | +4.5 | 37 | 83 | 10.9 | 1.55 | +7.0 | 46 | 48 | 4.8 |
| supertrend_mult=4.0_atr_period=10_sl_atr_mult=2.5_trailing_atr_mult=2.0_exit_on_ema_loss=False | 1.17 | +4.3 | 37 | 83 | 11.4 | 1.54 | +6.9 | 46 | 48 | 4.8 |

## xauby_actionzone — 36 combos, 32 pass gates

| combo | IS pf | IS net% | IS wr% | IS trd | IS mdd% | OOS pf | OOS net% | OOS wr% | OOS trd | OOS mdd% |
|---|---|---|---|---|---|---|---|---|---|---|
| ap=2_fz=1_bx=0_sl3.5 | 1.06 | +5.8 | 34 | 282 | 13.4 | 0.86 | -6.3 | 37 | 132 | 13.5 |
| ap=2_fz=1_bx=1_sl3.5 | 1.06 | +5.8 | 34 | 282 | 13.4 | 0.86 | -6.3 | 37 | 132 | 13.5 |
| ap=2_fz=1_bx=0_sl2.5 | 1.00 | +0.4 | 34 | 282 | 16.8 | 0.85 | -6.7 | 37 | 132 | 13.7 |
| ap=2_fz=1_bx=1_sl2.5 | 1.00 | +0.4 | 34 | 282 | 16.8 | 0.85 | -6.7 | 37 | 132 | 13.7 |
| ap=1_fz=2_bx=0_sl3.5 | 1.14 | +28.7 | 37 | 534 | 18.4 | 0.84 | -13.1 | 36 | 244 | 21.5 |

## xauby_smc_pro — 16 combos, 6 pass gates

| combo | IS pf | IS net% | IS wr% | IS trd | IS mdd% | OOS pf | OOS net% | OOS wr% | OOS trd | OOS mdd% |
|---|---|---|---|---|---|---|---|---|---|---|
| confluence_min_score=1.5_require_liquidity_sweep=False_fvg_min_atr=0.0 | 1.18 | +3.2 | 38 | 76 | 6.3 | 1.60 | +3.5 | 44 | 36 | 2.9 |
| confluence_min_score=2.0_require_liquidity_sweep=False_fvg_min_atr=0.0 | 1.18 | +3.2 | 38 | 76 | 6.3 | 1.60 | +3.5 | 44 | 36 | 2.9 |
| confluence_min_score=0.5_require_liquidity_sweep=False_fvg_min_atr=0.0 | 1.32 | +11.4 | 38 | 167 | 7.9 | 1.22 | +3.1 | 35 | 71 | 4.5 |
| confluence_min_score=1.0_require_liquidity_sweep=False_fvg_min_atr=0.0 | 1.32 | +11.4 | 38 | 167 | 7.9 | 1.22 | +3.1 | 35 | 71 | 4.5 |
| confluence_min_score=0.5_require_liquidity_sweep=False_fvg_min_atr=0.25 | 1.32 | +11.0 | 38 | 165 | 7.8 | 1.16 | +2.5 | 34 | 70 | 5.0 |

## Reading

* **supertrend_ema200 is the clear PF winner and generalizes**: 52/96 combos
  pass the gates and the whole top-5 clusters around `mult=4.0, atr_period=10,
  trailing 2.0×ATR` (OOS pf 1.54–1.63, OOS +7%, mdd < 5%). OOS beating IS —
  wider SuperTrend bands cut whipsaw; the parameter surface is smooth, so this
  is structure, not a lucky cell.
* **xauby_smc_pro comes second**: `confluence_min_score>=1.5,
  require_liquidity_sweep=False, fvg_min_atr=0` gives OOS pf 1.60 (+3.5%,
  36 trades, mdd 2.9%). Tightening confluence trades quantity for quality;
  requiring the sweep or padding FVG with an ATR floor only hurts.
* **xauby_actionzone fails OOS in all 36 variants** (best OOS pf 0.86) even
  after adding ATR-stop exits — the IS edge (pf up to 1.14) does not carry
  into 2024–2026 BTC. Consistent with the walk-forward: CDC's gold profile
  isn't a BTC 4h strategy without deeper redesign.
* **bbrsi_mean_reversion: 0/54 combos pass** — no configuration produced a
  positive in-sample net with enough trades. Mean reversion on BTC 4h loses
  after costs across the entire grid; the "best" OOS cell (pf 1.23) trades
  8 times in 2 years, i.e. noise.
* Caveat: ranking by OOS pf after gating is still a selection on OOS — treat
  the exact winner as a candidate for the walk-forward/regime pipeline
  (`regime_strategy_eval.py`), not a deployable config. The cluster stability
  (many near-identical neighbors) is the real signal here.
