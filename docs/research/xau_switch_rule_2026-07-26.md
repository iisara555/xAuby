# XAU config switch rule — pre-registered 2026-07-26

**Checker:** `scripts/xau_regime_switch_check.py` (reports only; changes nothing).

## Why a rule instead of a judgement call

XAU now runs `long-only ungated + D1-gated shorts`, chosen because gold has been
falling since its 2026-02 peak and that config is the strongest cell in a decline
(+22.61% over 2026-03..06 vs buy-and-hold's -23.21%).

Over the full 4.02y sample the picture reverses: `long-only + D1 on` wins on
PF (1.96 vs 1.38) and MDD (9.22% vs 14.42%) — because that sample is **90% bull**.

So the current choice is **regime-conditional, not permanent**. The moment gold
resumes a sustained uptrend, the other config is better. Deciding *then*, in the
moment, what counts as "sustained" is precisely the mechanism that produced the
mislabeled July certificate. The rule is therefore fixed now, before the market
turns, and the checker only reports whether it has fired.

## The rule

Daily phase uses the **same definition already in the repo** — no new indicator,
no new free parameter (`scripts/btc_wfa_multi_strategy.py::_phase_label`):

> 1d close vs EMA200, plus the EMA200 slope over the trailing 21 bars, computed
> from closes **strictly before** the evaluated bar.

Confirmation window: **30 consecutive daily closes.**

| condition | run this config |
|---|---|
| 30 consecutive `bull` days | `long-only + D1 on` |
| 30 consecutive non-`bull` days (`bear` or `sideways`) | `long-only ungated + D1-gated shorts` |

**30 was fixed before the rule was measured.** It is "about one month", the unit
every analysis in this thread used. It was not chosen by trying candidate values
and keeping the best — doing that would make this rule another post-hoc artifact.

## How the rule behaves

Measured after fixing it, over OKX XAUT-USDT 1d:

| date | switches to | trigger | gold close |
|---|---|---|---|
| 2023-03-25 | long-only + D1 on | 30 consecutive bull days | 1976.1 |
| 2023-09-08 | ungated long + D1 shorts | 30 consecutive non-bull days | 1919.9 |
| 2023-11-23 | long-only + D1 on | 30 consecutive bull days | 1996.1 |
| **2026-06-28** | **ungated long + D1 shorts** | 30 consecutive non-bull days | 4023.1 |

**Four firings in four years** — roughly one a year, not a whipsaw machine. It
held `long-only + D1 on` from 2023-11 through 2026-06, i.e. across essentially
the whole gold bull run, which is the behavior the 4-year numbers argue for.

Daily phase mix over the sample: **bull 1115 days, bear 75, sideways 58** — bull
89.4% of the time, consistent with the 37/40 monthly split used elsewhere.

## Current state (2026-07-25)

- gold close **4055.1**, phase **bear**
- **57 consecutive non-bull days**, confirmation already met
- **Rule says: run `long-only ungated + D1-gated shorts`** — the config now
  deployed

The rule fired for this config on **2026-06-28**, about four weeks before it was
actually applied.

**Read that agreement carefully.** It is *not* independent confirmation that the
config is right. The rule is built from the same regime concept that motivated
choosing the config, so it would be surprising if they disagreed. What the rule
adds is not extra evidence — it is a commitment about **when we stop**.

## Operating it

1. Run `PYTHONPATH=. python3 scripts/xau_regime_switch_check.py` — weekly is
   ample; the confirmation window is 30 days.
2. If `RULE SAYS RUN` differs from what is live, switch — waiting for a "better
   entry" is the failure mode this exists to prevent.
3. Switching still requires a **flat position** (XAU has `disable_stop_loss:
   true`, `position_pct: 0.95`) and a controlled restart.
4. `long-only + D1 on` is `use_d1_regime_filter: true` with
   `use_d1_regime_filter_long` **removed or set true**; the current config is the
   same with `use_d1_regime_filter_long: false`. Keep `bot_config.yaml` and
   `coin_whitelist.json` in sync — a test enforces it.

## Limitations

- **The rule is not certified.** It is a pre-commitment device, not a validated
  strategy component. It has never been walk-forwarded, and doing so on this
  sample would mostly measure the 2023 bull transition.
- Only **4 firings** exist. Two of them are within seven months of each other in
  2023; the rule's behavior in a long bear market is untested because the sample
  has one.
- Phase labels **lag by construction** — the EMA200 slope stayed `bull` for
  months into the 2026 decline, which is why the 2026 firing lands in June rather
  than March. The rule will always be late to a turn. That is the accepted cost
  of not reacting to noise.
- Evaluated on XAUT-USDT (proxy, correlation 0.99/1.00 to XAU-USDT-SWAP), not the
  swap itself, whose history is too short.
- The rule addresses **which config**, not whether XAU should trade at all. Over
  4 years no config beat holding gold on raw return; that question is separate
  and unresolved.
