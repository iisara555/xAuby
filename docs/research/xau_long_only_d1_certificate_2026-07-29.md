# XAU ActionZone long-only + D1 certificate

- **Preset:** `okx-xau-actionzone-v1`
- **Fingerprint:** `6b01b6f2598f3881`
- **Issued:** 2026-07-29
- **Protocol:** `scripts/certify_preset.py`, `saas-preset-acceptance` v1
- **Venue series:** OKX `XAUT-USDT`, 4h, Jul 2022 – Jul 2026
- **Grid evidence:** `docs/research/xau_okx_pf_grid_2026-07-29.md`

## Verdict

**CERTIFIED under the protocol's long-only gate.** The exact preset produced
positive net return, so the short-side edge test does not apply. The generated
record is `xauby/saas/certificates/okx-xau-actionzone-v1.json`; its fingerprint
must match the preset or the catalog automatically reverts to `not_assessed`.

| metric | full XAUT proxy | native XAU swap check |
|---|---:|---:|
| Profit factor | 2.18 | 2.16 |
| Net return | +80.72% | +23.39% |
| Max drawdown | 8.48% | 6.20% |
| Trades | 102 | 25 |

The generated certificate records PF 2.18, 45.1% win rate, 8.5% rounded max
drawdown, and 102 trades. The native column is a confirmation from the grid
artifact, not the certification series, because `XAU-USDT-SWAP` begins only in
April 2025.

## Certified execution profile

```yaml
allowed_sides: [long]
enable_short: false
use_d1_regime_filter: true
use_d1_regime_filter_long: true
use_d1_regime_filter_short: true
ap_smoothing: 2
require_fresh_zone: true
fresh_zone_window: 3
require_slow_slope: false
slow_slope_bars: 3
entry_thrust_min: 0.5
exit_on_bear_cross: false
disable_stop_loss: true
position_pct: 0.95
minimal_roi: {"0": 8.0, "1440": 5.0, "4320": 3.0}
```

The fingerprint also covers the remaining explicit execution-profile values in
`xauby/saas/preset_specs.py`, including RSI/volume pass-throughs, ATR values,
breakeven disabled, fixed TP disabled, and the 240-minute cooldown.

## Significance and limits

- The bootstrap used 10,000 samples over 102 equity-relative trade returns:
  median +80.59%, 5th percentile +31.24%, 95th percentile +151.33%, with
  estimated probability of a profitable reordered sample 99.9%.
- The observed return-path drawdown was not unusually favorable versus shuffled
  trade order (`p=0.3918`). The losing-streak ordering was favorable but near
  the tail (`p=0.0535`).
- Deflated Sharpe is deliberately not computed. The search covered 432 cells,
  but no same-unit Sharpe variance was supplied; inventing one would make the
  adjustment misleading.
- This is not a pristine holdout. The same four-year period helped select the
  profile, and the native swap check has only 25 trades over 1.3 years.
- No six-month forward shadow record exists. Certification means the exact
  profile cleared the repository's pre-registered long-only gate; it does not
  mean the strategy is all-weather or that future profit is assured.

## Reproduction record

The certificate was produced on GitHub Actions run `30410961436` from commit
`829de91f9bd1f349a112f37df941a9b08a8c64ec`. The earlier 432-cell grid was
produced by run `30374751197`; its result JSON SHA-256 is
`d759cf7c99bd5f6bdeea49df080b1f10f49304e0a5a8a675f25d3831aa10a212`.
