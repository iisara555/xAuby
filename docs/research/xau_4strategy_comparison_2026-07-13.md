# XAU (4h) — 4-strategy comparison, 13 July 2026

Markdown companion to `xauby_4strategy_gold_comparison_2026-07-13.pdf` (same
directory). The PDF is the presentation copy; this file exists so the numbers are
greppable, diffable, and citable from config and code review.

> ## ⚠️ The "live config" label in this report is wrong
>
> Verified 2026-07-26 by reproducing every headline metric with this repo's own
> `run_plugin_replay` on `research_data/backtest_candles_4h_paxgusdt_full.csv`
> (the same 12,810 bars): the ActionZone row measures
> **`enable_short: false` + `use_d1_regime_filter: true`** — long-only with the
> D1 regime filter ON. The deployed config is long+short with D1 **off**.
>
> | Metric | Report ("live config") | Reproduced long-only + D1 ON | Reproduced **deployed** config |
> |---|---|---|---|
> | Trades | 133 | 132 | 421 |
> | Profit factor | 2.00 | 2.00 | **1.28** |
> | Win rate | 45.9% | 46.2% | 34.4% |
> | Net | +95.9% | +96.4% | +73.7% |
> | Max drawdown | 8.3% | 7.6% | **29.2%** |
> | IS PF | 1.75 | 1.76 | **1.02** |
> | OOS PF | 2.27 | 2.26 | 1.74 |
>
> Seven metrics across two independent windows match long-only+D1 and none match
> the deployed config. (Small residuals come from reproducing the daily regime
> frame by resampling the 4h series rather than using true daily bars.)
>
> **Consequences:**
> 1. **The deployed XAU config is not certified by this report.** Its measured
>    profile is PF 1.28 at 29.2% max drawdown — and 29.2% exceeds the 25%
>    `risk.drawdown_guard.max_drawdown_pct` kill-switch.
> 2. This report does **not** supersede `../actionzone_config_search_2026-07.md`;
>    it independently **confirms** it. Both find long-only + D1 to be the good
>    configuration.
> 3. The comparison against Donchian / SMC Pro / SuperTrend is unaffected and
>    remains valid — those three lose on this data under any reading.
>
> Everything below is the report as delivered, with the ActionZone row understood
> as long-only + D1 ON.

The four-strategy comparison itself is sound work; only the ActionZone row's
config label is wrong. See the box above before citing any number here.

## Protocol

| Element | Setting |
|---|---|
| Engine | `PositionSimulator` / `ReplayEngine` — the same production replay path live parity is validated against, not a vectorized backtest |
| Data | PAXGUSDT 4h as proxy for XAUUSDT, 2020-08 → 2026-07, 12,810 bars |
| Costs | fee 0.05%, slippage 2 bps, funding 0.004%/8h (matches OKX live) |
| Sizing | `risk_pct` 2%/trade, cap 25% |
| Validation | full period + IS/OOS 70/30 + 5-fold walk-forward + trade-level bootstrap CI (3,000 resamples) + top-5-winner stress test |

Strategies 2–4 were run on their **default configs**, which were tuned for BTC
(Donchian and SuperTrend+EMA200 both tag `required_timeframes: 1h`). This is a
comparison of what happens when they are pointed at gold as-is — not a claim that
they cannot work on gold after retuning.

## Full period (2020-08 → 2026-07)

| Strategy | Net | PF | WR | MDD | Trades | Sharpe | Calmar |
|---|---|---|---|---|---|---|---|
| **ActionZone (live config)** | **+95.9%** | **2.00** | 45.9% | 8.3% | 133 | 1.35 | 1.48 |
| Donchian Trend | +5.5% | 1.43 | 40.2% | 5.0% | 97 | 0.43 | 0.19 |
| SMC Pro | −5.2% | 0.76 | 37.3% | 9.1% | 177 | −0.52 | −0.10 |
| SuperTrend+EMA200 | −0.7% | 0.92 | 42.6% | 3.8% | 68 | −0.10 | −0.03 |

## IS/OOS 70/30

| Strategy | IS Net | IS PF | OOS Net | OOS PF | OOS Sharpe |
|---|---|---|---|---|---|
| **ActionZone** | **+36.7%** | **1.75** | **+50.2%** | **2.27** | 2.26 |
| Donchian Trend | −2.6% | 0.72 | +8.9% | 3.60 | 1.72 |
| SMC Pro | −8.1% | 0.49 | +3.7% | 1.57 | 1.08 |
| SuperTrend+EMA200 | −3.0% | 0.34 | +2.4% | 1.69 | 0.86 |

ActionZone is the only strategy profitable in **both** windows. The other three
lose in IS (2020–2023) and recover in OOS (~2023–2026), which is the gold bull
run (~$1,950 → ~$4,170) — any long-biased trend follower looks good there. That
pattern is the warning sign, not the edge.

## 5-fold walk-forward (profit factor per fold, non-overlapping ~2,562 bars each)

| Strategy | Fold1 | Fold2 | Fold3 | Fold4 | Fold5 | Profitable folds |
|---|---|---|---|---|---|---|
| **ActionZone** | 1.22 | 0.77 | 1.38 | 3.35 | 2.15 | **4/5** |
| Donchian Trend | 0.59 | 0.79 | 0.43 | 1.16 | 4.08 | 2/5 |
| SMC Pro | 0.27 | 0.31 | 0.64 | 1.09 | 1.70 | 2/5 |
| SuperTrend+EMA200 | 0.22 | 0.73 | 0.22 | 0.78 | 1.70 | 1/5 |

