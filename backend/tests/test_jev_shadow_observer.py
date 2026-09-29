from __future__ import annotations

import sys
from pathlib import Path
from threading import Event

from fastapi.testclient import TestClient

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.core.config import get_settings  # noqa: E402
from app.core.config import Settings  # noqa: E402
from app.core import rate_limit as rate_limit_module  # noqa: E402
from app.db import database as database_module  # noqa: E402
from app.db.database import session_scope  # noqa: E402
from app.db.models import StrategyRun, TradeIntent  # noqa: E402
from app.main import create_app  # noqa: E402
from app.services import jev_shadow_observer as observer  # noqa: E402
from app.services.scheduler_service import scheduler_service  # noqa: E402
from app.services.trading_calendar_service import trading_calendar_service  # noqa: E402


def _app(monkeypatch, tmp_path):
    monkeypatch.setenv("APP_LOGIN_PASSWORD", "test-pass")
    monkeypatch.setenv("SQLITE_DB_PATH", str(tmp_path / "jev.db"))
    monkeypatch.setattr(trading_calendar_service, "ensure_years", lambda years: None)
    monkeypatch.setattr(scheduler_service, "start", lambda: None)
    monkeypatch.setattr(scheduler_service, "stop", lambda: None)
    get_settings.cache_clear()
    database_module._engine = None
    database_module._session_local = None
    rate_limit_module._limiter.reset()
    return create_app()


def _intent():
    with session_scope() as db:
        run = StrategyRun(trigger_source="manual", run_type="trade", status="running")
        db.add(run)
        db.flush()
        intent = TradeIntent(
            run_id=run.id, intent_key=f"test-{run.id}", trade_date="2026-09-23",
            symbol="600519", action="BUY", quantity=100, price=100.0,
            notional=10000.0, status="reserved",
        )
        db.add(intent)
        db.flush()
        return run.id, intent.id


def _proposal(run_id, intent_id):
    return {
        "run_id": run_id, "intent_id": intent_id,
        "action": "BUY", "symbol": "600519", "quantity": 100,
        "account": "should-not-persist", "as_of": "2026-09-23T10:00:00+08:00",
        "evidence": [{
            "source": "market_quote", "stance": "neutral",
            "as_of": "2026-09-23T09:59:00+08:00",
            "fact": {"last_price": 100.0, "provider": "mx_query_market/HQ/latest_price"},
        }],
    }


def _detail(client, run_id):
    login = client.post("/api/aniu/login", json={"password": "test-pass"})
    token = login.json()["token"]
    result = client.get(f"/api/aniu/runs/{run_id}", headers={"Authorization": f"Bearer {token}"})
    assert result.status_code == 200
    return result.json()


def test_no_key_persists_disabled_without_network(monkeypatch, tmp_path):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    monkeypatch.setenv("TYPESAFE_MODEL", "jev-1.12.0")
    with TestClient(_app(monkeypatch, tmp_path)) as client:
        run_id, intent_id = _intent()
        future = observer.enqueue_jev_shadow(_proposal(run_id, intent_id))
        assert future is not None
        future.result(timeout=3)
        audit = _detail(client, run_id)["jev_assessments"]
        assert len(audit) == 1
        assert audit[0]["status"] == "disabled"
        assert audit[0]["model_requested"] == "jev-1.12.0"
        assert audit[0]["intent_id"] == intent_id
        assert "account" not in str(audit)


def test_typesafe_key_loads_from_backend_env_file(monkeypatch, tmp_path):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text("TYPESAFE_API_KEY=from-env-file\n", encoding="utf-8")
    assert Settings(_env_file=env_file).typesafe_api_key == "from-env-file"


def test_shadow_result_is_visible_and_does_not_wait_for_api(monkeypatch, tmp_path):
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key")
    monkeypatch.setenv("TYPESAFE_MODEL", "jev-1.12.0")
    entered = Event()
    release = Event()

    def assess(proposal, *, api_key, model):
        assert api_key == "test-key"
        assert model == "jev-1.12.0"
        entered.set()
        assert release.wait(timeout=3)
        return {
            "status": "ok", "model_used": model,
            "probabilities": {"evidence_sufficient": 0.8, "contradictory_evidence": 0.1, "requires_review": 0.3},
            "usage": {"input_tokens": 100, "output_tokens": 12},
            "latency_ms": 125, "error_code": None,
        }

    monkeypatch.setattr(observer, "assess_jev_shadow", assess)
    with TestClient(_app(monkeypatch, tmp_path)) as client:
        run_id, intent_id = _intent()
        future = observer.enqueue_jev_shadow(_proposal(run_id, intent_id))
        assert future is not None
        assert entered.wait(timeout=3)
        queued = _detail(client, run_id)["jev_assessments"]
        assert queued[0]["status"] == "queued"
        assert queued[0]["model_requested"] == "jev-1.12.0"
        release.set()
        future.result(timeout=3)
        audit = _detail(client, run_id)["jev_assessments"]
        assert audit[0]["status"] == "ok"
        assert audit[0]["model_used"] == "jev-1.12.0"
        assert audit[0]["intent_id"] == intent_id
        assert audit[0]["probabilities"]["requires_review"] == 0.3
        assert audit[0]["usage"]["input_tokens"] == 100
        assert "test-key" not in str(audit)


def test_shadow_outage_is_recorded_without_raising(monkeypatch, tmp_path):
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key")

    def fail(proposal, *, api_key, model):
        raise RuntimeError("upstream outage secret")

    monkeypatch.setattr(observer, "assess_jev_shadow", fail)
    with TestClient(_app(monkeypatch, tmp_path)) as client:
        run_id, intent_id = _intent()
        future = observer.enqueue_jev_shadow(_proposal(run_id, intent_id))
        assert future is not None
        future.result(timeout=3)
        audit = _detail(client, run_id)["jev_assessments"]
        assert audit[0]["status"] == "error"
        assert audit[0]["error_code"] == "unexpected"
        assert "secret" not in str(audit)


def test_full_queue_records_skipped_assessment(monkeypatch, tmp_path):
    class FullCapacity:
        def acquire(self, *, blocking):
            return False

    monkeypatch.setattr(observer, "_CAPACITY", FullCapacity())
    recorded = Event()
    original = observer._record_skipped

    def record(proposal, error_code, model):
        try:
            original(proposal, error_code, model)
        finally:
            recorded.set()

    monkeypatch.setattr(observer, "_record_skipped", record)
    with TestClient(_app(monkeypatch, tmp_path)) as client:
        run_id, intent_id = _intent()
        assert observer.enqueue_jev_shadow(_proposal(run_id, intent_id)) is None
        assert recorded.wait(timeout=3)
        audit = _detail(client, run_id)["jev_assessments"]
        assert audit[0]["status"] == "skipped"
        assert audit[0]["error_code"] == "queue_full"
