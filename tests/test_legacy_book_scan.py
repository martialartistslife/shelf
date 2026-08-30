"""End-to-end route regressions for legacy price-point UPC-A + 5 book scans."""

from unittest.mock import AsyncMock, patch

import pytest

from app.services import provider_result
from tests.conftest import _insert_borrower, _insert_item, _insert_location


KRISTY_UPC5 = "07807300350143506"
KRISTY_ISBN13 = "9780590435062"
OTHER_CANDIDATE = "9780439435062"
KRISTY_EAN13_PLUS5 = "007807300350143506"

# Real-world ambiguity discovered during physical acceptance testing:
# Hound at the Hospital carries UPC 0 78073 00399 0 + supplement 44891 and
# printed ISBN 0-439-44891-3. The same supplement also forms a real 0-590 ISBN,
# so the barcode cannot be safely resolved by prefix preference.
HOUND_UPC5 = "07807300399044891"
HOUND_EAN13_PLUS5 = "007807300399044891"
HOUND_ISBN13 = "9780439448918"
HOUND_OTHER_ISBN13 = "9780590448918"


def _metadata(title: str) -> dict:
    return {"title": title, "authors": "Ann M. Martin"}


def _hound_lookup_result(isbn: str):
    titles = {
        HOUND_ISBN13: "Hound at the Hospital",
        HOUND_OTHER_ISBN13: "101 Wacky Facts About Snakes & Reptiles",
    }
    metadata = {"title": titles[isbn], "authors": "Scholastic author"}
    return (
        metadata,
        "openlibrary",
        {},
        provider_result.found("openlibrary", metadata),
    )


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

    def test_two_verified_candidates_offer_explicit_choices(self, admin_client, db):
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
        assert "Which book is this?" in resp.text
        assert "matches more than one book" in resp.text
        assert "scan the printed ISBN" in resp.text
        assert KRISTY_ISBN13 in resp.text
        assert OTHER_CANDIDATE in resp.text
        enqueue.assert_not_called()
        assert db.execute(
            "SELECT id FROM items WHERE isbn IN (?, ?)",
            (KRISTY_ISBN13, OTHER_CANDIDATE),
        ).fetchone() is None

    def test_real_hound_ambiguity_can_be_confirmed_and_is_remembered(
        self, admin_client, db
    ):
        async def lookup(isbn, hc_token, client, *, google_api_key=None):
            return _hound_lookup_result(isbn)

        lookup_mock = AsyncMock(side_effect=lookup)
        with patch("app.routers.items_common._lookup_metadata", new=lookup_mock), \
             patch("app.routers.items.cover_queue.enqueue"):
            first = admin_client.post("/api/scan", data={
                "isbn": HOUND_UPC5,
                "media_type": "book",
                "mode": "add",
            })
            assert first.status_code == 200
            assert "Which book is this?" in first.text
            assert "Hound at the Hospital" in first.text
            assert "101 Wacky Facts About Snakes" in first.text
            assert "0439448913" in first.text

            mapping = db.execute(
                "SELECT isbn13 FROM legacy_book_mappings WHERE barcode = ?",
                (HOUND_UPC5,),
            ).fetchone()
            assert mapping is None

            confirmed = admin_client.post("/api/scan", data={
                "isbn": HOUND_UPC5,
                "media_type": "book",
                "mode": "add",
                "legacy_confirm_isbn13": HOUND_ISBN13,
            })

        assert confirmed.status_code == 200
        assert "Hound at the Hospital" in confirmed.text
        assert lookup_mock.await_count == 4

        row = db.execute(
            "SELECT title, isbn, isbn10 FROM items WHERE isbn = ?",
            (HOUND_ISBN13,),
        ).fetchone()
        assert row is not None
        assert row["title"] == "Hound at the Hospital"
        assert row["isbn"] == HOUND_ISBN13
        assert row["isbn10"] == "0439448913"

        mapping = db.execute(
            "SELECT barcode, isbn13 FROM legacy_book_mappings WHERE barcode = ?",
            (HOUND_UPC5,),
        ).fetchone()
        assert mapping is not None
        assert mapping["barcode"] == HOUND_UPC5
        assert mapping["isbn13"] == HOUND_ISBN13

        # The exact leading-zero EAN-13 + 5 scanner representation shares the
        # learned mapping and therefore needs no metadata-provider call.
        no_lookup = AsyncMock(side_effect=AssertionError("mapping should win"))
        with patch("app.routers.items_common._lookup_metadata", new=no_lookup):
            remembered = admin_client.post("/api/scan", data={
                "isbn": HOUND_EAN13_PLUS5,
                "mode": "lookup",
            })

        assert remembered.status_code == 200
        assert "Hound at the Hospital" in remembered.text
        assert b"found" in remembered.content
        no_lookup.assert_not_awaited()

    def test_unverified_confirmation_cannot_create_a_mapping(self, admin_client, db):
        async def lookup(isbn, hc_token, client, *, google_api_key=None):
            return _hound_lookup_result(isbn)

        with patch(
            "app.routers.items_common._lookup_metadata",
            new=AsyncMock(side_effect=lookup),
        ):
            resp = admin_client.post("/api/scan", data={
                "isbn": HOUND_UPC5,
                "media_type": "book",
                "mode": "add",
                "legacy_confirm_isbn13": KRISTY_ISBN13,
            })

        assert resp.status_code == 200
        assert "Which book is this?" in resp.text
        assert db.execute(
            "SELECT isbn13 FROM legacy_book_mappings WHERE barcode = ?",
            (HOUND_UPC5,),
        ).fetchone() is None
        assert db.execute(
            "SELECT id FROM items WHERE isbn = ?", (KRISTY_ISBN13,)
        ).fetchone() is None

    def test_stale_confirmation_cannot_switch_to_a_different_unique_match(
        self, admin_client, db
    ):
        async def lookup(isbn, hc_token, client, *, google_api_key=None):
            if isbn == HOUND_OTHER_ISBN13:
                return _hound_lookup_result(isbn)
            return None, "manual", {}, provider_result.no_match("openlibrary")

        with patch(
            "app.routers.items_common._lookup_metadata",
            new=AsyncMock(side_effect=lookup),
        ), patch("app.routers.items.cover_queue.enqueue") as enqueue:
            resp = admin_client.post("/api/scan", data={
                "isbn": HOUND_UPC5,
                "media_type": "book",
                "mode": "add",
                "legacy_confirm_isbn13": HOUND_ISBN13,
            })

        assert resp.status_code == 200
        assert "Couldn" in resp.text
        assert "safely verify the selected book" in resp.text
        enqueue.assert_not_called()
        assert db.execute(
            "SELECT isbn13 FROM legacy_book_mappings WHERE barcode = ?",
            (HOUND_UPC5,),
        ).fetchone() is None
        assert db.execute(
            "SELECT id FROM items WHERE isbn IN (?, ?)",
            (HOUND_ISBN13, HOUND_OTHER_ISBN13),
        ).fetchone() is None

    def test_selected_candidate_can_be_learned_after_it_becomes_unique(
        self, admin_client, db
    ):
        async def lookup(isbn, hc_token, client, *, google_api_key=None):
            if isbn == HOUND_ISBN13:
                return _hound_lookup_result(isbn)
            return None, "manual", {}, provider_result.no_match("openlibrary")

        with patch(
            "app.routers.items_common._lookup_metadata",
            new=AsyncMock(side_effect=lookup),
        ), patch("app.routers.items.cover_queue.enqueue"):
            resp = admin_client.post("/api/scan", data={
                "isbn": HOUND_UPC5,
                "media_type": "book",
                "mode": "add",
                "legacy_confirm_isbn13": HOUND_ISBN13,
            })

        assert resp.status_code == 200
        assert "Hound at the Hospital" in resp.text
        mapping = db.execute(
            "SELECT isbn13 FROM legacy_book_mappings WHERE barcode = ?",
            (HOUND_UPC5,),
        ).fetchone()
        assert mapping is not None
        assert mapping["isbn13"] == HOUND_ISBN13

    @pytest.mark.parametrize(
        ("mode", "barcode", "expected_owned"),
        [
            ("add", HOUND_UPC5, 1),
            ("wishlist", HOUND_EAN13_PLUS5, 0),
        ],
    )
    def test_remembered_mapping_applies_to_creating_modes_without_candidate_recheck(
        self, admin_client, db, mode, barcode, expected_owned
    ):
        db.execute(
            "INSERT INTO legacy_book_mappings (barcode, isbn13) VALUES (?, ?)",
            (HOUND_UPC5, HOUND_ISBN13),
        )
        db.commit()

        async def lookup(isbn, hc_token, client, *, google_api_key=None):
            assert isbn == HOUND_ISBN13
            return _hound_lookup_result(isbn)

        lookup_mock = AsyncMock(side_effect=lookup)
        with patch("app.routers.items_common._lookup_metadata", new=lookup_mock), \
             patch("app.routers.items.cover_queue.enqueue"):
            resp = admin_client.post("/api/scan", data={
                "isbn": barcode,
                "media_type": "book",
                "mode": mode,
            })

        assert resp.status_code == 200
        assert "Hound at the Hospital" in resp.text
        assert lookup_mock.await_count == 1
        row = db.execute(
            "SELECT isbn, isbn10, owned FROM items WHERE isbn = ?",
            (HOUND_ISBN13,),
        ).fetchone()
        assert row is not None
        assert row["isbn"] == HOUND_ISBN13
        assert row["isbn10"] == "0439448913"
        assert row["owned"] == expected_owned

    def test_remembered_mapping_applies_to_every_existing_item_mode(
        self, admin_client, db
    ):
        home = _insert_location(db, "Home")
        target = _insert_location(db, "Target")
        borrower = _insert_borrower(db, "Reader")
        item_id = _insert_item(
            db,
            title="Hound at the Hospital",
            isbn=HOUND_ISBN13,
            location_id=home,
        )
        db.execute(
            "INSERT INTO legacy_book_mappings (barcode, isbn13) VALUES (?, ?)",
            (HOUND_UPC5, HOUND_ISBN13),
        )
        db.commit()

        no_lookup = AsyncMock(side_effect=AssertionError("mapping should win"))
        with patch("app.routers.items_common._lookup_metadata", new=no_lookup):
            lend = admin_client.post("/api/scan", data={
                "isbn": HOUND_UPC5,
                "mode": "lend",
                "borrower_id": str(borrower),
            })
            returned = admin_client.post("/api/scan", data={
                "isbn": HOUND_EAN13_PLUS5,
                "mode": "return",
            })
            moved = admin_client.post("/api/scan", data={
                "isbn": HOUND_UPC5,
                "mode": "move",
                "location_id": str(target),
            })
            inventoried = admin_client.post("/api/scan", data={
                "isbn": HOUND_EAN13_PLUS5,
                "mode": "inventory",
                "location_id": str(home),
            })
            looked_up = admin_client.post("/api/scan", data={
                "isbn": HOUND_UPC5,
                "mode": "lookup",
            })
            rated = admin_client.post("/api/scan", data={
                "isbn": HOUND_EAN13_PLUS5,
                "mode": "quick_rate",
            })

        assert "Lent to Reader" in lend.text
        assert "Returned from Reader" in returned.text
        assert b"moved" in moved.content
        assert b"relocated" in inventoried.content
        assert "Hound at the Hospital" in looked_up.text
        assert "Marked as read" in rated.text
        no_lookup.assert_not_awaited()

        row = db.execute(
            "SELECT location_id, reading_status, date_finished FROM items WHERE id = ?",
            (item_id,),
        ).fetchone()
        assert row["location_id"] == home
        assert row["reading_status"] == "read"
        assert row["date_finished"] is not None
        checkout = db.execute(
            "SELECT checked_in FROM checkouts WHERE item_id = ?", (item_id,)
        ).fetchone()
        assert checkout is not None
        assert checkout["checked_in"] is not None

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

        with patch(
            "app.routers.items_common._lookup_metadata",
            new=AsyncMock(side_effect=lookup),
        ), patch("app.routers.items.cover_queue.enqueue") as confirm_enqueue:
            confirmed = admin_client.post("/api/scan", data={
                "isbn": KRISTY_UPC5,
                "media_type": "book",
                "mode": "add",
                "legacy_confirm_isbn13": KRISTY_ISBN13,
            })

        assert "safely verify the selected book" in confirmed.text
        confirm_enqueue.assert_not_called()
        assert db.execute(
            "SELECT isbn13 FROM legacy_book_mappings WHERE barcode = ?",
            (KRISTY_UPC5,),
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
