# TRDGO.US Earnings / Smart Money Move — PENDING APPROVALS AND BLOCKERS (2026-11-11)

## Needs the owner's decision
1. **Push** the local branches to GitHub: `feature/sm-handoff-nov10` (kit v6.3 source + tooling + D1/D2/D4), `feature/security-hardening` (app security fixes + 15:50 close). Nothing is on GitHub yet.
2. **Kit v6.4** (publisher D1/D2/D4 + bar-flag case): BUILT and rehearsed 20/20 (`release_candidates\sm_v6_4\`, zip sha cf6a581d…, anchor 35a07fb3…, staging block + v6_4 scripts ready). Install = same steps as v6.3. No scoring, no budget change.
3. **App deploy** of `889e1332`: security hardening + tracker 15:50 close. Installer re-pin + window between Smart Money Move runs. Before it: confirm the VPS ACCESS_PASSWORD is not a placeholder (new build refuses to start otherwise); decide `LOGIN_MFA=1` and `TRUSTED_PROXIES`.
4. **Friday-holiday Monday-BMO schedule (D3)**: add a variant trigger that falls inside a Wed-close..Thu-15:30 window in such weeks (e.g. Thu 20:35 VPS when Friday is a holiday), or make the variant task daily (its gates skip when nothing applies). Code needs no change.
5. **Factor F7 options capture** (`-WithF7`, +2 requests per stock): currently OFF by scope default, never formally decided. Yes/no.
6. **Alert channel** for refusals/halts (now visible on the dashboard as BLOCKED/STALE only): email or webhook? Which address?
7. **Script self-pinning**: the `C:\smlive` scripts are not hash-pinned by anything above them. Option: a read-only manifest checked by the attestation renew task.
8. **Dashboard label fixes** (app side): STALE threshold = cadence + grace (publisher now provides `live_every_minutes`), mode of the last LIVE pass, title mojibake, ETF strip "0.00".

## Needs data or access I do not have
- Diffs of the Nov 10 edits to runner / engine20 / live (`C:\backups\sm_edits_20261110\DIFFS_vs_kit_v6_1.txt`, command already given).
- Historical accuracy rebuild (E09 / P1-07): requires the production ledger + outcomes files (or a read-only copy).
- `collector.settings.txt` content as of Nov 10 08:48 (gates say it is correct; confirm nothing else changed in it).

## Running without further approval
- Scheduled LIVE passes under kit v6.3 (150/700, every 6 h), hourly DRY_RUN, Monday-BMO variant Thu/Fri/Mon, attestation renew.
