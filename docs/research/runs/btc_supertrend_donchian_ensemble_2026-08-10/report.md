# BTC SuperTrend + Donchian 50/50 locked shadow run

Verdict: **REJECT**

| book | net | PF | MDD | Sharpe | +months | trades |
|---|---:|---:|---:|---:|---:|---:|
| Champion | +17.37% | 1.471 | 9.81% | 0.593 | 29/79 | 135 |
| 50/50 ensemble | +25.87% | 1.572 | 4.52% | 0.874 | 32/79 | 264 |

## Pre-registered gates

- PASS `native_history_sufficient`
- PASS `full_net_uplift`
- PASS `full_profit_factor_noninferiority`
- PASS `full_drawdown_reduction`
- PASS `full_sharpe_edge`
- PASS `positive_month_edge`
- PASS `profitable_folds`
- FAIL `drawdown_noninferior_folds`
- PASS `recent_net_positive`
- PASS `recent_profit_factor`
- PASS `member_monthly_correlation`
- PASS `weight_sensitivity`
- FAIL `donchian_4h_exploratory_parity`

Virtual sleeve conflict: 0 bars (0.00%). This is diagnostic: the record is shadow-only and is not executable in the current one-way live account.

The Donchian member and 50/50 allocation were selected after reviewing the 2026-08-08 exploratory branch, so this is a locked robustness and implementation-parity check rather than a pristine unseen holdout. Passing permits credential-free forward shadow only. The isolated virtual sleeves may hold opposing sides and are not executable on the current one-way live account.

Passing proposes an ensemble certificate for forward shadow only. It does not approve live trading, account netting, allocation changes, or tenant configuration changes.
