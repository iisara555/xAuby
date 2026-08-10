# BTC SMC structure-alpha candidate

Status: **protocol locked; native-OKX result pending**.

The next orthogonal-alpha candidate is `xauby_smc_pro`, using BOS/CHoCH
structure events with fair-value-gap, order-block, and premium/discount
confluence. It is structurally different from the trend-following BTC Champion,
but the existing evidence does not establish that it is better: the July
single-split result was positive while its monthly walk-forward was slightly
negative overall.

The historical report also mislabeled SMC as long+short. Its grid never set the
plugin's `allow_short` switch, whose default is false, so those rows were
long-only. The locked protocol at
`docs/research/protocols/btc_smc_structure_challenger_v1.json` corrects that
ambiguity by preregistering two otherwise identical arms:

- primary: SMC long-only, `confluence_min_score=1.0`;
- diagnostic comparator: the same profile with only `allow_short=true`.

The run uses native OKX `BTC-USDT-SWAP` 4H candles, production-resolved costs
and sizing, full history, and five chronological folds with past-only warmup.
Besides absolute PF/net/drawdown/trade gates, it requires a positive latest
fold, at least one fold that profits while the Champion is nonpositive, and
fold-net correlation no greater than 0.80. These are intentionally demanding:
SMC is useful to an ensemble only if it contributes a distinct return pattern,
not merely another BTC signal.

Passing creates only a proposed certificate for forward shadow. The preset is
`live_certified=false`; this work does not change a tenant config, capital
allocation, the live engine, or deployment state.
