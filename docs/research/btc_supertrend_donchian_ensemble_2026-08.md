# BTC SuperTrend + Donchian 50/50 shadow certification

**Status: protocol locked; result unmeasured.** This document is not a
certificate. The locked workflow must run on the exact merge commit before an
ensemble record may be proposed for the Shadow Arena.

## Scope

The candidate is a credential-free virtual portfolio on native OKX
`BTC-USDT-SWAP` 4H candles:

- 50% SuperTrend L+S Champion (`okx-btc-supertrend-v1`)
- 50% Donchian long-only 4H research sleeve
- 1,000 USDT initial portfolio equity; each 500 USDT sleeve sizes from its own
  continuously compounded equity
- 0.05% fee and 0.02% slippage per fill, plus the existing locked funding model
- independent sleeve positions, including measurement of opposing-side bars

The complete machine-readable identity, data window, weights, folds, recent
window, costs, and gates are frozen in
[`protocols/btc_supertrend_donchian_ensemble_v1.json`](protocols/btc_supertrend_donchian_ensemble_v1.json).
Changing any member config or weight changes the fingerprint and invalidates the
run.

## Exploratory provenance — not certification evidence

The selection came from branch `claude/xauusdt-backtest-strategy-x5psn1`, which
was behind `main` and used older research harnesses. Only these compressed raw
artifacts and their explanatory reports were carried forward; the branch code,
`CLAUDE.md`, and Donchian-short implementation were not merged.

| archive | SHA-256 of committed `.json.gz` |
|---|---|
| `btc_champion_search_2026-08-08.results.json.gz` | `43e358766a975864db9b466248c6508924bb515832b5c2afb5f073538b7f571e` |
| `btc_donchian_wfa_2026-08-08.results.json.gz` | `299f711269a6f8665f76f45dda4d350b59aea9ab20b7d6936ecd8ac21f42013d` |
| `alpha_correlation_btc_2026-08-10.results.json.gz` | `e87c592e8353848d8b7a9d4fca13d09b4d0fc7ebc7e52a5eaf51fc34218e8278` |

The reported +27.04% portfolio return is therefore a hypothesis, not a locked
result. In particular, the exploratory correlation study reset capital by
calendar month; this protocol instead replays a single chronological clock and
continuous sleeve equity.

## Acceptance

Every registered gate must pass: 10% net uplift over Champion, non-inferior PF,
at least 20% lower MDD, +0.10 Sharpe, three additional positive months, 4/5
profitable folds, 4/5 fold-level MDD non-inferiority, positive latest 24 months
with PF at least 1.10, member monthly correlation at most 0.60, and robust 40/60
and 60/40 controls.

The workflow always uploads `results.json`, `report.md`, reproducibility
metadata, and a `proposed_certificate.json`, including on rejection. A rejected
run stops before runtime integration. A passing run permits only a second PR
that adds forward-shadow support.

Even after a pass, the record remains `shadow_only=true` and
`live_certified=false`. It does not authorize tenant changes, live order flow,
one-way account netting, engine restart, or deployment.
