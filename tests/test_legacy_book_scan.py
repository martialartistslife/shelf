"""End-to-end route regressions for legacy price-point UPC-A + 5 book scans."""

from unittest.mock import AsyncMock, patch

from app.services import provider_result
from tests.conftest import _insert_item


KRISTY_UPC5 = "07807300350143506"
KRISTY_ISBN13 = "9780590435062"
OTHER_CANDIDATE = "9780439435062"


def _metadata(title: str) -> dict:
    return {"title": title, "authors": "Ann M. Martin"}


class TestLegacyBookScan:
    def test_unique_candidate_resolves_and_saves_correct_isbn(self, admin_client, db):
        correct = _metadata("Kristy and the Mother's Day Surprise")

        async def lookup(isbn, hc_token, client, *, google_api_key=None):
            if isbn == KRISTY_ISBN13:
                return (
                    correct,
                    "openlibrary",
                    {},
                    provider_result.found("openlibrary", correct),
                )
            assert isbn == OTHER_CANDIDATE
            return None, "manual", {}, provider_result.no_match("openlibrary")

        lookup_mock = AsyncMock(side_effect=lookup)
        with patch("app.routers.items_common._lookup_metadata", new=lookup_mock), \
             patch("app.routers.items.cover_queue.enqueue"):
            resp = admin_client.post("/api/scan", data={
                "isbn": KRISTY_UPC5,
                "media_type": "book",
                "mode": "add",
            })

        assert resp.status_code == 200
        assert "Kristy and the Mother&#39;s Day Surprise" in resp.text or \
               "Kristy and the Mother's Day Surprise" in resp.text
        assert lookup_mock.await_count == 2

        row = db.execute(
            "SELECT title, isbn, isbn10 FROM items WHERE isbn = ?",
            (KRISTY_ISBN13,),
        ).fetchone()
        assert row is not None
        assert row["title"] == "Kristy and the Mother's Day Surprise"
        assert row["isbn"] == KRISTY_ISBN13
        assert row["isbn10"] == "059043506X"

    def test_two_verified_candidates_are_rejected_as_ambiguous(self, admin_client, db):
        async def lookup(isbn, hc_token, client, *, google_api_key=None):
            metadata = _metadata(f"Candidate {isbn}")
            return (
                metadata,
                "openlibrary",
                {},
                provider_result.found("openlibrary", metadata),
            )

        with patch(
            "app.routers.items_common._lookup_metadata",
            new=AsyncMock(side_effect=lookup),
        ), patch("app.routers.items.cover_queue.enqueue") as enqueue:
            resp = admin_client.post("/api/scan", data={
                "isbn": KRISTY_UPC5,
                "media_type": "book",
                "mode": "add",
            })

        assert resp.status_code == 200
        assert "matches more than one book" in resp.text
        assert "scan the printed ISBN" in resp.text
        enqueue.assert_not_called()
        assert db.execute(
            "SELECT id FROM items WHERE isbn IN (?, ?)",
            (KRISTY_ISBN13, OTHER_CANDIDATE),
        ).fetchone() is None

    def test_unchecked_candidate_prevents_a_confident_guess(self, admin_client, db):
        correct = _metadata("Kristy and the Mother's Day Surprise")

        async def lookup(isbn, hc_token, client, *, google_api_key=None):
            if isbn == KRISTY_ISBN13:
                return (
                    correct,
                    "openlibrary",
                    {},
                    provider_result.found("openlibrary", correct),
                )
            return (
                None,
                "manual",
                {},
                provider_result.transport_failed("openlibrary"),
            )

        with patch(
            "app.routers.items_common._lookup_metadata",
            new=AsyncMock(side_effect=lookup),
        ), patch("app.routers.items.cover_queue.enqueue") as enqueue:
            resp = admin_client.post("/api/scan", data={
                "isbn": KRISTY_UPC5,
                "media_type": "book",
                "mode": "add",
            })

        assert resp.status_code == 200
        assert "Couldn" in resp.text
        assert "safely verify this older book barcode" in resp.text
        enqueue.assert_not_called()
        assert db.execute(
            "SELECT id FROM items WHERE isbn = ?", (KRISTY_ISBN13,)
        ).fetchone() is None

    def test_lookup_mode_finds_existing_book_from_legacy_barcode(self, admin_client, db):
        _insert_item(
            db,
            title="Kristy and the Mother's Day Surprise",
            isbn=KRISTY_ISBN13,
        )
        db.commit()

        with patch("app.routers.items_common._lookup_metadata") as lookup:
            resp = admin_client.post("/api/scan", data={
                "isbn": KRISTY_UPC5,
                "mode": "lookup",
            })

        assert resp.status_code == 200
        assert b"found" in resp.content
        assert b"Kristy" in resp.content
        lookup.assert_not_called()
