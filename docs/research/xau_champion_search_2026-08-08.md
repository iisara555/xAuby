# XAUUSDT — champion search: config re-validation and rival strategies

- **Run date:** 2026-08-08
- **Venue:** OKX. Selection on `XAUT-USDT` 4h (8,886 bars, 2022-07-19 → 2026-08-08);
  confirmation on native `XAU-USDT-SWAP` 4h (2,916 bars, 2025-04-09 → 2026-08-08).
- **Harness:** `scripts/xau_champion_search.py` (config + strategy stages),
  `scripts/xau_harness.py` (six-variant catalog), `scripts/xau_okx_pf_grid.py`
  (IS/OOS split, folds, validity gates).
- **Result JSON SHA-256:** `4920ba56a628a361bbec36c7dbe5756d41b8a4ba249ee355e6e4acbf3057d6ac`
  (archived at `docs/research/artifacts/xau_champion_search_2026-08-08.results.json.gz`)
- **No production config was changed by this run.**

## Verdict

1. **The D1 regime gate should stay ON.** This is the one broad, non-cherry-picked
   result in the study.
2. **The deployed config is not the best cell, but it is the most consistent one.**
   A cell exists that earns materially more (+115.31% vs +80.72% full-history) and
   also wins the native-contract check. Its advantage does not generalise across
   the grid, and it gives up worst-fold consistency and drawdown-phase discipline
   to get there. It is a **certification candidate, not a rollout**.
3. **No rival strategy is competitive on gold.** ActionZone remains champion by a
   wide margin. `xauby_donchian_trend` is the only plausible challenger and is not
   live-eligible.

## 1. D1 gate: on or off

Six-variant continuous replay at the deployed structural shape, full proxy history:

| variant | PF | net | MDD | trades | win | Sharpe |
|---|---:|---:|---:|---:|---:|---:|
| **long-only D1 on** (deployed) | **2.183** | **+80.72%** | **8.48%** | 102 | 45.1% | 1.47 |
| long-only D1 off | 1.790 | +77.44% | 11.40% | 148 | 38.5% | 1.26 |
| long+short D1 on | 1.571 | +66.58% | 11.01% | 157 | 39.5% | 1.11 |
| L:D1off S:D1on | 1.447 | +63.56% | 12.02% | 203 | 36.0% | 0.98 |
| L:D1on S:D1off | 1.206 | +32.33% | 15.28% | 236 | 35.2% | 0.56 |
| long+short D1 off | 1.169 | +29.93% | 17.99% | 282 | 33.3% | 0.50 |

D1 on wins on profit factor, net, drawdown and Sharpe simultaneously — there is no
axis on which turning it off is the better trade. This is not an artifact of the
deployed cell: averaged over all 288 cells of the config grid below,

| shape | cells passing gates | mean OOS PF | mean OOS MDD |
|---|---:|---:|---:|
| long-only D1 on | 128 | **1.700** | **10.16%** |
| long-only D1 off | 144 | 1.557 | 12.53% |

Adding shorts hurts on this data under every gating arrangement, which reproduces
the finding the 2026-07-29 rollout was based on. The deployed config also
reproduces its certificate exactly (PF 2.183 / +80.72% / MDD 8.48% / 102 trades),
so nothing has drifted in the ten days since it was issued.

## 2. Config search — 288 cells

Axes: shape (long-only D1 on/off) x `ap_smoothing` (1,2) x `fresh_zone_window`
(2,3,4) x slope filter (off, on/3) x `entry_thrust_min` (0.0, 0.5, 0.8) x
`minimal_roi` ladder (none, tight 5/3.5/2, live 8/5/3, wide 12/8/5).

The ladder is the axis that has never been searched. Every XAU certificate to date
has *measured* it at 8/5/3 and inherited it unexamined. `exit_on_bear_cross` is
pinned to the deployed `false`; the 2026-07-29 grid varied it across all six
shapes and it reached no finalist.

Ranking is worst-of-IS/OOS profit factor, the same rule the 2026-07-29 study used.

**The deployed cell ranks #7 of 288.** The top six are all long-only D1 on, `ap2`,
`thrust 0.0`, **wide ladder**; `fresh_zone_window` and the slope filter are inert
among them (identical OOS and native results).

| cell | IS PF | OOS PF / net | full PF / net / MDD | native PF / net |
|---|---:|---:|---:|---:|
| `ap2_fz3_sl0_th0_bx0_roiwide` (best) | 2.578 | 2.209 / +29.61% | **2.396 / +115.31% / 9.03%** | **2.283 / +30.77%** |
| `ap2_fz3_sl0_th0.5_bx0_roilive` (**deployed**) | 2.209 | 2.149 / +22.90% | 2.183 / +80.72% / 8.48% | 2.162 / +23.39% |

### Why this is a candidate and not a rollout

