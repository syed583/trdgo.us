"""Institutional Intelligence (SEC 13F) — DEVELOPMENT-ONLY read service.

A completely separate module from the Earnings AI BUY/SELL engine. It reads the
TRDGO-US-DEV Supabase project (where the bounded 13F dataset lives) through its
OWN guarded engine, so it never touches production and never imports the AI
scoring. Its BULLISH/BEARISH/NEUTRAL labels describe institutional ownership
change ONLY and are deliberately distinct from trading BUY/SELL signals.

Every query is indexed, bounded and paginated; share CHANGES are reported as
quarterly 13F disclosure (delayed), never as live buy/sell order flow.
"""
from __future__ import annotations

import os
from functools import lru_cache
from urllib.parse import urlparse
from typing import Optional

_PROD_REF = "ddetojbocaxhcfxtcopc"
SOURCE = "Institutional Intelligence (SEC 13F, dev)"
DISCLOSURE = ("SEC Form 13F is delayed QUARTERLY disclosure (filed up to 45 days "
              "after quarter-end), NOT live order flow or real-time buying/selling.")
# The dev dataset captures one accepted holdings report per manager per quarter
# (filing_date = SEC acceptance date, day granularity). It does NOT record the
# submission form type, so amended filings (13F-A) cannot be distinguished from
# originals. These limitations are reported, never papered over.
AMENDMENT_NOTE = ("Amended 13F-A filings are not distinguished: the source ingestion "
                  "did not capture submission form type. Each manager appears once per "
                  "quarter in this dataset, so amendment double-counting does not arise "
                  "here, but amendment handling is not independently verifiable.")
IDENTITY_NOTE = ("Ticker shown is the CURRENT CUSIP->ticker mapping; it is not proof of "
                 "the security's ticker identity at the historical filing date.")


def _dev_url() -> Optional[str]:
    """The TRDGO-US-DEV connection, from INSTITUTIONAL_DATABASE_URL or .env.dev.
    Normalised to the psycopg driver + sslmode. Never returns production."""
    url = (os.getenv("INSTITUTIONAL_DATABASE_URL") or "").strip()
    if not url:
        try:
            from dotenv import dotenv_values
            here = os.path.dirname(os.path.abspath(__file__))
            url = (dotenv_values(os.path.join(here, ".env.dev")).get("DATABASE_URL") or "").strip()
        except Exception:  # noqa: BLE001
            url = ""
    if not url:
        return None
    if not url.startswith("postgresql+psycopg"):
        url = url.replace("postgresql://", "postgresql+psycopg://", 1)
    if "sslmode=" not in url:
        url += ("&" if "?" in url else "?") + "sslmode=require"
    return url


def _ref(url: str) -> Optional[str]:
    u = urlparse(url.replace("postgresql+psycopg", "postgresql"))
    return u.username.split(".", 1)[1] if "." in (u.username or "") else None


@lru_cache(maxsize=1)
def _engine():
    """A dedicated engine for the dev institutional DB. Refuses production."""
    from sqlalchemy import create_engine
    url = _dev_url()
    if not url:
        raise RuntimeError("INSTITUTIONAL: no dev DATABASE_URL configured")
    ref = _ref(url)
    if ref == _PROD_REF:
        raise RuntimeError("INSTITUTIONAL: refusing production project ref")
    return create_engine(url, connect_args={"sslmode": "require", "connect_timeout": 8},
                          pool_pre_ping=True, pool_size=3, max_overflow=2)


def available() -> bool:
    try:
        from sqlalchemy import text
        with _engine().connect() as c:
            c.execute(text("SET statement_timeout='30000'"))
            return bool(c.execute(text("SELECT to_regclass('public.institutional_holdings')")).scalar())
    except Exception:  # noqa: BLE001
        return False


def _conn():
    from sqlalchemy import text
    c = _engine().connect()
    c.execute(text("SET statement_timeout='30000'"))
    return c


def _quarters(c) -> list[str]:
    from sqlalchemy import text
    return [r[0] for r in c.execute(text(
        "SELECT DISTINCT report_quarter FROM institutional_holdings ORDER BY report_quarter")).all()]


