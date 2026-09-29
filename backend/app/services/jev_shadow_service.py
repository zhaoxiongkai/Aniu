from __future__ import annotations

import math
import os
import re
import time
from datetime import datetime
from typing import Any, Mapping

import httpx

from app.core.config import DEFAULT_TYPESAFE_MODEL, get_settings

JEV_MODEL = DEFAULT_TYPESAFE_MODEL
JEV_QUESTION_SET = "aniu-shadow-v1"
_API_URL = "https://api.typesafe.ai/v1/systemone"
_TIMEOUT_SECONDS = 5.0
_SOURCES = {"exchange_filing", "market_quote", "financial_report", "news", "strategy_signal"}
_STANCES = {"supports", "opposes", "neutral"}
_SENSITIVE = re.compile(
    r"(?:[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}|\b1[3-9]\d{9}\b|"
    r"\b\d{15,19}\b|(?:api[_-]?key|token|password|secret)\s*[:=])",
    re.IGNORECASE,
)
_QUESTIONS = {
    "evidence_sufficient": {
        "type": "noul",
        "instructions": "Is the stated evidence sufficient to support the proposed action, without assuming unstated facts?",
    },
    "contradictory_evidence": {
        "type": "noul",
        "instructions": "Does the stated evidence contain a material contradiction to the proposed action?",
    },
    "requires_review": {
        "type": "noul",
        "instructions": "Does this proposal require human review because its stated evidence is weak or contradictory?",
    },
}


class _InvalidProposal(ValueError):
    pass


def _timestamp(value: Any) -> str:
    if not isinstance(value, str) or len(value) > 35:
        raise _InvalidProposal("invalid timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise _InvalidProposal("invalid timestamp") from exc
    if parsed.tzinfo is None:
        raise _InvalidProposal("timestamp requires timezone")
    return value


def _state(proposal: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(proposal, Mapping):
        raise _InvalidProposal("invalid proposal")
    action = proposal.get("action")
    symbol = proposal.get("symbol")
    evidence = proposal.get("evidence")
    if action not in ("BUY", "SELL", "HOLD"):
        raise _InvalidProposal("invalid action")
    if not isinstance(symbol, str) or not re.fullmatch(r"\d{6}", symbol):
        raise _InvalidProposal("invalid symbol")
    if not isinstance(evidence, list) or len(evidence) > 8:
        raise _InvalidProposal("invalid evidence")
    clean_evidence = []
    for item in evidence:
        if not isinstance(item, Mapping):
            raise _InvalidProposal("invalid evidence item")
        source = item.get("source")
        stance = item.get("stance")
        fact = item.get("fact")
        if not isinstance(source, str) or not isinstance(stance, str) or source not in _SOURCES or stance not in _STANCES:
            raise _InvalidProposal("invalid evidence category")
        if isinstance(fact, Mapping):
            if set(fact) != {"last_price", "provider"}:
                raise _InvalidProposal("invalid quote fact")
            price = fact["last_price"]
            provider = fact["provider"]
            if (isinstance(price, bool) or not isinstance(price, (int, float)) or
                    not math.isfinite(price) or price <= 0 or
                    not isinstance(provider, str) or
                    not re.fullmatch(r"[a-zA-Z_/.-]{1,48}", provider)):
                raise _InvalidProposal("invalid quote fact")
            fact = {"last_price": float(price), "provider": provider}
        elif not isinstance(fact, str) or not fact.strip() or len(fact) > 280 or _SENSITIVE.search(fact):
            raise _InvalidProposal("unsafe evidence fact")
        else:
            fact = fact.strip()
        clean_evidence.append(
            {"source": source, "stance": stance, "as_of": _timestamp(item.get("as_of")), "fact": fact}
        )
    return {
        "action": action,
        "symbol": symbol,
        "as_of": _timestamp(proposal.get("as_of")),
        "evidence": clean_evidence,
    }


def _probability(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("invalid probability")
    result = float(value)
    if not math.isfinite(result) or not 0 <= result <= 1:
        raise ValueError("invalid probability")
    return result


def _parse_response(payload: Any, requested_model: str) -> dict[str, Any]:
    response_model = payload.get("model") if isinstance(payload, dict) else None
    alias = requested_model in {"jev-latest", "jev-preview"}
    if (not isinstance(response_model, str) or
            (response_model != requested_model and not (
                alias and re.fullmatch(r"jev-\d+\.\d+\.\d+", response_model)
            ))):
        raise ValueError("unexpected model")
    answers = payload.get("answers")
    usage = payload.get("usage")
    if not isinstance(answers, dict) or set(answers) != set(_QUESTIONS) or not isinstance(usage, dict):
        raise ValueError("invalid response fields")
    tokens = {}
    for field in ("input_tokens", "output_tokens"):
        value = usage.get(field)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError("invalid usage")
        tokens[field] = value
    probabilities = {}
    for name in _QUESTIONS:
        answer = answers[name]
        if not isinstance(answer, dict) or answer.get("type") != "noul":
            raise ValueError("invalid answer type")
        probabilities[name] = _probability(answer.get("noul"))
    return {"model_used": response_model, "probabilities": probabilities, "usage": tokens}


def assess_jev_shadow(
    proposal: Mapping[str, Any],
    *,
    api_key: str | None = None,
    model: str | None = None,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    """Observe a sanitized proposal; never authorize, reject, or execute an order."""
    requested_model = model if model is not None else get_settings().typesafe_model
    result: dict[str, Any] = {
        "status": "disabled",
        "question_set": JEV_QUESTION_SET,
        "model_requested": requested_model,
        "model_used": None,
        "probabilities": None,
        "usage": None,
        "latency_ms": 0,
        "error_code": None,
    }
    key = api_key if api_key is not None else os.environ.get("TYPESAFE_API_KEY", "")
    if not key or not key.strip():
        return result

    started = time.monotonic()
    try:
        state = _state(proposal)
        body = {"model": requested_model, "state": state, "questions": _QUESTIONS}
        headers = {"Authorization": f"Bearer {key.strip()}"}
        if client is None:
            with httpx.Client(timeout=_TIMEOUT_SECONDS) as owned_client:
                response = owned_client.post(_API_URL, json=body, headers=headers)
        else:
            response = client.post(_API_URL, json=body, headers=headers, timeout=_TIMEOUT_SECONDS)
        response.raise_for_status()
        result.update(_parse_response(response.json(), requested_model))
        result["status"] = "ok"
    except _InvalidProposal:
        result.update(status="error", error_code="invalid_proposal")
    except httpx.TimeoutException:
        result.update(status="error", error_code="timeout")
    except httpx.HTTPStatusError as exc:
        result.update(status="error", error_code=f"http_{exc.response.status_code}")
    except httpx.RequestError:
        result.update(status="error", error_code="transport")
    except (ValueError, TypeError, KeyError):
        result.update(status="error", error_code="invalid_response")
    except Exception:
        result.update(status="error", error_code="unexpected")
    finally:
        result["latency_ms"] = max(0, round((time.monotonic() - started) * 1000))
    return result
