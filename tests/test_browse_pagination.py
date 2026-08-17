import html
import re
from urllib.parse import parse_qs, urlparse


def _load_more_url(response) -> str:
    match = re.search(r'id="load-more"[^>]+hx-get="([^"]+)"', response.text)
    assert match, response.text
    return html.unescape(match.group(1))


def test_search_pagination_url_preserves_and_encodes_state(admin_client, db):
    db.executemany(
        "INSERT INTO items (title, media_type, source, reading_status, owned) "
        "VALUES (?, 'book', 'test', 'reading', 0)",
        [(f"A title & 1 number {number}",) for number in range(121)],
    )
    tag_id = db.execute("INSERT INTO tags (name) VALUES (?)", ("sci fi & signed",)).lastrowid
    db.execute("INSERT INTO item_tags (item_id, tag_id) SELECT id, ? FROM items", (tag_id,))
    db.commit()

    state = {
        "q": "A title & 1",
        "media_type_filter": "book",
        "location_filter": "",
        "sort": "title_asc",
        "reading_status": "reading",
        "owned": "0",
        "lent_out": "",
        "tag": "sci fi & signed",
        "view": "list",
        "per_page": "2",
    }
    response = admin_client.get("/api/search", params=state)
    assert response.status_code == 200
    assert '<template x-if=' not in response.text
    assert 'data-browse-view="list"' in response.text

    url = _load_more_url(response)
    query = parse_qs(urlparse(url).query, keep_blank_values=True)
    assert query == {
        "q": [state["q"]],
        "media_type_filter": ["book"],
        "sort": ["title_asc"],
        "reading_status": ["reading"],
        "owned": ["0"],
        "tag": [state["tag"]],
        "view": ["list"],
        "page": ["2"],
    }
    assert "sci+fi+%26+signed" in url


def test_browse_initial_pagination_uses_requested_view(admin_client, db):
    db.executemany(
        "INSERT INTO items (title, media_type, source) VALUES (?, 'book', 'test')",
        [(f"Book {number}",) for number in range(61)],
    )
    db.commit()

    response = admin_client.get("/browse", params={"view": "list", "q": "Book"})
    assert response.status_code == 200
    assert 'data-browse-view="list"' in response.text
    query = parse_qs(urlparse(_load_more_url(response)).query)
    assert query["q"] == ["Book"]
    assert query["view"] == ["list"]
    assert query["page"] == ["2"]


def test_list_pagination_sentinel_is_a_valid_replaceable_row(admin_client, db):
    db.executemany(
        "INSERT INTO items (title, media_type, source) VALUES (?, 'book', 'test')",
        [(f"Book {number}",) for number in range(3)],
    )
    db.commit()
    response = admin_client.get(
        "/api/search", params={"view": "list", "page": 1, "per_page": 2}
    )
    assert re.search(
        r'<tbody[^>]*>.*<tr id="load-more" hx-get="[^"]+" '
        r'hx-trigger="revealed" hx-swap="outerHTML">',
        response.text,
        re.DOTALL,
    )
