"""Legacy price-point UPC-A + 5 title-supplement support.

Before Bookland EAN became the sole recommended book barcode, mass-market
paperbacks and some juvenile books could carry a 12-digit *price-point* UPC.
That main UPC identified a publisher and price, not an individual title.  A
separate five-digit supplement supplied the publisher's title number.

That distinction matters here: treating the first 12 digits as an ordinary
UPC can confidently resolve the wrong book because many titles legitimately
shared it.  This module only turns a 17-digit scan into *candidate* ISBNs.  A
caller must verify the candidates against metadata and accept a result only
when it can choose one safely.

The publisher map is intentionally small and evidence-driven.  Add a company
prefix only after confirming how that publisher derived its five-digit title
supplement; guessing here recreates the exact wrong-book failure this module
exists to prevent.
"""

from dataclasses import dataclass

from app.services import isbn as isbn_svc
from app.services import upc as upc_svc


@dataclass(frozen=True, slots=True)
class LegacyBookBarcode:
    upc: str
    supplement: str
    isbn10_prefixes: tuple[str, ...]


# Scholastic's legacy price-point UPC company prefix.  Surviving examples from
# both 0-590 and 0-439 ISBN eras use the five-digit supplement as the final
# five data digits of the ISBN-10.  0-545 is deliberately absent: examples
# carrying that later Scholastic prefix use item-specific UPCs after the
# industry transition, not the legacy UPC+5 price-point form handled here.
_PUBLISHER_PREFIXES: dict[str, tuple[str, ...]] = {
    "078073": ("0590", "0439"),
}


def parse(raw: str) -> LegacyBookBarcode | None:
    """Parse a supported 12-digit price-point UPC plus five-digit supplement."""
    digits = upc_svc.normalize_barcode(raw)
    if len(digits) != 17:
        return None

    upc = digits[:12]
    supplement = digits[12:]
    if not upc_svc.validate_upc(upc):
        return None

    prefixes = _PUBLISHER_PREFIXES.get(upc[:6])
    if not prefixes:
        return None

    return LegacyBookBarcode(upc=upc, supplement=supplement, isbn10_prefixes=prefixes)


def _isbn10_check_digit(body9: str) -> str:
    """Return the ISBN-10 check character for exactly nine numeric digits."""
    total = sum(int(digit) * (10 - index) for index, digit in enumerate(body9))
    check = (11 - (total % 11)) % 11
    return "X" if check == 10 else str(check)


def isbn13_candidates(raw: str) -> tuple[str, ...]:
    """Return checksum-valid ISBN-13 candidates for a supported legacy scan.

    Candidate generation is deterministic, but candidate *selection* is not:
    one publisher company prefix can span more than one ISBN registrant
    prefix.  The scan route therefore asks the normal metadata cascade about
    every candidate and refuses to guess if the answer is not unique.
    """
    barcode = parse(raw)
    if barcode is None:
        return ()

    candidates: list[str] = []
    for prefix in barcode.isbn10_prefixes:
        body9 = prefix + barcode.supplement
        if len(body9) != 9 or not body9.isdigit():
            continue
        isbn10 = body9 + _isbn10_check_digit(body9)
        if not isbn_svc.validate_isbn10(isbn10):
            continue
        isbn13 = isbn_svc.isbn10_to_isbn13(isbn10)
        if isbn13 and isbn13 not in candidates:
            candidates.append(isbn13)

    return tuple(candidates)