def _meta(c, as_of: Optional[str] = None) -> dict:
    from sqlalchemy import text
    qs = _quarters(c)
    fd = c.execute(text("SELECT MIN(filing_date), MAX(filing_date) FROM institutional_holdings")).first()
    return {"source": SOURCE, "bounded_universe": "75 securities (development dataset)",
            "quarters_available": qs, "latest_quarter": qs[-1] if qs else None,
            "filing_date_range": [str(fd[0]) if fd[0] else None, str(fd[1]) if fd[1] else None],
            "as_of": as_of, "disclosure": DISCLOSURE,
            "filing_date_basis": "SEC acceptance date (day granularity); not the quarter-end",
            "amendment_handling": AMENDMENT_NOTE, "historical_identity_note": IDENTITY_NOTE}


# --------------------------------------------------------------------------- #
# A. Overview
# --------------------------------------------------------------------------- #
def overview(prev_q: str = "2026-Q1", cur_q: str = "2026-Q2") -> dict:
    from sqlalchemy import text
    with _conn() as c:
        row = c.execute(text("""
          WITH a AS (SELECT fund_id,cusip,SUM(shares) sh FROM institutional_holdings WHERE report_quarter=:a GROUP BY fund_id,cusip),
               b AS (SELECT fund_id,cusip,SUM(shares) sh FROM institutional_holdings WHERE report_quarter=:b GROUP BY fund_id,cusip),
               bf AS (SELECT DISTINCT fund_id FROM institutional_holdings WHERE report_quarter=:b)
          SELECT
            COUNT(*) FILTER (WHERE a.sh IS NULL AND b.sh IS NOT NULL) new_pos,
            COUNT(*) FILTER (WHERE a.sh IS NOT NULL AND b.sh IS NOT NULL AND b.sh>a.sh) increased,
            COUNT(*) FILTER (WHERE a.sh IS NOT NULL AND b.sh IS NOT NULL AND b.sh<a.sh) reduced,
            COUNT(*) FILTER (WHERE a.sh IS NOT NULL AND b.sh IS NOT NULL AND b.sh=a.sh) unchanged,
            COUNT(*) FILTER (WHERE a.sh IS NOT NULL AND b.sh IS NULL AND a.fund_id IN (SELECT fund_id FROM bf)) closed,
            COALESCE(SUM(b.sh),0)-COALESCE(SUM(a.sh),0) net_share_change
          FROM a FULL OUTER JOIN b USING (fund_id,cusip)"""), {"a": prev_q, "b": cur_q}).first()
        tot = c.execute(text("SELECT COUNT(*) FROM institutional_funds")).scalar()
        hold = c.execute(text("SELECT COUNT(*) FROM institutional_holdings")).scalar()
        sec = c.execute(text("SELECT COUNT(*) FROM institutional_securities")).scalar()
        m = _meta(c)
    return {"total_managers": tot, "total_reported_holdings": hold, "securities_tracked": sec,
            "latest_quarter": m["latest_quarter"], "comparison": f"{prev_q}->{cur_q}",
            "new_positions": row.new_pos, "increased_positions": row.increased,
            "reduced_positions": row.reduced, "closed_positions": row.closed,
            "unchanged_positions": row.unchanged,
            "total_reported_share_change": int(row.net_share_change),
            "meta": m}


# --------------------------------------------------------------------------- #
# B. Stock analyzer (optional point-in-time as_of)
# --------------------------------------------------------------------------- #
def _visible_quarters(c, cusip: Optional[str], as_of: Optional[str]) -> list[str]:
    """Quarters whose filings are PUBLICLY AVAILABLE on `as_of` (by acceptance
    date), ordered oldest->newest. With no as_of, all quarters for the security.
    A quarter counts only once at least one of its reports was accepted on/before
    the date -- the reporting-quarter end is never treated as the availability
    date."""
    from sqlalchemy import text
    gate = " AND filing_date <= :asof" if as_of else ""
    where_c = " AND cusip=:c" if cusip else ""
    p: dict = {}
    if as_of:
        p["asof"] = as_of
    if cusip:
        p["c"] = cusip
    return [r[0] for r in c.execute(text(
        f"SELECT DISTINCT report_quarter FROM institutional_holdings WHERE 1=1{where_c}{gate} "
        f"ORDER BY report_quarter"), p).all()]


