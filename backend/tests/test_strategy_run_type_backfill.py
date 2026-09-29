from __future__ import annotations

import sqlite3
from pathlib import Path
import sys

import pytest
from sqlalchemy import create_engine, text

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.core.config import get_settings
from app.db.database import _backfill_strategy_run_types
from app.db.models import Base


def _legacy_runs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db_path = tmp_path / "runs.db"
    monkeypatch.setenv("SQLITE_DB_PATH", str(db_path))
    get_settings.cache_clear()
    engine = create_engine(f"sqlite:///{db_path.as_posix()}")
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO strategy_runs (id, trigger_source, status, run_type, schedule_name, "
                "executed_actions, skill_payloads, decision_payload) VALUES "
                "(1, 'schedule', 'completed', 'analysis', '上午运行1号', NULL, NULL, NULL), "
                "(2, 'manual', 'completed', 'analysis', NULL, '[{\"action\": \"BUY\"}]', NULL, NULL), "
                "(3, 'manual', 'completed', 'analysis', NULL, NULL, "
                "'{\"tool_calls\": [{\"name\": \"mx_moni_cancel\"}]}', NULL), "
                "(4, 'manual', 'completed', 'trade', '盘前分析', NULL, NULL, NULL), "
                "(5, 'manual', 'completed', 'trade', NULL, NULL, NULL, NULL)"
            )
        )
    return db_path, engine


def test_backfill_preserves_inference_then_skips_large_payload_scan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path, engine = _legacy_runs(tmp_path, monkeypatch)
    try:
        _backfill_strategy_run_types(engine)
        with engine.connect() as connection:
            assert connection.execute(
                text("SELECT run_type FROM strategy_runs ORDER BY id")
            ).scalars().all() == ["trade", "trade", "trade", "analysis", "trade"]
            assert connection.execute(
                text("SELECT name FROM app_migrations")
            ).scalar_one() == "strategy_run_types_v1"

        # A second startup must not read the large historical JSON columns.
        queries: list[str] = []
        original_connect = sqlite3.connect

        def traced_connect(*args, **kwargs):
            connection = original_connect(*args, **kwargs)
            connection.set_trace_callback(queries.append)
            return connection

        monkeypatch.setattr(sqlite3, "connect", traced_connect)
        _backfill_strategy_run_types(engine)
        assert not any("executed_actions" in query for query in queries)
        assert not any("UPDATE strategy_runs" in query for query in queries)
    finally:
        engine.dispose()
        get_settings.cache_clear()


def test_failed_backfill_rolls_back_rows_and_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, engine = _legacy_runs(tmp_path, monkeypatch)
    try:
        with engine.begin() as connection:
            connection.execute(text(
                "CREATE TRIGGER fail_second_run BEFORE UPDATE OF run_type ON strategy_runs "
                "WHEN NEW.id = 2 BEGIN SELECT RAISE(FAIL, 'interrupted migration'); END"
            ))
        with pytest.raises(sqlite3.IntegrityError, match="interrupted migration"):
            _backfill_strategy_run_types(engine)

        with engine.connect() as connection:
            assert connection.execute(text(
                "SELECT run_type FROM strategy_runs WHERE id = 1"
            )).scalar_one() == "analysis"
            assert connection.execute(text(
                "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' AND name = 'app_migrations'"
            )).scalar_one() == 0

        with engine.begin() as connection:
            connection.execute(text("DROP TRIGGER fail_second_run"))
        _backfill_strategy_run_types(engine)
        with engine.connect() as connection:
            assert connection.execute(text(
                "SELECT run_type FROM strategy_runs WHERE id = 1"
            )).scalar_one() == "trade"
    finally:
        engine.dispose()
        get_settings.cache_clear()
