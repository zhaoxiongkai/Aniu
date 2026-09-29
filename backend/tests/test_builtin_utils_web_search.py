from __future__ import annotations

from pathlib import Path
import sys

import httpx
import pytest

sys.path.append(str(Path(__file__).resolve().parents[1]))

from skills.builtin_utils import handler


class _Response:
    def __init__(self, status_code: int, text: str) -> None:
        self.status_code = status_code
        self.text = text

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _Client:
    def __init__(self, response: _Response | Exception, calls: list[str]) -> None:
        self._response = response
        self._calls = calls

    def __enter__(self) -> _Client:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def get(self, url: str, **_kwargs: object) -> _Response:
        self._calls.append(url)
        if isinstance(self._response, Exception):
            raise self._response
        return self._response


def _client_factory(
    responses: list[_Response | Exception], calls: list[str]
):
    def factory(**_kwargs: object) -> _Client:
        return _Client(responses.pop(0), calls)

    return factory


def test_web_search_keeps_duckduckgo_results_as_primary(monkeypatch) -> None:
    calls: list[str] = []
    duckduckgo_markup = """
    <a class="result__a" href="https://example.com/duck">Duck <b>Result</b></a>
    <div class="result__snippet">Duck snippet</div>
    """
    monkeypatch.setattr(
        handler.httpx,
        "Client",
        _client_factory([_Response(200, duckduckgo_markup)], calls),
    )

    result = handler.Skill().do_web_search(
        arguments={"query": "Aniu", "count": 3}, context={}
    )

    assert result["ok"] is True
    assert result["result"]["provider"] == "duckduckgo-html"
    assert result["result"]["items"] == [
        {
            "title": "Duck Result",
            "url": "https://example.com/duck",
            "snippet": "Duck snippet",
        }
    ]
    assert calls == ["https://html.duckduckgo.com/html/"]


@pytest.mark.parametrize(
    "primary_response",
    [
        _Response(202, "anti-automation response"),
        _Response(200, "<html>no results</html>"),
        httpx.RequestError("DuckDuckGo unavailable"),
    ],
)
def test_web_search_falls_back_to_bing_when_primary_has_no_valid_results(
    monkeypatch, primary_response: _Response | Exception
) -> None:
    calls: list[str] = []
    bing_markup = """
    <li class="b_algo"><h2><a href="https://example.com/bing">Bing <strong>Result</strong></a></h2>
    <div class="b_caption"><p>Bing snippet</p></div></li>
    """
    monkeypatch.setattr(
        handler.httpx,
        "Client",
        _client_factory([primary_response, _Response(200, bing_markup)], calls),
    )

    result = handler.Skill().do_web_search(
        arguments={"query": "Aniu", "count": 3}, context={}
    )

    assert result["ok"] is True
    assert result["result"]["provider"] == "bing-html"
    assert result["result"]["items"] == [
        {
            "title": "Bing Result",
            "url": "https://example.com/bing",
            "snippet": "Bing snippet",
        }
    ]
    assert calls == [
        "https://html.duckduckgo.com/html/",
        "https://www.bing.com/search",
    ]
