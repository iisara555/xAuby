# GitHub-hosted Actions runners

All checked-in GitHub Actions jobs run on the standard `ubuntu-latest` runner.
The repository is public, so standard GitHub-hosted runner usage is free, and
each job receives a fresh VM maintained by GitHub.

These runners are build and research infrastructure only. Workflows must not
receive OKX API keys, tenant config, production `.env` files, SSH access to the
trading VPS, or deployment credentials. Production deployment remains manual.

## Workflows

- `secret-scan.yml` runs lint and the tracked-file/history secret scan on every
  same-repository PR and every push to `main`.
- `test-python.yml` runs the full Python suite when Python, dependency, or
  Python-workflow files change.
- `test-frontend.yml` builds the website when `Website/` or its workflow changes.
- `security.yml` audits Python and Node dependencies when manifests or the audit
  workflow change, on a weekly schedule, and on manual dispatch.
- `btc-supertrend-grid-research.yml` is manual-only and runs the BTC grid as one
  multiprocessing job.

The PR workflows retain their same-repository guard. A path-filtered workflow
that does not apply reports no check at all; that is expected, not a stuck job.

## Research limits

The standard hosted runner has four CPU cores, so the BTC grid defaults to four
workers. GitHub-hosted jobs can run for at most six hours; the grid timeout is
therefore 360 minutes. Final JSON and report artifacts are retained for 30 days.

If a grid cannot finish within that limit, split the search into explicit,
reproducible manual runs rather than moving it to the trading VPS.

## VPS policy

Never run the full pytest suite, `npm ci`, `npm run build`, an optimizer, or a
backtest on the 1 vCPU / 2 GB trading VPS. Use focused tests there and let the PR
workflows perform the full gate on GitHub-hosted runners.

## Troubleshooting

GitHub provisions hosted runners automatically; no operator PC or runner service
needs to stay online. For a queued or failed job, inspect the Actions run and its
logs, then re-run the affected job after addressing the reported GitHub service,
dependency, or test failure.

The retired self-hosted runner may remain registered for historical visibility,
but no checked-in workflow targets its labels and it can be removed safely from
the repository's Actions runner settings.
