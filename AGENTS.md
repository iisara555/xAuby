## Multi-agent workflow

Several agents work on this repo at once — Claude (local and web) and Codex (on
the VPS). `main` is not an ordinary branch: it is what CI validates, what Vercel
deploys, **and what a live money-trading engine pulls and restarts on**
(`scripts/deploy_from_github.sh` defaults to `main`). Treat it accordingly.

### Where to write code

| Path on the VPS | Who touches it | Purpose |
|-----------------|----------------|---------|
| `/opt/xauby/current` | **only `xauby update`** | production checkout systemd runs |
| `~/xauby-work` | any agent, freely | edit, commit, push, open PRs |

**Never edit files in `/opt/xauby/current`.** `deploy_from_github.sh` stashes
uncommitted changes *silently* (`git stash push -m "deploy-backup-..."`) and then
restarts the live engine — so work-in-progress there is lost without an error,
while a real position is open. Clone a separate working copy instead:

```bash
git clone https://github.com/iisara555/xAuby.git ~/xauby-work
```

`deploy_from_github.sh` resolves `ROOT` from its own location, so running
`xauby update` out of a working clone deploys the **wrong** checkout. Run it only
from `/opt/xauby/current`.

### Branches

- One branch, one agent. Never let two agents write the same branch — that is how
  `main` diverged before.
- Prefix by agent: `claude/*`, `codex/*`.
- **Never force-push `main`.** The VPS deploys with `git merge origin/main
  --ff-only`, so a rewritten history makes deployment refuse to proceed.
- Land work through a PR. CI (`secret-scan` + `test`) is the gate; it runs on
  every PR. An agent may merge its own PR once CI is green — human review is not
  required, so work continues when another agent is unavailable.

### Before you push

```bash
PYTHONPATH=. python3 -m pytest -q          # full suite must pass
python3 scripts/scan_secrets.py --tracked --history
cd Website && npm run build                # only if you touched Website/
```

### Deploying

- **Vercel** (`Website/`) is wired up in the Vercel dashboard, not in this repo —
  there is no `vercel.json` or Vercel workflow here, so check the dashboard for
  the actual branch settings. Auto-deploy from `main` is fine: no money at risk.
- **VPS** (trading engine) is **manual only**: run `xauby update` from
  `/opt/xauby/current`, and pick a moment with no open position. Never automate it.
- `core/` is gitignored runtime state (DB, logs, equity peak, locks). Never commit it.

## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

When the user types `/graphify`, use the installed graphify skill or instructions before doing anything else.

Rules:
- For codebase questions, first run `graphify query "<question>"` when graphify-out/graph.json exists. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.
- Dirty graphify-out/ files are expected after hooks or incremental updates; dirty graph files are not a reason to skip graphify. Only skip graphify if the task is about stale or incorrect graph output, or the user explicitly says not to use it.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).
