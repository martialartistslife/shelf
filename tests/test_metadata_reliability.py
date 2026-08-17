"""Deterministic coverage for the sequential metadata fallback boundary."""

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.config import COVER_HTTP_TIMEOUT, METADATA_HTTP_TIMEOUT, metadata_provider_order
from app.routers.items import _lookup_metadata
from app.services import covers, googlebooks, hardcover, openlibrary


ISBN = "9780306406157"
GOOGLE_KEY = "test-google-key"
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
        metadata, source, _ = await _lookup_metadata(ISBN, None, AsyncMock(), GOOGLE_KEY)
    assert (metadata, source) == (META, "google")
    hc.assert_not_awaited()


@pytest.mark.asyncio
async def test_lookup_skips_google_without_key():
    with patch("app.routers.items.openlibrary.lookup", AsyncMock(return_value=None)), \
         patch("app.routers.items.googlebooks.lookup", AsyncMock()) as google:
        assert await _lookup_metadata(ISBN, None, AsyncMock()) == (None, "manual", {})
    google.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [
    googlebooks.GoogleBooksAccessError("access"),
    googlebooks.GoogleBooksQuotaError("quota"),
    googlebooks.GoogleBooksMalformedResponse("malformed"),
    googlebooks.GoogleBooksTransportError("transport"),
])
async def test_google_failure_does_not_block_later_provider(monkeypatch, failure):
    monkeypatch.setenv("METADATA_PROVIDER_ORDER", "google,openlibrary,hardcover")
    with patch("app.routers.items.googlebooks.lookup", AsyncMock(side_effect=failure)), \
         patch("app.routers.items.openlibrary.lookup", AsyncMock(return_value=META)) as ol:
        metadata, source, _ = await _lookup_metadata(ISBN, None, AsyncMock(), GOOGLE_KEY)
    assert (metadata, source) == (META, "openlibrary")
    ol.assert_awaited_once()


@pytest.mark.asyncio
async def test_hardcover_exception_falls_back_to_google():
    with patch("app.routers.items.openlibrary.lookup", AsyncMock(return_value=None)), \
         patch("app.routers.items.hardcover.lookup_by_isbn", AsyncMock(side_effect=ValueError("bad response"))), \
         patch("app.routers.items.googlebooks.lookup", AsyncMock(return_value=META)):
        metadata, source, _ = await _lookup_metadata(ISBN, "token", AsyncMock(), GOOGLE_KEY)
    assert (metadata, source) == (META, "google")


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["openlibrary", "google"])
async def test_hardcover_enrichment_exception_preserves_primary_metadata(source, monkeypatch):
    primary = {"title": "Already Valid", "authors": "An Author"}
    if source == "google":
        monkeypatch.setenv("METADATA_PROVIDER_ORDER", "google,openlibrary,hardcover")
    provider_results = {
        "openlibrary": primary if source == "openlibrary" else None,
        "google": primary if source == "google" else None,
    }
    with patch("app.routers.items.openlibrary.lookup",
               AsyncMock(return_value=provider_results["openlibrary"])), \
         patch("app.routers.items.googlebooks.lookup",
               AsyncMock(return_value=provider_results["google"])), \
         patch("app.routers.items.hardcover.lookup_by_isbn",
               AsyncMock(side_effect=RuntimeError("enrichment unavailable"))):
        metadata, actual_source, hc_ids = await _lookup_metadata(
            ISBN, "token", AsyncMock(), GOOGLE_KEY if source == "google" else None)
    assert metadata == primary
    assert actual_source == source
    assert hc_ids == {}


@pytest.mark.asyncio
async def test_all_provider_failures_are_controlled():
    with patch("app.routers.items.openlibrary.lookup", AsyncMock(side_effect=httpx.ConnectError("offline"))), \
         patch("app.routers.items.hardcover.lookup_by_isbn", AsyncMock(side_effect=ValueError("malformed"))), \
         patch("app.routers.items.googlebooks.lookup", AsyncMock(side_effect=RuntimeError("broken"))):
        assert await _lookup_metadata(ISBN, "token", AsyncMock(), GOOGLE_KEY) == (None, "manual", {})


@pytest.mark.asyncio
async def test_configured_provider_order_is_honored(monkeypatch):
    monkeypatch.setenv("METADATA_PROVIDER_ORDER", "google,openlibrary,hardcover")
    complete_meta = {**META, "series_name": "Series", "description": "Description"}
    with patch("app.routers.items.googlebooks.lookup", AsyncMock(return_value=complete_meta)) as google, \
         patch("app.routers.items.openlibrary.lookup", AsyncMock()) as ol, \
         patch("app.routers.items.hardcover.lookup_by_isbn", AsyncMock()) as hc:
        _, source, _ = await _lookup_metadata(ISBN, "token", AsyncMock(), GOOGLE_KEY)
    assert source == "google"
    google.assert_awaited_once()
    ol.assert_not_awaited()
    hc.assert_not_awaited()


@pytest.mark.parametrize(
    ("configured", "expected", "warning"),
    [
        ("google,unknown,openlibrary", ("google", "openlibrary"), "unknown providers: unknown"),
        ("google,openlibrary,google", ("google", "openlibrary"), "duplicate providers: google"),
    ],
)
def test_invalid_provider_order_warns_and_is_deterministic(monkeypatch, caplog,
                                                           configured, expected, warning):
    monkeypatch.setenv("METADATA_PROVIDER_ORDER", configured)
    with caplog.at_level("WARNING", logger="app.config"):
        assert metadata_provider_order() == expected
    assert warning in caplog.text


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


