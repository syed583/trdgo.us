"""The production gate checker must fail loudly: any mandatory FAIL (or NOT VERIFIED) => non-zero exit; advisory findings do not."""
import importlib.util
from pathlib import Path

PATH = Path(__file__).resolve().parents[2] / "docs" / "handover_2026-10-27" / "phaseC" / "prod_gate_checks.py"
spec = importlib.util.spec_from_file_location("prod_gate_checks", PATH)
g = importlib.util.module_from_spec(spec)
spec.loader.exec_module(g)


def test_all_mandatory_pass_exits_zero(capsys):
    r = g.Results()
    r.add("a", True)
    r.add("b", True)
    assert r.exit_code() == 0


def test_a_mandatory_failure_exits_one(capsys):
    r = g.Results()
    r.add("a", True)
    r.add("b", False, "broken")
    assert r.exit_code() == 1


def test_a_mandatory_not_verified_is_not_a_pass(capsys):
    r = g.Results()
    r.add("a", False, status="NOT VERIFIED")
    assert r.exit_code() == 1


def test_an_advisory_failure_does_not_change_the_exit_code_unless_made_mandatory(capsys):
    r = g.Results()
    r.add("a", True)
    r.add("least privilege", False, "over-privileged", mandatory=False)
    assert r.exit_code() == 0
    r2 = g.Results()
    r2.add("least privilege", False, "over-privileged", mandatory=True)          # --require-least-privilege
    assert r2.exit_code() == 1
    assert "advisory" in capsys.readouterr().out


def test_copy_block_parser_reads_the_rows_of_a_restore_listing():
    text = "-- header\nCOPY public.demo_trades (id, strategy, symbol) FROM stdin;\n1\tgrok\tAAPL\n2\tmeta\tMSFT\n\\.\nCOPY public.other (x) FROM stdin;\n9\n\\.\n"
    cols, rows = g.parse_copy(text)
    assert cols == ["id", "strategy", "symbol"] and rows == [["1", "grok", "AAPL"], ["2", "meta", "MSFT"]]
    assert g.parse_copy("nothing here") == (None, [])


def test_host_kind_and_port_override_never_expose_credentials():
    url = "postgresql://user:secret@aws-1-eu-central-1.pooler.supabase.com:6543/postgres"
    assert g.host_kind(url).startswith("pooler") and g.with_port(url, 5432).endswith(":5432/postgres")
    assert g.host_kind("postgresql://u:p@db.abcdefgh.supabase.co:5432/postgres").startswith("direct")