def stock(ticker: str, as_of: Optional[str] = None,
          prev_q: Optional[str] = None, cur_q: Optional[str] = None) -> dict:
    from sqlalchemy import text
    tk = (ticker or "").upper().strip()
    with _conn() as c:
        mp = c.execute(text("SELECT cusip,sec_issuer_name,ticker,security_type,exchange,confidence,status "
                            "FROM institutional_cusip_map WHERE UPPER(ticker)=:t"), {"t": tk}).first()
        if not mp:
            return {"status": "UNKNOWN_TICKER", "ticker": tk, "meta": _meta(c, as_of)}
        cusip = mp.cusip
        # Select the latest PUBLICLY-AVAILABLE quarter as-of the date (not always
        # the newest quarter in the DB), and the visible quarter before it.
        vis = _visible_quarters(c, cusip, as_of)
        base = {"status": "OK", "ticker": mp.ticker, "company_name": mp.sec_issuer_name,
                "cusip": cusip, "security_type": mp.security_type, "exchange": mp.exchange,
                "mapping_confidence": mp.confidence, "mapping_status": mp.status,
                "as_of": as_of, "amendment_handling": AMENDMENT_NOTE,
                "historical_identity_note": IDENTITY_NOTE}
        if not vis:
            m = _meta(c, as_of)
            return {**base, "visible_quarters": [], "effective_quarter": None,
                    "comparison_quarters": None, "verifiable": False,
                    "note": "No 13F filings were publicly available on this date.",
                    "institutional_holders": 0, "total_reported_shares": 0,
                    "previous_quarter_shares": 0, "current_quarter_shares": 0,
                    "net_share_change": 0, "pct_change": None,
                    "new_positions": 0, "increased_positions": 0, "reduced_positions": 0,
                    "closed_positions": 0, "unchanged_positions": 0,
                    "institutional_classification": "n/a", "net_share_direction": "flat",
                    "classification_basis": "no data visible as-of date",
                    "latest_filing_date": None, "data_freshness": None, "meta": m}
        eff_cur = cur_q if cur_q in vis else vis[-1]
        if prev_q in vis:
            eff_prev = prev_q
        else:
            older = [q for q in vis if q < eff_cur]
            eff_prev = older[-1] if older else None

        gate = " AND filing_date <= :asof" if as_of else ""
        p = {"c": cusip, "b": eff_cur}
        if as_of:
            p["asof"] = as_of
        cur = c.execute(text(f"""
          SELECT SUM(shares) sh, COUNT(DISTINCT fund_id) holders, MAX(filing_date) latest
          FROM institutional_holdings WHERE cusip=:c AND report_quarter=:b {gate}"""), p).first()
        cur_sh = int(cur.sh or 0); cur_hold = cur.holders or 0; cur_latest = str(cur.latest) if cur.latest else None

        if eff_prev is None:
            # Single quarter visible -> report current holdings only, no comparison.
            m = _meta(c, as_of)
            return {**base, "visible_quarters": vis, "effective_quarter": eff_cur,
                    "comparison_quarters": None, "verifiable": True,
                    "note": "Only one reporting quarter is publicly available as-of this date; "
                            "quarter-over-quarter change is not computed.",
                    "institutional_holders": cur_hold, "total_reported_shares": cur_sh,
                    "previous_quarter_shares": None, "current_quarter_shares": cur_sh,
                    "net_share_change": None, "pct_change": None,
                    "new_positions": None, "increased_positions": None, "reduced_positions": None,
                    "closed_positions": None, "unchanged_positions": None,
                    "institutional_classification": "n/a",
                    "classification_basis": "single visible quarter; no prior quarter to compare",
                    "net_share_direction": "flat",
                    "latest_filing_date": cur_latest, "data_freshness": cur_latest, "meta": m}

        p["a"] = eff_prev
        prev = c.execute(text(f"""
          SELECT SUM(shares) sh FROM institutional_holdings WHERE cusip=:c AND report_quarter=:a {gate}"""), p).first()
        prev_sh = int(prev.sh or 0)
        chg = c.execute(text(f"""
          WITH a AS (SELECT fund_id,SUM(shares) sh FROM institutional_holdings WHERE cusip=:c AND report_quarter=:a {gate} GROUP BY fund_id),
               b AS (SELECT fund_id,SUM(shares) sh FROM institutional_holdings WHERE cusip=:c AND report_quarter=:b {gate} GROUP BY fund_id),
               bf AS (SELECT DISTINCT fund_id FROM institutional_holdings WHERE cusip=:c AND report_quarter=:b {gate})
          SELECT COUNT(*) FILTER (WHERE a.sh IS NULL AND b.sh IS NOT NULL) new_pos,
                 COUNT(*) FILTER (WHERE a.sh IS NOT NULL AND b.sh IS NOT NULL AND b.sh>a.sh) inc,
                 COUNT(*) FILTER (WHERE a.sh IS NOT NULL AND b.sh IS NOT NULL AND b.sh<a.sh) red,
                 COUNT(*) FILTER (WHERE a.sh IS NOT NULL AND b.sh IS NOT NULL AND b.sh=a.sh) unch,
                 COUNT(*) FILTER (WHERE a.sh IS NOT NULL AND b.sh IS NULL AND a.fund_id IN (SELECT fund_id FROM bf)) closed
          FROM a FULL OUTER JOIN b USING (fund_id)"""), p).first()
        net = cur_sh - prev_sh
        # institutional classification by fund-count balance (separate from net shares)
        signal = ("bullish" if chg.inc > chg.red else "bearish" if chg.red > chg.inc else "neutral")
        m = _meta(c, as_of)
    return {**base, "visible_quarters": vis, "effective_quarter": eff_cur,
            "comparison_quarters": f"{eff_prev}->{eff_cur}", "verifiable": True,
            "institutional_holders": cur_hold, "total_reported_shares": cur_sh,
            "previous_quarter_shares": prev_sh, "current_quarter_shares": cur_sh,
            "net_share_change": net, "pct_change": round(net / prev_sh * 100, 2) if prev_sh else None,
            "new_positions": chg.new_pos, "increased_positions": chg.inc,
            "reduced_positions": chg.red, "closed_positions": chg.closed, "unchanged_positions": chg.unch,
            "institutional_classification": signal,
            "classification_basis": "fund-count balance (increasing vs reducing managers); separate from net-share direction",
            "net_share_direction": "up" if net > 0 else "down" if net < 0 else "flat",
            "latest_filing_date": cur_latest, "data_freshness": cur_latest, "meta": m}


