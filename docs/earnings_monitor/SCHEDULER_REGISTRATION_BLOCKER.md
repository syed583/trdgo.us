# Scheduler registration: blocker and the authorized procedure (2026-11-04)

## What was blocked
During the A.5 review pass I tried to run, in one PowerShell call: `deploy\smart_move_collector\register_collection_task.ps1` (without `-Create`, which only prints the plan), `Get-ScheduledTask -TaskName TRDGO-SmartMove-Collector`, and `verify_collector.ps1`.
The Claude Code permission classifier refused the call with the reason **"Unauthorized Persistence"** (the script registers a Windows scheduled task, so the classifier treats running it as creating persistence, even though the print-only form creates nothing).
I did not retry, rephrase or route around it. No task was created, changed or removed, on this machine or any other.

## What is therefore NOT tested
* The real `Register-ScheduledTask` call (trigger shape: hourly, 24 hours, every day, plus at boot; single instance; 2 hour limit; restart on failure).
* The task running the wrapper under the real Task Scheduler, and its recovery after a reboot.
Everything the wrapper does when it runs is covered by the stub end-to-end tests, the failure scenarios and the replay; the scheduler shell around it is not.

## Authorized procedure to complete the test (owner decision; nothing here is automatic)
Option 1, the owner runs it in their own PowerShell (preferred, no permission change):
1. Print only: `.\register_collection_task.ps1` (creates nothing).
2. On a test machine, create as the current user without the boot trigger: `.\register_collection_task.ps1 -RunAs CurrentUser -SkipBootTrigger -Create`, with the settings file set to DRY RUN (the template is DRY RUN).
3. Check `Get-ScheduledTask -TaskName TRDGO-SmartMove-Collector` and run `.\verify_collector.ps1`.
4. Remove it: `.\register_collection_task.ps1 -Remove`.
Option 2, the owner adds an explicit permission rule for these commands in Claude Code settings, then asks for the test to be run in this session.
On the VPS, registration happens only at the Stage D step of the deployment sequence, after explicit approval, in DRY RUN; switching to LIVE is a separate edit of the settings file by the owner.
