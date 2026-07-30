# Binance TH Spot BTC/XAUT certification protocol

Status: implementation ready; no live configuration changed.

## Instruments and costs

- Target: `binance-th-spot-usdt`, native Binance TH spot client.
- Pairs: `BTCUSDT` and `XAUTUSDT`; long-only, 1×, stop protected.
- Trading fee: 0.10% per fill. Research and certification must retain the
  target's fee plus configured slippage.

## Pre-registered search

- BTC: `supertrend_ema200`, 1H entries, D1 gate off/on; 192 cells.
- XAUT: `xauby_actionzone`, 4H entries, D1 gate off/on; 144 cells.
- Selection uses a chronological 70/30 IS/OOS split with 300 warm-up bars.
  Both windows must be profitable and meet the trade floors. Finalists are
  replayed over five chronological folds.
- Run only on a non-trading runner:

  `PYTHONPATH=. python3 scripts/binance_th_spot_grid.py --pair all`

The harness writes only to `core/` unless `--out` is supplied. It cannot edit a
preset, certificate, tenant config, or live process.

## Certificate gate

Protocol v2 requires venue-native history of at least 365 days, PF above 1,
positive net return, at least 30 trades, drawdown no greater than 25%, and a
bootstrap probability of profit of at least 90%. A passing record must preserve
the preset fingerprint and structured venue/source metadata.

BTC is eligible to run this gate on Binance TH native history. XAUT selection
may use Binance Global `PAXGUSDT` as a long-history research proxy, followed by
a Binance TH `XAUTUSDT` cross-check. Proxy results can never certify a Binance
TH preset. Until `XAUTUSDT` has 12 months of native history, its record remains
`not_assessed`, the preset remains SIM-only, and `live_certified` remains false.
