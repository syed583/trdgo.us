# Reproducing the H1b data (raw candles are not in Git)

The 62 raw daily-candle files (18 MB) are kept out of Git. `candles_raw_index.json` lists each file's size and SHA-256. To regenerate them:

1. Developer machine with `backend/.env` holding `UNUSUAL_WHALES_API_KEY` (never committed, never printed by the scripts).
2. Verify the frozen manifest: `sha256sum h1b_manifest.json` must equal the line in `h1b_manifest.json.sha256` (`1da8d6b319710baf66e8f896e3d84179e8eaa677134dfc2e3bce0443f2cafd5f`).
3. Dry run (sends nothing): `python docs/smart_move/scripts/h1b.py run --manifest docs/smart_move/h1b/h1b_manifest.json --manifest-sha256 <hash>`.
4. Execution (62 Unusual Whales + 48 SEC requests, sequential, 2 s apart): add `--execute --out-dir <new empty dir>`. The runner never overwrites, stops on any error and refuses a manifest whose hash differs. It needs the application's SEC cache (`backend/.sec_cache`) for the two cached filings and the ticker map recorded in the manifest.
5. Compare `<out>/candles_raw/*.json` hashes with `candles_raw_index.json`. Provider data can change after the fact (late corrections, adjustments); a mismatch must be reported, not silently accepted, and the dates in the manifest (`end_date=2026-10-29`, `timeframe=3Y`) must not be altered.
6. Analysis: `h1b_analyze.py` and `h1b_baseline_feasibility.py` (offline, no request) regenerate every table in `h1b/` and `h1b/baseline/`.
Each repetition spends the same provider requests again and needs its own approval.
