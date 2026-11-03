# Smart Money Move: reliable automatic scheduling (plan; nothing here is active)

## What the November 3 case showed
* The frozen engine allows a prediction only while an event is **48 to 24 hours before its report** (the engine reports each event as OPEN or CLOSED and gives the closing time). Once an event is inside its last 24 hours nothing can be predicted for it, ever.
* The Engine20 run was authorised and executed on 2026-11-03 08:29Z. By then every event dated Nov 3 had already left its window, so all 295 were CLOSED. Only events dated Nov 4 were OPEN, and the run's cap let it collect evidence for and score **20 of 524** of them.
* So two separate things limit coverage: **timing** (a run must happen while the window is open) and **cap/selection** (the run is limited by the governor: 50 requests a run, at most 100 a day, top names by liquidity).

## What is built now (local, in the next monitor update; not deployed)
* **Window watch:** when the live engine is installed, the monitor reads the engine's own verdicts (never re-implementing the rule) and records, per read: events inside the window, events inside it **without a result**, the first closing time, and the missing count by date. The Smart Money Move view shows it, and one alert (rule `SMART_MOVE_WINDOW`) fires per closing time when a window closes within 6 hours (`sm_window_alert_h`) while events in it still have no result. With only a stored snapshot the windows cannot be read, and the view says so instead of guessing.
* The monitor still never starts a collection.

## What a reliable schedule needs (decisions for the owner; each is a separate approval)
1. **Where the engine and the evidence store live.** Today collection runs on the development machine and the result reaches the server only by a manual private copy of a snapshot, so it cannot be automatic. A fully automatic system needs Stage C (engine on the server) **and** the collector running on the server's own evidence store.
2. **A scheduled collector (Windows Task Scheduler, a separate task, not the monitor).** It runs the existing Engine20 collection profile under the governor and your signed attestation. Suggested cadence: twice on each trading day, around 07:00 ET (events whose window opens, i.e. those reporting 24 to 48 hours later, mainly before-open names) and 15:00 ET (after-close names), each run limited to 50 requests. The engine's own plan (`SMART_MOVE_PLAN`, hourly, records what is missing) is what the task should collect, in the engine's priority order.
3. **Coordination with the provider limit.** Server and collector must share the three-request rule: Stage B machine slots if they run on the same machine; the lease broker if they run on different machines.
4. **Caps.** At 50 requests a run and 100 a day only about 20 to 26 events per day can be scored out of roughly 500 reporting. To score more names the cap must be raised deliberately (a measured cost per event of about 2 to 3 requests means 100 requests cover roughly 30 to 40 events). This is a coverage decision, not a technical one.
5. **Attestation renewal and the kill switch** stay manual and are never bypassed.

## How a missed window will be noticed
The `SMART_MOVE_WINDOW` alert (and the red line in the Smart Money Move view) names the dates and counts of events still without a result and the time the first window closes. After a window closes the view shows `NO PREDICTION AVAILABLE` with the reason, and the history is never rewritten.

## Measured coverage under the current limits (the frozen engine's own planner on the real Engine20 store, as of the 2026-11-03 08:29Z run)
* Events dated Nov 4 and OPEN at that moment: **524**. The engine's own selection (liquidity and size rules) considered **363** of them eligible for scoring; **20** already had fresh evidence (the 20 scored).
* Requests the engine's plan asks for to give **all** remaining eligible stocks a full set of evidence: **690** (about 2 per stock; the evidence kinds are the 3-year bars, earnings history, volatility and IV context).
* By budget, how many eligible stocks get fully funded (in addition to the 20 already fresh): 50 requests -> about 25 stocks; 100 requests -> about 50; 300 requests -> about 150; 690 requests -> all 343.
* Under the current limits (50 per run, at most 100 per day) at most **about 50 of roughly 360 eligible events a day (14%)** can be scored, and that assumes the runs happen while those events are inside their window. The 20 of 524 seen on Nov 4 is the single-run result.
* The provider imposes none of these limits: they are the governor's own, set by the owner. Covering all eligible events needs about 700 requests for a day's set, which is a budget decision (and, at three concurrent requests, about 10 to 20 minutes of collection time, not a throughput problem).
