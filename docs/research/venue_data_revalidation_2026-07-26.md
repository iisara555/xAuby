# Re-validation on OKX venue data — both live pairs

**Date:** 2026-07-26. **Roadmap item:** P0.2.
**Harness:** `scripts/validate_on_venue_data.py` (added with this document).
**Prerequisite:** P0.1 — before it, no backtest could reach OKX at all.

Every certificate in `docs/research/` was produced on Binance data while the
engine trades OKX perpetual swaps. This re-runs the **deployed** configs against
candles pulled from OKX itself.

Reproduce:

```bash
PYTHONPATH=. python3 scripts/validate_on_venue_data.py --pair all
```

## Headline

| verdict | pair |
|---|---|
| **PASS — certificate survives the venue change** | BTC `supertrend_ema200` |
| **Cannot be certified on native venue data; a third dataset confirms it is marginal** | XAU `xauby_actionzone` |

## Results

All runs use the deployed config resolved through `_prepare_backtest_config` —
the same resolver live and replay use — with production fee, slippage, and
funding from `bot_config.yaml`.

| run | source | bars | window | n | WR% | PF | net% | MDD% | CAGR% | Calmar | Sharpe |
|---|---|---|---|---|---|---|---|---|---|---|---|
| BTC okx-swap | BTC-USDT-SWAP | 14485 | 2019-12 → 2026-07 | 134 | 38.06 | **1.52** | 19.35 | 9.81 | 2.76 | 0.28 | 0.65 |
| BTC cert window | BTC-USDT-SWAP | 12342 | 2021-01 → 2026-06 | **111** | 41.44 | **1.59** | **16.39** | 3.87 | 2.79 | 0.72 | 0.70 |
| XAU okx-swap | XAU-USDT-SWAP | 2838 | 2025-04 → 2026-07 | 90 | 37.78 | 1.42 | 28.66 | 9.44 | 22.33 | 2.37 | 1.27 |
| overlap: real swap | XAU-USDT-SWAP | 2838 | 2025-04 → 2026-07 | 90 | 37.78 | 1.42 | 28.66 | 9.44 | 22.33 | 2.37 | 1.27 |
| overlap: XAUT proxy | XAUT-USDT | 2838 | 2025-04 → 2026-07 | 87 | 40.23 | 1.39 | 25.74 | 9.89 | 20.11 | 2.03 | 1.17 |
| **XAUT full depth** | XAUT-USDT | 8808 | 2022-07 → 2026-07 | 296 | 32.77 | **1.17** | 31.02 | **17.66** | 7.03 | 0.40 | 0.51 |

Proxy fidelity over the 2838 aligned bars: **close-price correlation 1.00,
bar-return correlation 0.99.**

## 1. BTC passes, and the numbers improve on the real venue

Clipped to the certificate's exact window (2021-01 → 2026-06, 300-bar warmup
lead-in), the OKX swap replay produces **111 trades — the identical trade count
the certificate reports**. That exact match is the strongest available evidence
that this harness reproduces the certified setup rather than approximating it.

On that same window the venue change moves the result **up**: **+16.39%** on
OKX BTC-USDT-SWAP versus the **+9.8% compounded** the certificate claims on
Binance spot, with PF 1.59 and MDD 3.87%.

Caveat #1 of `btc_supertrend_ema200_certificate_2026-07.md` reads: *"Proxy data.
Backtest candles are Binance spot BTCUSDT. Live target is OKX BTC-USDT perpetual
swap."* **That caveat is now closed.** The remaining caveat (selection-on-OOS) is
untouched by this work.

Note the honest limit of the BTC result: PF 1.52 and CAGR 2.76% over the full
6.6y is *positive but modest*. It passes; it is not spectacular.

## 2. XAU cannot be certified on its own venue data

**OKX lists XAU-USDT-SWAP only from 2025-04-09** — 1.29 years, 2838 4h bars.
That is too short for the walk-forward the roadmap asked for. This is a hard
constraint, not a tooling gap.

## 3. The gold-token proxy assumption is validated

The 6-year XAU certificate rests entirely on the premise that a gold token
stands in for the gold swap. Nobody had measured that. On the overlapping window:

- price correlation **1.00**, bar-return correlation **0.99**
- strategy metrics within noise: PF 1.42 vs 1.39, MDD 9.44% vs 9.89%,
  WR 37.78% vs 40.23%, 90 vs 87 trades

