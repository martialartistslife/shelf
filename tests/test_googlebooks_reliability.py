"""Regression coverage for credentialed Google Books access (all HTTP is mocked)."""
import asyncio
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.services import covers, googlebooks


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


def test_cover_search_skips_google_without_key_and_keeps_openlibrary_fallback():
    async def handler(request):
        assert request.url.host == "openlibrary.org"
        return httpx.Response(200, json={"docs": [{"cover_i": 123}]})
    async def exercise():
        async with client_for(handler) as client:
            with patch("app.services.googlebooks.search_covers", AsyncMock()) as google:
                results = await covers.search_cover_by_title("Dune", "Frank Herbert", client)
                google.assert_not_awaited()
                assert results[0]["source"] == "Open Library"
    run(exercise())


def test_cover_search_google_failure_keeps_openlibrary_fallback():
    async def handler(_request):
        return httpx.Response(200, json={"docs": [{"cover_i": 123}]})
    async def exercise():
        async with client_for(handler) as client:
            with patch("app.services.googlebooks.search_covers",
                       AsyncMock(side_effect=googlebooks.GoogleBooksTransportError("down"))) as google:
                results = await covers.search_cover_by_title(
                    "Dune", None, client, google_api_key="cover-key")
            assert google.await_args.kwargs["api_key"] == "cover-key"
            assert results[0]["source"] == "Open Library"
    run(exercise())


@pytest.mark.parametrize("outcome,expected", [
    (None, {"ok": True, "message": "Connected to Google Books"}),
    (googlebooks.GoogleBooksAccessError("Google Books credentials were rejected"),
     {"ok": False, "message": "Google Books credentials were rejected"}),
    (googlebooks.GoogleBooksQuotaError("Google Books quota exceeded"),
     {"ok": False, "message": "Google Books quota exceeded"}),
    (googlebooks.GoogleBooksTransportError("Google Books connection failed"),
     {"ok": False, "message": "Google Books connection failed"}),
    (googlebooks.GoogleBooksMalformedResponse("Google Books returned an invalid response"),
     {"ok": False, "message": "Google Books returned an invalid response"}),
])
def test_connection_results_are_sanitized(outcome, expected):
    effect = outcome if isinstance(outcome, Exception) else None
    with patch("app.services.googlebooks._volumes", AsyncMock(side_effect=effect)):
        result = run(googlebooks.test_connection("sentinel-connection-key"))
    assert result == expected
    assert "sentinel-connection-key" not in repr(result)


def test_transport_exception_and_url_do_not_expose_key(caplog):
    sentinel = "sentinel-transport-key"
    seen_urls = []
    def handler(request):
        seen_urls.append(str(request.url))
        raise httpx.ConnectError("offline", request=request)
    async def exercise():
        async with client_for(handler) as client:
            with pytest.raises(googlebooks.GoogleBooksTransportError) as caught:
                await googlebooks.lookup("isbn", client, api_key=sentinel)
        assert sentinel not in repr(caught.value)
        assert sentinel not in repr(caught.value.__cause__)
    run(exercise())
    assert all(sentinel not in url and "key=" not in url for url in seen_urls)
    assert sentinel not in caplog.text