**The two changes do not help independently.** Holding `ap2_fz3_sl0`:

| thrust | ladder | IS PF | OOS PF | OOS net |
|---:|---|---:|---:|---:|
| 0.0 | wide | 2.578 | **2.209** | +29.61% |
| 0.5 | live (**deployed**) | 2.209 | 2.149 | +22.90% |
| 0.8 | live | 1.844 | 1.989 | +19.34% |
| 0.0 | live | 2.036 | 1.881 | +20.59% |
| 0.0 | none | 2.521 | 1.891 | +17.47% |
| 0.5 | wide | 2.901 | 1.844 | +19.37% |
| 0.5 | none | 2.885 | 1.852 | +15.74% |
| 0.8 | wide | 2.470 | 1.243 | +5.12% |

On the single IS/OOS split, both single-factor moves away from the deployed cell
are *worse* than the deployed cell. Only the pair improves it. A winner that sits
on an interaction, where each constituent hurts alone, is the shape overfitting
takes.

**The winning ladder is not generally better.** Averaged across every cell that
passes the validity gates:

| ladder | cells | mean IS PF | mean OOS PF | mean OOS net |
|---|---:|---:|---:|---:|
| none | 60 | 1.872 | **1.807** | +19.15% |
| live (8/5/3) | 72 | 1.839 | 1.700 | +19.39% |
| wide (12/8/5) | 68 | **2.101** | 1.639 | **+20.76%** |
| tight (5/3.5/2) | 72 | 1.771 | 1.383 | +10.12% |

The wide ladder has the **highest mean IS PF and a below-median mean OOS PF** —
in-sample/out-of-sample divergence, the second overfitting signature. Its edge at
the winning cell is concentrated, not a property of the ladder.

**Folds are kinder than the single split.** Five chronological folds, each with a
non-traded 300-bar lead-in:

| cell | fold PFs | profitable | worst PF | compounded | trades |
|---|---|---:|---:|---:|---:|
| deployed (th0.5 + live) | 2.022, 1.787, 1.788, 3.462, 1.955 | 5/5 | **1.787** | +81.82% | 102 |
| candidate (th0 + wide) | 2.451, 1.313, 2.038, 5.265, 1.719 | 5/5 | 1.313 | **+112.74%** | 102 |
| th0.5 + wide | 2.939, 1.643, 2.282, 4.741, 1.242 | 5/5 | 1.242 | +103.55% | 94 |
| th0 + live | 1.902, 1.364, 1.630, 3.840, 1.615 | 5/5 | 1.364 | +74.17% | 110 |
| th0 + no ladder | 1.407, 1.485, 2.148, 5.953, 0.976 | 4/5 | 0.976 | +60.37% | 86 |

Across folds the wide ladder helps at **both** thrust settings, which contradicts
the single-split reading above. The two measurements disagree; that disagreement
is itself the finding. What survives both: the deployed cell has the **best
worst-fold PF (1.787)** of anything tested, and the candidate buys ~31pp of
compounded return by accepting a weaker floor (1.313).

**The candidate is less defensive in the live-relevant regime.** From the 2026-02
gold peak (2026-03-01 onward, 962 traded bars):

| cell | net | MDD | trades |
|---|---:|---:|---:|
| deployed | **-2.00%** | **2.00%** | 1 |
| candidate | -4.74% | 4.75% | 3 |
| buy & hold | -18.17% | 24.26% | — |

Both configs are doing their job — the D1 gate keeps the book essentially flat
through an 18% decline. But dropping `entry_thrust_min` to 0 admits three entries
where the deployed filter admitted one, and all of them lost. n=1 versus n=3 is
far too small to conclude from; it is a directional caution, not a measurement.

### Cost of adopting the candidate

`minimal_roi` is inside the certificate's config fingerprint. Changing it revokes
`6b01b6f2598f3881`, and because `okx-xau-actionzone-v1` is also `live_certified`,
`xauby/saas/catalog.py` will refuse to build until the preset is re-certified via
`scripts/certify_preset.py` or given an explicit `operator_override`. That is the
intended friction, not an obstacle to route around.

**Recommendation:** run `scripts/certify_preset.py` on
`ap2_fz3_sl0_th0_bx0_roiwide` and decide from the certificate. Do not adopt it on
this document alone. If the goal is return, the candidate is the better cell; if
the goal is the smoothest equity curve, the deployed config is already the best
cell in a 288-cell search on the metric it was chosen for.

## 3. Rival strategies

Every registered plugin replayed over the same frames, long-only and long+short,
each on its **own** default config — a rival is judged on its own risk model, not
wearing ActionZone's stop-less 95% fixed-fraction ladder.

**Net is not comparable across rows; profit factor is.** ActionZone sizes at
`position_pct 0.95` and runs 29–35% exposure; the rivals use risk-based sizing at
`risk_pct 2%` and run 8–19% exposure. Their small nets are a sizing artifact.

