"""Regression coverage for the sequential metadata fallback boundary."""

from unittest.mock import AsyncMock, patch

import httpx


ISBN = "9780306406157"


async def test_openlibrary_failure_falls_back_to_hardcover():
    from app.routers import items_common

    hardcover_meta = {
        "title": "Hardcover Result",
        "hardcover_book_id": 12,
        "hardcover_edition_id": 34,
    }
    with patch.object(items_common.openlibrary, "lookup", new=AsyncMock(side_effect=RuntimeError("offline"))), \
         patch.object(items_common.hardcover, "lookup_by_isbn", new=AsyncMock(return_value=hardcover_meta)), \
         patch.object(items_common.googlebooks, "lookup", new=AsyncMock()) as google:
        metadata, source, hc_ids, _ = await items_common._lookup_metadata(
            ISBN, "token", AsyncMock()
        )

    assert metadata == hardcover_meta
    assert source == "hardcover"
    assert hc_ids == {"hardcover_book_id": 12, "hardcover_edition_id": 34}
    google.assert_not_awaited()


async def test_provider_failures_are_isolated_until_google_fallback():
    from app.routers import items_common

    google_meta = {"title": "Google Result"}
    with patch.object(items_common.openlibrary, "lookup", new=AsyncMock(side_effect=OSError("offline"))), \
         patch.object(items_common.hardcover, "lookup_by_isbn", new=AsyncMock(side_effect=ValueError("bad response"))), \
         patch.object(items_common.googlebooks, "lookup", new=AsyncMock(return_value=google_meta)):
        metadata, source, _, _ = await items_common._lookup_metadata(
            ISBN, "token", AsyncMock()
        )

    assert metadata == google_meta
    assert source == "google"


async def test_enrichment_failure_preserves_primary_metadata():
    from app.routers import items_common

    primary = {"title": "Open Library Result"}
    with patch.object(items_common.openlibrary, "lookup", new=AsyncMock(return_value=primary)), \
         patch.object(items_common.hardcover, "lookup_by_isbn", new=AsyncMock(side_effect=RuntimeError("unavailable"))):
        metadata, source, hc_ids, _ = await items_common._lookup_metadata(
            ISBN, "token", AsyncMock()
        )

    assert metadata == primary
    assert source == "openlibrary"
    assert hc_ids == {}


async def test_openlibrary_keeps_edition_when_work_enrichment_fails(monkeypatch):
    from app.services import openlibrary

    calls = []

    async def handler(request):
        calls.append(request.url.path)
        if request.url.path.startswith("/isbn/"):
            return httpx.Response(200, json={
                "title": "Edition Title",
                "works": [{"key": "/works/OL1W"}],
            })
        raise httpx.ReadTimeout("work unavailable", request=request)

    monkeypatch.setattr(openlibrary, "_rate_limit", AsyncMock())
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await openlibrary.lookup(ISBN, client)

    assert result == {
        "title": "Edition Title",
        "subtitle": None,
        "publisher": None,
        "page_count": None,
        "isbn10": None,
    }
    assert calls.count("/works/OL1W.json") == 1


async def test_openlibrary_search_transport_failure_returns_empty(monkeypatch):
    from app.services import openlibrary

    async def handler(request):
        raise httpx.ConnectTimeout("offline", request=request)

    monkeypatch.setattr(openlibrary, "_rate_limit", AsyncMock())
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert await openlibrary.search_books("Dune", client) == []


def test_title_search_provider_failure_returns_empty_fragment(admin_client):
    from app.routers import items_catalog

    with patch.object(items_catalog.openlibrary, "search_books", new=AsyncMock(side_effect=RuntimeError("offline"))):
        response = admin_client.get("/api/title-search?q=Dune&media_type=book")

    assert response.status_code == 200


async def test_known_provider_cover_is_tried_before_openlibrary(tmp_path, monkeypatch):
    from app.services import covers

    attempted = []

    async def download(url, dest, client):
        attempted.append(url)
        return True

    monkeypatch.setattr(covers, "COVERS_DIR", tmp_path)
    with patch.object(covers, "_download", side_effect=download):
        result = await covers.download_cover(
            1, ISBN, "https://books.google.com/cover.jpg", None, AsyncMock()
        )

    assert result == "covers/1.jpg"
    assert attempted == ["https://books.google.com/cover.jpg"]
