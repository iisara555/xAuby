# BTC SuperTrend + Donchian 50/50 shadow certification

**Status: REJECTED on 2026-08-10; no certificate and no runtime candidate.**
The locked workflow ran on merge commit
`78f1b2b67e9939d28249f770a49e897a3fadfdf9` ([run 31391136677](https://github.com/iisara555/xAuby/actions/runs/31391136677)).
It published failed evidence exactly as required and stopped before Shadow Arena
integration.

## Locked result

The 50/50 book improved the full-history headline numbers: +25.87% net versus
+17.37% Champion, PF 1.572 versus 1.471, MDD 4.52% versus 9.81%, Sharpe 0.874
versus 0.593, and 32 versus 29 positive months. The latest 24 complete months
also remained positive (+4.82%, PF 1.446), member monthly correlation was 0.105,
and both weight-sensitivity controls passed.

It nevertheless failed two pre-registered gates:

- only 2/5 chronological folds had MDD no higher than Champion; 4/5 were
  required
- current Donchian 4H replay missed exploratory parity by one trade, -1.21
  percentage points net, and -0.0166 PF

Fold 5 was negative for both books and worse for the ensemble (-3.11% versus
-2.64%). Under the locked protocol this is a rejection even though most
aggregate metrics improved. No thresholds or weights were changed after seeing
the result.

## Parity repair rerun — 2026-08-17

The exploratory-parity mismatch was an implementation error in the
certification harness, not strategy drift. The hash-locked exploratory
full-history run began at Donchian's native 240-bar minimum, while the first
certification run incorrectly reused the 300-bar no-trade lead-in intended for
chronological folds and windows. That removed the first trade from the parity
comparison.

[Workflow run 31990266216](https://github.com/iisara555/xAuby/actions/runs/31990266216)
on commit `b131b41337315c3912e1c76397359377df051218` separated the parity replay
without changing the portfolio replay, member configs, 50/50 weights, or any
acceptance threshold. The parity arm matched all locked values exactly: 130
trades, +35.579902% net, PF 1.657913689, and 6.740837% MDD.

The overall verdict remains **REJECTED**. The unchanged portfolio still passed
only 2/5 fold-level drawdown non-inferiority checks against the required 4/5.
Accordingly, regime-aware ensemble, dynamic-sizing, and runtime-shadow
integration remain blocked.

Committed evidence is under
[`runs/btc_supertrend_donchian_ensemble_2026-08-10/`](runs/btc_supertrend_donchian_ensemble_2026-08-10/).
The immutable `results.json` SHA-256 is
`d341aa285ae40710b4f5aa9b1c2bf41a09c59832c90538c0d6eb5daf81264ae5`.

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
