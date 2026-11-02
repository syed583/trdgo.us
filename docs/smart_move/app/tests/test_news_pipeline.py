"""News collection pipeline: all eligible tickers, documented endpoint and parameters, paging, empty / failed / not-attempted states, de-duplication, pre-cutoff reads, governor limits. Mock service only, no network."""
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "backend"))
import smart_move_dq_audit as DQ  # noqa: E402
import smart_move_live as L  # noqa: E402
import smart_move_v2_collect as C  # noqa: E402
from smvshadow import governor as gv  # noqa: E402

UTC = timezone.utc
T0 = datetime(2026, 11, 1, 12, tzinfo=UTC)


def hl(i, hours_ago, sym="A", source="Business Wire", text=None):
    t = (T0 - timedelta(hours=hours_ago)).isoformat().replace("+00:00", "Z")
    return {"created_at": t, "headline": text or "Acme Corp announces item %d" % i, "source": source, "tickers": [sym], "sentiment": "neutral", "is_major": False, "tags": [], "meta": {}}


class NewsSvc:
    """RefreshService._call contract. `pages` maps (symbol, page) -> list of rows; a missing key = empty list; `fail` = symbols that fail."""

    def __init__(self, tmp, pages=None, fail=()):
        self.store = L.LiveStore(tmp)
        self.clock = gv.FakeClock(T0)
        self.calls, self.pages, self.fail = [], pages or {}, set(fail)

    def _call(self, kind, symbol, params, rid, priority="core"):
        self.calls.append((kind, symbol, dict(params), rid))
        if symbol in self.fail:
            return None, None, self.clock.now()
        raw = json.dumps({"data": self.pages.get((symbol, params.get("page", 0)), [])}).encode()
        return raw, self.store.put(raw), self.clock.now()


# ------------------------------------------------------------------ coverage and endpoint
def test_every_eligible_ticker_gets_one_news_task_when_news_is_requested():
    syms = ["S%02d" % i for i in range(95)]
    store = L.LiveStore(Path(__import__("tempfile").mkdtemp()))
    tasks = [t for t in C.plan(syms, store, T0, include_news=True) if t["kind"] == "uw_news"]
    assert len(tasks) == 95 and {t["symbol"] for t in tasks} == set(syms) and len({t["id"] for t in tasks}) == 95


def test_the_documented_endpoint_and_ticker_filter_are_used_and_page_zero_is_omitted():
    sent = []

    class Uw:
        def set_background(self, b):
            pass

        def get(self, path, params):
            sent.append((path, dict(params)))
            return {"status": "OK", "data": []}
    f = object.__new__(L.InfraFetcher)
    f.uw = Uw()
    f.fetch("uw_news", "NVDA", {"limit": 25})
    f.fetch("uw_news", "NVDA", {"limit": 25, "page": 1})
    assert sent == [("/api/news/headlines", {"ticker": "NVDA", "limit": 25}), ("/api/news/headlines", {"ticker": "NVDA", "limit": 25, "page": 1})]


def test_the_collector_default_covers_every_eligible_stock():
    src = (ROOT / "docs" / "smart_move" / "app" / "v2_live_run.py").read_text(encoding="utf-8")
    assert '"--news-sample", type=int, default=-1' in src and "news_left != 0" in src


# ------------------------------------------------------------------ paging
def test_a_full_page_that_is_still_inside_the_lookback_requests_the_next_page_up_to_the_cap(tmp_path):
    full = lambda off: [hl(off + i, 1 + i) for i in range(25)]                                     # 25 headlines inside the last day: older items may be on the next page
    svc = NewsSvc(tmp_path, pages={("A", 0): full(0), ("A", 1): full(100), ("A", 2): full(200)})
    acc = C.run(svc, C.plan(["A"], svc.store, T0, include_news=True) and [t for t in C.plan(["A"], svc.store, T0, include_news=True) if t["kind"] == "uw_news"], max_requests=20)
    assert [c[2].get("page") for c in svc.calls] == [None, 1, 2] and acc["news_pages_added"] == 2 and acc["attempted"] == 3   # capped at NEWS_MAX_PAGES
    merged = C.merge_news(svc.store, [r for r in svc.store.read(C.EVIDENCE) if r["kind"] == "uw_news"])
    assert len(merged) == 75


def test_no_extra_page_when_the_page_is_short_or_already_reaches_beyond_the_lookback(tmp_path):
    short = [hl(i, 1 + i) for i in range(10)]
    old_tail = [hl(i, 1 + i * 24) for i in range(25)]                                              # 25 headlines spanning 24 days
    svc = NewsSvc(tmp_path, pages={("S", 0): short, ("O", 0): old_tail})
    acc = C.run(svc, [t for t in C.plan(["S", "O"], svc.store, T0, include_news=True) if t["kind"] == "uw_news"], max_requests=20)
    assert acc["attempted"] == 2 and "news_pages_added" not in acc


def test_pages_respect_the_request_budget(tmp_path):
    full = [hl(i, 1 + i) for i in range(25)]
    svc = NewsSvc(tmp_path, pages={("A", 0): full, ("A", 1): full})
    acc = C.run(svc, [t for t in C.plan(["A"], svc.store, T0, include_news=True) if t["kind"] == "uw_news"], max_requests=2)
    assert acc["attempted"] == 2 and acc.get("budget_exhausted") is True


