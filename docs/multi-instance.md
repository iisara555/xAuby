# Running multiple instances on one host

xAuby separates **config** from **mutable data** so several engines (different
accounts / exchanges / pair sets) can run side by side from the same code
checkout without colliding. Both roots are env-driven and default to the
single-instance layout, so existing deployments need no changes.

| Concern | Env var | Resolver | Default |
|---|---|---|---|
| Config files (`bot_config.yaml`, `coin_whitelist.json`, `.env`) | `XAUBY_CONFIG_DIR` | `config_root()` (`xauby/runtime/paths.py`) | current working dir |
| Mutable data (SQLite DB, lock, state JSON, logs, sim balances) | `XAUBY_HOME` + `XAUBY_INSTANCE_ID` | `runtime_root()` (`xauby/runtime/paths.py`) | `core/` (+ `/<id>`) |

With nothing set, config resolves from the repo root and data lives in `core/`
exactly as before.

## Recommended layout

```
<repo>/                         # shared code
  instances/
    acct1/
      bot_config.yaml
      coin_whitelist.json
      .env                      # 0600; per-instance secrets
    acct2/
      bot_config.yaml
      coin_whitelist.json
      .env
  core/
    acct1/xauby.db  …           # data for acct1 (XAUBY_INSTANCE_ID=acct1)
    acct2/xauby.db  …
```

Launch one instance:

```bash
XAUBY_CONFIG_DIR=instances/acct1 XAUBY_INSTANCE_ID=acct1 \
  python run_xauby.py --live
```

`XAUBY_CONFIG_DIR` re-roots the engine's `_project_root` (so the whitelist is
read from the instance dir) and the `.env` load at startup; a relative
`--config` is resolved under it too. `XAUBY_INSTANCE_ID` namespaces every
runtime artifact under `core/<id>/`. The single-process lock is per data root,
so each instance gets its own lock.

## systemd (one unit per instance)

```ini
# /etc/systemd/system/xauby@.service  (templated unit)
[Service]
Environment=XAUBY_CONFIG_DIR=/root/xAuby/instances/%i
Environment=XAUBY_INSTANCE_ID=%i
WorkingDirectory=/root/xAuby
ExecStart=/root/xAuby/venv/bin/python run_xauby.py --live
```

Enable with `systemctl enable --now xauby@acct1` etc. Each instance reads its own
`.env`, so secrets never need to be shared between accounts.

## Secrets

`.env` holds exchange API keys; the launcher writes it `0600` (owner-only) and
each instance keeps its own under `XAUBY_CONFIG_DIR`. Credentials are resolved
by exchange-specific env names (`credential_env_names`, see
[multi-exchange-ccxt.md](multi-exchange-ccxt.md)), so two instances on different
exchanges use different variables in their respective `.env` files.

## Caveats

- The code checkout is shared; a `git pull` updates all instances at once.
- This is "scale by running more engines", not one engine driving many pairs —
  see the per-tick pair loop in `xauby/engine/loop.py`.
