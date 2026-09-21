# Binance TH weekly CDC paper candidate

This directory is an isolated paper-trading candidate for `BTCTHB` and
`SOLTHB`. It is not loaded by the production tenant and does not change the
running OKX engine.

The strategy follows the basic CDC ActionZone V3 colour method on confirmed
weekly candles:

- buy spot when a completed week is `GREEN`;
- hold an existing long until a completed week is `RED`;
- stay flat in all other zones;
- never short, use leverage, or act on the forming week.

The profile assumes a paper balance of 100,000 THB, caps each symbol at 45% of
paper equity, reserves 10%, and models the standard Binance TH fiat-pair fee of
0.25% per fill plus 10 bps of slippage. These are research assumptions, not a
claim about the user's account tier or future execution.

Run a read-only signal snapshot from public Binance TH data:

```bash
PYTHONPATH=. python3 scripts/weekly_cdc_snapshot.py
```

The snapshot validates ordering, complete weekly intervals, missing candles,
OHLCV values, at least 100 completed weeks, and exclusion of the forming week.
It does not place or simulate orders and does not assess profitability.

Before any live activation, backtest the final rules with native venue data and
fees, forward-test the isolated paper tenant, review drawdown and trade count,
then use the repository's controlled deployment and restart gates. Do not copy
this directory over an active tenant config.
