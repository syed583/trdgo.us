"""
Form 13F institutional holdings: ingest, quarter comparison and scoring.

Shape of the problem
--------------------
13F is filed by the manager, not the company, so no per-ticker feed exists.
The only way to answer "who bought AAPL" is to take the SEC's quarterly bulk
dataset, invert it, and keep the result. That is done once per quarter here
and then queried from Postgres, so a search costs one indexed lookup rather
than a download.

What gets dropped, and why it matters
-------------------------------------
A raw INFOTABLE is not a list of share positions:

  * ~140,000 rows a quarter carry PUTCALL, so they are option exposure. Adding
    them to a share count produces a number that is not a share count.
  * ~22,000 rows are SSHPRNAMTTYPE 'PRN' -- bond principal, not shares.
  * ~2,000 submissions are 13F-NT notices, which report no holdings at all.
  * Amendments restate or extend an earlier filing, so ingesting both copies
    doubles that manager's stake.

Each of those is handled during ingest rather than left for the caller.

Why the score uses shares and not dollars
-----------------------------------------
VALUE is reported inconsistently. In one quarter, for one CUSIP, filers
implied Apple prices of $253.79, $271.86, $54.61 and $56.32 -- some are still
reporting thousands. Share counts have no such ambiguity, and every metric
here is share-based for that reason. Value is stored and displayed, never
scored.
"""

from __future__ import annotations

import csv
import io
import json
import re
import urllib.error
import urllib.request
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from sqlalchemy import func

import sec_service as sec
from database import SessionLocal, engine
from models_institutional import (
    InstitutionalActivity, InstitutionalFund, InstitutionalHolding,
    InstitutionalIngest, InstitutionalSecurity, create_all,
)

INDEX_URL = "https://www.sec.gov/data-research/sec-markets-data/form-13f-data-sets"
BASE = "https://www.sec.gov"

_DOWNLOADS = Path(__file__).resolve().parent / ".cache" / "form13f"

_MONTHS = {m: i + 1 for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun",
     "jul", "aug", "sep", "oct", "nov", "dec"])}

# Scoring thresholds. A quarter-old aggregate should move the needle, not
# decide the trade, so the band is deliberately narrow and the neutral zone
# wide -- most quarters genuinely are neutral.
STRONG_PCT = 5.0
MODERATE_PCT = 1.5
STRONG_RATIO = 1.6
MODERATE_RATIO = 1.2
MIN_FUNDS = 8

# How many securities to keep per-fund detail for.
#
# A quarter holds 2.3 million positions across 22,627 securities, and two
# quarters of all of it is several million rows -- more than this database
# should carry for a signal worth five points, and hours to write over a
# remote connection. The distribution is steeply skewed: the 500 most widely
# held securities account for roughly 40% of all positions and include every
# liquid US name anyone is likely to search. The long tail is micro-caps and
# closed-end funds.
#
# Raise it if the coverage gap bites; the cost is close to linear.
UNIVERSE_SIZE = 600

# Rows per bulk insert. The original implementation issued a SELECT and an
# INSERT per position, which is two million round trips to a remote database
# for one quarter -- hours of work for something that takes minutes in bulk.
BATCH = 5000

# Said plainly wherever this number is shown. A reader cannot tell the age of
# a 13F figure from the figure itself, and six weeks is the best case.
STALENESS_NOTE = (
    "13F is filed up to 45 days after quarter end. This is a slow "
    "confirmation signal, not a trading trigger.")


# ---------------------------------------------------------------------------
# dataset discovery
# ---------------------------------------------------------------------------


def _fetch(url: str, timeout: int = 300) -> Optional[bytes]:
    headers = {k: v for k, v in sec.SEC_HEADERS.items() if k.lower() != "host"}
    try:
        with urllib.request.urlopen(
                urllib.request.Request(url, headers=headers),
                timeout=timeout) as response:
            data = response.read()
            if response.headers.get("Content-Encoding") == "gzip":
                import gzip
                data = gzip.decompress(data)
            return data
    except (urllib.error.HTTPError, urllib.error.URLError,
            TimeoutError, OSError):
        return None


def _dataset_end(name: str) -> tuple:
    """Sort key from the filing window's end date, e.g. 31may2026."""
    match = re.search(r"-(\d{2})([a-z]{3})(\d{4})_form13f", name, re.I)
    if not match:
        return (0, 0, 0)
    return (int(match.group(3)), _MONTHS.get(match.group(2).lower(), 0),
            int(match.group(1)))


def list_datasets() -> list[str]:
    """Every quarterly dataset the SEC publishes, oldest first."""
    body = _fetch(INDEX_URL, timeout=60)
    if not body:
        return []
    links = set(re.findall(
        r'href="([^"]*form-13f-data-sets/[^"]*\.zip)"',
        body.decode("utf-8", "ignore"), re.I))
    return sorted(links, key=_dataset_end)


