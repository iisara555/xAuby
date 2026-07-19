# Certification — SuperTrend+EMA200 long+short, BTC 4h

**Status: PASS (out-of-sample, fixed config).**
Date: 2026-07-19. Validator: `scripts/btc_wfa_multi_strategy.py`.
Raw: `core/btc_wfa/cert_supertrend_fixed.json`.

## Certified configuration

Winning cell of the PF grid search
(`docs/btc_pf_grid_4h_2026-07.md`), held **constant** — no per-window
re-optimization:

```yaml
strategy: supertrend_ema200
primary_timeframe: 4h
confirm_timeframe: ""        # strategy uses a single timeframe
enable_short: true
supertrend_mult: 4.0
atr_period: 10
ema_period: 200
sl_atr_mult: 3.0
trailing_atr_mult: 2.0
exit_on_ema_loss: true
exit_on_supertrend_flip: true
entry_on_flip_only: true
```

## Certification method

1. The config was **selected** on the last 30% of history (2024-mid → 2026-07)
   by the PF grid search.
2. It was then **frozen** and replayed month-by-month across **all 66 test
   months** (2021-01 → 2026-06). The first ~70% of that span is genuinely
   out-of-sample relative to where the config was chosen.
3. Every month uses a 300-bar warmup lead-in of prior bars that only warms
   indicators (no warmup trades), decisions on the last closed bar, fills at
   the next bar open, and production fee/slippage/funding from `bot_config.yaml`.
   No look-ahead.

## Result (66 out-of-sample months)

| phase | months | +months | avg mo % | compounded % | worst mo % | best mo % | trades |
|---|---|---|---|---|---|---|---|
| ALL | 66 | 24 | +0.15 | **+9.8** | -1.73 | +4.51 | 111 |
| bull | 38 | 14 | +0.19 | +7.3 | -1.73 | +4.51 | 63 |
| bear | 23 | 8 | +0.08 | +1.8 | -1.24 | +3.77 | 39 |
| sideways | 5 | 2 | +0.11 | +0.5 | -0.43 | +1.10 | 9 |

Sizing: risk-based, `risk_pct` ≈ 3% of equity per SL distance (NOT the CDC
fixed-fraction profile). Worst single month -1.73%; positive-compounded in all
three market phases.

## Caveats (read before any live capital)

* **Proxy data.** Backtest candles are Binance **spot** BTCUSDT. Live target is
  OKX **BTC-USDT perpetual swap**. Funding, spread, and fill behavior differ;
  the funding approximation is a flat `funding_rate_8h`, not real OKX funding
  history.
* **Selection-on-OOS.** The config is the grid winner ranked by OOS PF; the
  66-month replay re-uses part of that data. The genuine evidence is (a) the
  smooth parameter cluster around `mult=4.0` and (b) positivity in the earlier,
  un-selected span — not the single point estimate.
* **Never traded live.** No OKX live fills exist for this strategy. Standard
  path is sim/paper on the real OKX feed before real capital.
* Monthly returns are modest (+0.15%/mo avg). This certifies *robustness of
  sign*, not a high-return claim.

## Repro

```bash
python scripts/btc_wfa_multi_strategy.py fetch
python - <<'PY'
import scripts.btc_wfa_multi_strategy as w; w._init_worker()
WIN = {"enable_short": True, "supertrend_mult": 4.0, "atr_period": 10,
       "sl_atr_mult": 3.0, "trailing_atr_mult": 2.0, "exit_on_ema_loss": True}
for y, m in w._test_months():
    t = w._slice(w._G["df4"], w._month_ts(y, m), w._month_ts(*w._month_add(y, m, 1)), w.WARMUP_BARS)
    df, skip = t
    r = w._run("supertrend_ema200", df, WIN, skip)
    print(f"{y}-{m:02d}", w._phase_label(w._month_ts(y, m)), round(r["net_profit_pct"], 2), r["total_trades"])
PY
```
