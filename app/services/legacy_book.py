"""Legacy price-point UPC-A + 5 title-supplement support.

Before Bookland EAN became the sole recommended book barcode, mass-market
paperbacks and some juvenile books could carry a 12-digit *price-point* UPC.
That main UPC identified a publisher and price, not an individual title. A
separate five-digit supplement supplied the publisher's title number.

That distinction matters here: treating the first 12 digits as an ordinary
UPC can confidently resolve the wrong book because many titles legitimately
shared it. This module turns a supported 17-digit scan into candidate ISBNs,
then can verify those candidates through the caller's normal metadata lookup.
It never chooses between multiple plausible books by guesswork.

The publisher map is intentionally small and evidence-driven. Add a company
prefix only after confirming how that publisher derived its five-digit title
supplement; guessing here recreates the exact wrong-book failure this module
exists to prevent.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal

from app.services import isbn as isbn_svc
from app.services import upc as upc_svc


@dataclass(frozen=True, slots=True)
class LegacyBookBarcode:
    upc: str
    supplement: str
    isbn10_prefixes: tuple[str, ...]


ResolutionOutcome = Literal[
    "not_legacy",
    "found",
    "not_found",
    "ambiguous",
    "inconclusive",
]


@dataclass(frozen=True, slots=True)
class LegacyBookResolution:
    outcome: ResolutionOutcome
    candidates: tuple[str, ...] = ()
    isbn13: str | None = None
    metadata: dict[str, Any] | None = None
    source: str = "manual"
    hc_ids: dict[str, Any] | None = None
    cascade: Any = None


# Scholastic's legacy price-point UPC company prefix. Surviving examples from
# both 0-590 and 0-439 ISBN eras use the five-digit supplement as the final
# five data digits of the ISBN-10. 0-545 is deliberately absent: examples
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
    prefix. The scan route therefore asks the normal metadata cascade about
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


LookupResult = tuple[dict[str, Any] | None, str, dict[str, Any], Any]
Lookup = Callable[[str], Awaitable[LookupResult]]


async def resolve(raw: str, lookup: Lookup) -> LegacyBookResolution:
    """Verify a legacy barcode against metadata without ever guessing.

    Each generated ISBN candidate goes through the caller's ordinary book
    metadata cascade. Exactly one verified hit is required. A rejected key,
    rate limit, or transport failure on any non-winning candidate makes the
    whole decision inconclusive: Shelf must not call one candidate unique
    merely because it could not actually check another candidate.
    """
    candidates = isbn13_candidates(raw)
    if not candidates:
        return LegacyBookResolution("not_legacy")

    matches: list[tuple[str, dict[str, Any], str, dict[str, Any], Any]] = []
    inconclusive = False

    for candidate in candidates:
        metadata, source, hc_ids, cascade = await lookup(candidate)
        if metadata:
            matches.append((candidate, metadata, source, hc_ids, cascade))
            continue

        outcome = getattr(cascade, "outcome", "no_match")
        if outcome not in {"no_match", "no_credential"}:
            inconclusive = True

    if inconclusive:
        return LegacyBookResolution("inconclusive", candidates=candidates)
    if len(matches) > 1:
        return LegacyBookResolution("ambiguous", candidates=candidates)
    if not matches:
        return LegacyBookResolution("not_found", candidates=candidates)

    isbn13, metadata, source, hc_ids, cascade = matches[0]
    return LegacyBookResolution(
        "found",
        candidates=candidates,
        isbn13=isbn13,
        metadata=metadata,
        source=source,
        hc_ids=hc_ids,
        cascade=cascade,
    )
