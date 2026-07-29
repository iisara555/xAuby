# P2.2 — Off-site backup and credential-key recovery

This runbook is the operational half of P2.2.  It keeps the three things that
are needed to recover a tenant deliberately separate:

| Artifact | Lives where | Protected by |
| --- | --- | --- |
| SaaS archive (`.tar.gz.enc`) | dedicated rclone remote | `XAUBY_BACKUP_ENCRYPTION_KEY` (AES-256-GCM) |
| Recovery bundle (`.asc`) | same remote | offline GPG private key |
| GPG private key | operator-controlled offline store | never imported on the VPS |

The recovery bundle contains the backup encryption key and the credential
master-key lineage, encrypted to the operator's public GPG key. It is never
written plaintext to the VPS filesystem or sent to rclone unencrypted.

## One-time setup

1. Create an offline GPG recovery key on a machine that is not the trading VPS.
   Store its private key in the operator's password manager or hardware-backed
   offline storage. Export only the public key to the VPS.
2. Create a dedicated, encrypted rclone remote directory. Do not point it at a
   general-purpose remote root. Install `rclone` on the VPS and create
   `/etc/xauby/rclone.conf` as `root:xauby-control`, mode `0640`.
3. Import the recovery **public** key into the service-owned keyring:

   ```bash
   sudo -u xauby-control gpg --homedir /var/lib/xauby/backup-gpg --import recovery-public.asc
   ```

4. Provision the separate backup encryption key without printing it:

   ```bash
   sudo /opt/xauby/current/venv/bin/python scripts/ensure_backup_encryption_key.py
   ```

5. Edit `/etc/xauby/backup.env` as `root:xauby-control`, mode `0640`:

   ```dotenv
   XAUBY_BACKUP_RCLONE_DESTINATION=your-crypt-remote:xauby/production
   XAUBY_BACKUP_RCLONE_CONFIG=/etc/xauby/rclone.conf
   XAUBY_BACKUP_OFFSITE_RETENTION_DAYS=30
   XAUBY_BACKUP_GPG_HOMEDIR=/var/lib/xauby/backup-gpg
   XAUBY_BACKUP_GPG_RECIPIENT=<full-recovery-key-fingerprint>
   ```

   Do not place the GPG private key, exchange credentials, or any secret in the
   repository. The backup service refuses to upload if the destination, AES
   key, GPG recipient, rclone config, or executables are missing.

6. Install the updated unit, reload systemd, and make one manual backup:

   ```bash
   sudo systemctl daemon-reload
   sudo systemctl start xauby-backup.service
   sudo journalctl -u xauby-backup.service -n 50 --no-pager
   ```

The daily timer runs at 03:15 ICT with up to 15 minutes of random delay. Local
archives remain under `/var/lib/xauby/backups` for 7 days; the off-site remote
keeps daily archive and recovery-bundle pairs for 30 days by default.

## Monthly restore drill

Download one `.tar.gz.enc` archive to a protected local path, then run this on
the VPS or a disposable recovery host that has the current control and backup
environment files:

```bash
python -m scripts.saas_restore /secure/path/xauby-saas-daily-....tar.gz.enc --restore-drill
```

The drill verifies checksums and SQLite integrity, then decrypts every stored
exchange and Telegram credential from the backup without printing or writing
plaintext credentials. It does not modify the running control DB, tenant
config, or engine state. Record the archive name, timestamp, and successful
output in the operations log.

## Disaster recovery

1. Download the matching archive and `.asc` recovery bundle from the off-site
   remote.
2. On an isolated recovery host, decrypt the recovery bundle with the offline
   GPG private key. Keep the resulting JSON mode `0600`; never paste it into a
   terminal history or ticket.
3. Populate a fresh `/etc/xauby/control.env` with the active credential master
   key, version, and retained previous-key entries from that bundle. Populate
   `/etc/xauby/backup.env` with the backup encryption key.
4. Run `--restore-drill` first. Stop all engines, then use
   `scripts/saas_restore.py <archive> --apply --confirm-engines-stopped` only
   after reviewing the backup manifest and exchange state.
5. Reconcile every position with the exchange before allowing any engine to
   trade. A restored database is not evidence that a live position still agrees
   with the venue.

## Credential-master-key rotation

Rotation changes the active key version and re-encrypts every exchange and
Telegram credential in one transaction. It requires a verified backup and a
controlled stop of the control service plus all tenant engines:

```bash
python scripts/rotate_credential_master_key.py --apply --confirm-services-stopped
```

The script stages the new active key **and retains the old key** before it
changes database rows, so an interrupted run remains decryptable. Afterward,
restart services through the controlled-release process and run a restore drill.
Keep prior key entries for at least the longest off-site retention window plus
one successful restore drill; they are necessary to recover older archives.

Never rotate a key by editing `XAUBY_CREDENTIAL_MASTER_KEY` alone.
