# BTC/XAUUSDT 15-minute candidate screen — 9 September 2026

Created six research candidates in LONA and completed 12 remote backtests on
native OKX swap candles. **All six failed the preregistered screening gate.**
They are retained in `registry.json` with `status=rejected_screen`, zero live
weight, and their LONA strategy/report IDs. No candidate is certified for the
xAuby production Arena. No runtime plugin, tenant configuration or deployment
was changed by this study.

## What was tested

The same three fixed families were tested independently on `BTC-USDT-SWAP`
and `XAU-USDT-SWAP`, long and short, on **15m**:

| Family | Closed-bar entry | Intended market condition |
| --- | --- | --- |
| Donchian 48 + EMA200 | Break previous 48-bar channel with EMA direction and ER20 >= 0.30 | Directional expansion |
| EMA20/session-VWAP pullback | Reclaim EMA20 after a pullback, aligned with EMA50/200 and UTC-session VWAP; ER20 >= 0.25 | Trend pullbacks |
| Bollinger/RSI range | Re-enter BB(20,2) after an outside close with RSI14 < 35 or > 65; ER20 <= 0.25 | Ranging prices |

These are explicitly specified research implementations, not a claim that a
published indicator is profitable and not a reproduction of xAuby's live
plugins. [Donchian channel documentation](https://www.quantconnect.com/docs/v2/writing-algorithms/indicators/supported-indicators/donchian-channel)
and [intraday VWAP documentation](https://www.quantconnect.com/docs/v2/writing-algorithms/indicators/supported-indicators/intraday-vwap)
provide the indicator definitions. The re-entry rule uses the distinction in
[John Bollinger's rules](https://www.bollingerbands.com/bollinger-band-rules)
between a band touch and a reversal signal. The ER thresholds and risk/exit
parameters are this study's hypotheses, fixed before the first result.

## Data and execution

Each uploaded dataset contains **17,568 confirmed 15m bars**, 2026-03-09 00:00
through 2026-09-07 23:45 UTC, with no missing timestamps, duplicates or zero-volume
bars. These are the actual USDT swap instruments, not gold-token/spot proxies.
Only public OHLCV was uploaded. Dataset hashes and LONA IDs are in `registry.json`.

Development window: 12 March–9 July, with a 9 March warmup feed. Untuned holdout:
10 July–7 September, with a 7 July warmup feed. Flatten signals begin on 8 July
and 6 September, respectively, leaving two days for final execution. All twelve
reports end flat. The periods are independent simulations, each starting with
10,000 USDT; their returns must not be added as a continuous portfolio.

Orders use next-bar open fills (`buy_on_close=false`), consistent with the
[Backtrader market-order model](https://www.backtrader.com/docu/order-creation-execution/order-creation-execution/).
Each position is capped at 25% of equity at the signal close, with nominal 0.5%
ATR risk, 1x leverage and current OKX quantity increments. ATR stops/trails are
**close-based exits executed at the next open**, not intrabar protective orders;
actual loss can exceed nominal risk. This is a material limitation for 15m.

Costs are **0.07% per side**: an assumed 0.05% taker fee plus 0.02% friction.
This is a proportional cost approximation, not a reconstructed spread/slippage
path. Historical funding, historical contract-rule changes, liquidation, market
impact and account-specific fees are not modeled. Candidate results therefore
do not establish live execution parity.

## Results after modeled costs

| Pair | Candidate | Development net | Holdout net | Holdout PF | Holdout max DD | Holdout trades |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| BTCUSDT | Donchian | -2.95% | -1.55% | 0.79 | 3.51% | 69 |
| BTCUSDT | EMA/VWAP pullback | -4.94% | -1.14% | 0.49 | 1.41% | 29 |
| BTCUSDT | BB/RSI range | -4.20% | -2.18% | 0.34 | 2.48% | 52 |
| XAUUSDT | Donchian | -5.34% | -1.78% | 0.70 | 2.54% | 75 |
| XAUUSDT | EMA/VWAP pullback | -1.12% | -0.55% | 0.60 | 0.72% | 20 |
| XAUUSDT | BB/RSI range | -4.11% | -1.86% | 0.27 | 1.86% | 64 |

The frozen gate required positive development and holdout returns, holdout
PF >= 1.20, at least 30 holdout trades, maximum DD <= 10%, and no open trades.
None passed. The planned 0.12%-per-side stress run was conditional on passing;
it was not run. This study used **24 credits**, leaving **74** of the account's
100 monthly credits (two credits had been used in the earlier daily smoke test).

Costs consume the apparent edge in several cases. BTC Donchian's holdout gross
PnL was +82.82 USDT, but modeled costs were 237.65 USDT, producing -154.83 USDT
net. XAU Donchian similarly went from +80.97 gross to -178.19 net. A lower fee
assumption would not by itself establish that either strategy is deployable.

## Regime and side observations

`screen_results.json` attributes realized net PnL by entry regime, direction and
exit month. Regimes here use **ER20 calculated on the prior completed bar**;
they are descriptive research labels, not the production xAuby classifier.

Two positive subgroups are worth preserving as hypotheses for a future locked
study: BTC Donchian LONG trades contributed +76.28 USDT across 39 holdout trades
(PF 1.19), while its SHORT trades lost 231.11 USDT. XAU VWAP LONG trades
contributed +37.63 USDT across only 11 trades (PF 1.87), while its nine SHORT
trades all lost, totaling 92.39 USDT.

These are **post-hoc slices of the combined strategy**, not separately tested
long-only candidates. Removing shorts changes capital, sizing and eligible
entry timing. They cannot inherit the combined run's holdout status, certify a
new profile, or justify a positive allocation. Six inspected holdouts also
introduce selection bias. A follow-up should preregister its side policy and
timeframe comparison, use another untouched window, and include native xAuby
replay before any Arena admission. The sample is 180 days, not multiple cycles.

## Evidence and reproducibility

- `strategy.py`: exact source template. `registry.json` records per-candidate
  default substitutions, remote IDs, every request, dataset hashes and status.
- `protocol.json`: frozen windows, family parameters, cost model and gates.
- `reports/*.json`: all 12 LONA full reports and individual gross trade ledgers.
- `audit_results.py`: independent gross-to-net reconciliation, reported-fill
  checks against OHLCV opens, contract-step checks, window/overlap checks and
  descriptive regime attribution. All twelve reports pass; max DD is retained
  from LONA's summary rather than reconstructed from closed trades.
- `screen_results.json`: audited metrics, failure reasons and side/regime/month
  breakdowns. LONA's intraday Sharpe field was zero throughout, so it was not
  used for screening or ranking.

Public candles remain in the ignored checkout-local
`core/lona_15m_candidates/` and in the two LONA datasets. On this research
checkout, reproduce the accounting audit (no backtest execution) with:

```bash
python3 docs/research/lona_15m_2026_09/audit_results.py --data-dir core/lona_15m_candidates
PYTHONPATH=. python3 -m pytest -q tests/test_lona_intraday_audit.py
```

To repeat remote runs, upload the hashed CSVs if necessary, create each strategy
from the template with its `source_default_overrides`, then pass the recorded
request fields to LONA. The current `prepare_data.py` exports this fixed window
from the source CSVs fetched by `scripts/fetch_okx_xau_history.py`; validate the
hashes if the exchange subsequently revises historical candles. No backtest or
optimizer was executed on the trading VPS.