| strategy (long-only) | maturity | full PF | full net | exposure | native PF | gate |
|---|---|---:|---:|---:|---:|---|
| **`xauby_actionzone` (deployed)** | production | **2.183** | +80.72% | 29.3% | **2.162** | — |
| `xauby_donchian_trend` | undeclared | 2.112 | +8.69% | 19.1% | 1.951 | pass |
| `supertrend_ema200` | production | 1.480 | +3.03% | 8.1% | 1.291 | fail |
| `bbkc_squeeze` | undeclared | 1.301 | +2.68% | 8.3% | 2.006 | fail |
| `btc_ema_pullback` | paper | 1.168 | +2.34% | 9.1% | 1.501 | pass |
| `squeeze_momentum` | undeclared | 1.121 | +2.52% | 24.7% | 1.514 | pass |
| `vol_breakout` | undeclared | 0.909 | -0.58% | — | 2.259 | fail |
| `xauby_smc_pro` | undeclared | 0.881 | -1.91% | — | 2.052 | fail |
| `sol_ema_pullback` | undeclared | 0.873 | -0.47% | — | 0.698 | fail |
| `bbrsi_mean_reversion` | undeclared | 0.806 | -1.54% | — | 1.120 | fail |
| `xauby_vwap_pullback` | undeclared | 0.608 | -5.51% | — | 0.776 | fail |
| `rsi2_meanrev` | undeclared | 0.601 | -6.61% | — | 0.566 | fail |
| `simple_scalp_plus` | undeclared | 0.636 | -30.33% | — | 0.788 | fail |
| `ict_lite_strategy` | undeclared | 0.031 | -0.67% | — | 0.000 | fail |

"gate" is the study's validity gate (>=40 IS trades, >=18 OOS trades, both windows
net positive), not a certification.

Observations:

- **`xauby_donchian_trend` is the only real challenger**, at PF 2.112 full / 1.951
  native with a 2.86% max drawdown, 5/5 on the validity gate. It is also
  `maturity` **undeclared**, so `_load_strategy_for_symbol` refuses it on a `live`
  pair — correctly, fail-closed. Judging it against ActionZone needs a run at
  matched sizing, which this study did not do. That is the one follow-up worth
  funding.
- **`supertrend_ema200`, the other production plugin, does not transfer to gold.**
  PF 1.480 long-only and PF 0.903 (net -1.16%) long+short. It stays on BTC.
- **Shorts hurt on gold for every plugin that emits them**: `supertrend_ema200`
  1.480 → 0.903, `squeeze_momentum` 1.121 → 0.880, `bbrsi_mean_reversion` 0.806 →
  0.473. This independently reproduces the ActionZone short-side result from a
  completely different set of entry rules.
- Rows where `__long` and `__ls` are identical (`donchian_trend`, `bbkc_squeeze`,
  `btc_ema_pullback`, `vol_breakout`, `smc_pro`, `sol_ema_pullback`,
  `simple_scalp_plus`, `vwap_pullback`, `rsi2_meanrev`, `ict_lite`) are plugins
  that never emit a short signal, so `enable_short` is inert for them.
- Several plugins score far better on the native swap than on the proxy
  (`vol_breakout` 0.909 → 2.259, `smc_pro` 0.881 → 2.052, `bbkc_squeeze` 1.301 →
  2.006). The native window is 1.3 years of mostly one regime and roughly 20–40
  trades; treat those as noise, not as a second opinion.

## Limitations

- **Selection and confirmation share a series.** 288 cells were searched on the
  same four years the headline numbers are quoted from. The native swap check is a
  different contract but an overlapping period and only 1.3 years, so it is a
  consistency check, not a holdout.
- **No forward record.** Nothing here has been observed out of sample in time.
- **The two robustness measurements disagree** about the wide ladder (single split
  says the gain needs both changes, folds say the ladder helps alone). Neither was
  pre-registered as the tiebreak.
- **The drawdown-phase comparison is n=1 vs n=3 trades.** It is directional only.
- **Buy-and-hold gold returned +152.22% over the full period** against the
  deployed config's +80.72%, at 25.11% max drawdown versus 8.48%. The strategy's
  case on this data is risk-adjusted return, not absolute return. Any discussion
  of "better config" should carry that comparison.

## Reproduction

```bash
PYTHONPATH=. python3 scripts/xau_champion_search.py --stage all \
    --workers 4 --top-n 10 --out-dir core/xau_champion
```

Roughly two hours on four cores. The six-variant table in section 1 comes from
`scripts/xau_harness.py` primitives; folds and the drawdown-phase slice reuse
`scripts/xau_okx_pf_grid.py::_fold_slices` and `_run_frame`.
