"""Deterministic coverage for the sequential metadata fallback boundary."""

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.routers.items import _lookup_metadata
from app.services import covers, hardcover, openlibrary


ISBN = "9780306406157"
META = {"title": "Fallback Book", "cover_url": "https://books.google.com/cover.jpg"}


@pytest.mark.asyncio
@pytest.mark.parametrize("ol_result", [httpx.ConnectTimeout("offline"), None])
async def test_openlibrary_timeout_or_miss_falls_back_to_hardcover(ol_result):
    ol = AsyncMock(side_effect=ol_result) if isinstance(ol_result, Exception) else AsyncMock(return_value=ol_result)
    with patch("app.routers.items.openlibrary.lookup", ol), \
         patch("app.routers.items.hardcover.lookup_by_isbn", AsyncMock(return_value=META)), \
         patch("app.routers.items.googlebooks.lookup", AsyncMock()) as google:
        metadata, source, _ = await _lookup_metadata(ISBN, "token", AsyncMock())
    assert metadata == META
    assert source == "hardcover"
    google.assert_not_awaited()


@pytest.mark.asyncio
async def test_timeout_without_hardcover_token_falls_back_to_google():
    with patch("app.routers.items.openlibrary.lookup", AsyncMock(side_effect=httpx.ReadTimeout("slow"))), \
         patch("app.routers.items.hardcover.lookup_by_isbn", AsyncMock()) as hc, \
         patch("app.routers.items.googlebooks.lookup", AsyncMock(return_value=META)):
        metadata, source, _ = await _lookup_metadata(ISBN, None, AsyncMock())
    assert (metadata, source) == (META, "google")
    hc.assert_not_awaited()


@pytest.mark.asyncio
async def test_hardcover_exception_falls_back_to_google():
    with patch("app.routers.items.openlibrary.lookup", AsyncMock(return_value=None)), \
         patch("app.routers.items.hardcover.lookup_by_isbn", AsyncMock(side_effect=ValueError("bad response"))), \
         patch("app.routers.items.googlebooks.lookup", AsyncMock(return_value=META)):
        metadata, source, _ = await _lookup_metadata(ISBN, "token", AsyncMock())
    assert (metadata, source) == (META, "google")


@pytest.mark.asyncio
async def test_all_provider_failures_are_controlled():
    with patch("app.routers.items.openlibrary.lookup", AsyncMock(side_effect=httpx.ConnectError("offline"))), \
         patch("app.routers.items.hardcover.lookup_by_isbn", AsyncMock(side_effect=ValueError("malformed"))), \
         patch("app.routers.items.googlebooks.lookup", AsyncMock(side_effect=RuntimeError("broken"))):
        assert await _lookup_metadata(ISBN, "token", AsyncMock()) == (None, "manual", {})


@pytest.mark.asyncio
async def test_configured_provider_order_is_honored(monkeypatch):
    monkeypatch.setenv("METADATA_PROVIDER_ORDER", "google,openlibrary,hardcover")
    complete_meta = {**META, "series_name": "Series", "description": "Description"}
    with patch("app.routers.items.googlebooks.lookup", AsyncMock(return_value=complete_meta)) as google, \
         patch("app.routers.items.openlibrary.lookup", AsyncMock()) as ol, \
         patch("app.routers.items.hardcover.lookup_by_isbn", AsyncMock()) as hc:
        _, source, _ = await _lookup_metadata(ISBN, "token", AsyncMock())
    assert source == "google"
    google.assert_awaited_once()
    ol.assert_not_awaited()
    hc.assert_not_awaited()


@pytest.mark.asyncio
async def test_openlibrary_keeps_edition_when_work_fails_and_fetches_work_once(monkeypatch):
    calls = []

    async def handler(request):
        calls.append(request.url.path)
        if request.url.path.startswith("/isbn/"):
            return httpx.Response(200, json={"title": "Edition Title", "works": [{"key": "/works/OL1W"}]})
        raise httpx.ReadTimeout("work unavailable", request=request)

    monkeypatch.setattr(openlibrary, "_rate_limit", AsyncMock())
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await openlibrary.lookup(ISBN, client)
    assert result == {"title": "Edition Title", "subtitle": None, "publisher": None,
                      "page_count": None, "isbn10": None}
    assert calls.count("/works/OL1W.json") == 1


@pytest.mark.asyncio
async def test_hardcover_isbn10_fallback_uses_converted_value():
    responses = [None, None]
    seen = []

    async def graphql(query, variables, **kwargs):
        seen.append(variables["isbn"])
        return responses.pop(0)

    with patch("app.services.hardcover._graphql", side_effect=graphql):
        assert await hardcover.lookup_by_isbn(ISBN, AsyncMock(), token="token") is None
    assert seen == [ISBN, "0306406152"]


@pytest.mark.asyncio
async def test_known_alternative_cover_is_tried_before_openlibrary(tmp_path, monkeypatch):
    attempted = []

    async def download(url, dest, client):
        attempted.append(url)
        return True

    monkeypatch.setattr(covers, "COVERS_DIR", tmp_path)
    with patch("app.services.covers._download", side_effect=download):
        result = await covers.download_cover(1, ISBN, META["cover_url"], None, AsyncMock())
    assert result == "covers/1.jpg"
    assert attempted == [META["cover_url"]]


@pytest.mark.asyncio
async def test_title_search_transport_failure_returns_empty(monkeypatch):
    async def handler(request):
        raise httpx.ConnectTimeout("offline", request=request)

    monkeypatch.setattr(openlibrary, "_rate_limit", AsyncMock())
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert await openlibrary.search_books("Dune", client) == []


def test_title_search_provider_exception_does_not_500(admin_client):
    with patch("app.routers.items.openlibrary.search_books",
               AsyncMock(side_effect=RuntimeError("provider bug"))):
        response = admin_client.get("/api/title-search?q=Dune&media_type=book")
    assert response.status_code == 200
