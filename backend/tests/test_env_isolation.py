"""Outside production, saved predictions / signal history are redirected to _local twins."""
import database as d


def test_history_tables_get_local_twin():
    sql = ("INSERT INTO model_predictions (model) VALUES (:m) ON CONFLICT DO NOTHING; "
           "SELECT * FROM earnings_signal_snapshots s JOIN score_snapshots c ON 1=1")
    out = d.isolate_sql(sql)
    assert "model_predictions_local" in out and "earnings_signal_snapshots_local" in out
    assert "score_snapshots_local" in out
    assert "model_predictions " not in out


def test_unrelated_tables_and_index_names():
    assert d.isolate_sql("SELECT * FROM app_users") == "SELECT * FROM app_users"
    assert d.isolate_sql("CREATE INDEX IF NOT EXISTS ix_model_predictions_x ON model_predictions (a)") == \
        "CREATE INDEX IF NOT EXISTS ix_model_predictions_local_x ON model_predictions_local (a)"
    out = d.isolate_sql("SELECT 1 FROM intraday_sig_v2_changes JOIN intraday_sig_v2 ON 1=1")
    assert "app_users" not in out and out.count("_local") == 2


def test_production_flag(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    assert d.is_production()
    monkeypatch.setenv("APP_ENV", "staging")
    assert not d.is_production()


# --- local must never touch production data -------------------------------------------
import pytest  # noqa: E402

PROD_URL = f"postgresql+psycopg://postgres.{d.PROD_REF}:pw@aws-1-eu-central-1.pooler.supabase.com:5432/postgres"
DEV_URL = "postgresql+psycopg://postgres.qzmsozpyypjdxzfprhoz:pw@aws-1-eu-central-1.pooler.supabase.com:5432/postgres"


def test_non_production_is_redirected_to_the_dev_database():
    assert d.resolve_database_url(PROD_URL, False, lambda: DEV_URL) == DEV_URL


def test_non_production_without_a_dev_database_refuses_to_start():
    with pytest.raises(RuntimeError, match="PRODUCTION"):
        d.resolve_database_url(PROD_URL, False, lambda: None)
    with pytest.raises(RuntimeError):                       # a "dev" url that is really production
        d.resolve_database_url(PROD_URL, False, lambda: PROD_URL)


def test_production_and_unrelated_urls_pass_through():
    assert d.resolve_database_url(PROD_URL, True) == PROD_URL
    assert d.resolve_database_url("sqlite:///x.db", False) == "sqlite:///x.db"


def test_this_test_session_is_not_connected_to_production():
    assert d.PROD_REF not in str(d.engine.url)
    assert d.PROD_REF not in (d.DATABASE_URL or "")


def test_every_protected_table_is_rewritten_in_every_statement_form():
    forms = ["INSERT INTO {t} (a) VALUES (1)", "UPDATE {t} SET a = 1", "DELETE FROM {t}",
             "SELECT * FROM {t} x JOIN {t} y ON 1=1", "CREATE TABLE IF NOT EXISTS {t} (a int)",
             "ALTER TABLE {t} ADD COLUMN b int", "SELECT count(*) FROM public.{t}"]
    for t in d.ISOLATED_TABLES:
        for f in forms:
            out = d.isolate_sql(f.format(t=t))
            assert f"{t}_local" in out, (t, f)
            import re
            assert not re.search(rf"(?<![A-Za-z0-9_]){t}(?![A-Za-z0-9_])", out), (t, f, out)


# (ORM-managed tables -- score_snapshots, trade_calls, demo_trades -- have real schemas other tests rely on;
# they are covered by the pure rewrite test above and, for demo_trades, end-to-end by test_intraday_safety.
# These three raw-SQL tables are created from scratch here.)
@pytest.mark.parametrize("table", ["model_predictions", "earnings_signal_snapshots", "ped_scores"])
def test_a_local_write_lands_in_the_twin_never_in_the_real_table(table):
    """Executes real statements through the app's engine: the real table must never appear."""
    from sqlalchemy import text
    raw0 = d.engine.raw_connection()                        # did the twin already exist (real schema)?
    try:
        existed = raw0.cursor().execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (f"{table}_local",)).fetchone()
    finally:
        raw0.close()
    db = d.SessionLocal()
    try:
        db.execute(text(f"CREATE TABLE IF NOT EXISTS {table} (id INTEGER PRIMARY KEY, v TEXT)"))
        db.execute(text(f"DELETE FROM {table}"))
        db.execute(text(f"INSERT INTO {table} (id, v) VALUES (1, 'local')"))
        db.commit()
        assert db.execute(text(f"SELECT count(*) FROM {table}")).scalar() == 1
    finally:
        db.close()
    raw = d.engine.raw_connection()                         # bypasses the rewrite: sees the real names
    try:
        cur = raw.cursor()
        names = {r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        assert f"{table}_local" in names
        if table in names:                                  # a stale real table from before isolation: untouched
            marker = cur.execute(f"SELECT count(*) FROM {table} WHERE v = 'local'").fetchone()[0]                 if "v" in {c[1] for c in cur.execute(f"PRAGMA table_info({table})").fetchall()} else 0
            assert marker == 0, f"local code wrote into the real table {table}"
    finally:
        raw.close()
        if not existed:                                     # leave no scratch table behind for other tests
            raw1 = d.engine.raw_connection()
            try:
                raw1.cursor().execute(f"DROP TABLE IF EXISTS {table}_local")
                raw1.commit()
            finally:
                raw1.close()


def test_no_source_file_hardcodes_the_production_project():
    import pathlib
    root = pathlib.Path(d.__file__).parent
    allowed = {"database.py", "dev_config.py", "conftest.py", "test_env_isolation.py",
               "institutional_dashboard_service.py", "test_dev_isolation.py", "pg_compat_check.py"}   # guards that REFUSE prod
    offenders = [p.name for p in root.rglob("*.py")
                 if ".venv" not in p.parts and p.name not in allowed
                 and d.PROD_REF in p.read_text(encoding="utf-8", errors="ignore")]
    assert not offenders, f"production project ref found in {offenders}"