def download_dataset(link: str) -> Optional[Path]:
    """Fetch one dataset to disk, reusing it if already present."""
    _DOWNLOADS.mkdir(parents=True, exist_ok=True)
    name = link.rsplit("/", 1)[-1]
    target = _DOWNLOADS / name
    if target.exists() and target.stat().st_size > 1_000_000:
        return target

    body = _fetch(BASE + link if link.startswith("/") else link)
    if not body:
        return None
    # Written via a temporary name so an interrupted download is never
    # mistaken for a complete one on the next run.
    staging = target.with_suffix(".part")
    staging.write_bytes(body)
    staging.replace(target)
    return target


# ---------------------------------------------------------------------------
# parsing
# ---------------------------------------------------------------------------


def _quarter(period: str) -> Optional[str]:
    """'31-MAR-2026' -> '2026-Q1'."""
    if not period:
        return None
    try:
        moment = datetime.strptime(period.strip(), "%d-%b-%Y")
    except ValueError:
        return None
    return f"{moment.year}-Q{(moment.month - 1) // 3 + 1}"


def _date(value: str):
    try:
        return datetime.strptime(value.strip(), "%d-%b-%Y").date()
    except (ValueError, AttributeError):
        return None


def _rows(archive: zipfile.ZipFile, name: str):
    with archive.open(name) as handle:
        stream = io.TextIOWrapper(handle, encoding="utf-8", errors="ignore")
        yield from csv.DictReader(stream, delimiter="\t")


def _num(value) -> Optional[float]:
    try:
        return float(str(value).replace(",", "")) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def parse_dataset(path: Path, quarter: Optional[str] = None) -> dict:
    """
    Read one dataset into per-fund holdings for a single reported quarter.

    A dataset is a *filing window*, not a quarter: the March-May 2026 file
    holds ten thousand reports for Q1 2026 plus stragglers for five earlier
    quarters. Without filtering on the reported period, those stragglers would
    be compared against the wrong baseline.
    """
    archive = zipfile.ZipFile(path)

    # Which submissions belong to the quarter we want, and are real holdings
    # reports rather than "I hold nothing" notices.
    submissions: dict[str, dict] = {}
    periods: dict[str, int] = {}
    for row in _rows(archive, "SUBMISSION.tsv"):
        period = _quarter(row.get("PERIODOFREPORT", ""))
        if period:
            periods[period] = periods.get(period, 0) + 1
        kind = (row.get("SUBMISSIONTYPE") or "").upper()
        if not kind.startswith("13F-HR"):
            continue                      # 13F-NT reports no holdings
        submissions[row["ACCESSION_NUMBER"]] = {
            "cik": (row.get("CIK") or "").strip(),
            "quarter": period,
            "filed": _date(row.get("FILING_DATE", "")),
            "amendment": kind.endswith("/A"),
        }

    target = quarter or (max(periods, key=periods.get) if periods else None)
    if not target:
        return {"status": "NO_DATA", "quarter": None}

    wanted = {a: s for a, s in submissions.items() if s["quarter"] == target}

    names: dict[str, str] = {}
    amend_type: dict[str, str] = {}
    for row in _rows(archive, "COVERPAGE.tsv"):
        acc = row["ACCESSION_NUMBER"]
        if acc in wanted:
            names[acc] = (row.get("FILINGMANAGER_NAME") or "").strip()
            amend_type[acc] = (row.get("AMENDMENTTYPE") or "").strip().upper()

    funds: dict[str, dict] = {}
    skipped_options = skipped_non_share = 0

    for row in _rows(archive, "INFOTABLE.tsv"):
        acc = row.get("ACCESSION_NUMBER")
        meta = wanted.get(acc)
        if not meta:
            continue

        if (row.get("PUTCALL") or "").strip():
            skipped_options += 1          # option exposure, not a share position
            continue
        if (row.get("SSHPRNAMTTYPE") or "").strip().upper() != "SH":
            skipped_non_share += 1        # bond principal
            continue

        shares = _num(row.get("SSHPRNAMT"))
        if shares is None:
            continue

        cusip = (row.get("CUSIP") or "").strip().upper()
        if not cusip:
            continue

        cik = meta["cik"]
        fund = funds.setdefault(cik, {
            "cik": cik,
            "name": names.get(acc, ""),
            "filed": meta["filed"],
            "positions": {},
            # A restatement replaces everything the manager said before, so
            # its rows must not be added to the original's.
            "restated": False,
        })
        if meta["amendment"] and amend_type.get(acc) == "RESTATEMENT":
            if not fund["restated"]:
                fund["positions"] = {}
                fund["restated"] = True
        elif fund["restated"]:
            continue                      # original superseded by a restatement

        slot = fund["positions"].setdefault(
            cusip, {"cusip": cusip, "issuer": (row.get("NAMEOFISSUER") or "").strip(),
                    "shares": 0.0, "value": 0.0})
        slot["shares"] += shares
        slot["value"] += _num(row.get("VALUE")) or 0.0

    return {
        "status": "OK",
        "quarter": target,
        "funds": funds,
        "submissions": len(wanted),
        "holdings": sum(len(f["positions"]) for f in funds.values()),
        "skipped_options": skipped_options,
        "skipped_non_share": skipped_non_share,
        "periods_present": periods,
    }


