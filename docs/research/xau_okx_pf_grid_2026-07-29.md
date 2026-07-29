# XAUUSDT perpetual — OKX profit-factor grid

- **Run date:** 2026-07-28/29
- **Venue:** OKX
- **Strategy:** `xauby_actionzone`, 4h entries with optional 1d regime gate
- **Reproduction harness:** `scripts/xau_okx_pf_grid.py`
- **Successful GitHub Actions run:** [30374751197](https://github.com/iisara555/xAuby/actions/runs/30374751197), commit `17e7693`
**Result JSON SHA-256:** `d759cf7c99bd5f6bdeea49df080b1f10f49304e0a5a8a675f25d3831aa10a212`

## Verdict

The best **robust** candidate is not the cell with the single highest OOS PF.
It is this long-only + D1 configuration:

```yaml
enable_short: false
use_d1_regime_filter: true
use_d1_regime_filter_long: true
ap_smoothing: 2
require_fresh_zone: true
fresh_zone_window: 3
require_slow_slope: false
entry_thrust_min: 0.5
exit_on_bear_cross: false
```

It produced **IS PF 2.209 / OOS PF 2.149**, full-proxy PF **2.183**, and
native XAU-USDT-SWAP PF **2.162**. Most importantly, all five chronological
folds were profitable, with a worst-fold PF of **1.104**.

This is materially stronger than the current live shape and the requested
long-only + D1 anchor using today's structural parameters. It should be treated
as the next rollout candidate, not as a new certificate: 432 cells were searched
and the native contract only has 1.30 years of data. No production config was
changed by this research run.

**2026-07-29 addendum:** after the operator selected this rollout, the exact
preset was replayed through `scripts/certify_preset.py` and received certificate
fingerprint `6b01b6f2598f3881`. See
`docs/research/xau_long_only_d1_certificate_2026-07-29.md`. The new certificate
uses the repository's narrow long-only gate and retains the selection/holdout
limitations above; it is not an all-weather claim.

## Required comparisons

| case | structural settings | IS PF / net / trades | OOS PF / net / MDD / trades |
|---|---|---:|---:|
| current live | ap2, fz3, slope3, thrust0; long D1 off, short D1 on | 1.233 / +20.05% / 150 | 1.498 / +27.51% / 10.17% / 67 |
| D1 + long-only, current structure | ap2, fz3, slope3, thrust0 | 2.029 / +43.34% / 81 | 1.881 / +20.59% / 6.17% / 29 |
| highest OOS PF | long-only D1 off; ap2, fz2, slope off, thrust0.5 | 1.545 / +30.11% / 101 | **2.199** / +31.51% / 6.63% / 38 |
| **recommended balanced winner** | **long-only D1 on; ap2, fz3, slope off, thrust0.5** | **2.209 / +47.04% / 77** | **2.149 / +22.90% / 6.17% / 25** |

The highest-OOS cell is not the recommendation because its full-history and
native-contract checks weaken to PF 1.795 and 1.587, and one of its five folds
loses money. The balanced winner gives up only 0.050 OOS PF while being much
more consistent across datasets and time segments.

## Full-history and native-contract checks

| case | XAUT proxy full: PF / net / MDD / trades | XAU swap native: PF / net / MDD / trades |
|---|---:|---:|
| current live | 1.348 / +53.07% / 14.42% / 217 | 1.473 / +25.86% / 11.16% / 71 |
| D1 + long-only anchor | 1.964 / +72.86% / 9.22% / 110 | 1.943 / +21.54% / 6.20% / 29 |
| highest OOS PF | 1.795 / +71.11% / 11.40% / 139 | 1.587 / +18.75% / 9.25% / 41 |
| **recommended balanced winner** | **2.183 / +80.72% / 8.48% / 102** | **2.162 / +23.39% / 6.20% / 25** |

The native sample is a confirmation check only. OKX XAU-USDT-SWAP begins on
2025-04-09, so it is too short to select a four-year configuration honestly.

## Five chronological folds

Each fold has a non-traded 300-bar lead-in where history exists. “Compounded
net” compounds the five independently reported fold returns for comparison;
the uninterrupted full-history replay above remains the authoritative total.

| case | fold PFs | profitable folds | worst PF | compounded net | trades |
|---|---|---:|---:|---:|---:|
| current live | 1.134, 1.435, 0.962, 1.360, 1.680 | 4/5 | 0.962 | +54.29% | 219 |
| D1 + long-only anchor | 1.796, 0.944, 1.818, 3.450, 1.786 | 4/5 | 0.944 | +72.63% | 111 |
| highest OOS PF | 1.548, 0.797, 1.681, 2.464, 2.132 | 4/5 | 0.797 | +71.20% | 139 |
| **recommended balanced winner** | **1.940, 1.104, 1.985, 3.112, 2.169** | **5/5** | **1.104** | **+80.12%** | **103** |

This is the main reason to prefer the balanced winner. It is the only compared
configuration that remains net-positive and above PF 1 in every fold.

## Best valid cell for every directional/D1 shape

These are ranked within each shape by OOS PF after the pre-declared gates. The
`exit_on_bear_cross` true/false twins returned identical metrics at the top, so
the simpler `false` setting is shown.

| shape | best structure | IS PF | OOS PF | OOS net | OOS MDD | OOS trades |
|---|---|---:|---:|---:|---:|---:|
| long-only D1 off | ap2, fz2, slope off, thrust0.5 | 1.545 | **2.199** | +31.51% | 6.63% | 38 |
| long-only D1 on | ap2, fz3, slope off, thrust0.5 | 2.209 | 2.149 | +22.90% | 6.17% | 25 |
| live shape: long D1 off, short D1 on | ap2, fz2, slope off, thrust0.5 | 1.222 | 2.077 | +41.11% | 10.82% | 53 |
| long+short D1 on | ap2, fz2, slope3, thrust0.5 | 1.437 | 2.002 | +31.54% | 9.88% | 40 |
| long+short D1 off | ap2, fz1, slope5, thrust0.5 | 1.271 | 1.469 | +12.83% | 9.88% | 36 |
| long D1 on, short D1 off | ap2, fz2, slope5, thrust0.5 | 1.136 | 1.383 | +17.09% | 8.66% | 54 |

## Protocol

- Selection series: OKX `XAUT-USDT`, 4h, 8,821 bars from 2022-07-19 08:00
  UTC through 2026-07-28 08:00 UTC.
- Native confirmation: OKX `XAU-USDT-SWAP`, 4h, 2,851 bars from 2025-04-09
  08:00 UTC through 2026-07-28 08:00 UTC.
- Split: 70/30 at 2025-05-13 08:00 UTC; OOS receives 300 bars of history that
  cannot trade.
- Grid: 6 directional/D1 shapes × 72 structural cells = **432 cells**.
- Structural axes: `ap_smoothing` 1/2; fresh-zone window 1/2/3; slope off,
  slope3, or slope5; thrust 0/0.5; bear-cross exit off/on.
- Validity gate: IS trades >= 40, OOS trades >= 18, and positive net in both.
  **396** cells passed; **0** cells failed to execute.
- Ranking: primary rank is OOS PF. The balanced rank maximizes
  `min(IS PF, OOS PF)` under the same gate.
- Live economics were preserved: fee 0.05% per fill, 2 bps modeled slippage per
  fill, flat funding 0.004% per 8h, position size 95%, live ROI schedule, and
  disabled stop loss.

## Config decision and rollout boundary

Research evidence supports replacing the current asymmetric long+short shape
with the balanced long-only + D1 candidate above. A production change must keep
all runtime sources aligned:

- tenant `bot_config.yaml` under `strategy.config.xauby_actionzone`;
- tenant `coin_whitelist.json` XAU `strategy_params` (runtime winner);
- side controls: `allowed_sides: [long]`, `enable_short: false`, and
  `short_live_enabled: false`.

Do not copy the repo config over the tenant files. Preserve tenant owner/ACLs,
back up with metadata, edit in place, and use the controlled preflight/restart
procedure. Because this affects live capital, this report intentionally does
not apply or restart anything without a separate explicit operator approval.

## Limitations

- This is selection-on-OOS across 432 cells, not a pristine unseen holdout.
- Only 25 OOS and 25 native trades support the recommended cell; PF uncertainty
  is therefore material.
- The four-year selection instrument is tokenized gold spot, not the perpetual.
  Earlier venue work measured close correlation near 1.00 and return correlation
  near 0.99 on the overlap, but liquidity and basis are not identical.
- Funding uses the configured flat approximation, not historical OKX funding.
- The period is a strong gold regime; forward paper/shadow evidence should be
  required before calling this candidate certified.
