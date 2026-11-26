# tank.home.lan

Updated: 2026-01-12

Docker host, 10.9.0.30; backup + cron jobs; ZFS pool `tank`.

## Backup

- `restic` nightly at 02:30 (systemd timer `restic-nightly.timer`); prune Sunday 03:30.
- Repo: `rest:restic://tank/backup` (credentials in `/opt/backup/.env` — location only).

## Services

- Dashboard on `tank:8090` (nginx → caddy → app container `dash`); log: `/var/log/dash/app.log`.
- `iot-relay` container bridges VLAN 10 → lab; allowlist in `/opt/iot-relay/allow.conf`.

## Quirks

- ZFS auto-snapshot job and restic share the pool; keep snapshot space ≥ 20% free
  (2026-01-08, learned the hard way: restic filled the pool and snapshots were lost).
