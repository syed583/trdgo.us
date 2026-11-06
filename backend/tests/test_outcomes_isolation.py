"""The outcomes table is production history: outside production every statement naming it uses its _local twin."""
import database


def test_outcomes_table_is_environment_isolated():
    assert "earnings_event_outcomes" in database.ISOLATED_TABLES
    sql = ("INSERT INTO earnings_event_outcomes (symbol) VALUES (:s) ON CONFLICT (symbol, earnings_date) DO UPDATE "
           "SET status = excluded.status WHERE earnings_event_outcomes.status <> 'GRADED'")
    out = database.isolate_sql(sql)
    assert "earnings_event_outcomes_local (symbol)" in out
    assert "WHERE earnings_event_outcomes_local.status" in out
    assert out.count("earnings_event_outcomes_local") == 2 and " earnings_event_outcomes " not in out
