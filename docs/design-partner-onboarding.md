# Onboarding a design partner

Roadmap P2.5. Two free SIM-only partners, on the invite flow that already
exists. Capacity is `max_users=3` (owner + two) and `max_active_engines=2`;
both are hardcoded in `xauby/saas/settings.py` and the roadmap says explicitly
not to raise them yet.

Everything below is done on the control-plane host, as the owner.

---

## 0. Before you invite anyone: prove email works

Skipping this means finding out that SMTP is broken from a design partner who
tells you the invite never arrived — a failure indistinguishable from them
ignoring it.

```bash
python -m scripts.check_email                      # config + connect + login
python -m scripts.check_email --to you@example.com # send yourself a real one
```

`--to` is the only step that proves delivery. Authentication succeeding says
the credentials are right; it does not say the provider will accept your `From`
address or that the message clears a spam filter. **Open the message, and check
the spam folder too.**

Settings live in `/etc/xauby/control.env`:

| Variable | Notes |
|---|---|
| `XAUBY_SMTP_HOST` | |
| `XAUBY_SMTP_PORT` | 587 (STARTTLS) or 465 (implicit TLS — detected automatically) |
| `XAUBY_SMTP_USERNAME` / `XAUBY_SMTP_PASSWORD` | |
| `XAUBY_SMTP_FROM` | **Required** unless the username is itself an address. Several providers use an opaque token (SendGrid's username is the literal `apikey`), and falling back to it produces an invalid `From`. |
| `XAUBY_SMTP_USE_SSL` | Only for implicit TLS on a non-standard port. |

Restart `xauby-control.service` after editing.

If email is not configured, invites still work — the response carries the link
and `delivery: "manual"`, and you send it yourself. What you must not ignore is
`delivery: "failed"`: that means SMTP *is* configured and is broken.

## 1. Invite

Workspace → Admin → Users → Invite, or:

```bash
curl -X POST https://<host>/api/v1/admin/invites \
  -H "X-CSRF-Token: <token>" -H "Cookie: <session>" \
  -d '{"email":"partner@example.com"}'
```

The link is valid for 7 days. A fourth user is refused with 409 — that is the
capacity ceiling doing its job, not a bug.

## 2. What the partner does

1. Opens the invite link, sets a password (or signs in with Google).
2. Picks a preset and saves a trading profile. This compiles their own
   `bot_config.yaml` and `coin_whitelist.json` under
   `/etc/xauby/tenants/<slug>/`.
3. Connects exchange API keys. **Tell them to create the key with withdrawals
   disabled and IP-restricted** to the egress IPs shown on that screen. The
   control plane now asks the venue directly whether withdrawal is off (P2.4);
   if their key can withdraw, the connection reports it rather than passing
   silently.
4. Optionally connects Telegram for alerts.

The tenant is simulation-only throughout. `live_activation_enabled` is what
keeps it that way — not the partner's role, since `/api/v1/live/activate` is
open to any tenant owner by design. Leave it off for the pilot.

## 3. Start their engine

```bash
sudo systemctl start xauby-engine@<slug>.service
sudo systemctl status xauby-engine@<slug>.service
```

`ExecStartPre` runs `/usr/local/libexec/xauby-materialize-credentials`, which
writes the tenant's decrypted credentials to `/run/xauby/credentials/<slug>.env`
on tmpfs. **If you have not reinstalled that helper since the P2.1 fix, do it
now** — the repo copy is not what systemd runs:

```bash
sudo install -m 0755 deploy/xauby-materialize-credentials /usr/local/libexec/
```

Before that fix, a brand-new SIM-only tenant — the default state of every
invited user — could not start at all, and per-tenant Telegram alerts were
disabled on every reboot.

## 4. Confirm it survives a reboot

The acceptance criterion is not "it started once".

```bash
sudo reboot
# then, once it is back:
systemctl is-active xauby-engine@<slug>.service
grep TELEGRAM_ENABLED /run/xauby/credentials/<slug>.env    # expect "true"
```

Ask the partner to confirm they are still receiving Telegram messages. A silent
alert channel is the failure mode that looks like everything working.

## 5. The two-week soak

P2.5 is met when a non-owner tenant has provisioned, connected keys, run SIM for
two weeks, and is still receiving alerts after a host reboot. Only the last part
is checkable in code — `tests/test_design_partner_journey.py` walks the whole
path up to the point where only time is left.

Worth watching during the soak, because none of it is automated yet:

- Does their engine appear in the daily backup? (`/var/lib/xauby/backups`, and
  off-host since P2.2.)
- Do they hit anything that requires you? Every such moment is a gap in the
  product, and the Phase 3 unlock condition is "design partners use it without
  help".

---

## Known gaps a partner will meet

Written down because they will come up, and "we know" is a better answer than
discovering it live.

- **There is no exchange-disconnect endpoint.** A partner cannot remove their
  own API keys through the product; you have to do it for them. The stored
  credential is what the engine reads, and clearing it does correctly wipe the
  plaintext on tmpfs (P2.1) — there is simply no button.
- **No ToS, privacy policy, or risk disclosure exists.** Fine for two people you
  know personally; a blocker before anyone pays, and the first item of Phase 3.
- **Restore from the off-host backup is manual** — fetch with the provider's own
  tooling, then `scripts/saas_restore.py`. The uploader deliberately cannot read
  back (P2.2).
- **Public signup is a hard 404.** Invitation is the only route in, on purpose.
