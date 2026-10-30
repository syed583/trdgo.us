# Offline price-only baseline (research only; development-period pipeline check done once)

Files: `prereg_baseline_v1.json` (+ `.sha256`, frozen BEFORE the code was written), `smart_baseline.py`, `run_dev.py`, `test_smart_baseline.py`, `results/` (the one development run), `DEV_RUN1_RESULTS.md`.

Reproduce (PowerShell or bash, from this folder; no provider request, no database):
1. Verify the pre-registration: `sha256sum -c prereg_baseline_v1.json.sha256` (expected `9316348ba0d290342c7cefb551ec973b463029e1371a3db8a81a18493f8ff77e`).
2. Candle files: the 62 raw files are not in Git. Put them in a folder whose files match `../h1b/candles_raw_index.json` (regeneration procedure and its request cost: `../h1b/REPRODUCE.md`; needs a new approval).
3. Tests: `SMV_CANDLES=<candles folder> python -m pytest test_smart_baseline.py -q` (14 tests run without the data, 15 with it).
4. The development run (refuses to run twice, refuses on any hash mismatch): `python run_dev.py --candles-dir <candles folder> --out-dir <new empty folder>`.
Validation and test are sealed: `load_events()` raises for any split other than `dev`, drops sealed rows before their outcome columns are read, and `load_series()` never reads a price after 2025-10-01. Unsealing is not implemented; it needs a written approval and a new pre-registration hash.
