# Reliable unattended scheduling for Smart Move (proposal; nothing here is activated)

## What happened
Windows Modern Standby suspended the whole machine for 2h36m (11:33–14:09 UTC) and 2h05m (14:53–16:58 UTC) on 1 Nov. A suspended PC runs nothing: no process, no timer. The collector was correct (resumed by itself, repeated no request); it simply was not running.
Read-only facts about this machine: Acer laptop; power plan "Acer": on AC sleep-after = never and wake timers = enabled; on battery sleep-after = 1 h; hibernate after 12 h on AC; 28 % battery at the check.

## Design rules (already in the code)
1. Work is **restart-safe**: checkpoints, de-duplication, per-request governor, calendar guard (refreshes or stops, never captures against a stale calendar).
2. Prefer **short ticks** (`scheduler_tick.py`, every 30 min) over one 10-hour process: each tick decides from the clock, so a gap costs nothing but time. The tick is plan-only until three switches are set.
3. A missed window is **reported, not hidden**: `MISSED_WINDOW` alerts and `expired_before_collection` in the report.

## Options, best first
| Option | Reliability | Notes |
|---|---|---|
| **A. Always-on host (VPS or a mini PC)** with the systemd timer in `docs/smart_move/deploy/` | highest | No sleep. Needs: a separate credentials file, the three live-collection switches, the UW 3-concurrent budget shared with the production app (the same token), and your authorization. Python 3.13 and the pinned requirements. |
| **B. This laptop, hardened (by you)** | medium | Keep it on AC (idle sleep is already off on AC); set "when I close the lid" = Do nothing for AC; keep the Task Scheduler task with `WakeToRun=true` (wake timers are enabled on AC) and `StartWhenAvailable=true` (a missed tick runs on wake); never rely on battery. A closed lid on battery will still sleep. |
| **C. Per-process keep-awake** (implemented: `smart_move_keep_awake.py`) | partial | Blocks idle sleep while a collector runs; no setting changed; cannot stop a closed lid, manual Sleep or low-battery hibernate. Used by the one-time runner. |

## Recommended
Use **A** for the recurring collection and **B + C** only for supervised one-off runs. Before any run: plug in, lid open, check the battery, confirm the guard reports a fresh calendar.

## Settings I did not and will not change without you
Power plan, lid action, wake timers, Task Scheduler registration. Registering the task is a one-line `schtasks /Create /XML` after review (task file ships `Enabled=false`).