## Bootstrap CI + stress test

| Strategy | Trades | PF 90% CI | P(PF<1.0) | PF after cutting top-5 winners | Net after cut |
|---|---|---|---|---|---|
| **ActionZone** | 133 | [1.32, 3.02] | **0.2%** | **1.59** | **+56.1%** |
| Donchian Trend | 97 | [0.84, 2.29] | 13.3% | 0.79 | −2.6% |
| SMC Pro | 177 | [0.55, 1.04] | 92.0% | 0.60 | −8.8% |
| SuperTrend+EMA200 | 68 | [0.53, 1.57] | 60.5% | 0.49 | −4.1% |

`P(PF<1.0)` is the share of 3,000 trade-level resamples that came out losing.

## Conclusion

Keep ActionZone as the sole XAU strategy. There is no statistical basis for
switching or adding another strategy right now. To make Donchian / SMC Pro /
SuperTrend viable on gold they would need to be (1) retuned for gold 4h rather
than run on BTC defaults, (2) re-measured with this same walk-forward + bootstrap
protocol, and (3) checked specifically for edge outside the recent bull leg.

## Relationship to the earlier study

`../actionzone_config_search_2026-07.md` (2026-07-12) recommended **long-only
with the D1 regime filter on**, and reported that the then-live long+short /
`fresh_zone_window: 1` config lost money in all five walk-forward folds. It also
documented the phase-lock hazard: with `enable_short: true` and `fz1` the
reversal entry is always blocked, so the strategy locks into one side.

Given the reproduction in the box at the top of this file, this report's
ActionZone row **is** that recommended config. The two studies therefore agree
rather than conflict, and they agree using different protocols — a 144-combo
config search with 5-fold WFA, and a 4-strategy comparison with bootstrap CI.
That is a stronger result than either alone.

`fresh_zone_window: 3` was applied to `coin_whitelist.json` after the earlier
study, and it does fix the phase-lock. What it does **not** do is close the gap
to long-only+D1: the deployed long+short config still measures PF 1.28 at 29.2%
max drawdown against 2.00 at 7.6%. Neither study certifies it.

## Open items

Recorded here so the caveats travel with the numbers:

1. **The config label is wrong** — see the box at the top. This is the item that
   matters: the deployed XAU config has no passing certificate, and its measured
   29.2% max drawdown exceeds the 25% `drawdown_guard` threshold. The decision
   this forces (switch the live config to long-only+D1, or accept the deployed
   config's real profile explicitly) is tracked as P0.3 in
   `../roadmap_2026H2.md`.
2. **Proxy asset.** The data is PAXGUSDT 4h spot standing in for XAUUSDT-SWAP.
   The PDF notes an OKX cross-check in an earlier round of the same session, but
   that cross-check is not committed. Re-running this protocol on OKX
   XAU-USDT-SWAP data is tracked as P0.1/P0.2.
3. **The run's parameters are not listed in the report**, which is why the label
   error survived. Whatever the certification pipeline in P1.4 produces must emit
   the full resolved strategy config alongside the metrics — a certificate that
   does not state what it measured cannot be checked, and this one wasn't for
   two weeks.
4. **`partial_tp_pct: 12.0` was unreachable** in both live and replay, because
   the `minimal_roi` rung at 8.0% pre-empts it and a full exit always wins the
   tick. It contributed nothing to any result here and has been removed from
   `coin_whitelist.json` and `bot_config.yaml`; `validate_exit_config`
   (`xauby/runtime/exits.py`) now refuses the combination at startup.

## Reproducing this

```bash
pip install -r requirements-dev.txt
PYTHONPATH=. python3 - <<'PY'
import pandas as pd, yaml
from xauby.backtest.data import normalize_ohlcv_df
from xauby.backtest.replay import run_plugin_replay
from xauby.runtime.trading_config import split_legacy_runtime_config

cfg = yaml.safe_load(open('bot_config.yaml'))
base, trading = split_legacy_runtime_config(
    cfg, "xauby_actionzone", symbol="XAUUSDT", for_live=False)
merged = dict(cfg); merged["trading"] = trading
df4 = normalize_ohlcv_df(
    pd.read_csv('research_data/backtest_candles_4h_paxgusdt_full.csv'))
d = df4.copy(); d['dt'] = pd.to_datetime(d['timestamp'], unit='s')
df1 = normalize_ohlcv_df(d.set_index('dt').resample('1D').agg(
    {'timestamp':'first','open':'first','high':'max','low':'min',
     'close':'last','volume':'sum'}).dropna().reset_index(drop=True))

for label, ov, rg in [
    ("deployed (long+short, D1 off)", {}, None),
    ("long-only + D1 on", {"enable_short": False,
                           "use_d1_regime_filter": True}, df1),
]:
    s = run_plugin_replay(df4.copy(), strategy_config={**dict(base), **ov},
        engine_config=merged, symbol="XAUUSDT",
        strategy_name="xauby_actionzone", primary_timeframe="4h",
        df_regime=rg, regime_timeframe="1d" if rg is not None else None)
    print(f"{label:32s} trades={s['total_trades']:>4} "
          f"PF={s['profit_factor']:.2f} WR={s['win_rate']:.1f} "
          f"net={s['net_profit_pct']:+.1f} MDD={s['max_drawdown_pct']:.1f}")
PY
```

The daily frame is resampled from the 4h series rather than fetched, which is why
reproduced numbers sit within a fraction of a percent of the report rather than
exactly on it. Fetching true daily PAXG bars would tighten it further.
