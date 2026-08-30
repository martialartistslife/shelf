"""Route-level regressions for Issue #54 ISBN persistence invariants."""

from unittest.mock import AsyncMock, patch

from tests.conftest import _insert_item


BAD_ISBN13 = "9780441172710"
BAD_ISBN10 = "0441172718"
DUNE_ISBN13 = "9780441172719"
DUNE_ISBN10 = "0441172717"
HOBBIT_ISBN13 = "9780547928227"
HOBBIT_ISBN10 = "054792822X"
ISBN979 = "9791234567896"


def _stored_pair(db, item_id):
    row = db.execute(
        "SELECT isbn, isbn10 FROM items WHERE id = ?", (item_id,)
    ).fetchone()
    return row["isbn"], row["isbn10"]


def _committed_item(db, **fields):
    item_id = _insert_item(db, **fields)
    db.commit()
    return item_id


class TestInvalidRouteInputs:
    def test_manual_add_rejects_bad_isbn13_without_inserting(self, editor_client, db):
        before = db.execute("SELECT COUNT(*) FROM items").fetchone()[0]
        response = editor_client.post(
            "/api/items/manual",
            data={"title": "Bad checksum", "isbn": BAD_ISBN13, "media_type": "book"},
        )
        assert response.status_code == 200
        assert b"Invalid ISBN" in response.content
        assert db.execute("SELECT COUNT(*) FROM items").fetchone()[0] == before

    def test_manual_add_rejects_bad_isbn10_without_inserting(self, editor_client, db):
        before = db.execute("SELECT COUNT(*) FROM items").fetchone()[0]
        response = editor_client.post(
            "/api/items/manual",
            data={"title": "Bad ISBN-10", "isbn": BAD_ISBN10, "media_type": "book"},
        )
        assert response.status_code == 200
        assert b"Invalid ISBN" in response.content
        assert db.execute("SELECT COUNT(*) FROM items").fetchone()[0] == before

    def test_scan_rejects_bad_checksum_before_lookup_or_insert(self, admin_client, db):
        before = db.execute("SELECT COUNT(*) FROM items").fetchone()[0]
        with patch(
            "app.routers.items.items_common._lookup_metadata",
            new=AsyncMock(side_effect=AssertionError("lookup must not run")),
        ):
            response = admin_client.post(
                "/api/scan",
                data={"isbn": BAD_ISBN13, "media_type": "book", "mode": "add"},
            )
        assert response.status_code == 200
        assert b'data-scan-status="error"' in response.content
        assert db.execute("SELECT COUNT(*) FROM items").fetchone()[0] == before

    def test_title_search_add_rejects_bad_checksum(self, editor_client, db):
        before = db.execute("SELECT COUNT(*) FROM items").fetchone()[0]
        response = editor_client.post(
            "/api/books/add",
            data={"isbn": BAD_ISBN13, "media_type": "book"},
        )
        assert response.status_code == 200
        assert b"Invalid ISBN" in response.content
        assert db.execute("SELECT COUNT(*) FROM items").fetchone()[0] == before

    def test_store_queue_rejects_bad_checksum(self, editor_client, db):
        before = db.execute("SELECT COUNT(*) FROM items").fetchone()[0]
        response = editor_client.post(
            "/api/store/queue", json={"isbns": [BAD_ISBN13]}
        )
        assert response.status_code == 200
        assert response.json()["results"] == [
            {"isbn": BAD_ISBN13, "status": "invalid"}
        ]
        assert db.execute("SELECT COUNT(*) FROM items").fetchone()[0] == before


class TestCanonicalRouteWrites:
    def test_manual_isbn10_input_stores_canonical_pair(self, editor_client, db):
        with patch(
            "app.routers.items.covers.download_cover", new=AsyncMock(return_value=None)
        ):
            response = editor_client.post(
                "/api/items/manual",
                data={"title": "The Hobbit", "isbn": HOBBIT_ISBN10, "media_type": "book"},
            )
        assert response.status_code == 200
        row = db.execute(
            "SELECT isbn, isbn10 FROM items WHERE title = 'The Hobbit'"
        ).fetchone()
        assert (row["isbn"], row["isbn10"]) == (HOBBIT_ISBN13, HOBBIT_ISBN10)

    def test_editing_to_isbn10_replaces_both_identifier_fields(self, editor_client, db):
        item_id = _committed_item(
            db, title="Changed edition", isbn=DUNE_ISBN13, isbn10=DUNE_ISBN10
        )
        response = editor_client.post(
            f"/api/items/{item_id}", data={"isbn": HOBBIT_ISBN10}
        )
        assert response.status_code == 200
        assert _stored_pair(db, item_id) == (HOBBIT_ISBN13, HOBBIT_ISBN10)

    def test_editing_to_978_isbn13_derives_matching_isbn10(self, editor_client, db):
        item_id = _committed_item(db, title="Dune", isbn=None, isbn10=None)
        response = editor_client.post(
            f"/api/items/{item_id}", data={"isbn": DUNE_ISBN13}
        )
        assert response.status_code == 200
        assert _stored_pair(db, item_id) == (DUNE_ISBN13, DUNE_ISBN10)

    def test_editing_to_979_clears_stale_isbn10(self, editor_client, db):
        item_id = _committed_item(
            db, title="Modern ISBN", isbn=DUNE_ISBN13, isbn10=DUNE_ISBN10
        )
        response = editor_client.post(
            f"/api/items/{item_id}", data={"isbn": ISBN979}
        )
        assert response.status_code == 200
        assert _stored_pair(db, item_id) == (ISBN979, None)

    def test_clearing_isbn_clears_companion_field(self, editor_client, db):
        item_id = _committed_item(
            db, title="No identifier", isbn=DUNE_ISBN13, isbn10=DUNE_ISBN10
        )
        response = editor_client.post(f"/api/items/{item_id}", data={"isbn": ""})
        assert response.status_code == 200
        assert _stored_pair(db, item_id) == (None, None)

    def test_invalid_edit_is_rejected_without_changing_existing_pair(self, editor_client, db):
        item_id = _committed_item(
            db, title="Keep valid pair", isbn=DUNE_ISBN13, isbn10=DUNE_ISBN10
        )
        response = editor_client.post(
            f"/api/items/{item_id}", data={"isbn": BAD_ISBN13}
        )
        assert response.status_code == 400
        assert response.text == "Invalid ISBN"
        assert _stored_pair(db, item_id) == (DUNE_ISBN13, DUNE_ISBN10)
