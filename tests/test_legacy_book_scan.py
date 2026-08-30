"""End-to-end route regressions for legacy price-point UPC-A + 5 book scans."""

from unittest.mock import AsyncMock, patch

import pytest

from app.services import provider_result
from tests.conftest import _insert_borrower, _insert_item, _insert_location


KRISTY_UPC5 = "07807300350143506"
KRISTY_ISBN13 = "9780590435062"
OTHER_CANDIDATE = "9780439435062"
KRISTY_EAN13_PLUS5 = "007807300350143506"


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

    @pytest.mark.parametrize("barcode", [KRISTY_UPC5, KRISTY_EAN13_PLUS5])
    def test_lookup_mode_finds_uniquely_verified_existing_book(
        self, admin_client, db, barcode
    ):
        _insert_item(
            db,
            title="Kristy and the Mother's Day Surprise",
            isbn=KRISTY_ISBN13,
        )
        db.commit()

        async def lookup(isbn, hc_token, client, *, google_api_key=None):
            if isbn == KRISTY_ISBN13:
                metadata = _metadata("Kristy and the Mother's Day Surprise")
                return metadata, "openlibrary", {}, provider_result.found(
                    "openlibrary", metadata
                )
            return None, "manual", {}, provider_result.no_match("openlibrary")

        lookup_mock = AsyncMock(side_effect=lookup)
        with patch("app.routers.items_common._lookup_metadata", new=lookup_mock):
            resp = admin_client.post("/api/scan", data={
                "isbn": barcode,
                "mode": "lookup",
            })

        assert resp.status_code == 200
        assert b"found" in resp.content
        assert b"Kristy" in resp.content
        assert lookup_mock.await_count == 2

    def test_local_candidate_alone_is_not_identity_proof(self, admin_client, db):
        _insert_item(db, title="Locally owned candidate", isbn=KRISTY_ISBN13)
        db.commit()

        async def lookup(isbn, hc_token, client, *, google_api_key=None):
            metadata = _metadata(f"Candidate {isbn}")
            return metadata, "openlibrary", {}, provider_result.found(
                "openlibrary", metadata
            )

        with patch(
            "app.routers.items_common._lookup_metadata",
            new=AsyncMock(side_effect=lookup),
        ):
            resp = admin_client.post("/api/scan", data={
                "isbn": KRISTY_UPC5,
                "mode": "lookup",
            })

        assert "matches more than one book" in resp.text
        assert "scan the printed ISBN" in resp.text
        assert "Locally owned candidate" not in resp.text

    def test_ambiguous_barcode_cannot_trigger_any_existing_item_mode(
        self, admin_client, db
    ):
        item_id = _insert_item(
            db,
            title="Locally owned candidate",
            isbn=KRISTY_ISBN13,
            location_id=None,
        )
        borrower_id = _insert_borrower(db, "Reader")
        target_location = _insert_location(db, "Target")
        db.commit()

        async def lookup(isbn, hc_token, client, *, google_api_key=None):
            metadata = _metadata(f"Candidate {isbn}")
            return metadata, "openlibrary", {}, provider_result.found(
                "openlibrary", metadata
            )

        mode_data = {
            "lend": {"borrower_id": str(borrower_id)},
            "return": {},
            "move": {"location_id": str(target_location)},
            "inventory": {"location_id": str(target_location)},
            "lookup": {},
            "quick_rate": {},
        }
        lookup_mock = AsyncMock(side_effect=lookup)
        with patch("app.routers.items_common._lookup_metadata", new=lookup_mock):
            for mode, extra in mode_data.items():
                resp = admin_client.post(
                    "/api/scan",
                    data={"isbn": KRISTY_UPC5, "mode": mode, **extra},
                )
                assert "matches more than one book" in resp.text
                assert "scan the printed ISBN" in resp.text

        row = db.execute(
            "SELECT location_id, reading_status, date_finished FROM items WHERE id = ?",
            (item_id,),
        ).fetchone()
        assert row["location_id"] is None
        assert row["reading_status"] is None
        assert row["date_finished"] is None
        assert db.execute(
            "SELECT id FROM checkouts WHERE item_id = ?", (item_id,)
        ).fetchone() is None

    @pytest.mark.parametrize(
        "failure",
        [
            provider_result.transport_failed("openlibrary"),
            provider_result.rate_limited("openlibrary"),
            provider_result.rejected("openlibrary", status=401),
        ],
        ids=["network", "rate-limit", "rejected-credential"],
    )
    def test_incomplete_competing_candidate_blocks_existing_item_modes(
        self, admin_client, db, failure
    ):
        item_id = _insert_item(
            db,
            title="Kristy and the Mother's Day Surprise",
            isbn=KRISTY_ISBN13,
            location_id=None,
        )
        borrower_id = _insert_borrower(db, "Reader")
        target_location = _insert_location(db, "Target")
        db.commit()

        async def lookup(isbn, hc_token, client, *, google_api_key=None):
            if isbn == KRISTY_ISBN13:
                metadata = _metadata("Kristy and the Mother's Day Surprise")
                return metadata, "openlibrary", {}, provider_result.found(
                    "openlibrary", metadata
                )
            return None, "manual", {}, failure

        mode_data = {
            "lend": {"borrower_id": str(borrower_id)},
            "return": {},
            "move": {"location_id": str(target_location)},
            "inventory": {"location_id": str(target_location)},
            "lookup": {},
            "quick_rate": {},
        }
        lookup_mock = AsyncMock(side_effect=lookup)
        with patch("app.routers.items_common._lookup_metadata", new=lookup_mock):
            for mode, extra in mode_data.items():
                resp = admin_client.post(
                    "/api/scan",
                    data={"isbn": KRISTY_UPC5, "mode": mode, **extra},
                )
                assert "safely verify this older book barcode" in resp.text

        row = db.execute(
            "SELECT location_id, reading_status, date_finished FROM items WHERE id = ?",
            (item_id,),
        ).fetchone()
        assert row["location_id"] is None
        assert row["reading_status"] is None
        assert row["date_finished"] is None
        assert db.execute(
            "SELECT id FROM checkouts WHERE item_id = ?", (item_id,)
        ).fetchone() is None
