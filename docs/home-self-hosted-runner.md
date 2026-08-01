# Home self-hosted GitHub Actions runner

The repository uses a dedicated Windows x64 runner on the operator's home PC for
**compute-heavy research only** — optimizers and backtest grids that would
starve the 1 vCPU / 2 GB live trading VPS.

> **PR CI no longer runs here.** When the repository became public on
> 2026-08-01, CI moved to GitHub-hosted `ubuntu-latest`. GitHub's hardening
> guidance is to use self-hosted runners only with private repositories,
> because a fork can otherwise run arbitrary code on this machine through a
> pull request; and hosted runners are free and unmetered for public
> repositories, which removed the reason CI was here in the first place. See
> "Where CI runs" in `AGENTS.md`.
>
> The only workflow still targeting this machine is
> `btc-supertrend-grid-research.yml`, which is `workflow_dispatch`-only and so
> cannot be triggered by a fork.

## Trust boundary

Use a dedicated OS account and runner directory. The account must not have an
OKX API key, tenant config, production `.env`, rclone credentials, SSH access to
the trading VPS, or personal files. A workflow executes repository code with the
runner account's permissions.

**This repository is public**, which is exactly the configuration GitHub warns
against for self-hosted runners: a fork can attempt to run its own code here
through a pull request. Two things keep that shut, and both must stay:

1. the only workflow targeting this machine is `workflow_dispatch`-only, so no
   pull request of any kind can trigger it;
2. every `pull_request` workflow keeps the fork guard
   (`head.repo.full_name == github.repository`) and runs on GitHub-hosted
   runners anyway.

Also set **Settings → Actions → General → Fork pull request workflows from
outside collaborators** to *Require approval for all outside collaborators*.

Do not add other public repositories to this runner. Keep production deployment
manual; the runner is research infrastructure, never a trading-engine host.

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
   `xauby-home-01` and add one custom label:

   ```text
   xauby-backtest
   ```

   The `xauby-ci` label is deliberately gone — CI runs on GitHub-hosted
   runners. An existing runner still carrying it should have it removed, so
   nothing can accidentally be scheduled here by label alone.

4. Install and start it as a Windows service when `config.cmd` prompts, or use
   the generated `svc.cmd` commands from an Administrator terminal. In the
   repository runner page, require status **Idle** and labels
   `self-hosted`, `Windows`, `X64`, and `xauby-backtest`.

The registration token is short-lived and secret. Enter it only on the home PC;
never paste it into chat, a repository file, an Actions secret, or a shell log.

## Workflows

- `secret-scan.yml` (`lint`, `secret-scan`), `test-python.yml`,
  `test-frontend.yml` and `security.yml` run on GitHub-hosted `ubuntu-latest`,
  **not on this machine**. `secret-scan.yml` runs on every PR; the other three
  are path-filtered, and a workflow skipped that way reports nothing at all —
  expected, not a stuck check.
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

## Unblocking a dispatched backtest

If a manually dispatched grid says **Waiting for a runner to pick up this job**,
verify:

1. the PC is awake and the runner service is active;
2. GitHub shows the runner as **Idle** rather than **Offline**;
3. the labels match `self-hosted`, `Windows`, `X64`, `xauby-backtest`;
4. another grid is not already using the machine.

Only one research grid may own the runner at a time. PR CI is unaffected by any
of this — it runs on GitHub-hosted runners and does not depend on this machine
being online.

There is no longer a reason to register extra runner instances here: the
parallelism that once required them now comes free from GitHub-hosted runners.