**The certificate's proxy *methodology* was sound.** What was wrong with the July
certificate was the config it measured (long-only + D1 filter, documented in
`xau_deployed_config_reproduction_2026-07-26.md`), not the use of a proxy.

## 4. A third independent dataset confirms the deployed XAU config is marginal

| dataset | window | PF | MDD% |
|---|---|---|---|
| PAXGUSDT (Binance) | 6y | 1.28 | 29.2 |
| **XAUT-USDT (OKX)** | **4.02y** | **1.17** | **17.66** |
| XAU-USDT-SWAP (OKX) | 1.29y | 1.42 | 9.44 |
| *July certificate claim* | *6y* | *2.00* | *8.3* |

Three datasets, two venues, three different instruments now agree the deployed
config sits around **PF 1.2**, not the certified PF 2.00.

## 5. The trap in validating XAU on native data alone

The native swap window (1.29y) scores **PF 1.42, MDD 9.44%, CAGR 22.33%,
Sharpe 1.27**. The 4-year view of the same config scores **PF 1.17, MDD 17.66%,
CAGR 7.03%, Sharpe 0.51**.

**2025-04 → 2026-07 is a favorable gold regime, not a representative sample.**
Anyone who followed P0.2's instruction literally — validate on native venue data —
would have come away with a materially over-optimistic picture and called it a
venue-verified certificate. The longer proxy series is what prevents that error,
which inverts the roadmap's framing: the proxy is not a compromise to be retired,
it is the only thing here providing enough history to be honest.

`drawdown_guard.max_drawdown_pct` is 25.0. The 4-year MDD of 17.66% fits under
it; the 6-year PAXG MDD of 29.2% does not.

## 6. The configured XAU backtest path is broken

`backtest.data_source: okx` and `strategy_params.backtest_data_proxy: PAXGUSDT`
are **mutually incompatible**: OKX does not list PAXG-USDT-SWAP, so resolving
the XAU proxy raises OKX error 51001. Before P0.1 the same call failed silently
into an empty frame and fell back to stale cache — which is exactly why this was
never noticed.

**Recommended fix: switch the XAU backtest proxy to OKX `XAUT-USDT`.**

| | Binance PAXGUSDT | OKX PAXG-USDT | **OKX XAUT-USDT** |
|---|---|---|---|
| history | ~6y | 0.78y | **4.02y** |
| same venue as live | no | yes | **yes** |
| reachable | geo-blocked here | yes | **yes** |
| correlation to XAU swap | unmeasured | — | **0.99 / 1.00** |

OKX PAXG-USDT spot is shallower (0.78y) than the swap it would extend, so it is
useless for this purpose. XAUT-USDT gives ~3x the native history on the venue we
trade, with measured fidelity.

This document does **not** apply that change — it touches a certified pair's
config and belongs to P0.3.

## 7. A second silent-failure vector, fixed

Binance Global refuses this environment (HTTP 451; via curl, HTTP 200 with a JSON
error object). `download_klines` broke out of its pagination loop on both and
returned an empty frame, so *"the venue refuses to serve us"* was indistinguishable
from *"this symbol has no history."* Same bug class as P0.1, one layer up.

`download_klines` now raises when it collects **zero** candles and a cause is
known, while still returning partial data on a mid-pagination error and a quiet
empty frame for a genuinely empty result. Covered by
`tests/test_backtest_okx_data.py::TestBinanceFailuresAreNotSilent`.

## Limitations

- The certificate's 6-year PAXG series is **not reproducible from this
  environment** (Binance geo-block). The PF 1.28 / MDD 29.2% figures for PAXG are
  carried over from `xau_deployed_config_reproduction_2026-07-26.md`, which ran
  against cached data; they are not re-derived here.
- XAU runs attach no D1 regime frame. Correct for the deployed config
  (`use_d1_regime_filter: false`), but it means these runs say nothing about the
  long-only + D1 variant that the July certificate actually measured.
- Funding is the flat `backtest.funding_rate_8h` approximation, not real OKX
  funding history — unchanged from the certificates.
- Slippage stays at the configured `slippage_bps: 2.0`. Gold-token spot books
  (XAUT) are thinner than the XAU swap, so the proxy's fills are, if anything,
  optimistic on that axis.
