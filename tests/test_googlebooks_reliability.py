"""Regression coverage for credentialed Google Books access (all HTTP is mocked)."""
import asyncio

import httpx
import pytest

from app.services import googlebooks


def run(coro):
    return asyncio.run(coro)


def client_for(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_isbn_and_search_use_header_not_query_string():
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"items": [{"volumeInfo": {"title": "Book"}}]})
    async def exercise():
        async with client_for(handler) as client:
            await googlebooks.lookup("9780000000000", client, api_key="sentinel-secret")
            await googlebooks.search_by_title_author("Book", "Author", client,
                                                     api_key="sentinel-secret")
    run(exercise())
    assert len(requests) == 2
    assert all(r.headers["X-Goog-Api-Key"] == "sentinel-secret" for r in requests)
    assert all("sentinel-secret" not in str(r.url) and "key=" not in str(r.url) for r in requests)


def test_missing_key_makes_no_request():
    def handler(_request):
        raise AssertionError("Google must be skipped")
    async def exercise():
        async with client_for(handler) as client:
            assert await googlebooks.lookup("isbn", client) is None
            assert await googlebooks.search_by_title_author("title", None, client) == []
    run(exercise())


@pytest.mark.parametrize("status,payload,error", [
    (401, {}, googlebooks.GoogleBooksAccessError),
    (403, {}, googlebooks.GoogleBooksAccessError),
    (403, {"error": {"errors": [{"reason": "dailyLimitExceeded"}]}},
     googlebooks.GoogleBooksQuotaError),
    (429, {}, googlebooks.GoogleBooksQuotaError),
    (503, {}, googlebooks.GoogleBooksUpstreamError),
])
def test_http_failure_classification(status, payload, error):
    async def exercise():
        async with client_for(lambda _: httpx.Response(status, json=payload)) as client:
            with pytest.raises(error):
                await googlebooks.lookup("isbn", client, api_key="secret")
    run(exercise())


def test_no_result_and_malformed_response_are_distinct():
    async def exercise():
        async with client_for(lambda _: httpx.Response(200, json={})) as client:
            assert await googlebooks.lookup("isbn", client, api_key="secret") is None
        async with client_for(lambda _: httpx.Response(200, content=b"not-json")) as client:
            with pytest.raises(googlebooks.GoogleBooksMalformedResponse):
                await googlebooks.lookup("isbn", client, api_key="secret")
    run(exercise())


def test_malformed_optional_fields_preserve_metadata():
    payload = {"items": [{"volumeInfo": {"title": "Usable", "authors": "bad",
        "industryIdentifiers": [None, {"type": "ISBN_13", "identifier": 3}],
        "imageLinks": [], "publishedDate": 2020}}]}
    async def exercise():
        async with client_for(lambda _: httpx.Response(200, json=payload)) as client:
            result = await googlebooks.lookup("isbn", client, api_key="secret")
            assert result["title"] == "Usable"
    run(exercise())