def test_duplicates_across_pages_are_removed_and_originals_are_kept_unchanged(tmp_path):
    p0 = [hl(i, 1 + i) for i in range(25)]
    svc = NewsSvc(tmp_path, pages={("A", 0): p0, ("A", 1): p0[20:] + [hl(99, 40)]})
    C.run(svc, [t for t in C.plan(["A"], svc.store, T0, include_news=True) if t["kind"] == "uw_news"], max_requests=10)
    recs = [r for r in svc.store.read(C.EVIDENCE) if r["kind"] == "uw_news"]
    assert len(C.merge_news(svc.store, recs)) == 26 and len(recs) == 2 and all(r["raw_sha256"] for r in recs)


# ------------------------------------------------------------------ states, errors, limits
def test_failed_and_empty_and_not_attempted_are_three_different_states(tmp_path):
    svc = NewsSvc(tmp_path, pages={("OKK", 0): [hl(1, 5, "OKK")]}, fail={"BAD"})
    tasks = [t for t in C.plan(["OKK", "EMP", "BAD"], svc.store, T0, include_news=True) if t["kind"] == "uw_news"]
    acc = C.run(svc, tasks, max_requests=10)
    assert acc["failed"] == 1 and acc["ok"] == 2 and acc["failures"][0]["symbol"] == "BAD"
    ev = [{"symbol": s, "earnings_date": "2026-11-03", "scored_as_of": T0.isoformat()} for s in ("OKK", "EMP", "BAD", "NEV")]
    out = DQ.news_coverage(ev, svc.store.read(C.EVIDENCE), lambda sha: json.loads(svc.store.get(sha)), {"OKK": "Acme Corp"}, failed_calls=acc["failures"])
    st = {p["symbol"]: p["state"] for p in out["per_stock"]}
    assert st == {"OKK": "OK", "EMP": "EMPTY", "BAD": "FAILED", "NEV": "NOT_ATTEMPTED"}                 # a failed call leaves no evidence record, so the run report supplies it; an unattempted stock stays separate
    assert DQ.news_coverage(ev, svc.store.read(C.EVIDENCE), lambda sha: json.loads(svc.store.get(sha)), {})["states"]["NOT_ATTEMPTED"] == 2    # without that report a failure cannot be told from not-attempted: said so, not guessed
    assert out["news_guidance_eligible"] == 0 and out["eligible_stocks"] == 4 and out["news_attempted"] == 3


def test_authentication_failure_stops_the_run_and_counts_the_unattempted(tmp_path):
    svc = NewsSvc(tmp_path)
    stop = {"v": False}
    orig = svc._call

    def call(kind, symbol, params, rid, priority="core"):
        out = orig(kind, symbol, params, rid, priority)
        stop["v"] = True                                                                           # e.g. HTTP 401: the governor sets the provider stop
        return None, None, svc.clock.now()
    svc._call = call
    acc = C.run(svc, [t for t in C.plan(["A", "B", "C"], svc.store, T0, include_news=True) if t["kind"] == "uw_news"], max_requests=10, stopped=lambda: stop["v"])
    assert acc["stopped"] is True and acc["attempted"] == 1 and acc["failed"] == 1


# ------------------------------------------------------------------ timestamps and look-ahead
def test_news_published_after_the_capture_is_never_valid_pre_cutoff_news(tmp_path):
    svc = NewsSvc(tmp_path, pages={("A", 0): [hl(1, 5), hl(2, -3, text="Acme Corp lowers full-year guidance after the call")]})     # the second one is stamped 3 h AFTER the capture
    C.run(svc, [t for t in C.plan(["A"], svc.store, T0, include_news=True) if t["kind"] == "uw_news"], max_requests=5)
    ev = [{"symbol": "A", "earnings_date": "2026-11-03", "scored_as_of": T0.isoformat()}]
    out = DQ.news_coverage(ev, svc.store.read(C.EVIDENCE), lambda sha: json.loads(svc.store.get(sha)), {"A": "Acme Corp"})
    assert out["valid_pre_cutoff_headlines"] == 1 and out["per_stock"][0]["state"] == "OK"
    early = C.load_inputs(svc.store, "A", T0 - timedelta(hours=1))                                  # a read as of BEFORE the retrieval sees no news record at all
    assert early["news"] is None
    assert len(C.load_inputs(svc.store, "A", T0)["news"]) == 2                                      # stored as received; the cutoff filter is applied by the classifier / scorer


def test_publication_and_retrieval_times_are_both_preserved(tmp_path):
    svc = NewsSvc(tmp_path, pages={("A", 0): [hl(1, 5)]})
    C.run(svc, [t for t in C.plan(["A"], svc.store, T0, include_news=True) if t["kind"] == "uw_news"], max_requests=5)
    rec = [r for r in svc.store.read(C.EVIDENCE) if r["kind"] == "uw_news"][0]
    body = json.loads(svc.store.get(rec["raw_sha256"]))["data"][0]
    assert rec["retrieved_at"] == T0.isoformat() and body["created_at"].endswith("Z") and body["source"] and body["tickers"] == ["A"] and rec["params"]["ticker"] == "A"
