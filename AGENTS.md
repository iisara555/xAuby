## Multi-agent workflow

Several agents work on this repo at once — Claude (local and web) and Codex (on
the VPS). `main` is not an ordinary branch: it is what CI validates, what Vercel
deploys, **and what a live money-trading engine pulls and restarts on**
(`scripts/deploy_from_github.sh` defaults to `main`). Treat it accordingly.

### Where to write code

| Path on the VPS | Who touches it | Purpose |
|-----------------|----------------|---------|
| `/opt/xauby/current` | **controlled deploys only** | active release symlink systemd runs |
| `~/xauby-work` | any agent, freely | edit, commit, push, open PRs |

**Never edit files in `/opt/xauby/current`.** A controlled deploy may replace the
active release, and `deploy_from_github.sh` may stash uncommitted changes
silently (`git stash push -m "deploy-backup-..."`). Keep work-in-progress in a
separate clone instead:

```bash
git clone https://github.com/iisara555/xAuby.git ~/xauby-work
```

`deploy_from_github.sh` resolves `ROOT` from its own location, so never run
`xauby update` from a working clone. On the SaaS/systemd host, activate a staged
release and restart the systemd units; the checkout-scoped
`controlled_restart_engine.sh` is for non-systemd installs.

### Branches

- One branch, one agent. Never let two agents write the same branch — that is how
  `main` diverged before.
- Prefix by agent: `claude/*`, `codex/*`.
- **Never force-push `main`.** The VPS deploys with `git merge origin/main
  --ff-only`, so a rewritten history makes deployment refuse to proceed.
- Land work through a PR. CI (`lint`, `secret-scan`, `test-python`,
  `test-frontend`, dependency audit) is the gate; it runs on GitHub-hosted
  `ubuntu-latest` for every same-repository PR. An agent may merge its own PR
  once CI is green — human review is not required, so work continues when
  another agent is unavailable.

### Where CI runs

**CI runs on GitHub-hosted `ubuntu-latest`. The home runner is for backtests
only.** This inverted the previous rule when the repository became public on
2026-08-01, and the reasoning is worth keeping:

- GitHub's hardening guidance is to use self-hosted runners **only with private
  repositories** — a fork can otherwise run arbitrary code on the operator's
  machine through a pull request.
