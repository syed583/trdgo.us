# Synthetic season results (invented data; mechanics only, no accuracy meaning)

prereg SHA-256 b25bd8d7889461561405ad8ec3c225b00a9202e9c75bf4b88e3e14ef7e31eb87

| symbol | history/trend/skew | calendar | continuation | contrarian | reasons (continuation) |
|---|---|---|---|---|---|
| S00 | up/up/call_rich | VENDOR_CONFIRMED | BUY | NO_TRADE | RULE |
| S01 | up/down/flat | VENDOR_CONFIRMED | NO_TRADE | BUY | BELOW_MARGIN |
| S02 | down/up/put_rich | VENDOR_CONFIRMED | NO_TRADE | SELL | BELOW_MARGIN |
| S03 | down/flat/call_rich | VENDOR_CONFIRMED | NO_TRADE | NO_TRADE | BELOW_MARGIN |
| S04 | mixed/up/flat | VENDOR_CONFIRMED | NO_TRADE | NO_TRADE | BELOW_MARGIN |
| S05 | mixed/flat/put_rich | VENDOR_CONFIRMED | NO_TRADE | NO_TRADE | BELOW_MARGIN |
| S06 | quiet/down/call_rich | VENDOR_CONFIRMED | NO_TRADE | BUY | BELOW_MARGIN |
| S07 | quiet/flat/flat | VENDOR_CONFIRMED | NO_TRADE | NO_TRADE | BELOW_MARGIN |
| S08 | up/down/put_rich | VENDOR_CONFIRMED | NO_TRADE | NO_TRADE | CONFLICT |
| S09 | down/up/call_rich | STALE | NO_TRADE | NO_TRADE | STALE_CALENDAR |
| S10 | down/down/flat | ESTIMATED | NO_TRADE | NO_TRADE | TIMING_CONTRADICTED |
| S11 | mixed/up/put_rich | ESTIMATED | NO_TRADE | NO_TRADE | SINGLE_VENDOR |
| S12 | mixed/flat/call_rich | ESTIMATED | NO_TRADE | NO_TRADE | SOURCE_ESTIMATION |
| S13 | quiet/up/flat | UNKNOWN | NO_TRADE | NO_TRADE | NO_OBSERVATION |
| S14 | quiet/flat/put_rich | VENDOR_CONFIRMED | NO_TRADE | NO_TRADE | BELOW_MARGIN |
| S15 | up/down/call_rich | VENDOR_CONFIRMED | NO_TRADE | BUY | BELOW_MARGIN |
| S16 | up/flat/flat | VENDOR_CONFIRMED | BUY | BUY | RULE |
| S17 | down/down/put_rich | VENDOR_CONFIRMED | SELL | NO_TRADE | RULE |
| S18 | mixed/up/call_rich | VENDOR_CONFIRMED | BUY | NO_TRADE | RULE |
| S19 | mixed/down/flat | VENDOR_CONFIRMED | NO_TRADE | NO_TRADE | BELOW_MARGIN |
| S20 | quiet/up/put_rich | VENDOR_CONFIRMED | NO_TRADE | SELL | BELOW_MARGIN |
| S21 | quiet/flat/call_rich | VENDOR_CONFIRMED | NO_TRADE | NO_TRADE | BELOW_MARGIN |
| S22 | up/up/flat | VENDOR_CONFIRMED | BUY | NO_TRADE | RULE |
| S23 | up/flat/put_rich | STALE | NO_TRADE | NO_TRADE | STALE_CALENDAR |
| S24 | down/down/call_rich | ESTIMATED | NO_TRADE | NO_TRADE | TIMING_CONTRADICTED |
| S25 | down/flat/flat | ESTIMATED | NO_TRADE | NO_TRADE | SINGLE_VENDOR |
| S26 | mixed/down/put_rich | ESTIMATED | NO_TRADE | NO_TRADE | SOURCE_ESTIMATION |
| S27 | quiet/up/call_rich | UNKNOWN | NO_TRADE | NO_TRADE | NO_OBSERVATION |

signals: {"continuation": {"BUY": 4, "NO_TRADE": 23, "SELL": 1}, "contrarian": {"NO_TRADE": 22, "BUY": 4, "SELL": 2}}

reason codes: {"BELOW_MARGIN": 24, "RULE": 11, "STALE_CALENDAR": 4, "TIMING_CONTRADICTED": 4, "SINGLE_VENDOR": 4, "SOURCE_ESTIMATION": 4, "NO_OBSERVATION": 4, "CONFLICT": 1}
