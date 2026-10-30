# Updated development replay (saved data, valid evidence only) and the separate SEC-F3 experiment

No request, no sealed data, no outcome loaded, no accuracy statement. This is a replay: the event's own date and label are taken as stored after the fact. Baseline prereg 9316348ba0d290342c7cefb551ec973b463029e1371a3db8a81a18493f8ff77e; score40 prereg b25bd8d7889461561405ad8ec3c225b00a9202e9c75bf4b88e3e14ef7e31eb87.

SEC-derived prior events (EXPERIMENTAL; a separate experiment, not the pre-registered provider source): usable by tag {"AMC_RECONCILED": 82, "AMC_UNCONFIRMED": 590, "BMO_CERTAIN": 687}; ambiguous and excluded {"CLUSTER": 196, "DURING_MARKET": 57, "PROVIDER_MISMATCH": 5}.

## A_provider_history | F7_sensitivity_on_P2_events

events 40, replayed 40

| | BUY | SELL | NO TRADE |
|---|---|---|---|
| continuation | 0 | 0 | 40 |
| contrarian | 0 | 0 | 40 |

coverage (available points of 40): {"22": 40}

missing evidence: {"F3:INSUFFICIENT_HISTORY": 40}

reason codes (per variant): {"ILLIQUID_OR_UNKNOWN": 18, "INSUFFICIENT_COVERAGE": 80, "TIMING_CONTRADICTED": 2}

## A_provider_history | valid_evidence_only

events 186, replayed 186

| | BUY | SELL | NO TRADE |
|---|---|---|---|
| continuation | 0 | 0 | 186 |
| contrarian | 0 | 0 | 186 |

coverage (available points of 40): {"2": 3, "12": 183}

missing evidence: {"F3:INSUFFICIENT_HISTORY": 186, "F5:SUSPECT_WINDOW": 3, "F7:NOT_CAPTURED": 186}

reason codes (per variant): {"ILLIQUID_OR_UNKNOWN": 88, "INSUFFICIENT_COVERAGE": 372, "SUSPECT_WINDOW": 6, "TIMING_CONTRADICTED": 4}

## B_moderate_adds_AMC_unconfirmed | F7_sensitivity_on_P2_events

events 40, replayed 40

| | BUY | SELL | NO TRADE |
|---|---|---|---|
| continuation | 5 | 3 | 32 |
| contrarian | 4 | 3 | 33 |

coverage (available points of 40): {"22": 5, "37": 35}

missing evidence: {"F3:INSUFFICIENT_HISTORY": 5}

reason codes (per variant): {"BELOW_MARGIN": 38, "CONFLICT": 1, "ILLIQUID_OR_UNKNOWN": 18, "INSUFFICIENT_COVERAGE": 10, "RULE": 15, "TIMING_CONTRADICTED": 2}

## B_moderate_adds_AMC_unconfirmed | valid_evidence_only

events 186, replayed 186

| | BUY | SELL | NO TRADE |
|---|---|---|---|
| continuation | 19 | 9 | 158 |
| contrarian | 14 | 9 | 163 |

coverage (available points of 40): {"2": 1, "12": 59, "17": 2, "27": 124}

missing evidence: {"F3:INSUFFICIENT_HISTORY": 60, "F5:SUSPECT_WINDOW": 3, "F7:NOT_CAPTURED": 186}

reason codes (per variant): {"BELOW_MARGIN": 149, "ILLIQUID_OR_UNKNOWN": 88, "INSUFFICIENT_COVERAGE": 124, "RULE": 51, "SUSPECT_WINDOW": 6, "TIMING_CONTRADICTED": 4}

## B_strict_BMO_certain_plus_AMC_reconciled | F7_sensitivity_on_P2_events

events 40, replayed 40

| | BUY | SELL | NO TRADE |
|---|---|---|---|
| continuation | 1 | 2 | 37 |
| contrarian | 2 | 1 | 37 |

coverage (available points of 40): {"22": 26, "37": 14}

missing evidence: {"F3:INSUFFICIENT_HISTORY": 26}

reason codes (per variant): {"BELOW_MARGIN": 15, "CONFLICT": 1, "ILLIQUID_OR_UNKNOWN": 18, "INSUFFICIENT_COVERAGE": 52, "RULE": 6, "TIMING_CONTRADICTED": 2}

## B_strict_BMO_certain_plus_AMC_reconciled | valid_evidence_only

events 186, replayed 186

| | BUY | SELL | NO TRADE |
|---|---|---|---|
| continuation | 8 | 9 | 169 |
| contrarian | 9 | 9 | 168 |

coverage (available points of 40): {"2": 1, "12": 118, "17": 2, "27": 65}

missing evidence: {"F3:INSUFFICIENT_HISTORY": 119, "F5:SUSPECT_WINDOW": 3, "F7:NOT_CAPTURED": 186}

reason codes (per variant): {"BELOW_MARGIN": 79, "ILLIQUID_OR_UNKNOWN": 88, "INSUFFICIENT_COVERAGE": 242, "RULE": 35, "SUSPECT_WINDOW": 6, "TIMING_CONTRADICTED": 4}

