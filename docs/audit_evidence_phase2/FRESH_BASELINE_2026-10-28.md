# FRESH-stage baseline (production VPS, read-only), 2026-10-28 ~14:20 ET
Source: `C:\audit-reports\readonly_verify\earn_fresh.json` (verifier v2, SHA-256 9b421755ebb2193907ae98206a5447e42b5f95252b99d9a98d7928e7f71973c9). 537 calendar names.
Taken while the UW warmer was throttled by the market-hours hourly cap (400/h); compare after the overnight run (off-hours cap 6,000/h).

| Cache | <6h | 6-24h | 24-48h | 48-96h | >96h | missing |
|---|---:|---:|---:|---:|---:|---:|
| equity (earn:equity) | 14 | 205 | 171 | 141 | 6 | 0 |
| trade (earn:trade:default) | 16 | 186 | 168 | 141 | 11 | 15 |
| gemini hit-rate | 529 | - | - | - | - | 8 |

Scans (age h / bytes): claude 0.01/1,252,082 | gemini 0.01/988,052 | grok 0.01/1,249,772 | chatgpt 0.01/1,247,105 | meta 0.01/1,120,373 | kimi 0.01/1,179,754 | ped 0.01/1,042,841 | **claude_pro NOT PRESENT**.
Market: v3:spy15d age 0.01 h; kimi:market age 0.16 h.
Names older than the 48 h window: equity 147 (27.4%), trade 152 (28.3%) + 15 missing.
