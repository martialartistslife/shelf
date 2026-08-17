"""Credentialed access to the Google Books Volumes API.

This is deliberately the only module that knows the endpoint or authentication
mechanism.  Credentials are sent in a header and are never included in URLs.
"""
import logging
import re

import httpx

from app.config import METADATA_HTTP_TIMEOUT

logger = logging.getLogger(__name__)
VOLUMES_URL = "https://www.googleapis.com/books/v1/volumes"


class GoogleBooksError(Exception):
    """A sanitized Google Books failure safe to log or return to the UI."""


class GoogleBooksAccessError(GoogleBooksError):
    pass


class GoogleBooksQuotaError(GoogleBooksError):
    pass


class GoogleBooksUpstreamError(GoogleBooksError):
    pass


class GoogleBooksMalformedResponse(GoogleBooksError):
    pass


class GoogleBooksTransportError(GoogleBooksError):
    pass


def _quota_403(response: httpx.Response) -> bool:
    try:
        error = response.json().get("error", {})
        reasons = [e.get("reason", "") for e in error.get("errors", []) if isinstance(e, dict)]
        status = error.get("status", "")
        return status == "RESOURCE_EXHAUSTED" or any(
            "quota" in reason.casefold() or "ratelimit" in reason.casefold()
            or "limitexceeded" in reason.casefold() for reason in reasons
        )
    except (ValueError, AttributeError, TypeError):
        return False


async def _volumes(params: dict[str, str], client: httpx.AsyncClient,
                   api_key: str | None) -> list[dict]:
    """Fetch a Volumes result page, returning items or a classified failure."""
    if not api_key:
        return []
    try:
        response = await client.get(
            VOLUMES_URL, params=params, headers={"X-Goog-Api-Key": api_key},
            timeout=METADATA_HTTP_TIMEOUT,
        )
    except httpx.HTTPError as exc:
        raise GoogleBooksTransportError("Google Books connection failed") from exc

    if response.status_code == 429 or (response.status_code == 403 and _quota_403(response)):
        raise GoogleBooksQuotaError("Google Books quota exceeded")
    if response.status_code in (401, 403):
        raise GoogleBooksAccessError("Google Books credentials were rejected")
    if response.status_code != 200:
        raise GoogleBooksUpstreamError("Google Books service error")
    try:
        payload = response.json()
        if not isinstance(payload, dict):
            raise TypeError
        items = payload.get("items", [])
        if not isinstance(items, list):
            raise TypeError
        return [item for item in items if isinstance(item, dict)]
    except (ValueError, TypeError, AttributeError) as exc:
        raise GoogleBooksMalformedResponse("Google Books returned an invalid response") from exc


def _text(value):
    return value if isinstance(value, str) else None


def _authors(info: dict) -> str | None:
    values = info.get("authors", [])
    return ", ".join(v for v in values if isinstance(v, str)) or None if isinstance(values, list) else None


async def lookup(isbn: str, client: httpx.AsyncClient, api_key: str | None = None) -> dict | None:
    items = await _volumes({"q": f"isbn:{isbn}"}, client, api_key)
    if not items:
        return None
    info = items[0].get("volumeInfo", {})
    if not isinstance(info, dict) or not _text(info.get("title")):
        return None
    result = {
        "title": info["title"], "subtitle": _text(info.get("subtitle")),
        "authors": _authors(info), "publisher": _text(info.get("publisher")),
        "page_count": info.get("pageCount") if isinstance(info.get("pageCount"), int) else None,
        "description": _text(info.get("description")),
    }
    pub_date = _text(info.get("publishedDate")) or ""
    match = re.search(r"(\d{4})", pub_date)
    if match:
        result["publish_year"] = int(match.group(1))
    images = info.get("imageLinks", {})
    if isinstance(images, dict):
        for name in ("large", "medium", "thumbnail", "smallThumbnail"):
            url = _text(images.get(name))
            if url:
                result["cover_url"] = url.replace("http://", "https://").replace("zoom=1", "zoom=2")
                break
    identifiers = info.get("industryIdentifiers", [])
    if isinstance(identifiers, list):
        for ident in identifiers:
            if not isinstance(ident, dict):
                continue
            kind, value = ident.get("type"), _text(ident.get("identifier"))
            if kind == "ISBN_10" and value:
                result["isbn10"] = value
            elif kind == "ISBN_13" and value:
                result["isbn"] = value
    series = info.get("seriesInfo")
    if isinstance(series, dict):
        result["series_name"] = _text(series.get("title"))
        result["series_position"] = series.get("bookDisplayNumber")
    return result


async def search_by_title_author(title: str, author: str | None, client: httpx.AsyncClient,
                                 api_key: str | None = None, limit: int = 5) -> list[dict]:
    query = f'intitle:"{title}"' + (f' inauthor:"{author}"' if author else "")
    items = await _volumes({"q": query, "maxResults": str(limit)}, client, api_key)
    results = []
    for item in items:
        info = item.get("volumeInfo", {})
        if isinstance(info, dict) and _text(info.get("title")):
            results.append({"title": info["title"], "authors": _authors(info),
                            "description": _text(info.get("description"))})
    return results


async def search_covers(title: str, author: str | None, client: httpx.AsyncClient,
                        api_key: str | None = None, limit: int = 5) -> list[dict]:
    query = title + (f" inauthor:{author.split(',')[0].split('&')[0].strip()}" if author else "")
    items = await _volumes({"q": query, "maxResults": str(limit)}, client, api_key)
    results = []
    for item in items:
        info = item.get("volumeInfo", {})
        images = info.get("imageLinks", {}) if isinstance(info, dict) else {}
        if not isinstance(images, dict):
            continue
        thumb = _text(images.get("thumbnail")) or _text(images.get("smallThumbnail"))
        large = _text(images.get("large")) or _text(images.get("medium")) or thumb
        if thumb and large:
            results.append({"url": large.replace("http://", "https://"),
                            "thumbnail": thumb.replace("http://", "https://"),
                            "source": "Google Books"})
    return results


async def test_connection(api_key: str) -> dict:
    try:
        async with httpx.AsyncClient() as client:
            await _volumes({"q": "isbn:9780140328721", "maxResults": "1"}, client, api_key)
        return {"ok": True, "message": "Connected to Google Books"}
    except GoogleBooksError as exc:
        return {"ok": False, "message": str(exc)}
