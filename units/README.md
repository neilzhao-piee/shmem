# Scheduling the summarizer

The summarizer only helps if it actually runs. Two supported installs — pick one.

## 1. User-scope systemd timer (default: `make install-units`)

```sh
make install-units DIR=/path/to/memory     # -> ~/.config/systemd/user/shmem-summarize.{service,timer}
systemctl --user status shmem-summarize.timer
journalctl --user -u shmem-summarize.service -n 50
```

**Requires lingering.** A user-scope timer belongs to your *login session*: over SSH with no
graphical session, and after you log out, the user manager disappears and the timer silently
stops firing — the install looks fine and nothing ever runs. `make install-units` checks this
and refuses with the fix instead of installing a dead timer:

```sh
loginctl enable-linger $USER      # keep the user manager alive without a login session
```

If you cannot enable lingering (shared host, no policykit, container), use the system-scope
install below.

## 2. System-scope systemd timer

Copy the same two templates into `/etc/systemd/system`, replacing `@SHMEM_ROOT@`, and add
`User=` so the service still runs as the memory owner (the memory tree is a normal directory —
it does not need root):

```ini
# /etc/systemd/system/shmem-summarize.service  (add to the template)
User=you

# /etc/systemd/system/shmem-summarize.timer
OnCalendar=*-*-* 05:30:00 Europe/Berlin    # local wall clock; a timezone may be appended
Persistent=true                            # run once at boot if the time was missed
```

```sh
sudo systemctl daemon-reload
sudo systemctl enable --now shmem-summarize.timer
systemctl list-timers shmem-summarize.timer
```

Set the memory root and any options through `Environment=` lines in the service (see
`config.example.env` for every variable), rather than a shell profile — a systemd service has
no login environment.

Optional chain (if your setup derives an injectable digest and an injected agent block from
the journal, run them after each summarize so the injected context never lags the journal):

```ini
ExecStartPost=/bin/bash -lc '@SHMEM_ROOT@/tools/make-digest.sh'
ExecStartPost=/bin/bash -lc '@SHMEM_ROOT@/tools/update-agents-block.sh'
```

## 3. macOS (launchd)

No systemd there. Install `launchd.example.plist` with your memory root substituted:

```sh
sed 's|@SHMEM_ROOT@|/Users/you/memory|g' units/launchd.example.plist > ~/Library/LaunchAgents/shmem-summarize.plist
launchctl load ~/Library/LaunchAgents/shmem-summarize.plist
```

## Verify it fired

Whichever install you chose, the proof is the journal, not the timer state: the newest
`journal/<YYYY-MM>.md` should gain an `### auto-summary` section after the scheduled time.

```sh
make stats                     # index size + stalled inbox deliveries
make summarize                 # run it by hand, same path the timer takes
```

## A backfill loop cannot converge on a live host

Running `make summarize` repeatedly "until processed=0" never terminates while
you are working: your own session files keep growing, and sessions of other
live agents reappear every pass too. That is correct behaviour — those sessions
are still moving. Judge backfill convergence by the state count
(`.summarize-state.json` stops growing) and by `shmem health`, not by
`processed=0` on the last pass. The quiet guard (`SHMEM_QUIET_SECONDS`) defers
still-growing sessions; it covers every adapter because adapter markers lead
with the last-activity epoch — a bare counter marker (the pre-0.7.0 hermes
form) silently opted out of the guard and re-spent model calls each run.
