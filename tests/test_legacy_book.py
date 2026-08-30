"""Regression coverage for legacy price-point UPC-A + 5 book barcodes."""

from app.services import legacy_book


# Real Scholastic copy supplied from the collection:
# UPC 0 78073 00350 1 + title supplement 43506
# printed ISBN 0-590-43506-X.
KRISTY_UPC5 = "07807300350143506"


def test_real_scholastic_upc5_generates_expected_isbn_candidate():
    parsed = legacy_book.parse(KRISTY_UPC5)
    assert parsed is not None
    assert parsed.upc == "078073003501"
    assert parsed.supplement == "43506"
    assert legacy_book.isbn13_candidates(KRISTY_UPC5) == (
        "9780590435062",
        "9780439435062",
    )


def test_formatting_between_upc_and_supplement_is_accepted():
    assert legacy_book.isbn13_candidates("0 78073 00350 1 + 43506") == (
        "9780590435062",
        "9780439435062",
    )


def test_invalid_upc_check_digit_is_not_treated_as_legacy_book():
    assert legacy_book.parse("07807300350243506") is None
    assert legacy_book.isbn13_candidates("07807300350243506") == ()


def test_unknown_publisher_prefix_is_not_guessed():
    # Valid UPC-A followed by a five-digit supplement, but no verified
    # publisher mapping.  Refusing to guess is part of the contract.
    assert legacy_book.isbn13_candidates("03600029145243506") == ()
