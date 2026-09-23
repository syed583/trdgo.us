import gzip
import hashlib
import json
import os
import threading
import time
import urllib.request
from pathlib import Path


# SEC responses are large (the ticker map is ~1MB, companyfacts is several MB)
# and change at most daily, so refetching them on every scoring pass was the
# single slowest step in building the earnings page.
_CACHE: dict[str, tuple[float, object]] = {}
_CACHE_LOCK = threading.Lock()
_CACHE_TTL = 6 * 3600.0

# The in-memory cache dies with the process, so every restart re-downloaded
# several megabytes per symbol -- that was the 9-17s cold cost on the first
# score of each ticker. Mirroring it to disk makes a restart nearly free while
# keeping the same 6-hour freshness rule.
_DISK_CACHE = Path(
    os.getenv("SEC_CACHE_DIR") or Path(__file__).parent / ".sec_cache")


def _disk_path(url: str) -> Path:
    """A filename-safe, collision-free path for a URL."""
    return _DISK_CACHE / f"{hashlib.sha256(url.encode()).hexdigest()}.json.gz"


def _disk_read(url: str):
    path = _disk_path(url)
    try:
        if time.time() - path.stat().st_mtime >= _CACHE_TTL:
            return None
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError, EOFError):
        # A missing, stale or half-written file simply means "refetch".
        return None


def _disk_write(url: str, payload) -> None:
    path = _disk_path(url)
    try:
        _DISK_CACHE.mkdir(parents=True, exist_ok=True)
        # Write-then-rename so a crash mid-write cannot leave a torn file that
        # later reads as valid JSON.
        temp = path.with_suffix(".tmp")
        with gzip.open(temp, "wt", encoding="utf-8") as handle:
            json.dump(payload, handle)
        temp.replace(path)
    except (OSError, ValueError):
        # Caching is an optimisation; failing to persist must never fail a
        # request.
        pass


SEC_BASE_URL = "https://data.sec.gov"
SEC_TICKER_URL = "https://www.sec.gov/files/company_tickers.json"

SEC_HEADERS = {
    "User-Agent": "US-Stock-Reader personal research contact@example.com",
    "Accept-Encoding": "gzip, deflate",
    "Host": "data.sec.gov"
}


def _get_json(url: str, use_sec_host: bool = True):
    with _CACHE_LOCK:
        hit = _CACHE.get(url)
    if hit and (time.time() - hit[0]) < _CACHE_TTL:
        return hit[1]

    cached = _disk_read(url)
    if cached is not None:
        with _CACHE_LOCK:
            _CACHE[url] = (time.time(), cached)
        return cached

    headers = SEC_HEADERS.copy()

    if not use_sec_host:
        headers.pop("Host", None)

    request = urllib.request.Request(
        url,
        headers=headers
    )

    with urllib.request.urlopen(
        request,
        timeout=20
    ) as response:

        data = response.read()

        if response.headers.get("Content-Encoding") == "gzip":
            import gzip
            data = gzip.decompress(data)

        parsed = json.loads(data.decode("utf-8"))

    with _CACHE_LOCK:
        _CACHE[url] = (time.time(), parsed)
    _disk_write(url, parsed)

    return parsed


def get_company_cik(symbol: str):
    symbol = symbol.upper()

    data = _get_json(
        SEC_TICKER_URL,
        use_sec_host=False
    )

    for item in data.values():

        if item["ticker"].upper() == symbol:

            cik = str(item["cik_str"]).zfill(10)

            return {
                "symbol": symbol,
                "cik": cik,
                "company_name": item["title"]
            }

    return None


def get_company_facts(symbol: str):
    company = get_company_cik(symbol)

    if not company:
        raise ValueError(
            f"SEC company not found for symbol {symbol}"
        )

    cik = company["cik"]

    url = (
        f"{SEC_BASE_URL}/api/xbrl/companyfacts/"
        f"CIK{cik}.json"
    )

    facts = _get_json(url)

    return {
        "symbol": symbol.upper(),
        "cik": cik,
        "company_name": facts.get(
            "entityName",
            company["company_name"]
        ),
        "facts": facts.get("facts", {})
    }