# --------------------------------------------------------------------------- #
# C. Rankings (per-security aggregates; shares, not value)
# --------------------------------------------------------------------------- #
_RANKINGS = {"accumulation", "distribution", "largest_increase", "largest_decrease",
             "most_new", "most_closed", "strongest_change"}


def rankings(kind: str = "accumulation", limit: int = 20, offset: int = 0,
             prev_q: str = "2026-Q1", cur_q: str = "2026-Q2") -> dict:
    from sqlalchemy import text
    kind = kind if kind in _RANKINGS else "accumulation"
    limit = max(1, min(int(limit or 20), 75)); offset = max(0, int(offset or 0))
    with _conn() as c:
        rows = c.execute(text("""
          WITH a AS (SELECT cusip,fund_id,SUM(shares) sh FROM institutional_holdings WHERE report_quarter=:a GROUP BY cusip,fund_id),
               b AS (SELECT cusip,fund_id,SUM(shares) sh FROM institutional_holdings WHERE report_quarter=:b GROUP BY cusip,fund_id),
               bf AS (SELECT DISTINCT fund_id FROM institutional_holdings WHERE report_quarter=:b),
               j AS (SELECT COALESCE(a.cusip,b.cusip) cusip, a.sh ash, b.sh bsh, a.fund_id afund FROM a FULL OUTER JOIN b USING (cusip,fund_id))
          SELECT cu.cusip, cu.sec_issuer_name, cu.ticker, cu.security_type,
            COALESCE(SUM(j.bsh),0) q2sh, COALESCE(SUM(j.ash),0) q1sh,
            COALESCE(SUM(j.bsh),0)-COALESCE(SUM(j.ash),0) net,
            COUNT(*) FILTER (WHERE j.ash IS NULL AND j.bsh IS NOT NULL) new_pos,
            COUNT(*) FILTER (WHERE j.ash IS NOT NULL AND j.bsh IS NOT NULL AND j.bsh>j.ash) inc,
            COUNT(*) FILTER (WHERE j.ash IS NOT NULL AND j.bsh IS NOT NULL AND j.bsh<j.ash) red,
            COUNT(*) FILTER (WHERE j.ash IS NOT NULL AND j.bsh IS NULL AND j.afund IN (SELECT fund_id FROM bf)) closed,
            COUNT(DISTINCT CASE WHEN j.bsh IS NOT NULL THEN j.afund END) holders
          FROM j JOIN institutional_cusip_map cu ON cu.cusip=j.cusip
          GROUP BY cu.cusip, cu.sec_issuer_name, cu.ticker, cu.security_type"""),
          {"a": prev_q, "b": cur_q}).all()
        m = _meta(c)
    data = [{"ticker": r.ticker, "company_name": r.sec_issuer_name, "cusip": r.cusip,
             "security_type": r.security_type, "q1_shares": int(r.q1sh), "q2_shares": int(r.q2sh),
             "net_share_change": int(r.net), "pct_change": round(r.net / r.q1sh * 100, 2) if r.q1sh else None,
             "new_positions": r.new_pos, "increased": r.inc, "reduced": r.red,
             "closed": r.closed, "holders": r.holders} for r in rows]
    key = {"accumulation": lambda x: -x["net_share_change"], "distribution": lambda x: x["net_share_change"],
           "largest_increase": lambda x: -x["net_share_change"], "largest_decrease": lambda x: x["net_share_change"],
           "most_new": lambda x: -x["new_positions"], "most_closed": lambda x: -x["closed"],
           "strongest_change": lambda x: -abs(x["net_share_change"])}[kind]
    data.sort(key=key)
    return {"kind": kind, "total": len(data), "limit": limit, "offset": offset,
            "rows": data[offset:offset + limit], "meta": m}


