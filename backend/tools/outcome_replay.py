"""REPLAY of the verified earnings events in outcome_sample.json through the corrected scoreboard (NO provider calls).

  * Reads outcome_sample.json (written by outcome_sample.py on this server) - read only.
  * One SELECT (READ ONLY transaction) of model_predictions: model, symbol, earnings_date, report_timing, predicted_at,
    signal, score. The legacy outcome columns are NOT selected.
  * Builds outcomes ONLY from the JSON's regular-session-verified events, runs the corrected scoreboard (earnings_scoreboard.build)
    for the eight earnings models and prints exact BUY / SELL / NEUTRAL, excluded, pending and missing-outcome counts per model,
    then cross-checks every figure against the sample file's own provisional accuracy.
  * Writes only --out JSON. No provider calls, no database writes.
Usage (backend dir, production venv): python <path>/outcome_replay.py --sample <outcome_sample.json> --out <file>.json
"""
from __future__ import annotations

import argparse
import base64
import gzip
import json
import os
import sys
import time
import types
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

_EMBEDDED = {
    "market_calendar": (
        "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"),
    "earnings_outcomes": (
        "H4sIAAAAAAAC/708a3PjNpLf9SuwTG2Z3NCKPJfkrpgoVR5bk7jOY8/Zcm6zLheHEiGLNxSpJUhrtLPz36+78SBAUh7vXvbm"
        "QywSQKPfD6AZz/NGs6QqsuJRsIonyzorC1Y29bLccBGx3ZoXLCkY13P4Ey9qtoRXC84eqyTlaQgTUrYud+PR6LrgrCp3bMsr"
        "5ov9ZlHmoVkcp0nNA5YV7L15RfBiveH7kIl1UvGULfa4VbVnmzLlOe1AzyNRJFuxLmtWrli9TmqJ0ZjN17BzkwPSoxFjf2Kv"
        "+aqsOLveAgEsamkTXAj8O4XV3KB2nCZ7M+SXFQ0W/GPNaqARZjCcQNyAEdig9w9pY5lgBaCW2KuCMXudCK423Fb8KSsbYc84"
        "EmyZl4KPCfHTVQ3MO8MXzyDewy2hZTZNhJK9+RDaDgMIC0N+nohavVpIZmZ1oHAslCIAwWWR75UmwO8lp7U9rNeJYGeX17ez"
        "c+af/Fs0mbDZHKbj/vn+WO4CKIiQkDz5Xs8AYNUuEzxkpxteZcvkmyu+i38rqw9sl9VrXJNnj+uaieQJ6AjYNgfmJqBGZQWC"
        "eEqyPFlkeVbv2aJZrXglNzi9OmcLAC7pgwUViUYQTYWSVAbErDNRl9Ve6temfCIZ+4Y8ifgxWyCT6SFg39AO7RvJsruiznIE"
        "LKEb9r2bXZ1fXP0c38xOz+YX11djdrE6zEICCNbRoJJJDKXO0Z6K3JyzPa+Ht3l3c3E2I3PK0ODrKiOxAbRFsvxwXK5WTCzX"
        "PAVLkuZVoNkxYMJW/ECbZARPvt4k1QdYD9a+SQrYCTShKWAgW2WIxhio/lCUuwJ22qI86myD+mrhM794C3+UVrGfb07PQUOU"
        "O2j3qfiuyuqaF+hiquwxK5Ic/YwgZ0IuIgbxpRlxTLwneaIlmoWMZ6hKEXsv9sUyzvljstzHyzJvNjiflHiV5bkgQ5HDBg81"
        "DV0ObUqMWSdPco8FR/BPSd6AvaXI+vHIA786WlXlhsXxqqmbiscxyzbEhKSAVQkhOhqpd3n5CEQ9yiVot8Aprhfgc2jehshF"
        "DhTXifz5t7LgcmG93xJ75bKzJCdlCMEJ4m5JbrYjudXxMsl5kSYVA83aLEcjwAI8hcJl/MjrS/jJKz+Oi2QDFASj0dvZ/Jfr"
        "8/jX2c0tKCvM9l6dHp94ozeXp/P43dkc3kzG37HBf1+xv6MF/R2EJrIU9QsEvEBdXJZNUQtEI2GrPKmN8o/ALM5/i1/fvXkz"
        "u0Evpkn3QZGamovpt98FCJn41fpAtBNkxYLnOVsph5aCdWgnV5cYwLbNIs8EqPtopFQPCJK/vFHXMnGs+66dJQ3LmkIv2nGp"
        "6PYE+Qb05PXba3wPf7zR6dsz/A1/YGCU8pWymfhDVqQ+yJPnkZHnvairh4Ad/+S+ichOQQWPAOQR+4YdAbgjRiqSMCN0AsZE"
        "s1wj44+sgCnXtHHo6Ad2BUrGshWYNxn0GBUcdxGArUSLAZc9LxgDBtnWD8Z5uQPVCaTPWJGliMiEIHA9TVUQWD3Dk1HGQ5sW"
        "BG1rPwmAnFS1QLfve4tN6QU9cECvgUbKYAMrRX0IWrJZDkADro26uEqZpFnFST/jcuWjTsfbZR2B6pZJTfIAJkQaEz3OfmLa"
        "Snpbea/vfvN6C35kx4dX3M4uLz0bP+9qdje/Ob3UiqNtKN6B6pQ730nDIuVVUK0iRJfQrpttzu9bf9Oqkt/GM7WyEwNlboem"
        "lqjkwOgIkITbsOkUBWRToiBMwfuMkZlVjHlNrPIaQHTvYh10uQDrMKNyVmiwQYtjFw2QbDQASqLQA+iiEOLMl6BZJZC3sF8h"
        "LvBZVZWVv/KU9eg4SOh8wv/+ofrsBa3c0n2c1IYQS2AkJR0JjHDu5mfoU+sEYj1Z+W6dgVXbWcSxziKktDaQ7ZkE3giq5YSa"
        "HdPsGMKgi0zAvma2Z1aY6yCoMn/Sq0767+qd47ZC8BG7qI1zbroqEzVrCQZ76fzwl+EF5mk6bgNHICmHqI3OS6ZCEFvewzbv"
        "x+y9hPgeeLGVubLkbRsjfMUlNPzAMAmAg8J+8iSNHhCpyhzPIRQGnGcYl1KHASQenhG9RsCzGxEcwj20OyUGBRbRCe2B9p0R"
        "0tACd1C/dzbTnqddZDydA4f0s30Dvl3D++yYGjp98Lg+mH2IRmf5WGDjvVz4gAHPTRCVnXhdG4VFJrmOU2XfMZj0885Oqlug"
        "xTdutvjW77B26kB1uThVO4XGOqeumcZpYIW6HbhuItDw6qFDuZI90t7NKQ6waOX1KoJPauvPVBsgs2V98DzflhiyVd0D7vrT"
        "5wBTPckNxSQJ98BETa8d2RVgmCWzbrkcHtXAj5AW4qMeweeWI5tMULY2ZegI/FTGEWD2krTH14KRwMAD+7Y4EGAQaExoV7nB"
        "wxdZTjnaIZWUToDqebuuipgH3s8DtR//T5kVvkI+OMRzKhpBQSHFTX1fM+BYEwOpluLRn9jJZDKehOxVT1Ml6lOZmWo+IHpT"
        "Sjl8wxhXddWo5lFoMosp/gi75wHG1qe9/CZA5wxmQK5w1KFSh6262sdpw30MoF1HrQzHcu+utydHvighaGhH/lrXo6gLWQEO"
        "HXKTWvlxCATrsqmwaIPRV3R0YKX/TrADLfn+2Jpd70raY8f5BxEqj68GgENFvYa3VKjjjBxq/wunDA6ZKCF5rinnSbBITfId"
        "7r/NllgNN1s780Fm4BzkXC/nmFcNtyeSfWlFDTBTkSLvLXyT5EKupFOaqbUaX4DHqflmi389Y6c08yWY4MRx/besWJX9+Wo/"
        "mgNOO0+W3Jdzp7oYHUP4lbsmj6j66A+Ppfz1Fjjwo1XNofymr6zw8JhsB8u9776TkHl+CMrJt8+AQTUQ0++/BOP7yRdhvPpW"
        "AxH88FyC9u+OxUhuEBt/muICsJ+v2PG/8h/7Cna4rcsKifUx0JIzu/2vS8g9oCx6rDj8Rl+7rcq0IcsPcTjDM01IV7moRcCe"
        "/ffV/wMR8fn5JcYk6SLOIGDOZ2x++vpyxi7esKvrOZv9+eJ2fssOHC8zX5qlp3I29uvpzdkvpzf+q0lAy6/uLi87CSs7x03a"
        "QZWh6JX/EYQGJpntAZidXIOg2m/Z+fUd0vHuZnZ2gUcrBuxANqJWuyN9CG0heRC4cfQ2QWzD63WZxuDxhD108grGlGcR7OJq"
        "Pvt5dmNoZOezN6d3l3M2MdA7rohBfju7nZ++fReqyqPzEkOMxb9Jy1sI1G9Pb35j/zn77dC9QoAFVHz37nZ2M2+V5OKKngHZ"
        "68NaMQhQizpUgu3J0H7xnLS6gmrlErb87zK9ZXTYZaPZy/BQci4A1l3ezW6ZH2mCog5FkSYp0jRFPaKiAaqiQbKiLl1RS1hk"
        "URZ1SYsMbWaDLo0ssqiLiDw99/qKnV1fvbm8OJsfvGM6v2Z378hyb2dzbbNTxj8u8wYrXle09ohijN6sa7fWzGfUYXjasyZt"
        "rXiBDh2ebjYxxm9NHVA8e/igNjowXGHq7YxfsKYeVGF7Uk/yrWuwZrXaoDckZ2HNoOf//mV2Mztk6Eq27Mef2JHMq47MoQue"
        "ccRFkj1xP607aal73qLCeFqPAXGV8fhO6tPNiyhpxqwnNWkVZg3wqDdP/Sf3GNc6fIN1JoHE2iYT8qgH4D+F6lAo0bV2b4yI"
        "6GV7TxryixeMqQ5xEhm6XsTjpkyUkD5voNLA6u0puI9OJg+GsWk9RBxu82ICnyMCFU5ua9ju/QWrs68nk2gy8XooI7Au2hpZ"
        "Xgi8qqH0yE8XhHab/tLRmvhrnuTLNd/s9T1LzT/KSi9djPlHvoQ81cd3PqYr6lQAhkAFN1nt662gMkuNZuq92pOsL+1F91DT"
        "3paGR3g8PAMH+TuEtsGY1h4xeC8Lb4fDWTeO2aDf3Fy/PWTOXhCMkzz3A/tYTp4/YTWHRSOxyT158aux5gmYXTXuJBFY+X9y"
        "CuP2oK9d2TvqGwBlH/hVJuC0h36VDjXubv3zPoLdeR10DwDtGR2Iw4eCBHVgKBg6KezO7OxgHRtWVoxxDg8rO7p4WhtowIQO"
        "kNqkA7pbzUbkUKpu1ECsjRqZSeZN0B5SVhQoPg+fYcQiwQCwCFkZkTGGbPgwo3928X9wEzJnBRxbxWuVrrzXvx8G9A5G3VcP"
        "ttrBqPr9YGte2Z6FtdweUDyY13350NU7Z44Db1jtyvvBgYchrevNdeBbSgcTzdNDR+9grH3EQTd/gRnulba1g6WkdKZlH4K2"
        "gyGb4HXISTiorG1aASoTWMAdbXVnYUQs7fNKfQwk0wb32F7rtV9KvOgxUJex9xFUUQ/4QGfzJszREVpclzF2I/EdaXxdpsne"
        "vmWyb1Vm1It1KNumTpwEzGlZVtgG1LZisGaLV+3vCfj7UM7MauE2hNAuvuw5wptk2cxADTcCT5OXeHNjGr9U9wV2HlnXMv/i"
        "gCnxjbUbf3v6Z19RKR0MO73VgU0GrV5Tih3WOmkqFQAXt20pje1J7uiPUyjeQA2u796x17+9CEvPUrdPHuoZSeGzHTUXXNTS"
        "030xeH7geyyrnw+f1jEhCpLmO0h1ugTMfMr2MY8zrl3NNDOWTQVTEGFSdECnHQODwWEriVQ9CZgY+3Qt0FT30hISvIjHx5OH"
        "wEoqNTfuAfCDaWrAvMXJID99AFN7um+NSt5VfAgxhy0kehk4AOEHn3VQecL2Ie5XFDOA1WRedDQfGYZ/oJsOry7rJFcLMIlV"
        "vyw862rvIq0QU0f95U5yxxIFVEh8WzN/vt/Ki+jQupTucGBZFnVWqKNgBXkynpgj/scmT6rYuR0WuKnouwx9o3oz+/nu8vSm"
        "cwFN5lolO3X8jiDGI1o6p9678ilLeXUk9PE8B7eCXaeCg0Q32KgCbqCgi13sN9UtPiLZcHWXe4xu6Fh2OjH/aFsd0f0IUQDP"
        "8CiPqAEetoZYM8ujAJfjCMJMtlvAA6/tIcZU6C+BTSkHg+BboS7ZJVQsAKk3stdRmwl5F8QTLDDStglJpcyipH4+foxqZaOD"
        "xG0a0V5AUOteYQim9ZG6+gCGUNcO7SU7SLWfRaZ2wCY5HcmTzBMoudzmUYM6brgrmxy7tKqq2Upg1A5CDYhj2u0GW2+p7Vfe"
        "8ieGKbjVDsD/AO9IMrs1IYb+eJlUEFaKUjekyS6uGlDLIaAVuE1lgAasAR1SPYiCrbPHNVjbsTQRhNfugFGGWvqQpd90CBcU"
        "PK6uDYZSGNgyqfoic76qzf2d3/ZR0qUQiADvekAP0Z25nZRtOFrsdbvDYd+Ksr63LobBHLCXBC8LZSyn1IhieWVuhqQn5ZZj"
        "7HkEvA8/UJyrLXSJ7pg+3jFLJyI3kynX7+FILIaMBa/BlSRNXvtpCORDNNoiM30fwC0DU8wNcQ4WkG8lT6vgKWdrnw5IqU7Z"
        "/VLyW90jq6UrFWaIRkvvTNLU7WDD/MurvAc72qhNXJqxvEwfqBmBRu+PT9pF8rIJAi9dkA/trnCNDa7BAfCb5KNPE0IMytM8"
        "2SzShH2MTKT5CPEpCO7V7r0qR/E/BuMS8YrXkDBVdstO35ffSIKMD7e6ZESbrZHzlU3Jomwq1QpuPIpqpTZ+B625vSmV6VpT"
        "NKJJ8t06AXcCcQbcLYABc212Ni0HAlGzG8doWPgDgkUKMEzK6p2kEFBzSELq6beTSRCYdqum8Kmc659PAa9lh4tikjVHN9fe"
        "q9YlqgvNgrazIfkYSwxEBHJFfT55NYHpC3Uq2E+0ZQuV+vohbbTvodZ6ClZYuIhaBR4BghyzixRKDkj8C+nCRLKi4AKkYQDg"
        "2Jec791UGU8r5Q2v5Pxf4PkCnmkG3lHKe9uyag/N4Nnv3/FiYMS8Wmdf+kwP3rf6q/uaE4obastbKb3LcpnkZiYAmjojKkls"
        "dtSYpzisX0GWbb+BbZWo+hfXemA6qP+ON2Vf/PcVtcs/UlMHqnxZZTWsk0EaWxW4DkM5VHX6Qw3HnL+s8Uids6TlgRwcZyJu"
        "8fCD7mScAv7WnoP3/D2PPqM/wPAIKSvKvyYRe305m0xOBiDa/BbNBnzYnvrhZFEJVcYEK/CGq1+qzi34Tr1QkTPWCUbnNfkP"
        "etdtTzFTzPkGrlPWpeSYqrf0FHMMTISSjCFOpOye9bYagFVSzGtpAfYpv7YR3+t+jIJnkdYJOcEnfuANxcFqGzZp5yvJ9s6F"
        "zQTF7HvNafQ2OS98+RjYkVY7c3Qt99SEmYPLeGijqY6o6B6hikyDUFVs2B8NaslTBVbH104s2totJwaIq4DY0yI91tBZRtT7"
        "GqmXMlDVSR27/Sb43lY0sWf0JnaSHNruVEQ3VM4dhCx/Br1lRIJVVdqU/GHa6duMhr6vso8UQ7al3fr7GNF2VPyBfQ0B42Wc"
        "ekFPZGo3Qpp8Kd3LuW43Y5e/sqkRJkdfxt6YtsTf+Ez6BgByAJ5G5CSbWn58hKFsle/p+H+NWTXGLmwnw6T8ZdSr7j+rCS1E"
        "ZCW/X4CydDv/EL+1mdkpLamWndRqnoPsgwFTRj+JZgkvfLTlJ5kGPqkMV22AisvRCB3bhUHLXM1syO2trOPBpb1XK7St1Zj+"
        "t7ngMzra9bgDTOvGFYxn8O758OKgYkLMIAqOez8gtLx8HO9k7umbQ3LTGk4Q2AryWFBB5OYfIUH7I57mkghrKHF8wDgYt98+"
        "2cBxhSVaR1z38OfhpS5IAiAXJGkfdEKKLQfdG8G3e1edjxcdYr3RP+WhDh1JD+NiBGVF/wNicvvn/jkbtW9Xn3unqgcFePSP"
        "ZUAAsCrzHHMpGyR+5CGzavwoMbfMC7+WkhmT+iy4L7tBcxxO3FTuFzxrZ182sG0ihI2hk6VrvqG2mYvqge8lSVmer5mosoGC"
        "xxQ2b7I8l58VH/y2sndkbn1tCZU4V+m1+dQyNKUnbaK+QVblkgQ/ZjcIQpecoL+QmqfM198rU796YD4XrcsGXdqLLxV+30rq"
        "d0g8TU/blE6qn5zD6V5qqZM7asEYsu7Pdo+/+b8CdO1pQi/QSl9ywXJ+cTu/uDp003Lg8kTfmGjR4/0AXpjc6W/g5L1K/97k"
        "BbcqHt68tvcjIADnigSr24l7eIekWl0FMMG0GFE+/PwdiXNtQYz9wtFZxZ+9uSLSVbdbn3HY/wbZGJSYsdURFm2eQv3abgU7"
        "O72d+1Fa4XWWafucBEHohg2PzoI5gYIlMxTPFRPZI36X3UJ51QXD5jjvhM0uYc2EQfocujKdYq/frruZFL7q2YUpYkCU8DqV"
        "r4d0JHCryU/e5mno3rgaujAGOoZeI54RRW7XyXrCbRVJ6dFB9nOrAgVGMxAwxJYdfQVNTRCjoQim+8dH/wtSB+CCtUMAAA=="),
    "earnings_scoreboard": (
        "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"
        "TJ3kRpknvSyTOWzsikzvhM9QbONfRS1cCDI7AAA="),
}

