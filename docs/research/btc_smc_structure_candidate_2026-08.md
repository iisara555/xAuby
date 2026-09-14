# BTC SMC structure-alpha candidate

Status: **REJECTED — research only, not eligible for Strategy Arena shadow or live trading**.

- Run: [GitHub Actions 31350341430](https://github.com/iisara555/xAuby/actions/runs/31350341430)
- Measured commit: `2645f80ee936a3524f87c865c1d8e2fcda6ad917`
- Protocol: `docs/research/protocols/btc_smc_structure_challenger_v1.json`
- Manifest SHA-256: `ac3817ee50d1b5d6f9763e2c94ca77c7549f045736855aab5bcfa40f68dbd1d1`
- Results SHA-256: `5bd1a91bc48035dca4c4d146aa302bff7052f0c15ad1eb077afb0209e356549e`
- Proposed-certificate SHA-256: `eeefa8a2d317022c5b985967fb178d6bc6264282fe2272830d26e9df90a2c129`
- Report SHA-256: `3ad97a258d3fb818158de6f629c1a996ace1a40856efa84733712ffa1efd4922`

## What was tested

The candidate uses `xauby_smc_pro`: BOS/CHoCH structure events with fair-value
gap, order-block, and premium/discount confluence. The locked run compared two
otherwise identical profiles on 14,573 native OKX `BTC-USDT-SWAP` 4H candles
from December 2019 through August 2026:

- primary: SMC long-only, `confluence_min_score=1.0`;
- side-policy comparator: the same profile with only `allow_short=true`.

The historical July WFA had described SMC as long+short, but its grid never set
`allow_short`; those published rows were long-only. This run is the first
predeclared SMC side-policy comparison in the repository.

## Full-history result

| arm | PF | net | MDD | trades | exposure | Calmar |
|---|---:|---:|---:|---:|---:|---:|
| BTC Champion | **1.510** | **+19.16%** | 9.81% | 136 | 9.52% | 0.277 |
| SMC long-only | 1.265 | +13.34% | **6.48%** | 243 | 16.04% | **0.297** |
| SMC long+short | 1.136 | +13.40% | 9.41% | 443 | 27.09% | 0.205 |

Long-only SMC has a real positive historical result and lower drawdown than the
Champion. Enabling shorts adds 200 trades and only 0.05 percentage points of
net return while reducing PF by 10.2%, increasing drawdown by 45.2%, and raising
exposure by 69%. The short arm is therefore not the direction to pursue.

## Five chronological folds

| fold | Champion PF / net | SMC long-only PF / net | SMC long+short PF / net |
|---:|---:|---:|---:|
| 1 | 1.274 / +2.58% | 1.595 / +5.95% | 1.547 / +8.15% |
| 2 | 1.815 / +6.75% | 0.944 / -0.44% | 1.020 / +0.40% |
| 3 | 1.499 / +2.13% | 1.056 / +0.62% | 1.007 / +0.13% |
| 4 | 2.568 / +9.51% | 2.158 / +8.76% | 1.294 / +5.66% |
| 5 | 0.596 / -2.71% | 0.839 / -1.69% | 0.932 / -1.16% |

The primary candidate passed 13 of 15 locked checks. It failed the two checks
that establish whether this is useful alpha now and whether it complements the
Champion:

- latest-fold net had to be positive; observed **-1.69%**;
- at least one fold had to be SMC-positive while the Champion was nonpositive;
  observed **zero**.

Fold-net correlation was 0.633, below the locked 0.80 ceiling, but low
correlation alone is insufficient when both strategies lose in the same only
Champion-negative fold. The candidate diversifies the shape of returns without
providing the required downside complement.

## Decision and next research direction

The failed certificate is published at
`xauby/saas/certificates/okx-btc-smc-structure-long-v1.json`. The catalog must
show `failed`, while `live_certified=false` remains unchanged. A failed preset
cannot enter Strategy Arena, cannot be promoted, and does not change tenant or
runtime configuration.

Do not loosen the gates or optimize another confluence value on this same
history and call it unseen evidence. If SMC continues, the defensible next
candidate is a separately versioned, regime-conditioned long-only design that
can switch off during the shared losing regime, selected with nested
walk-forward and then judged on new forward data. Until that exists, the BTC
Champion and certified LONG-D1 Challenger remain the valid ensemble track.
