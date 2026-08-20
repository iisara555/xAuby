# Institutional Certification Framework v2

## Status

Phase 1, increments 1-3: the protocol contract, immutable trial ledger, nested
purged walk-forward engine, and ledger-backed mandatory statistical gates are
implemented. This document is the threat model for the remaining increments;
it is not itself a claim that any strategy is certified.

## Trust boundary

Discovery and parameter search are untrusted inputs.  A passing verdict may be
issued only by the locked certification workflow, never by a discovery script,
an optimizer, a catalog label, or an operator-edited result file.

The workflow is fail-closed when data, code, configuration, protocol, trial
history, or execution assumptions cannot be identified exactly.

## Required evidence chain

1. Freeze a `CertificationProtocolV2` before the first candidate is evaluated.
2. Create one exclusive JSONL trial ledger from that protocol.
3. Call `start_trial()` before running every candidate, including retries and
   candidates expected to fail.
4. Append a terminal result with `finish_trial()`. Crashed trials remain pending
   and still count as attempted trials.
5. Verify the complete hash chain before statistical analysis.
6. Persist both the protocol SHA-256 and final ledger SHA-256 in the certificate
   artifact. The external final digest is required to detect tail truncation.
7. Obtain the multiple-testing trial count from `trials_started`; callers may
   not override it with a hand-entered value.
8. Materialize nested folds from the protocol's locked validation policy. Inner
   folds alone select a candidate; the selected candidate runs once on each
   outer holdout.
9. Keep an explicit embargo plus a purge interval equal to the maximum label or
   holding horizon before every inner and outer evaluation.
10. Prepend past-only warm-up bars through `WindowSlice`; `run_slice()` always
    forwards the exact leading count as a non-trading override.
11. Build the statistical return series only from untouched outer holdouts and
    preserve the exact basis named by `statistical_policy.sharpe_basis`.
12. Derive `n_trials`, completed-trial Sharpe dispersion, and the complete
    selection p-value family from one verified `TrialLedgerSnapshot`. The
    statistical gate has no caller arguments for trial count or Sharpe variance.
13. Reject while any trial is pending, when a completed trial lacks a comparable
    Sharpe or p-value, or when the exact selected trial is absent.
14. Require the selected trial to pass the pre-registered Bonferroni or
    Benjamini-Hochberg correction as well as PSR, DSR, circular moving-block
    bootstrap, and path-dependent drawdown permutation gates.

## Threats addressed in this increment

- Reporting only the winning parameter set.
- Claiming `n_trials=1` after a wider search.
- Editing or reordering an earlier attempt.
- Marking a candidate complete without first recording the attempt.
- Changing the pre-registered protocol after the search begins.
- Treating failed, aborted, retried, or crashed candidates as if they were never
  tested.
- Selecting a candidate on the same outer fold later reported as out-of-sample.
- Overlapping label horizons across training and evaluation boundaries.
- Trading indicator warm-up bars or reading future bars into warm-up.
- Reusing a validation plan against a shifted or reordered timeline.
- Supplying a fictional trial count or Sharpe variance to the DSR calculation.
- Omitting weak completed trials from the multiple-testing family.
- Mixing Sharpe values calculated from different return bases.
- Accepting a zero-variance series as statistically identifiable.
- Applying an order permutation test to a statistic or sample whose order
  cannot change the result.

Hash chaining alone cannot prove that a ledger tail was not removed. The locked
certificate must therefore retain the final ledger digest in a separately
published artifact. Signing and CI artifact provenance are later Phase 1 work.

## Remaining Phase 1 gates

- Venue-native execution and cost stress testing.
- One certification runner with immutable CI artifacts and adversarial
  self-certification tests.

Regime Ensemble v2 and dynamic position sizing remain blocked until all of the
above gates are implemented and the framework itself passes its test protocol.