# ---------------------------------------------------------------------------
# cusip <-> ticker
# ---------------------------------------------------------------------------


def _normalise(name: str) -> str:
    """Company names for matching: no punctuation, no corporate suffixes."""
    text = re.sub(r"[^A-Z0-9 ]", " ", (name or "").upper())
    text = re.sub(
        r"\b(INC|CORP|CORPORATION|CO|COMPANY|LTD|PLC|LLC|LP|HOLDINGS|HLDGS|"
        r"GROUP|GRP|THE|CLASS|CL|COM|NEW|SA|NV|AG)\b", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def refresh_securities(session) -> int:
    """
    Rebuild the CUSIP lookup from stored holdings.

    Run after ingest. It is one grouped scan rather than one per search, which
    is the whole point -- the same aggregate inline was costing twenty seconds
    on every ticker lookup.
    """
    # The name is taken by popularity, not alphabetically. Some filers write
    # "N/A" in the issuer field, and MAX() picks that over "APPLE INC" purely
    # because N sorts after A -- which left the biggest companies in the
    # dataset unresolvable.
    pairs = (
        session.query(
            InstitutionalHolding.cusip,
            InstitutionalHolding.issuer_name,
            func.count(InstitutionalHolding.id).label("n"),
        )
        .group_by(InstitutionalHolding.cusip, InstitutionalHolding.issuer_name)
        .all()
    )

    best: dict[str, tuple[str, int]] = {}
    totals: dict[str, int] = {}
    for cusip, issuer, count in pairs:
        totals[cusip] = totals.get(cusip, 0) + count
        clean = (issuer or "").strip()
        # Placeholders are never the real name of anything.
        if not clean or clean.upper() in ("N/A", "NA", "NONE", "UNKNOWN", "-"):
            continue
        if count > best.get(cusip, ("", 0))[1]:
            best[cusip] = (clean, count)

    rows = [(cusip, best.get(cusip, ("", 0))[0], totals[cusip])
            for cusip in totals]

    existing = {r.cusip: r for r in session.query(InstitutionalSecurity).all()}
    added = 0
    for cusip, issuer, holders in rows:
        record = existing.get(cusip)
        if record is None:
            record = InstitutionalSecurity(cusip=cusip)
            session.add(record)
            added += 1
        record.issuer_name = (issuer or "")[:255]
        record.match_name = _normalise(issuer)[:255]
        record.holders = holders
    session.commit()
    return len(rows)


def _from_unusual_whales(ticker: str, top: int):
    """
    The largest holders from the paid feed, or None if it cannot answer.

    A fallback rather than the lead: this returns the top holders per ticker,
    where the stored 13F datasets count every filer. Complete beats live for
    a figure that only changes once a quarter.
    """
    try:
        import uw_ownership_service as uwo

        out = uwo.institutional_activity(ticker, top)
    except Exception:  # noqa: BLE001 - the stored datasets still get a turn
        return None
    return out if out.get("status") == "OK" else None


def resolve_cusip(session, ticker: str) -> Optional[str]:
    """
    Find the CUSIP a ticker trades under.

    Inverted deliberately: a full CUSIP-to-ticker map would mean identifying
    every security in the dataset, but only the symbols someone searches
    matter. The company's registered name comes from the SEC ticker registry
    already in use here, and is matched against issuer names in the lookup
    table. Where several CUSIPs match, the most widely held wins -- a security
    thousands of managers hold is the common stock, not a warrant or a
    tracking line.
    """
    ticker = (ticker or "").upper().strip()
    if not ticker:
        return None

    # A ticker already resolved once is remembered.
    pinned = (session.query(InstitutionalSecurity)
              .filter(InstitutionalSecurity.ticker == ticker)
              .order_by(InstitutionalSecurity.holders.desc())
              .first())
    if pinned:
        return pinned.cusip

    names = _known_names(ticker)
    if not names:
        return _by_ticker_word(session, ticker)
    company = names[0]

    # Matched on the words, not on the spelling. The SEC registry and 13F
    # filers write the same company differently often enough that a prefix
    # test misses the largest names in the market: the registry calls XOM
    # "ExxonMobil Holdings Corp" while filers write "EXXON MOBIL CORP", and
    # Disney is "Walt Disney Co" against their "DISNEY WALT CO". Both were
    # sitting in the table under their correct CUSIPs, reported as though no
    # institution held them.
    best = None
    for name in names:
        for candidate in _candidates(session, name):
            score = _name_match(name, candidate.match_name or "")
            if score and (best is None or (score, candidate.holders or 0)
                          > (best[0], best[1].holders or 0)):
                best = (score, candidate)
        if best:
            break

    if not best:
        # Last resort: the ticker itself, as a whole word. Filers name some
        # issuers in ways no registry spelling reaches -- GE files as "GE
        # AEROSPACE" while the SEC still registers "General Electric Co", and
        # an ETF like QQQ has no registry entry at all but files as "INVESCO
        # QQQ TR".
        return _by_ticker_word(session, ticker)
    candidate = best[1]
    candidate.ticker = ticker
    session.commit()
    return candidate.cusip


def _known_names(ticker: str) -> list[str]:
    """
    Every name this company is on record under, normalised, best first.

    The registry name alone is not enough: BAC is registered as "BANK OF
    AMERICA CORP /DE/" where filers write "BANK AMERICA", and its own former
    name "BANKAMERICA CORP/DE/" is the spelling that matches theirs.
    """
    names: list[str] = []

    def add(raw: str) -> None:
        text = _normalise(raw or "")
        if text and text not in names:
            names.append(text)

    try:
        add((sec.get_company_cik(ticker) or {}).get("company_name", ""))
    except Exception:  # noqa: BLE001
        pass
    try:
        import sec_filings_service as filings

        payload = filings._submissions(ticker) or {}
        add(payload.get("name") or "")
        for former in payload.get("formerNames") or []:
            add(former.get("name") or "")
    except Exception:  # noqa: BLE001
        pass
    return names


def _by_ticker_word(session, ticker: str) -> Optional[str]:
    """The most widely held issuer whose name carries this ticker as a word."""
    if len(ticker) < 2:
        return None
    rows = (session.query(InstitutionalSecurity)
            .filter(InstitutionalSecurity.match_name.like(f"%{ticker}%"))
            .order_by(InstitutionalSecurity.holders.desc())
            .limit(25).all())
    for row in rows:
        if ticker in _words(row.match_name or ""):
            row.ticker = ticker
            session.commit()
            return row.cusip
    return None


# Words that say a company is a company rather than which one it is.
# Kept deliberately short. Every word dropped here is a word that can no
# longer tell two companies apart, and "AMERICAN AIRLINES" against "AMERICAN
# EXPRESS" is already handled by the one-shared-word rule below.
_NOISE = {"US", "USA", "OF", "AND", "DE", "THE"}


def _words(name: str) -> list[str]:
    return [w for w in (name or "").split() if len(w) > 1]


def _candidates(session, company: str) -> list:
    """Securities worth comparing: anything sharing a word with this name."""
    words = [w for w in _words(company) if w not in _NOISE] or _words(company)
    terms = []
    for word in words[:3]:
        terms.append(f"%{word}%")
        # One registry writes a name closed up where filers write it open:
        # "EXXONMOBIL" against "EXXON MOBIL". A whole-word search finds
        # neither in the other, so a long word also searches by its stem.
        if len(word) >= 8:
            terms.append(f"{word[:5]}%")

    seen: dict = {}
    for term in terms:
        rows = (session.query(InstitutionalSecurity)
                .filter(InstitutionalSecurity.match_name.isnot(None))
                .filter(InstitutionalSecurity.match_name.like(term))
                .order_by(InstitutionalSecurity.holders.desc())
                .limit(25).all())
        for row in rows:
            seen[row.cusip] = row
    return list(seen.values())


def _name_match(company: str, name: str) -> float:
    """
    How strongly two company names refer to the same company, 0 to 1.

    Three degrees, in order of how much they prove: the same name ignoring
    word order, one name contained in the other once spacing is discounted,
    and a majority of the distinguishing words in common. Anything less is
    not a match, because a wrong CUSIP here would report another company's
    institutional holders as this one's.
    """
    if not company or not name:
        return 0.0
    mine, theirs = set(_words(company)), set(_words(name))
    if mine == theirs:
        return 1.0

    squashed_a, squashed_b = company.replace(" ", ""), name.replace(" ", "")
    if squashed_a == squashed_b:
        return 1.0
    if len(squashed_a) >= 6 and len(squashed_b) >= 6 and (
            squashed_a.startswith(squashed_b) or squashed_b.startswith(squashed_a)):
        return 0.9

    strong_a = {w for w in mine if w not in _NOISE} or mine
    strong_b = {w for w in theirs if w not in _NOISE} or theirs
    shared = strong_a & strong_b
    if not shared:
        return 0.0
    ratio = len(shared) / max(len(strong_a), len(strong_b))
    # One shared word is only enough when it is the whole of one name -- "T
    # MOBILE US" and "MOBIL" share a word and are not the same company.
    if len(shared) == 1 and not (shared == strong_a or shared == strong_b):
        return 0.0
    return ratio if ratio >= 0.5 else 0.0


# ---------------------------------------------------------------------------
# ingest
# ---------------------------------------------------------------------------


def ingest_dataset(link: str, quarter: Optional[str] = None,
                   session=None) -> dict:
    """Download, parse and store one quarterly dataset."""
    create_all(engine)
    own = session is None
    session = session or SessionLocal()
    name = link.rsplit("/", 1)[-1]

    try:
        existing = (session.query(InstitutionalIngest)
                    .filter_by(dataset=name).one_or_none())
        if existing and existing.status == "OK":
            return {"status": "ALREADY_INGESTED", "dataset": name,
                    "report_quarter": existing.report_quarter,
                    "holdings": existing.holdings}

        path = download_dataset(link)
        if not path:
            return {"status": "DOWNLOAD_FAILED", "dataset": name}

        parsed = parse_dataset(path, quarter)
        if parsed["status"] != "OK":
            return {"status": parsed["status"], "dataset": name}

        target = parsed["quarter"]
        funds = parsed["funds"]

        # Keep per-fund detail only for the most widely held securities. The
        # tail is real data but it is micro-caps and closed-end funds, and
        # carrying it costs several million rows for names nobody searches.
        holders: dict[str, int] = {}
        for fund in funds.values():
            for cusip in fund["positions"]:
                holders[cusip] = holders.get(cusip, 0) + 1
        universe = {
            cusip for cusip, _ in
            sorted(holders.items(), key=lambda kv: kv[1], reverse=True)[:UNIVERSE_SIZE]
        }

        # Fund identities first, so holdings can reference them by id.
        existing_funds = {
            f.cik: f.id for f in session.query(
                InstitutionalFund.cik, InstitutionalFund.id).all()
        }
        fresh = [
            {"cik": cik, "fund_name": fund["name"] or cik}
            for cik, fund in funds.items() if cik not in existing_funds
        ]
        for i in range(0, len(fresh), BATCH):
            session.bulk_insert_mappings(InstitutionalFund, fresh[i:i + BATCH])
            session.flush()
        if fresh:
            existing_funds = {
                f.cik: f.id for f in session.query(
                    InstitutionalFund.cik, InstitutionalFund.id).all()
            }

        # Re-ingesting a quarter replaces it rather than adding to it, which
        # is what makes a corrected dataset safe to load twice.
        session.query(InstitutionalHolding).filter(
            InstitutionalHolding.report_quarter == target).delete(
                synchronize_session=False)
        session.flush()

        pending: list[dict] = []
        stored = 0
        for cik, fund in funds.items():
            fund_id = existing_funds.get(cik)
            if not fund_id:
                continue
            for position in fund["positions"].values():
                if position["cusip"] not in universe:
                    continue
                pending.append({
                    "fund_id": fund_id,
                    "cusip": position["cusip"],
                    "issuer_name": position["issuer"][:255],
                    "shares": position["shares"],
                    "position_value": position["value"],
                    "report_quarter": target,
                    "filing_date": fund["filed"],
                })
                if len(pending) >= BATCH:
                    session.bulk_insert_mappings(InstitutionalHolding, pending)
                    session.flush()
                    stored += len(pending)
                    pending = []
        if pending:
            session.bulk_insert_mappings(InstitutionalHolding, pending)
            stored += len(pending)

        log = existing or InstitutionalIngest(dataset=name)
        log.report_quarter = target
        log.submissions = parsed["submissions"]
        log.holdings = stored
        log.skipped_options = parsed["skipped_options"]
        log.skipped_non_share = parsed["skipped_non_share"]
        log.status = "OK"
        log.detail = (f"{parsed['submissions']} holdings reports, "
                      f"{stored} positions across {len(universe)} securities")
        session.add(log)
        session.commit()

        # The lookup table is what makes a ticker search fast; rebuilding it
        # here keeps it in step with whatever was just loaded.
        refresh_securities(session)

        # Every cached comparison was computed against the quarters that
        # existed before this one arrived, so all of them are now answers to
        # the wrong question. Loading a new quarter has to invalidate them or
        # the panel keeps reporting "no previous quarter" forever.
        session.query(InstitutionalActivity).delete(synchronize_session=False)
        session.commit()

        return {"status": "OK", "dataset": name, "report_quarter": target,
                "submissions": parsed["submissions"], "holdings": stored,
                "securities": len(universe),
                "skipped_options": parsed["skipped_options"],
                "skipped_non_share": parsed["skipped_non_share"]}
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        return {"status": "ERROR", "dataset": name, "detail": str(exc)[:300]}
    finally:
        if own:
            session.close()


def ingest_latest(count: int = 2) -> dict:
    """
    Load the newest ``count`` datasets, which is what a comparison needs.

    Two is the default because the signal is a quarter-over-quarter change and
    that takes exactly two quarters.
    """
    datasets = list_datasets()
    if not datasets:
        return {"status": "NO_DATASETS"}
    results = [ingest_dataset(link) for link in datasets[-count:]]
    return {"status": "OK", "ingested": results}


# ---------------------------------------------------------------------------
# comparison and scoring
# ---------------------------------------------------------------------------


def _classify(previous: Optional[float], current: Optional[float]) -> str:
    if not previous and current:
        return "NEW POSITION"
    if previous and not current:
        return "CLOSED"
    if previous and current:
        if current > previous * 1.005:
            return "INCREASED"
        if current < previous * 0.995:
            return "REDUCED"
    return "UNCHANGED"


def _score(funds_increasing: int, funds_decreasing: int, new_positions: int,
           closed_positions: int, net_pct: Optional[float],
           total_funds: int) -> tuple[float, str]:
    """
    Map the quarter's change onto -5..+5.

    Two things have to agree before this reaches a strong reading: the breadth
    of managers moving the same way, and the size of the net share change. One
    large fund doubling its stake moves the percentage without telling you
    much about institutional opinion, and a broad drift of tiny adjustments
    moves the fund counts without moving any real money.
    """
    if total_funds < MIN_FUNDS:
        return 0.0, "NEUTRAL"

    buyers = funds_increasing + new_positions
    sellers = funds_decreasing + closed_positions
    ratio = buyers / sellers if sellers else (STRONG_RATIO if buyers else 1.0)
    pct = net_pct or 0.0

    if ratio >= STRONG_RATIO and pct >= STRONG_PCT:
        return 5.0, "BULLISH"
    if ratio >= MODERATE_RATIO and pct >= MODERATE_PCT:
        return 3.0, "BULLISH"
    if ratio <= 1 / STRONG_RATIO and pct <= -STRONG_PCT:
        return -5.0, "BEARISH"
    if ratio <= 1 / MODERATE_RATIO and pct <= -MODERATE_PCT:
        return -3.0, "BEARISH"
    return 0.0, "NEUTRAL"


def _quarters(session) -> list[str]:
    """
    Which quarters are loaded, newest first.

    Read from the ingest log rather than DISTINCT over the holdings table:
    that distinct scans a million rows and was costing several seconds on
    every lookup, to answer a question the log already records exactly.
    """
    rows = (session.query(InstitutionalIngest.report_quarter)
            .filter(InstitutionalIngest.status == "OK").all())
    return sorted({r[0] for r in rows if r[0]}, reverse=True)


# Fund families reorganise. Vanguard moved the same holdings into newly
# registered entities, and each move filed as a brand-new position of a
# billion-odd shares alongside an equal sale by the old entity -- which put
# "VANGUARD CAPITAL MANAGEMENT LLC bought 1.5bn shares" at the top of the
# buyers list for a quarter in which Vanguard bought nothing.
#
# So a family whose own changes cancel out is treated as paperwork: the legs
# are flagged, kept out of the buyer and seller lists, and counted separately.
# The test is deliberately strict -- the family's net has to be small against
# the gross it moved, and there must be legs both ways -- because a family
# that genuinely bought does not cancel out.
REREGISTRATION_NET = 0.2


def _family(name: str) -> str:
    """The first word of a manager's name: VANGUARD, BLACKROCK, FIDELITY."""
    return (name or "").strip().upper().split()[0] if (name or "").strip() else ""


def _flag_reregistrations(changes: list) -> int:
    """Mark legs of a family reorganisation. Returns how many were flagged."""
    families: dict = {}
    for row in changes:
        if row.get("share_change"):
            families.setdefault(_family(row.get("fund")), []).append(row)

    flagged = 0
    for family, rows in families.items():
        if not family or len(rows) < 2:
            continue
        gross = sum(abs(r["share_change"]) for r in rows)
        net = abs(sum(r["share_change"] for r in rows))
        both_ways = (any(r["share_change"] > 0 for r in rows)
                     and any(r["share_change"] < 0 for r in rows))
        moved_whole_positions = any(r["state"] in ("NEW POSITION", "CLOSED")
                                    for r in rows)
        if both_ways and moved_whole_positions and gross and net < gross * REREGISTRATION_NET:
            for row in rows:
                row["reregistration"] = True
                flagged += 1
    return flagged


def compute_institutional_activity(ticker: str, top: int = 10) -> dict:
    """
    Quarter-over-quarter institutional change for one ticker.

    Unusual Whales answers this per ticker, already reconciled to the
    company. The 13F bulk ingest below stays as the fallback and as the
    record: it is 1.3 GB a quarter, lands weeks late, holds two quarters at
    a time, and had to match each ticker to a CUSIP by company name -- which
    is how XOM, DIS, BAC, GE and QQQ came back as "no 13F issuer matched
    this ticker" while thousands of funds held them.
    """
    ticker = (ticker or "").upper().strip()
    session = SessionLocal()
    try:
        quarters = _quarters(session)
        if not quarters:
            return {"ticker": ticker, "status": "NOT_INGESTED",
                    "detail": "No 13F dataset has been loaded yet.",
                    "source": "SEC 13F"}

        latest = quarters[0]
        previous = quarters[1] if len(quarters) > 1 else None

        cusip = resolve_cusip(session, ticker)
        if not cusip:
            # The stored datasets count every filer, which is why they lead.
            # When a ticker is not in them at all, the provider's list of the
            # largest holders is a better answer than none -- and says so.
            provider = _from_unusual_whales(ticker, top)
            if provider:
                return provider
            return {"ticker": ticker, "status": "UNKNOWN_SECURITY",
                    "latest_quarter": latest,
                    "detail": "No 13F issuer matched this ticker.",
                    "source": "SEC 13F"}

        def holdings(quarter: Optional[str]) -> dict[int, dict]:
            if not quarter:
                return {}
            rows = (session.query(InstitutionalHolding, InstitutionalFund)
                    .join(InstitutionalFund,
                          InstitutionalFund.id == InstitutionalHolding.fund_id)
                    .filter(InstitutionalHolding.cusip == cusip,
                            InstitutionalHolding.report_quarter == quarter)
                    .all())
            return {
                h.fund_id: {"shares": h.shares or 0.0,
                            "value": h.position_value or 0.0,
                            "fund": f.fund_name, "cik": f.cik}
                for h, f in rows
            }

        now, before = holdings(latest), holdings(previous)

        increased = decreased = unchanged = opened = closed = 0
        changes: list[dict] = []

        for fund_id in set(now) | set(before):
            current = now.get(fund_id, {}).get("shares") or 0.0
            prior = before.get(fund_id, {}).get("shares") or 0.0
            state = _classify(prior, current)

            if state == "INCREASED":
                increased += 1
            elif state == "REDUCED":
                decreased += 1
            elif state == "NEW POSITION":
                opened += 1
            elif state == "CLOSED":
                closed += 1
            else:
                unchanged += 1

            meta = now.get(fund_id) or before.get(fund_id) or {}
            changes.append({
                "fund": meta.get("fund"),
                "cik": meta.get("cik"),
                "state": state,
                "shares": current,
                "previous_shares": prior,
                "share_change": current - prior,
                "value": meta.get("value"),
            })

        total_now = sum(v["shares"] for v in now.values())
        total_before = sum(v["shares"] for v in before.values())
        net = total_now - total_before
        net_pct = (round(net / total_before * 100, 2)
                   if total_before else None)

        score, signal = _score(increased, decreased, opened, closed,
                               net_pct, len(now))

        transfers = _flag_reregistrations(changes)
        real = [c for c in changes if not c.get("reregistration")]
        # The same reorganisation inflated the new/closed counts, so those are
        # corrected too rather than only the lists a reader can see.
        opened -= sum(1 for c in changes
                      if c.get("reregistration") and c["state"] == "NEW POSITION")
        closed -= sum(1 for c in changes
                      if c.get("reregistration") and c["state"] == "CLOSED")

        movers = sorted(real, key=lambda c: c["share_change"], reverse=True)
        buyers = [c for c in movers if c["share_change"] > 0][:top]
        sellers = [c for c in reversed(movers) if c["share_change"] < 0][:top]

        return {
            "ticker": ticker,
            "cusip": cusip,
            "signal": signal.lower(),
            "score": score,
            "latest_quarter": latest,
            "previous_quarter": previous,
            "total_funds": len(now),
            "funds_increasing": increased,
            "funds_decreasing": decreased,
            "funds_unchanged": unchanged,
            "new_positions": opened,
            "closed_positions": closed,
            "total_shares_current": total_now,
            "total_shares_previous": total_before,
            "net_share_change": net,
            "net_share_change_pct": net_pct,
            "top_buyers": buyers,
            "top_sellers": sellers,
            "family_transfers": transfers,
            # Said plainly, because a reader cannot tell from the numbers how
            # old they are, and six weeks is the best case.
            "staleness_note": STALENESS_NOTE,
            "freshness": __import__("freshness").for_quarterly(latest),
            "status": "OK",
            "source": "SEC 13F",
        }
    finally:
        session.close()


def get_institutional_activity(ticker: str, top: int = 10,
                               refresh: bool = False) -> dict:
    """
    Cached read of one ticker's institutional change.

    The computation joins thousands of holdings across two quarters and takes
    seconds against a remote database. The answer changes once a quarter, so
    it is computed once and stored -- which is the whole reason the activity
    table exists rather than the numbers being derived on every request.
    """
    ticker = (ticker or "").upper().strip()
    session = SessionLocal()
    try:
        create_all(engine)
        quarters = _quarters(session)
        latest = quarters[0] if quarters else None
        if not latest:
            return {"ticker": ticker, "status": "NOT_INGESTED",
                    "detail": "No 13F dataset has been loaded yet.",
                    "source": "SEC 13F"}

        row = (session.query(InstitutionalActivity)
               .filter_by(ticker=ticker, report_quarter=latest).one_or_none())
        if row is not None and not refresh:
            return {
                "ticker": ticker,
                "signal": (row.signal or "neutral").lower(),
                "score": row.institutional_score,
                "latest_quarter": row.report_quarter,
                "previous_quarter": row.previous_quarter,
                "total_funds": row.total_funds,
                "funds_increasing": row.funds_increasing,
                "funds_decreasing": row.funds_decreasing,
                "funds_unchanged": row.funds_unchanged,
                "new_positions": row.new_positions,
                "closed_positions": row.closed_positions,
                "total_shares_current": row.total_shares_current,
                "total_shares_previous": row.total_shares_previous,
                "net_share_change": row.net_share_change,
                "net_share_change_pct": row.net_share_change_pct,
                "top_buyers": json.loads(row.top_buyers or "[]"),
                "top_sellers": json.loads(row.top_sellers or "[]"),
                "staleness_note": STALENESS_NOTE,
                "freshness": __import__("freshness").for_quarterly(
                    locals().get("latest")),
            "freshness": __import__("freshness").for_quarterly(latest),
                "computed_at": (row.computed_at.isoformat()
                                if row.computed_at else None),
                "status": "OK",
                "source": "SEC 13F",
            }
    finally:
        session.close()

    computed = compute_institutional_activity(ticker, top=top)
    if computed.get("status") == "OK":
        _store_activity(computed)
    return computed


def _store_activity(payload: dict) -> None:
    session = SessionLocal()
    try:
        row = (session.query(InstitutionalActivity)
               .filter_by(ticker=payload["ticker"],
                          report_quarter=payload["latest_quarter"])
               .one_or_none())
        if row is None:
            row = InstitutionalActivity(
                ticker=payload["ticker"],
                report_quarter=payload["latest_quarter"])
            session.add(row)
        row.previous_quarter = payload.get("previous_quarter")
        row.funds_increasing = payload.get("funds_increasing")
        row.funds_decreasing = payload.get("funds_decreasing")
        row.funds_unchanged = payload.get("funds_unchanged")
        row.new_positions = payload.get("new_positions")
        row.closed_positions = payload.get("closed_positions")
        row.total_funds = payload.get("total_funds")
        row.total_shares_current = payload.get("total_shares_current")
        row.total_shares_previous = payload.get("total_shares_previous")
        row.net_share_change = payload.get("net_share_change")
        row.net_share_change_pct = payload.get("net_share_change_pct")
        row.institutional_score = payload.get("score")
        row.signal = payload.get("signal")
        row.top_buyers = json.dumps(payload.get("top_buyers") or [])
        row.top_sellers = json.dumps(payload.get("top_sellers") or [])
        session.commit()
    except Exception:  # noqa: BLE001
        session.rollback()
    finally:
        session.close()


def ingest_status() -> dict:
    """What has been loaded, for the settings screen."""
    session = SessionLocal()
    try:
        create_all(engine)
        rows = (session.query(InstitutionalIngest)
                .order_by(InstitutionalIngest.ingested_at.desc()).all())
        quarters = _quarters(session)
        return {
            "quarters_loaded": quarters,
            "datasets": [
                {"dataset": r.dataset, "quarter": r.report_quarter,
                 "holdings": r.holdings, "status": r.status,
                 "ingested_at": r.ingested_at.isoformat() if r.ingested_at else None}
                for r in rows
            ],
            "status": "OK" if quarters else "NOT_INGESTED",
            "source": "SEC 13F",
        }
    finally:
        session.close()
