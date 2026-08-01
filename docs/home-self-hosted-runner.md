# Home self-hosted GitHub Actions runner

The repository uses a dedicated Windows x64 runner on the operator's home PC for
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

Recommended capacity is Windows x64 with at least 4 CPU cores, 8 GB RAM, and
20 GB free disk. Eight cores and 16 GB RAM are preferable for a four-worker BTC
grid. Use the native Windows runner from an ordinary dedicated local account;
WSL is not required. The PC must stay awake and online while jobs run.

## One-time registration

1. Sign in to GitHub as the repository owner and open
   **xAuby → Settings → Actions → Runners → New self-hosted runner**.
2. Select **Windows / x64**. On the home PC, under a dedicated account such as
   `xauby-runner`, run the download and checksum commands GitHub displays.
3. Run GitHub's generated `config.cmd` command. Use runner name
   `xauby-home-01` and add both custom labels:

   ```text
   xauby-ci,xauby-backtest
   ```

4. Install and start it as a Windows service when `config.cmd` prompts, or use
   the generated `svc.cmd` commands from an Administrator terminal. In the
   repository runner page, require status **Idle** and labels
   `self-hosted`, `Windows`, `X64`, `xauby-ci`, and `xauby-backtest`.

The registration token is short-lived and secret. Enter it only on the home PC;
never paste it into chat, a repository file, an Actions secret, or a shell log.

## Workflows

- `.github/workflows/secret-scan.yml` (`lint`, `secret-scan`),
  `test-python.yml`, `test-frontend.yml` and `security.yml` target
  `[self-hosted, windows, x64, xauby-ci]` for PR and `main` gates.
- `secret-scan.yml` runs on every PR. The other three are path-filtered: the
  Python suite is skipped when no Python changed, the frontend build when
  `Website/` is untouched, and the dependency audit unless a dependency
  manifest changed. A workflow that is skipped this way reports nothing at
  all — that is expected, not a stuck check.
- `.github/workflows/btc-supertrend-grid-research.yml` targets
  `[self-hosted, windows, x64, xauby-backtest]` and starts only from
  **Actions → BTC SuperTrend OKX grid research → Run workflow**.
- The BTC grid is one job using local multiprocessing. Start with four workers;
  reduce to two if the PC becomes memory-bound. Only one research grid may own
  the runner at a time.

Final BTC JSON/report artifacts are uploaded for 30 days. They are compact; raw
candle and per-shard interchange files stay on the runner and are not uploaded.

## Keeping the runner awake

A job that dies within seconds of starting and logs `The runner has received a
shutdown signal` was not failed by the code — the runner process went away
underneath it. This happened on 2026-08-01 to a job that started four seconds
after four others had completed normally. Two settings prevent it:

```powershell
# Never sleep or hibernate on AC. A sleeping PC drops in-flight jobs and
# leaves later ones queued with no runner to claim them.
powercfg /change standby-timeout-ac 0
powercfg /change hibernate-timeout-ac 0
```

Run the runner as a **Windows service** (`svc.cmd install` then `svc.cmd
start`) rather than `run.cmd` in a console window. A console session ends when
the user logs out or the window closes; the service survives both and restarts
with the machine.

## Running more than one runner

GitHub assigns at most one job per runner at a time, so a single runner
serialises the whole PR gate: on 2026-08-01 `secret-scan` → `lint` →
`test` → `node-dependencies` ran strictly back to back, each starting within
three seconds of the previous finishing, for ~6-7 minutes of wall time that
was almost entirely idle waiting.

Registering additional runners on the same PC restores parallelism. Each needs
its own directory and its own registration token, with **identical labels** so
any of them can claim any job:

```powershell
mkdir C:\actions-runner-2 ; cd C:\actions-runner-2
# unpack the same runner package, then register with the same labels
./config.cmd --url https://github.com/iisara555/xAuby --token <TOKEN> `
             --name xauby-home-02 `
             --labels self-hosted,windows,x64,xauby-ci `
             --work _work
./svc.cmd install ; ./svc.cmd start
```

Two or three instances suit a typical desktop: `lint`, `secret-scan`,
`test-python` and `test-frontend` then overlap instead of queueing. Do not give
the extra instances the `xauby-backtest` label — a research grid is expected to
own the machine alone, and the note above about one grid at a time still holds.

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
