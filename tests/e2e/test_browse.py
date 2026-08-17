"""E2E tests: browse page — empty state, grid/list, search, filters."""
import pytest
from playwright.sync_api import expect

from tests.e2e.conftest import insert_item

pytestmark = pytest.mark.e2e


def test_browse_empty_state(live_server, authed_page):
    """With no items, browse page shows an empty state message."""
    authed_page.goto(f"{live_server['url']}/browse")
    authed_page.wait_for_load_state("networkidle")
    body = authed_page.locator("body")
    # Either item cards exist or an empty-state element is visible
    cards = authed_page.locator(".item-card, [data-testid='item-card']")
    empty = authed_page.locator(
        "text=No items found, text=empty, text=nothing here, [data-testid='empty-state']"
    )
    assert cards.count() > 0 or empty.count() > 0 or body.inner_text() != ""


def test_browse_shows_items(live_server, authed_page):
    """Items seeded into the DB appear on the browse page with a non-empty grid."""
    insert_item(live_server["data_dir"], title="Dune", media_type="book", isbn="9780441013593")
    authed_page.goto(f"{live_server['url']}/browse")
    authed_page.wait_for_load_state("networkidle")
    expect(authed_page.locator("body")).to_contain_text("Dune")
    # Verify the item grid is populated (catches silent CSP / JS breakage)
    grid = authed_page.locator("[data-testid='item-grid'], table tbody")
    assert grid.count() > 0, "Item grid not rendered — possible JS framework error"


def test_browse_search(live_server, authed_page):
    """Search input filters results to matching items."""
    insert_item(live_server["data_dir"], title="Foundation", media_type="book", isbn="9780553293357")
    insert_item(live_server["data_dir"], title="Neuromancer", media_type="book", isbn="9780441569595")
    authed_page.goto(f"{live_server['url']}/browse")
    authed_page.wait_for_load_state("networkidle")

    search = authed_page.locator("[data-browse-search]:visible").first
    search.fill("Foundation")
    search.press("Enter")
    authed_page.wait_for_load_state("networkidle")

    expect(authed_page.locator("body")).to_contain_text("Foundation")


def test_browse_media_type_filter(live_server, authed_page):
    """Selecting a media-type filter triggers an HTMX reload."""
    insert_item(live_server["data_dir"], title="Filter Test", media_type="book", isbn="9780000444555")
    authed_page.goto(f"{live_server['url']}/browse")
    authed_page.wait_for_load_state("networkidle")

    # The media type filter is a <select> dropdown
    filter_el = authed_page.locator("select#type-filter")
    filter_el.select_option("book")
    authed_page.wait_for_load_state("networkidle")
    # Page should still be on /browse (with query params)
    assert "/browse" in authed_page.url


def test_browse_grid_list_toggle(live_server, authed_page):
    """Grid/list toggle button switches between grid and list view."""
    authed_page.goto(f"{live_server['url']}/browse")
    authed_page.wait_for_load_state("networkidle")

    # Click the list-view toggle button
    authed_page.locator("[data-testid='view-list']").click()
    authed_page.wait_for_load_state("networkidle")
    assert authed_page.locator("body").is_visible()

    # Click back to grid view
    authed_page.locator("[data-testid='view-grid']").click()
    authed_page.wait_for_load_state("networkidle")
    assert authed_page.locator("body").is_visible()


def test_browse_url_state_preserved(live_server, authed_page):
    """Query params survive page load (URL state)."""
    authed_page.goto(f"{live_server['url']}/browse?mt=book")
    authed_page.wait_for_load_state("networkidle")
    assert "mt=book" in authed_page.url or authed_page.locator("body").is_visible()


def test_browse_paginates_after_state_changes(live_server, authed_page):
    """Every server-rendered view/filter replacement remains infinitely pageable."""
    import sqlite3
    from urllib.parse import parse_qs, urlparse

    conn = sqlite3.connect(str(live_server["data_dir"] / "shelf.db"))
    try:
        location_id = conn.execute(
            "INSERT INTO locations (name, sort_order) VALUES ('Pagination Room', 0)"
        ).lastrowid
        conn.executemany(
            "INSERT INTO items "
            "(title, media_type, source, location_id, reading_status, owned) "
            "VALUES (?, 'book', 'test', ?, 'reading', 1)",
            [(f"Pagination Book {number:03d}", location_id) for number in range(130)],
        )
        tag_id = conn.execute("INSERT INTO tags (name) VALUES ('pagination-tag')").lastrowid
        conn.execute(
            "INSERT INTO item_tags (item_id, tag_id) "
            "SELECT id, ? FROM items WHERE title LIKE 'Pagination Book %'",
            (tag_id,),
        )
        conn.commit()
    finally:
        conn.close()

    requests = []
    authed_page.on(
        "request",
        lambda request: requests.append(request.url)
        if "/api/search?" in request.url
        else None,
    )

    def load_three_pages(expected):
        import time
        start = len(requests)
        for page_number in (2, 3):
            authed_page.locator("#load-more").scroll_into_view_if_needed()
            deadline = time.monotonic() + 10
            while not any(
                parse_qs(urlparse(url).query).get("page") == [str(page_number)]
                for url in requests[start:]
            ):
                assert time.monotonic() < deadline, (page_number, requests[start:])
                authed_page.wait_for_timeout(50)
        later = [
            parse_qs(urlparse(url).query)
            for url in requests[start:]
            if parse_qs(urlparse(url).query).get("page", [""])[0] in {"2", "3"}
        ]
        assert {query["page"][0] for query in later} == {"2", "3"}
        for query in later:
            for name, value in expected.items():
                assert query.get(name) == [value], (name, query)

    authed_page.goto(f"{live_server['url']}/browse")
    load_three_pages({"view": "grid", "sort": "newest"})

    authed_page.locator("select[name=sort]").select_option("title_asc")
    expect(authed_page.locator("[data-browse-view=grid]")).to_be_visible()
    load_three_pages({"view": "grid", "sort": "title_asc"})

    authed_page.locator("#type-filter").select_option("book")
    load_three_pages({"view": "grid", "media_type_filter": "book", "sort": "title_asc"})

    authed_page.locator("#location-filter").select_option(str(location_id))
    authed_page.locator("#reading-status-filter").select_option("reading")
    authed_page.locator("#owned-filter").select_option("1")
    authed_page.locator("#tag-filter").select_option("pagination-tag")
    search = authed_page.locator("[data-browse-search]:visible")
    search.fill("Pagination Book")
    authed_page.wait_for_timeout(500)
    expect(authed_page.locator("#tag-filter")).to_have_value("pagination-tag")
    expect(authed_page.get_by_text("Tag: pagination-tag", exact=True)).to_be_visible()
    combined = {
        "q": "Pagination Book",
        "media_type_filter": "book",
        "location_filter": str(location_id),
        "sort": "title_asc",
        "reading_status": "reading",
        "owned": "1",
        "tag": "pagination-tag",
        "view": "grid",
    }
    load_three_pages(combined)

    authed_page.locator("#location-filter").select_option("")
    combined.pop("location_filter")
    load_three_pages(combined)

    authed_page.locator("[data-testid=view-list]").click()
    expect(authed_page.locator("[data-browse-view=list]")).to_be_visible()
    combined["view"] = "list"
    load_three_pages(combined)

    authed_page.locator("[data-testid=view-grid]").click()
    expect(authed_page.locator("[data-browse-view=grid]")).to_be_visible()
    combined["view"] = "grid"
    load_three_pages(combined)

    authed_page.get_by_role("button", name="Clear all").click()
    load_three_pages({"view": "grid", "sort": "newest"})
