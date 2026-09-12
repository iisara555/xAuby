# Institutional Certification Framework v2

## Status

Phase 1 implementation is complete: the protocol contract, immutable trial
ledger, nested purged walk-forward engine, ledger-backed statistical gates,
venue-locked execution stress, unified runner, create-once artifact bundle, and
adversarial conformance suite are implemented. This is not itself a claim that
any strategy is certified. A strategy receives a verdict only from a complete
locked run whose external artifact digest is retained by CI.

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
15. Link every execution observation to the exact venue, symbol, market type,
    data SHA-256, and outer-fold index. Proxy rows and incomplete fold coverage
    fail closed.
16. Re-price the outer-holdout observations under at least three locked
    scenarios. Fees, two-sided slippage, adverse funding, latency, and partial
    fills are stressed together.
17. Feed only the protocol's designated stress-scenario returns into the
    statistical gate. Execution and significance cannot use different return
    streams.
18. Require execution-observation counts to equal `total_trades` from every
    outer holdout.
19. Run all validation, execution, statistics, ledger, and CI-provenance checks
    through `run_phase1_certification()` and derive the verdict from the logical
    conjunction of every check.
20. Write a new artifact directory exclusively. `certificate.json` is strict
    canonical JSON, its component hashes bind all evidence, and
    `certificate.sha256` supplies the external digest anchor. Existing bundles
    are never overwritten.

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
- Certifying against proxy data while labelling it venue-native.
- Omitting a weak outer fold from execution evidence.
- Reporting optimistic statistical returns while showing a separate stress
  table.
- Ignoring funding, latency, or partial-fill degradation.
- Rewriting an earlier artifact directory or changing one component without
  breaking its hash chain.
- Claiming CI provenance from a dirty source tree or another repository.

Hash chaining alone cannot prove that a ledger tail was not removed. The locked
certificate therefore retains the final ledger digest in a separately
published CI artifact, and operators must retain the external
`certificate.sha256` value outside the bundle. Verification without that
external anchor establishes internal consistency, not publisher authenticity.

## Phase 1 completion boundary

- `CertificationProtocolV2` freezes data, validation, execution, statistics,
  artifact policy, thresholds, and random seed before search.
- `TrialLedger` counts every attempted candidate and detects mutation.
- `nested_purged_walk_forward()` enforces inner-only selection, untouched outer
  holdouts, embargo, purge, timeline locking, and warm-up isolation.
- `evaluate_execution_stress()` enforces native identity, fold coverage, cost
  scenarios, fills, latency, and execution-to-replay trade parity.
- `evaluate_statistical_gate()` enforces bootstrap, permutation, PSR/DSR, and
  multiple-testing correction using ledger-derived search provenance.
- `run_phase1_certification()` is the only Phase 1 verdict composition path and
  writes a create-once artifact bundle.
- `.github/workflows/institutional-certification-v2.yml` runs the adversarial
  conformance suite, lint, secret scan, and uploads hash-indexed evidence.

Phase 2 (Regime Ensemble v2) may begin after the Phase 1 conformance workflow is
green on the landed commit. No certificate authorizes deployment, live capital,
position sizing, tenant configuration changes, or an engine restart.