JUMP_PCT = 35.0                 # day-over-day close change that is flagged (possible split or bad bar)
THIN_VOLUME = 0.25              # regular-row volume under this share of the neighbouring median is flagged
MODELS = {"claude_new": "Claude", "claude_pro": "Claude Pro", "gemini": "Gemini", "grok": "Grok",
          "chatgpt": "ChatGPT", "meta": "Meta", "kimi": "Kimi", "ped": "Pre-Earnings Direction"}
SQL_SNAPS = ("SELECT model, symbol, earnings_date, report_timing, snapshot_date, predicted_at, evaluated_at, "
             "actual_move_pct, signal, score FROM model_predictions WHERE earnings_date IS NOT NULL")


def load_embedded():
    for name in ("market_calendar", "earnings_outcomes", "earnings_scoreboard"):
        src = gzip.decompress(base64.b64decode(_EMBEDDED[name])).decode("utf-8")
        mod = types.ModuleType(name)
        mod.__file__ = f"<embedded {name}>"
        sys.modules[name] = mod
        exec(compile(src, mod.__file__, "exec"), mod.__dict__)
    eo, sb = sys.modules["earnings_outcomes"], sys.modules["earnings_scoreboard"]

    def _blocked(name):
        def f(*a, **k):
            raise RuntimeError(f"{name} is disabled in the replay tool")
        return f
    for fn in ("run", "ensure_table", "load_outcomes", "_save", "sync_legacy_columns", "events_to_review",
               "default_bars_fetcher"):
        setattr(eo, fn, _blocked("earnings_outcomes." + fn))
    for fn in ("scoreboard", "load_rows"):
        setattr(sb, fn, _blocked("earnings_scoreboard." + fn))
    return sys.modules["market_calendar"], eo, sb


