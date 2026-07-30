# Home self-hosted GitHub Actions runner

The repository uses a dedicated Linux x64 runner on the operator's home PC for
PR CI and compute-heavy research. This keeps full pytest, the Next.js build,
dependency audits, optimizers, and backtests off the 1 vCPU / 2 GB live trading
VPS and avoids GitHub-hosted runner minutes.

## Trust boundary

Use a dedicated OS account and runner directory. The account must not have an
OKX API key, tenant config, production `.env`, rclone credentials, SSH access to
the trading VPS, or personal files. A workflow executes repository code with the
runner account's permissions.

The checked-in PR workflows refuse fork PRs before assigning a self-hosted
runner. Do not remove that guard and do not add public repositories to this
runner. Keep production deployment manual; the runner is build/research
infrastructure, never a trading-engine host.

Recommended capacity is Linux x64 with at least 4 CPU cores, 8 GB RAM, and
20 GB free disk. Eight cores and 16 GB RAM are preferable for a four-worker BTC
grid. Native Ubuntu is simplest; Windows users should install the Linux runner
inside WSL2 with systemd enabled. The PC must stay awake and online while jobs
run.

## One-time registration

1. Sign in to GitHub as the repository owner and open
   **xAuby → Settings → Actions → Runners → New self-hosted runner**.
2. Select **Linux / x64**. On the home PC, under a dedicated account such as
   `xauby-runner`, run the download and checksum commands GitHub displays.
3. Run GitHub's generated `config.sh` command. Use runner name
   `xauby-home-01` and add both custom labels:

   ```text
   xauby-ci,xauby-backtest
   ```

4. Install and start it as a service using the `svc.sh` commands shown by
   GitHub. In the repository runner page, require status **Idle** and labels
   `self-hosted`, `linux`, `x64`, `xauby-ci`, and `xauby-backtest`.

The registration token is short-lived and secret. Enter it only on the home PC;
never paste it into chat, a repository file, an Actions secret, or a shell log.

## Workflows

- `.github/workflows/secret-scan.yml` and `security.yml` target
  `[self-hosted, linux, x64, xauby-ci]` for PR and `main` gates.
- `.github/workflows/btc-supertrend-grid-research.yml` targets
  `[self-hosted, linux, x64, xauby-backtest]` and starts only from
  **Actions → BTC SuperTrend OKX grid research → Run workflow**.
- The BTC grid is one job using local multiprocessing. Start with four workers;
  reduce to two if the PC becomes memory-bound. Only one research grid may own
  the runner at a time.

Final BTC JSON/report artifacts are uploaded for 30 days. They are compact; raw
candle and per-shard interchange files stay on the runner and are not uploaded.

## Unblocking a PR

If checks say **Waiting for a runner to pick up this job**, verify:

1. the PC is awake and the runner service is active;
2. GitHub shows the runner as **Idle** rather than **Offline**;
3. all five required labels match exactly;
4. another backtest or CI job is not already using the single runner.

After registering this runner, open the failed PR run and choose
**Re-run all jobs**. Merge to `main` only after every blocking check is green.

Do not work around an offline runner by changing `runs-on` back to
`ubuntu-latest`, running the full suite on the trading VPS, or bypassing the PR
gate.