# --------------------------------------------------------------------------- #
# D. Managers explorer (paginated, filterable)
# --------------------------------------------------------------------------- #
def managers(q: str = "", quarter: str = "2026-Q2", limit: int = 25, offset: int = 0) -> dict:
    from sqlalchemy import text
    limit = max(1, min(int(limit or 25), 100)); offset = max(0, int(offset or 0))
    p = {"q2": quarter, "lim": limit, "off": offset}
    where = ""
    if q:
        where = "AND f.fund_name ILIKE :q"; p["q"] = f"%{q}%"
    with _conn() as c:
        total = c.execute(text(f"""SELECT COUNT(*) FROM institutional_funds f WHERE EXISTS
            (SELECT 1 FROM institutional_holdings h WHERE h.fund_id=f.id AND h.report_quarter=:q2) {where}"""), p).scalar()
        rows = c.execute(text(f"""
          SELECT f.id, f.cik, f.fund_name,
            (SELECT COUNT(*) FROM institutional_holdings h WHERE h.fund_id=f.id AND h.report_quarter=:q2) holdings,
            (SELECT COUNT(DISTINCT cusip) FROM institutional_holdings h WHERE h.fund_id=f.id AND h.report_quarter=:q2) securities,
            (SELECT MAX(filing_date) FROM institutional_holdings h WHERE h.fund_id=f.id AND h.report_quarter=:q2) latest_filing
          FROM institutional_funds f
          WHERE EXISTS (SELECT 1 FROM institutional_holdings h WHERE h.fund_id=f.id AND h.report_quarter=:q2) {where}
          ORDER BY holdings DESC LIMIT :lim OFFSET :off"""), p).all()
        m = _meta(c)
    return {"total": total, "limit": limit, "offset": offset, "quarter": quarter,
            "rows": [{"cik": r.cik, "manager_name": r.fund_name, "holdings": r.holdings,
                      "securities_held": r.securities, "latest_filing_date": str(r.latest_filing)} for r in rows],
            "meta": m}


