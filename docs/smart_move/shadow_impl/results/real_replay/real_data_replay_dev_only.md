# Replay of the frozen 40-point engines on saved development-period data

Saved data only; no request; sealed validation/test untouched; no outcomes loaded; no accuracy statement. Baseline prereg 9316348ba0d290342c7cefb551ec973b463029e1371a3db8a81a18493f8ff77e; score40 prereg b25bd8d7889461561405ad8ec3c225b00a9202e9c75bf4b88e3e14ef7e31eb87.

**This is a replay, not a shadow prediction**: the calendar status of the time (date and BMO/AMC as the vendors showed them 48 h before) was never saved, so the event date and label are taken as stored after the fact.

## A. Prior events from the saved provider history (as the frozen design assumes)

events in dev scope 186; replayed 186

| | BUY | SELL | NO TRADE |
|---|---|---|---|
| continuation | 0 | 0 | 186 |
| contrarian | 0 | 0 | 186 |

coverage (available points of 40): {"2": 3, "12": 183}

missing evidence: {"F3:INSUFFICIENT_HISTORY": 186, "F5:SUSPECT_WINDOW": 3, "F7:NOT_CAPTURED": 186}

reason codes (per variant): {"ILLIQUID_OR_UNKNOWN": 88, "INSUFFICIENT_COVERAGE": 372, "SUSPECT_WINDOW": 6, "TIMING_CONTRADICTED": 4}

hindsight flags: {"LABEL_CONTRADICTED_BY_FILING_AFTER_THE_FACT": 2}

pair (continuation, contrarian): {"NO_TRADE/NO_TRADE": 186}

## B. Prior events derived from saved SEC item 2.02 filings (EXPLORATORY source; needs your approval before it counts)

events in dev scope 186; replayed 186

| | BUY | SELL | NO TRADE |
|---|---|---|---|
| continuation | 19 | 11 | 156 |
| contrarian | 17 | 10 | 159 |

coverage (available points of 40): {"12": 41, "17": 1, "27": 144}

missing evidence: {"F3:INSUFFICIENT_HISTORY": 41, "F5:SUSPECT_WINDOW": 1, "F7:NOT_CAPTURED": 186}

reason codes (per variant): {"BELOW_MARGIN": 171, "ILLIQUID_OR_UNKNOWN": 84, "INSUFFICIENT_COVERAGE": 84, "RULE": 57, "SUSPECT_WINDOW": 2, "TIMING_CONTRADICTED": 4}

hindsight flags: {"LABEL_CONTRADICTED_BY_FILING_AFTER_THE_FACT": 2}

pair (continuation, contrarian): {"BUY/BUY": 3, "BUY/NO_TRADE": 16, "NO_TRADE/BUY": 14, "NO_TRADE/NO_TRADE": 138, "NO_TRADE/SELL": 4, "SELL/NO_TRADE": 5, "SELL/SELL": 6}

## Calendar row formats and vendor flags (labels/flags only)

```
{
 "dev_window_uw_rows(source,label,sec_window)": {
  "company|postmarket|": 1,
  "company|postmarket|AMC": 90,
  "company|postmarket|DURING_MARKET": 3,
  "company|postmarket|OTHER_HOURS": 1,
  "company|premarket|": 4,
  "company|premarket|AMC": 3,
  "company|premarket|BMO": 89,
  "company|premarket|DURING_MARKET": 1,
  "company|premarket|OTHER_HOURS": 2,
  "company|unknown|": 1,
  "company|unknown|AMC": 1
 },
 "upcoming_uw_rows(source,label)": {
  "company|premarket": 2,
  "estimation|unknown": 34
 }
}
```

Unusual Whales label vs SEC filing window, dev window: {"agree": 179, "disagree": 3, "not_comparable": 14}

## Evidence classes

| Evidence | Class | Used by the replay |
|---|---|---|
| daily candles up to the last completed session | PIT_BY_DATE (retrieved 2026-10-30; adjustment of old closes not documented) | F3 reactions, F5, F8 |
| prior event dates and sides, provider history | HINDSIGHT-LABELLED (stored later; history starts 2024-10, older rows were dropped when saved) | A: too short for F3 |
| prior event dates and sides, SEC 2.02 acceptance times | PIT (immutable filings); AMC only 'not contradicted' | B: F3 |
| the event's own date and BMO/AMC label | HINDSIGHT (no 48 h calendar snapshot saved) | cutoff and window |
| UW source flag on past rows | HINDSIGHT (retrieved after the event) | not used |
| volatility context (F7) | MISSING_NOT_CAPTURED for the 50 symbols | none |
| sector mapping per symbol | MISSING (not saved) | F8 sector part unavailable |
| provider move fields, outcomes | never loaded | none |

