"""Best-effort, bounded Jev observation; never part of order authorization."""
from __future__ import annotations

import copy
import logging
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime, timezone
from threading import BoundedSemaphore
from typing import Any, Mapping

from app.db.database import session_scope
from app.core.config import get_settings
from app.db.models import JevAssessment, StrategyRun
from app.services.jev_shadow_service import JEV_QUESTION_SET, assess_jev_shadow

logger = logging.getLogger(__name__)
_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="jev-shadow")
_CAPACITY = BoundedSemaphore(16)
_SKIPPED_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="jev-skipped")
_SKIPPED_CAPACITY = BoundedSemaphore(16)


def _record_skipped(proposal: dict[str, Any], error_code: str, model: str) -> None:
    try:
        with session_scope() as db:
            if db.get(StrategyRun, proposal.get("run_id")) is None:
                return
            db.add(JevAssessment(
                intent_id=proposal.get("intent_id"), run_id=proposal.get("run_id"),
                action=str(proposal.get("action") or "")[:16],
                symbol=str(proposal.get("symbol") or "")[:16],
                as_of=str(proposal.get("as_of") or "")[:40],
                evidence_count=len(proposal.get("evidence") or []),
                status="skipped", error_code=error_code,
                question_set=JEV_QUESTION_SET, model_requested=model,
                finished_at=datetime.now(timezone.utc).replace(tzinfo=None),
            ))
    except Exception:
        logger.exception("Jev skipped audit persistence failed: intent_id=%s", proposal.get("intent_id"))


def _skip_async(proposal: Mapping[str, Any], error_code: str, model: str) -> None:
    if not _SKIPPED_CAPACITY.acquire(blocking=False):
        logger.warning("Jev skipped audit queue full: intent_id=%s", proposal.get("intent_id"))
        return
    try:
        future = _SKIPPED_EXECUTOR.submit(_record_skipped, copy.deepcopy(dict(proposal)), error_code, model)
    except Exception:
        _SKIPPED_CAPACITY.release()
        logger.exception("Jev skipped audit enqueue failed")
        return
    future.add_done_callback(lambda _: _SKIPPED_CAPACITY.release())


def _observe(proposal: dict[str, Any], key: str, model: str) -> None:
    intent_id = proposal.get("intent_id")
    run_id = proposal.get("run_id")
    try:
        with session_scope() as db:
            if db.get(StrategyRun, run_id) is None:
                return
            row = JevAssessment(
                intent_id=intent_id,
                run_id=run_id,
                action=str(proposal.get("action") or "")[:16],
                symbol=str(proposal.get("symbol") or "")[:16],
                as_of=str(proposal.get("as_of") or "")[:40],
                evidence_count=len(proposal.get("evidence") or []),
                status="queued",
                question_set=JEV_QUESTION_SET,
                model_requested=model,
            )
            db.add(row)
            db.flush()
            row_id = row.id
    except Exception:
        logger.exception("Jev observation reservation failed: intent_id=%s", intent_id)
        return

    try:
        result = assess_jev_shadow(proposal, api_key=key, model=model)
    except Exception:
        logger.exception("Jev observation failed unexpectedly: intent_id=%s", intent_id)
        result = {
            "status": "error", "model_used": None, "probabilities": None,
            "usage": None, "latency_ms": None, "error_code": "unexpected",
        }
    try:
        with session_scope() as db:
            row = db.get(JevAssessment, row_id)
            if row is None:
                return
            row.status = result["status"]
            row.model_used = result["model_used"]
            row.probabilities = result["probabilities"]
            row.usage = result["usage"]
            row.latency_ms = result["latency_ms"]
            row.error_code = result["error_code"]
            row.finished_at = datetime.now(timezone.utc).replace(tzinfo=None)
    except Exception:
        logger.exception("Jev observation persistence failed: intent_id=%s", intent_id)


def enqueue_jev_shadow(proposal: Mapping[str, Any]) -> Future[None] | None:
    """Queue an optional assessment without waiting for network or database work."""
    settings = get_settings()
    key = (settings.typesafe_api_key or "").strip()
    model = settings.typesafe_model
    if not _CAPACITY.acquire(blocking=False):
        logger.warning("Jev observation queue full: intent_id=%s", proposal.get("intent_id"))
        _skip_async(proposal, "queue_full", model)
        return None
    try:
        snapshot = copy.deepcopy(dict(proposal))
        future = _EXECUTOR.submit(_observe, snapshot, key, model)
    except Exception:
        _CAPACITY.release()
        logger.exception("Jev observation enqueue failed")
        _skip_async(proposal, "enqueue_failed", model)
        return None
    future.add_done_callback(lambda _: _CAPACITY.release())
    return future
