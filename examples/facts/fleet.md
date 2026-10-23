# Fleet (home lab)

Updated: 2026-01-12

## Machines

| host | role | notes |
|---|---|---|
| `build1.home.lan` (10.9.0.21) | coding VM | agent-a runs here; 32 GB RAM; SSD |
| `tank.home.lan` (10.9.0.30) | docker host | backup + cron jobs; ZFS pool `tank` |
| `gw.home.lan` (10.9.0.1) | router/gateway | do not reboot during sync windows |

## Services

- **Backup**: `tank` runs `restic` nightly at 02:30 (systemd timer `restic-nightly.timer`).
  Prune runs Sunday 03:30. Repo: `rest:restic://tank/backup` (credentials in `/opt/backup/.env` — location only).
- **Web UI**: `tank:8090` serves the dashboard (nginx → caddy → app container `dash`).
  Log file: `/var/log/dash/app.log` on tank.

## Network

- VLAN 20 = lab, VLAN 10 = iot (isolated, no route to lab by default).
- iot devices reach lab only via the `iot-relay` container on tank (allowlist in `/opt/iot-relay/allow.conf`).

## Known quirks

- `build1`'s NTP drifts after suspend; run `timedatectl set-ntp true` if dates look off (2026-01-05, verified).
- `tank`'s ZFS auto-snapshot job and restic both use the pool; keep snapshot space ≥ 20% free (2026-01-08, learned the hard way: restic filled the pool and snapshots were lost).
