"""Regression coverage for legacy price-point UPC-A + 5 book barcodes."""

from app.services import legacy_book
from app.services import upc as upc_svc


# Real Scholastic copy supplied from the collection:
# UPC 0 78073 00350 1 + title supplement 43506
# printed ISBN 0-590-43506-X.
KRISTY_UPC5 = "07807300350143506"


def test_upca_validator_uses_standard_odd_position_weighting():
    # Both are published UPC-A examples with valid check digits. This pins the
    # weighting itself rather than only testing it indirectly through the
    # legacy parser.
    assert upc_svc.validate_upc("078073003501")
    assert upc_svc.validate_upc("036000291452")
    assert not upc_svc.validate_upc("078073003502")


def test_real_scholastic_upc5_generates_expected_isbn_candidate():
    parsed = legacy_book.parse(KRISTY_UPC5)
    assert parsed is not None
    assert parsed.upc == "078073003501"
    assert parsed.supplement == "43506"
    assert parsed.isbn10_prefixes == ("0590", "0439")
    assert legacy_book.isbn13_candidates(KRISTY_UPC5) == (
        "9780590435062",
        "9780439435062",
    )


def test_formatting_between_upc_and_supplement_is_accepted():
    assert legacy_book.isbn13_candidates("0 78073 00350 1 + 43506") == (
        "9780590435062",
        "9780439435062",
    )


def test_zero_padded_ean13_scanner_representation_is_accepted():
    assert legacy_book.isbn13_candidates("007807300350143506") == (
        "9780590435062",
        "9780439435062",
    )


def test_zero_padded_scanner_form_shares_the_same_mapping_key():
    assert legacy_book.mapping_key(KRISTY_UPC5) == KRISTY_UPC5
    assert legacy_book.mapping_key("007807300350143506") == KRISTY_UPC5


def test_arbitrary_ean13_plus_supplement_is_not_reinterpreted_as_upca():
    assert legacy_book.parse("107807300350143506") is None
    assert legacy_book.mapping_key("107807300350143506") is None


def test_invalid_upc_check_digit_is_not_treated_as_legacy_book():
    assert legacy_book.parse("07807300350243506") is None
    assert legacy_book.isbn13_candidates("07807300350243506") == ()


def test_unknown_publisher_prefix_is_not_guessed():
    # Valid UPC-A followed by a five-digit supplement, but no verified
    # publisher mapping. Refusing to guess is part of the contract.
    assert legacy_book.isbn13_candidates("03600029145243506") == ()