# --------------------------------------------------------------------------- #
# E. History (quarterly series for a ticker)
# --------------------------------------------------------------------------- #
def history(ticker: str) -> dict:
    from sqlalchemy import text
    tk = (ticker or "").upper().strip()
    with _conn() as c:
        cusip = c.execute(text("SELECT cusip FROM institutional_cusip_map WHERE UPPER(ticker)=:t"), {"t": tk}).scalar()
        if not cusip:
            return {"status": "UNKNOWN_TICKER", "ticker": tk, "meta": _meta(c)}
        rows = c.execute(text("""
          SELECT report_quarter, SUM(shares) sh, COUNT(DISTINCT fund_id) holders, MAX(filing_date) latest
          FROM institutional_holdings WHERE cusip=:c GROUP BY report_quarter ORDER BY report_quarter"""),
          {"c": cusip}).all()
        m = _meta(c)
    return {"status": "OK", "ticker": tk, "cusip": cusip,
            "series": [{"quarter": r.report_quarter, "reported_shares": int(r.sh),
                        "holders": r.holders, "latest_filing_date": str(r.latest)} for r in rows],
            "meta": m}


# --------------------------------------------------------------------------- #
# Filing metadata + mapping confidence
# --------------------------------------------------------------------------- #
def filing_metadata() -> dict:
    from sqlalchemy import text
    with _conn() as c:
        rows = c.execute(text("""
          SELECT report_quarter, COUNT(*) holdings, MIN(filing_date) earliest, MAX(filing_date) latest,
                 COUNT(DISTINCT fund_id) managers
          FROM institutional_holdings GROUP BY report_quarter ORDER BY report_quarter""")).all()
        m = _meta(c)
    return {"quarters": [{"quarter": r.report_quarter, "holdings": r.holdings, "managers": r.managers,
                          "earliest_filing": str(r.earliest), "latest_filing": str(r.latest)} for r in rows],
            "meta": m}


def mapping(confidence: str = "", limit: int = 100, offset: int = 0) -> dict:
    from sqlalchemy import text
    limit = max(1, min(int(limit or 100), 200)); offset = max(0, int(offset or 0))
    p = {"lim": limit, "off": offset}
    where = ""
    if confidence:
        where = "WHERE confidence=:conf"; p["conf"] = confidence.upper()
    with _conn() as c:
        total = c.execute(text(f"SELECT COUNT(*) FROM institutional_cusip_map {where}"), p).scalar()
        rows = c.execute(text(f"""SELECT cusip, sec_issuer_name, ticker, security_type, exchange,
          mapping_source, confidence, status, reason, is_current, verified_at
          FROM institutional_cusip_map {where} ORDER BY confidence, ticker LIMIT :lim OFFSET :off"""), p).all()
        counts = dict(c.execute(text("SELECT confidence, COUNT(*) FROM institutional_cusip_map GROUP BY confidence")).all())
        m = _meta(c)
    return {"total": total, "limit": limit, "offset": offset, "confidence_counts": counts,
            "rows": [{"cusip": r.cusip, "sec_issuer_name": r.sec_issuer_name, "ticker": r.ticker,
                      "security_type": r.security_type, "exchange": r.exchange, "source": r.mapping_source,
                      "confidence": r.confidence, "status": r.status, "reason": r.reason,
                      "is_current": r.is_current, "verified_at": str(r.verified_at)} for r in rows],
            "meta": m}
