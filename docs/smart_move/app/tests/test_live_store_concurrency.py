"""The collector (one process) appends to the evidence files while the monitor's engine reader (another process) reads them. A reader must never see a half-written record as an error, and never lose a record."""
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "backend"))
import smart_move_live as L  # noqa: E402

WRITER = """
import sys, json, time
sys.path.insert(0, sys.argv[1])
import smart_move_live as L
s = L.LiveStore(sys.argv[2])
pad = "x" * int(sys.argv[4])
for i in range(int(sys.argv[3])):
    s.append("cal_obs", {"i": i, "pad": pad})
"""


def test_a_reader_never_fails_on_a_record_being_written_and_never_loses_one(tmp_path):
    store_dir = tmp_path / "store"
    n = 600
    w = subprocess.Popen([sys.executable, "-c", WRITER, str(ROOT / "backend"), str(store_dir), str(n), "60000"])
    reader = L.LiveStore(store_dir)
    errors, reads, last = 0, 0, 0
    deadline = time.time() + 120
    while w.poll() is None and time.time() < deadline:
        try:
            rows = reader.read("cal_obs")
            reads += 1
            assert [r["i"] for r in rows] == list(range(len(rows))), "records out of order or lost"
            last = len(rows)
        except json.JSONDecodeError:
            errors += 1
    w.wait(timeout=60)
    final = reader.read("cal_obs")
    assert errors == 0, "a reader hit %d half-written record(s) in %d reads" % (errors, reads)
    assert len(final) == n and reads > 5


def test_a_half_written_last_record_is_skipped_but_damage_elsewhere_still_raises(tmp_path):
    s = L.LiveStore(tmp_path / "s")
    s.append("cal_obs", {"i": 0})
    f = tmp_path / "s" / "cal_obs.jsonl"
    f.write_text(f.read_text(encoding="utf-8") + '{"i": 1, "pa', encoding="utf-8")                     # torn tail, no newline
    assert [r["i"] for r in s.read("cal_obs")] == [0]
    f.write_text(chr(10).join(['{"i": 0}', "not json", '{"i": 2}', ""]), encoding="utf-8")
    try:
        s.read("cal_obs")
        raise AssertionError("corruption in the middle must raise")
    except json.JSONDecodeError:
        pass
