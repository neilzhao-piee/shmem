# Fleet (home lab)

Updated: 2026-01-12

Index only — per-machine detail lives in `facts/machines/<host>.md` (one file per
machine; do not grow this table into one).

## Machines

| host | role | detail file |
|---|---|---|
| `build1.home.lan` (10.9.0.21) | coding VM | machines/build1.md |
| `tank.home.lan` (10.9.0.30) | docker host, backups | machines/tank.md |
| `gw.home.lan` (10.9.0.1) | router/gateway | machines/gw.md |

## Shared network notes

- VLAN 20 = lab, VLAN 10 = iot (isolated, no route to lab by default).
- iot devices reach lab only via the `iot-relay` container on tank (see machines/tank.md).
