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