@pytest.mark.parametrize(
    ("contact", "expected_interval", "expected_user_agent"),
    [
        (None, 1.0, "Shelf/1.0 (self-hosted home library catalog)"),
        ("ops@example.test", 0.34,
         "Shelf/1.0 (self-hosted home library catalog; contact: ops@example.test)"),
    ],
)
def test_openlibrary_identification_and_rate(monkeypatch, contact, expected_interval,
                                             expected_user_agent):
    if contact is None:
        monkeypatch.delenv("OPENLIBRARY_CONTACT", raising=False)
    else:
        monkeypatch.setenv("OPENLIBRARY_CONTACT", contact)
    assert openlibrary._request_interval() == expected_interval
    assert openlibrary._request_headers() == {"User-Agent": expected_user_agent}


@pytest.mark.asyncio
async def test_metadata_timeout_is_applied_to_provider_requests(monkeypatch):
    seen = []

    async def handler(request):
        seen.append(request.extensions["timeout"])
        if request.url.host == "openlibrary.org":
            return httpx.Response(404)
        return httpx.Response(200, json={"items": []})

    monkeypatch.setattr(openlibrary, "_rate_limit", AsyncMock())
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert await openlibrary.lookup(ISBN, client) is None
        assert await googlebooks.lookup(ISBN, client, api_key=GOOGLE_KEY) is None
    expected = {
        "connect": METADATA_HTTP_TIMEOUT.connect,
        "read": METADATA_HTTP_TIMEOUT.read,
        "write": METADATA_HTTP_TIMEOUT.write,
        "pool": METADATA_HTTP_TIMEOUT.pool,
    }
    assert seen == [expected, expected]


@pytest.mark.asyncio
async def test_cover_timeout_is_applied_to_download(tmp_path):
    seen = []

    async def handler(request):
        seen.append(request.extensions["timeout"])
        return httpx.Response(200, content=b"\xff\xd8\xff" + b"x" * 1200)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert await covers._download("https://books.google.com/cover.jpg", tmp_path / "cover.jpg", client)
    assert seen == [{
        "connect": COVER_HTTP_TIMEOUT.connect,
        "read": COVER_HTTP_TIMEOUT.read,
        "write": COVER_HTTP_TIMEOUT.write,
        "pool": COVER_HTTP_TIMEOUT.pool,
    }]


def test_scan_openlibrary_timeout_hardcover_success(admin_client, db, monkeypatch):
    monkeypatch.setenv("HARDCOVER_TOKEN", "token")
    hardcover_meta = {"title": "Hardcover Result", "authors": "HC Author",
                      "hardcover_book_id": 12, "hardcover_edition_id": 34}
    with patch("app.routers.items.openlibrary.lookup",
               AsyncMock(side_effect=httpx.ConnectTimeout("offline"))), \
         patch("app.routers.items.hardcover.lookup_by_isbn",
               AsyncMock(return_value=hardcover_meta)), \
         patch("app.routers.items.googlebooks.lookup", AsyncMock()) as google, \
         patch("app.routers.items.covers.download_cover", AsyncMock(return_value=None)):
        response = admin_client.post("/api/scan", data={"isbn": ISBN, "media_type": "book"})
    assert response.status_code == 200
    item = db.execute("SELECT title, source FROM items WHERE isbn = ?", (ISBN,)).fetchone()
    assert dict(item) == {"title": "Hardcover Result", "source": "hardcover"}
    google.assert_not_awaited()


def test_scan_openlibrary_timeout_without_hardcover_uses_google(admin_client, db, monkeypatch):
    monkeypatch.delenv("HARDCOVER_TOKEN", raising=False)
    monkeypatch.setenv("GOOGLE_BOOKS_API_KEY", GOOGLE_KEY)
    google_meta = {"title": "Google Result", "authors": "GB Author"}
    with patch("app.routers.items.openlibrary.lookup",
               AsyncMock(side_effect=httpx.ReadTimeout("offline"))), \
         patch("app.routers.items.hardcover.lookup_by_isbn", AsyncMock()) as hc, \
         patch("app.routers.items.googlebooks.lookup", AsyncMock(return_value=google_meta)) as google, \
         patch("app.routers.items.covers.download_cover", AsyncMock(return_value=None)):
        response = admin_client.post("/api/scan", data={"isbn": ISBN, "media_type": "book"})
    assert response.status_code == 200
    item = db.execute("SELECT title, source FROM items WHERE isbn = ?", (ISBN,)).fetchone()
    assert dict(item) == {"title": "Google Result", "source": "google"}
    hc.assert_not_awaited()
    assert google.await_args.kwargs["api_key"] == GOOGLE_KEY


def test_add_from_search_passes_google_key(admin_client, monkeypatch):
    monkeypatch.setenv("GOOGLE_BOOKS_API_KEY", GOOGLE_KEY)
    with patch("app.routers.items._lookup_metadata",
               AsyncMock(return_value=(META, "google", {}))) as lookup, \
         patch("app.routers.items.covers.download_cover", AsyncMock(return_value=None)):
        response = admin_client.post("/api/books/add", data={"isbn": ISBN, "media_type": "book"})
    assert response.status_code == 200
    assert lookup.await_args.args[3] == GOOGLE_KEY
