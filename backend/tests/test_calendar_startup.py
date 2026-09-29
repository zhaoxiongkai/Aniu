from __future__ import annotations

import asyncio
from contextlib import contextmanager
from datetime import date
from pathlib import Path
import sys

import pytest

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app import main as main_module


def _stub_startup(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    events: list[str] = []

    @contextmanager
    def fake_session_scope():
        yield object()

    monkeypatch.setattr(main_module, "init_db", lambda: None)
    monkeypatch.setattr(main_module.skill_registry, "reload", lambda: None)
    monkeypatch.setattr(main_module, "session_scope", fake_session_scope)
    monkeypatch.setattr(
        main_module.skill_admin_service, "apply_persisted_state", lambda db: None
    )
    monkeypatch.setattr(main_module.scheduler_service, "start", lambda: events.append("start"))
    monkeypatch.setattr(main_module.scheduler_service, "stop", lambda: events.append("stop"))
    return events


def test_startup_does_not_fetch_next_year(monkeypatch: pytest.MonkeyPatch) -> None:
    events = _stub_startup(monkeypatch)

    def ensure_years(years: list[int]) -> None:
        if years == [date.today().year + 1]:
            raise AssertionError("future-year fetch entered startup critical path")
        events.append(f"calendar:{years[0]}")

    monkeypatch.setattr(main_module.trading_calendar_service, "ensure_years", ensure_years)

    async def check() -> None:
        async with main_module.app_lifespan(main_module.app):
            assert events == [f"calendar:{date.today().year}", "start"]

    asyncio.run(check())
    assert events == [f"calendar:{date.today().year}", "start", "stop"]


def test_startup_fails_closed_without_current_year(monkeypatch: pytest.MonkeyPatch) -> None:
    events = _stub_startup(monkeypatch)

    def ensure_years(years: list[int]) -> None:
        assert years == [date.today().year]
        raise RuntimeError("current-year calendar unavailable")

    monkeypatch.setattr(main_module.trading_calendar_service, "ensure_years", ensure_years)

    async def check() -> None:
        async with main_module.app_lifespan(main_module.app):
            pytest.fail("startup should not become ready")

    with pytest.raises(RuntimeError, match="current-year calendar unavailable"):
        asyncio.run(check())
    assert events == []