def _run_ro(sql):
    from sqlalchemy import text
    from database import SessionLocal
    db = SessionLocal()
    try:
        db.execute(text("SET TRANSACTION READ ONLY"))
        return [dict(r._mapping) for r in db.execute(text(sql))]
    finally:
        db.rollback()
        db.close()


def _d(v):
    if v is None or (isinstance(v, date) and not isinstance(v, datetime)):
        return v
    return v.date() if isinstance(v, datetime) else date.fromisoformat(str(v)[:10])


def _utc(v):
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return datetime.fromtimestamp(float(v), tz=timezone.utc)
    if isinstance(v, str):
        v = datetime.fromisoformat(v.replace("Z", "+00:00"))
    return v if v.tzinfo else v.replace(tzinfo=timezone.utc)



# ---------------------------------------------------------------------------------------------------------- replay
SQL_SNAPS_NO_OUTCOME = ("SELECT model, symbol, earnings_date, report_timing, predicted_at, signal, score "
                        "FROM model_predictions WHERE earnings_date IS NOT NULL")


def _fmt(st):
    return f"{st['correct']}/{st['graded']}" + (f" = {st['accuracy']}% CI{st['ci95']}" if st["graded"] else "")


def replay(sample, snaps, now):
    mc, eo, sb = load_embedded()
    outcomes, not_verified, ungraded = {}, [], []
    for r in sample["events"]:
        key = (r["symbol"], date.fromisoformat(r["earnings_date"]))
        if r["status"] == eo.GRADED and r.get("verified"):
            outcomes[key] = {"status": eo.GRADED, "move_pct": r["verified_move"], "direction": eo.direction_of(r["verified_move"]), "timing": r["kind"]}
        elif r["status"] == eo.GRADED:
            not_verified.append(key)
        else:
            ungraded.append(key)
            outcomes[key] = {"status": r["status"], "move_pct": None, "direction": None, "timing": r["kind"]}
    keys = {(r["symbol"], date.fromisoformat(r["earnings_date"])) for r in sample["events"]}
    rows_all = [{"model": r["model"], "symbol": r["symbol"], "earnings_date": _d(r["earnings_date"]), "report_timing": r["report_timing"],
                 "predicted_at": _utc(r["predicted_at"]), "signal": r["signal"], "score": r["score"]}
                for r in snaps if r["model"] in MODELS]
    rows_sample = [r for r in rows_all if (r["symbol"], r["earnings_date"]) in keys]
    res_sample = sb.build(rows_sample, outcomes, MODELS, now=now)
    res_all = sb.build(rows_all, outcomes, MODELS, now=now)
    return outcomes, not_verified, ungraded, rows_sample, res_sample, res_all


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    if os.path.exists(os.path.join(os.getcwd(), "database.py")) and os.getcwd() not in sys.path:
        sys.path.insert(0, os.getcwd())
    import database  # noqa: F401  (loads backend/.env)
    if (os.environ.get("APP_ENV") or "").strip().lower() != "production":
        sys.exit("APP_ENV is not 'production': refusing.")
    import hashlib
    raw = Path(a.sample).read_bytes()
    sample = json.loads(raw.decode("utf-8"))
    now = datetime.now(timezone.utc)
    snaps = _run_ro(SQL_SNAPS_NO_OUTCOME)
    outcomes, not_verified, ungraded, rows_sample, res_s, res_a = replay(sample, snaps, now)
    print(f"sample file SHA-256 {hashlib.sha256(raw).hexdigest()} | generated_at {sample.get('generated_at')} | "
          f"provider requests in that run: {sample.get('provider_requests_made')}")
    print(f"sample events: {len(sample['events'])} | verified GRADED outcomes loaded: "
          f"{sum(1 for o in outcomes.values() if o['status'] == 'GRADED')} | graded but data-flagged (excluded): {len(not_verified)} | "
          f"not graded (pending): {len(ungraded)} {[f'{s} {d}' for s, d in ungraded]}")
    print(f"snapshot rows read: {len(snaps)} (scored models: {sum(1 for r in snaps if r['model'] in MODELS)}; in the sample events: {len(rows_sample)})")
    labels, fb_dates, nsym = {}, 0, 0
    for v in sample.get("symbols", {}).values():
        if "shape" not in v:
            continue
        nsym += 1
        fb_dates += len(v.get("fallback_dates") or [])
        for k, n in v["shape"]["market_time_labels"].items():
            labels[k] = labels.get(k, 0) + n
    print(f"candle rows by market_time across {nsym} symbols: {labels} | dates that needed the unlabelled fallback: {fb_dates}")
    print("\nA) CORRECTED SCOREBOARD REPLAY - the sample events only (snapshot rows restricted to those events)")
    claimed = sample.get("provisional_accuracy_sample", {})
    mismatch, table = [], {}
    for m in res_s["models"]:
        ex = m["excluded"]
        print(f"  {m['label']:22s} events {m['events_total']:3d} eligible {m['events_eligible']:3d} graded {m['events_graded']:3d} "
              f"pending {m['events_pending']} missing-outcome {m['events_missing_outcome']} | excluded post-release "
              f"{ex['post_release_only']} ambiguous {ex['ambiguous_only']} retrospective {ex['retrospective_only']}")
        print(f"       BUY {_fmt(m['buy'])} | SELL {_fmt(m['sell'])} | NEUTRAL calls {m['neutral']['graded']} "
              f"(flat hits {m['neutral']['flat_hits']}) | rows by class {m['rows_by_class']}")
        table[m["model"]] = {"label": m["label"], "events_total": m["events_total"], "eligible": m["events_eligible"],
                             "graded": m["events_graded"], "pending": m["events_pending"], "missing_outcome": m["events_missing_outcome"],
                             "excluded": ex, "buy": [m["buy"]["graded"], m["buy"]["correct"], m["buy"]["ci95"]],
                             "sell": [m["sell"]["graded"], m["sell"]["correct"], m["sell"]["ci95"]],
                             "neutral_calls": m["neutral"]["graded"], "neutral_flat_hits": m["neutral"]["flat_hits"],
                             "rows_by_class": m["rows_by_class"]}
    print("\nB) CROSS-CHECK against the sample tool's own provisional accuracy (all snapshots of the scored models)")
    for m in res_a["models"]:
        c = claimed.get(m["model"])
        mine = [m["buy"]["graded"], m["buy"]["correct"], m["sell"]["graded"], m["sell"]["correct"], m["neutral"]["graded"], m["events_graded"]]
        if c is None:
            mismatch.append(m["model"])
            print(f"  {m['label']:22s} no entry in the sample file")
            continue
        theirs = [c["BUY"][0], c["BUY"][1], c["SELL"][0], c["SELL"][1], c["NEUTRAL_calls"], c["graded"]]
        ok = mine == theirs
        if not ok:
            mismatch.append(m["model"])
        print(f"  {m['label']:22s} replay {mine} | sample file {theirs} | {'IDENTICAL' if ok else 'DIFFERENT'}")
    print("\nCROSS-CHECK RESULT:", "all eight models identical" if not mismatch else f"DIFFERENT for {mismatch}")
    have = {(r["symbol"], r["earnings_date"]) for r in rows_sample}
    no_snap = sorted(f"{s} {d}" for s, d in outcomes if (s, d) not in have)
    print("sample events with no snapshot of any scored model:", no_snap or "none")
    Path(a.out).write_text(json.dumps({"generated_at": now.isoformat(timespec="seconds"), "sample_sha256": hashlib.sha256(raw).hexdigest(),
                                       "per_model_sample_events": table, "cross_check_mismatch": mismatch}, indent=1, default=str),
                           encoding="utf-8")
    print("wrote", a.out)


if __name__ == "__main__":
    main()
