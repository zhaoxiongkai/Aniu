from __future__ import annotations

import copy
import sys
from pathlib import Path

import httpx
import pytest

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.services.jev_shadow_service import JEV_MODEL, assess_jev_shadow  # noqa: E402
from app.core.config import get_settings  # noqa: E402


@pytest.fixture(autouse=True)
def default_model(monkeypatch):
    monkeypatch.delenv("TYPESAFE_MODEL", raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _proposal():
    return {
        "action": "BUY",
        "symbol": "600519",
        "as_of": "2026-09-23T10:00:00+08:00",
        "evidence": [
            {
                "source": "exchange_filing",
                "stance": "supports",
                "as_of": "2026-09-23T09:30:00+08:00",
                "fact": "The exchange filing states that guidance was revised upward.",
            }
        ],
        "account_id": "private-account",
        "quantity": 1000,
    }


def _response():
    return {
        "model": JEV_MODEL,
        "answers": {
            "evidence_sufficient": {"type": "noul", "noul": 0.8},
            "contradictory_evidence": {"type": "noul", "noul": 0.1},
            "requires_review": {"type": "noul", "noul": 0.3},
        },
        "usage": {"input_tokens": 98, "output_tokens": 12},
    }


def test_missing_key_disables_without_network(monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    result = assess_jev_shadow(_proposal())
    assert result["status"] == "disabled"
    assert result["probabilities"] is None


def test_fixed_model_and_minimal_state_with_mock_transport():
    def handler(request):
        assert request.url == "https://api.typesafe.ai/v1/systemone"
        assert request.headers["Authorization"] == "Bearer test-key"
        body = __import__("json").loads(request.content)
        assert body["model"] == JEV_MODEL
        assert set(body["questions"]) == {
            "evidence_sufficient", "contradictory_evidence", "requires_review"
        }
        assert body["state"] == {key: _proposal()[key] for key in ("action", "symbol", "as_of", "evidence")}
        assert "private-account" not in request.content.decode()
        return httpx.Response(200, json=_response())

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = assess_jev_shadow(_proposal(), api_key="test-key", client=client)

    assert result["status"] == "ok"
    assert result["probabilities"]["requires_review"] == 0.3
    assert result["usage"]["input_tokens"] == 98
    assert result["latency_ms"] >= 0
    assert "decision" not in result


@pytest.mark.parametrize("requested,returned", [
    ("jev-1.12.0", "jev-1.12.0"),
    ("jev-latest", "jev-1.13.0"),
])
def test_configured_model_is_requested_and_actual_model_is_recorded(requested, returned):
    def handler(request):
        body = __import__("json").loads(request.content)
        assert body["model"] == requested
        response = _response()
        response["model"] = returned
        return httpx.Response(200, json=response)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = assess_jev_shadow(_proposal(), api_key="test-key", model=requested, client=client)
    assert result["status"] == "ok"
    assert result["model_requested"] == requested
    assert result["model_used"] == returned


def test_pinned_model_mismatch_fails_closed():
    with httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json={**_response(), "model": "jev-1.12.0"})
    )) as client:
        result = assess_jev_shadow(_proposal(), api_key="test-key", model=JEV_MODEL, client=client)
    assert result["status"] == "error"
    assert result["error_code"] == "invalid_response"
    assert result["model_used"] is None


def test_gateway_structured_quote_is_accepted_without_private_fields():
    proposal = _proposal()
    proposal["evidence"][0]["fact"] = {
        "last_price": 100.0, "provider": "mx_query_market/HQ/latest_price"
    }

    def handler(request):
        body = __import__("json").loads(request.content)
        assert body["state"]["evidence"][0]["fact"]["last_price"] == 100.0
        assert "account_id" not in body["state"]
        return httpx.Response(200, json=_response())

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = assess_jev_shadow(proposal, api_key="test-key", client=client)
    assert result["status"] == "ok"


@pytest.mark.parametrize("fact", ["api_key: secret", "someone@example.com", "13800138000", "123456789012345678"])
def test_rejects_sensitive_evidence_without_network(fact):
    proposal = _proposal()
    proposal["evidence"][0]["fact"] = fact
    result = assess_jev_shadow(proposal, api_key="test-key", client=object())
    assert result["status"] == "error"
    assert result["error_code"] == "invalid_proposal"
    assert fact not in str(result)


def test_invalid_timestamp_rejects_without_network():
    proposal = _proposal()
    proposal["as_of"] = "2026-09-23"
    assert assess_jev_shadow(proposal, api_key="test-key", client=object())["error_code"] == "invalid_proposal"


@pytest.mark.parametrize("mutate", [
    lambda value: value.update(model="jev-latest"),
    lambda value: value["answers"]["evidence_sufficient"].update(noul=float("nan")),
    lambda value: value["answers"]["evidence_sufficient"].update(noul=1.3),
    lambda value: value["answers"].pop("requires_review"),
    lambda value: value["usage"].update(input_tokens=-1),
])
def test_malformed_response_is_error(mutate):
    payload = copy.deepcopy(_response())
    mutate(payload)
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload))) as client:
        result = assess_jev_shadow(_proposal(), api_key="test-key", client=client)
    assert result["status"] == "error"
    assert result["error_code"] == "invalid_response"
    assert result["probabilities"] is None


@pytest.mark.parametrize("response,error_code", [(httpx.Response(429), "http_429"), (httpx.Response(401), "http_401")])
def test_http_failure_returns_error_without_exposing_body(response, error_code):
    with httpx.Client(transport=httpx.MockTransport(lambda request: response)) as client:
        result = assess_jev_shadow(_proposal(), api_key="test-key", client=client)
    assert result["error_code"] == error_code
    assert "test-key" not in str(result)


def test_timeout_returns_error():
    def handler(request):
        raise httpx.ReadTimeout("secret details", request=request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = assess_jev_shadow(_proposal(), api_key="test-key", client=client)
    assert result["status"] == "error"
    assert result["error_code"] == "timeout"
    assert "secret" not in str(result)