- Standard GitHub-hosted runners are free and unmetered for public
  repositories, so the original reason for the home runner ("avoids
  GitHub-hosted runner minutes") no longer exists.
- Linux CI matches production. The engine runs on a Linux VPS; Windows CI was
  testing a platform nothing deploys to.
- It is faster and far more reliable. One runner serialised every job, and on
  2026-08-01 jobs sat queued for hours, died mid-run with `The runner has
  received a shutdown signal`, and timed out three times at a 30-minute
  ceiling. The same `pip-audit` command finishes in **22 seconds** on Linux.

Rules that still hold:

- Optimizers and backtests use `[self-hosted, windows, x64, xauby-backtest]`
  and must be manually dispatched; do not add a push/schedule trigger for them.
  That workflow is `workflow_dispatch`-only, so a fork PR cannot reach it.
- The runner account must not contain exchange keys, tenant config, production
  `.env`, rclone credentials, VPS SSH credentials, or personal files.
- Keep the fork-PR guard (`head.repo.full_name == github.repository`) in every
  `pull_request` workflow, and keep every workflow at `permissions: contents:
  read`. Nothing in CI needs to write.
- Never run the full suite, a frontend build, an optimizer, or a backtest on
  the trading VPS.

Installation and labels for the backtest runner are documented in
`docs/home-self-hosted-runner.md`. GitHub registration tokens are short-lived
secrets and must never be requested in chat or committed.

### Before you push

On a capable workstation or the home runner:

```bash
PYTHONPATH=. python3 -m pytest -q          # full suite must pass
python3 scripts/scan_secrets.py --tracked --history
cd Website && npm run build                # only if you touched Website/
```

On the VPS, substitute focused tests for the full suite and let the PR's
self-hosted CI run the full suite/build. See the resource rules below.

### Resource limits on the VPS

The trading VPS is a **1 vCPU / 2 GB** droplet, and the live engine shares it with
whatever an agent runs. The engine's systemd unit caps it at `MemoryHigh=330M`,
`MemoryMax=420M`, `CPUQuota=30%` — and `CPUQuota` is a **ceiling, not a
reservation**, so a heavy job alongside it can still starve the engine of CPU and
make it miss ticks or drop its WebSocket while a position is open.

Do **not** run these on the VPS:

- `npm run build` / `npm ci` in `Website/` — a Next.js build alone can exceed the
  free memory on a 2 GB box
- the full `pytest -q` suite
- the optimizer or a backtest (`scripts/optimize_pair_configs.py`,
  `scripts/replay_backtest.py`)

Let CI do that work: `test-python` runs the full suite and `test-frontend` the
frontend build, on GitHub-hosted runners for every same-repository PR that
touches the relevant files. Each workflow is path-filtered, so a PR that
changes no Python skips the suite entirely and a skipped workflow reports
nothing — that is expected, not a stuck check. On the VPS, keep to targeted
checks:

```bash
PYTHONPATH=. python3 -m pytest -q tests/test_<the_thing_you_changed>.py
```

Never move a heavy job to the VPS. It shares 1 vCPU with a live engine holding
real positions.

### Deploying

- **Vercel** (`Website/`) is wired up in the Vercel dashboard, not in this repo —
  there is no `vercel.json` or Vercel workflow here, so check the dashboard for
  the actual branch settings. Auto-deploy from `main` is fine: no money at risk.
- **VPS** (trading engine) is **manual only**. Never make restart-on-push or
  unattended deployment part of CI.
- A tracked open position is **supported during a controlled restart** and does
  not require waiting for a flat account. `scripts/controlled_restart_preflight.py`
  defaults to `allow_tracked_positions=True`; it permits a position represented
  in the runtime DB/state while refusing untracked orders, balances, ambiguous
  state, or exchange-verification failures. `--no-open-positions` remains
  available for an intentionally flat-only restart.
- Restart with exposure only after explicit operator authorization. Before the
  restart, back up the tenant config, runtime state, engine SQLite DB, and control
  DB; record the exchange position's symbol, side, quantity, and entry price; and
  require the preflight to report `SAFE`.
- Before activation, run `scripts/audit_release_readiness.py` against the staged
  code plus the **tenant** config/whitelist. After restart, run it again with the
  runtime DB and current run id; a release that has no replayed signal or no
  SHORT-side evidence when `--require-short` is requested remains blocked.
- On the SaaS/systemd host, stage the exact commit as an atomic release, keep a
  rollback target, restart `xauby-control.service` and the affected
  `xauby-engine@<tenant>.service`, and do not launch a second checkout-scoped
  engine. After restart, require both services to be active on the intended
  commit and confirm that DB/state and exchange still agree on symbol, side,
  quantity, and entry price, with no untracked orders, reconcile halt, degraded
  state, or startup error. Roll back the release and config backups if any gate
  fails.
- `core/` is gitignored runtime state (DB, logs, equity peak, locks). Never commit it.

### Config is not in the repo

The engine reads `bot_config.yaml` and `coin_whitelist.json` from
`/etc/xauby/tenants/<tenant>/`, not from the checkout: `config_root()` returns
`XAUBY_CONFIG_DIR`, and `whitelist_json_path` is joined onto it. **Editing the
repo's config files changes nothing in production** — deploying code and changing
config are two separate operations against two separate locations.

A single strategy key can therefore live in four places: repo YAML, repo
whitelist, tenant YAML (often twice — `strategy.config.<id>` and
`mode_indicator_profiles`), and the tenant whitelist, which wins at runtime.
Change a subset and the value silently fails to apply.

Before shipping a **startup guard**, check it against the *tenant* config.
`validate_exit_config` raises inside `LiteTradingEngine.start()` before either
lock is taken, so a guard that passes on repo config and fails on tenant config
turns a routine deploy into an outage.

Tenant files carry their own owner and ACLs. Back up with `cp -p` and edit in
place; never overwrite them with a copy from the repo.

### Two locks

`core/.engine.lock` is per-checkout and does not see an engine started elsewhere.
`/var/lib/xauby/account_locks/account_<hash>.lock` is per exchange account and is
the one that protects capital — a second live engine launched from a work clone
fails closed there within seconds, before placing any order (confirmed
2026-07-27). That is a backstop, not a licence to start one.

## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

When the user types `/graphify`, use the installed graphify skill or instructions before doing anything else.

Rules:
- For codebase questions, first run `graphify query "<question>"` when graphify-out/graph.json exists. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.
- Dirty graphify-out/ files are expected after hooks or incremental updates; dirty graph files are not a reason to skip graphify. Only skip graphify if the task is about stale or incorrect graph output, or the user explicitly says not to use it.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).
