import re


def normalize_isbn(isbn: str) -> str:
    return re.sub(r"[^0-9X]", "", isbn.upper())


def isbn10_to_isbn13(isbn10: str) -> str | None:
    if len(isbn10) != 10:
        return None
    digits = "978" + isbn10[:9]
    check = sum(int(d) * (1 if i % 2 == 0 else 3) for i, d in enumerate(digits))
    check = (10 - (check % 10)) % 10
    return digits + str(check)


def to_isbn13(raw: str) -> str | None:
    isbn = normalize_isbn(raw)
    # UPC-A (12 digits) -> EAN-13 by prepending 0
    if len(isbn) == 12 and isbn.isdigit():
        isbn = "0" + isbn
    if len(isbn) == 13 and isbn.isdigit():
        return isbn
    if len(isbn) == 10:
        return isbn10_to_isbn13(isbn)
    return None


def validate_isbn10(s: str) -> bool:
    if not isinstance(s, str) or not s:
        return False
    isbn = normalize_isbn(s)
    if len(isbn) != 10:
        return False
    if not isbn[:9].isdigit():
        return False
    if not (isbn[9].isdigit() or isbn[9] == "X"):
        return False
    digits = [10 if c == "X" else int(c) for c in isbn]
    total = sum((10 - i) * d for i, d in enumerate(digits))
    return total % 11 == 0


def validate_isbn13(s: str) -> bool:
    if not isinstance(s, str) or not s:
        return False
    isbn = normalize_isbn(s)
    if len(isbn) != 13 or not isbn.isdigit():
        return False
    if not (isbn.startswith("978") or isbn.startswith("979")):
        return False
    total = sum(int(d) * (1 if i % 2 == 0 else 3) for i, d in enumerate(isbn))
    return total % 10 == 0


def canonicalize_isbn_pair(
    isbn: str | None, isbn10: str | None = None
) -> tuple[str | None, str | None]:
    """Return the canonical ``(isbn13, isbn10)`` pair for stored identifiers.

    ``to_isbn13`` deliberately remains permissive because barcode detection
    callers use its UPC-A to EAN-13 conversion.  Persistence paths need a
    stricter contract: only checksum-valid ISBNs may reach ``items.isbn`` and
    ``items.isbn10``, and the companion value is always derived rather than
    trusted from a caller.

    The primary ``isbn`` value wins when both fields are supplied.  An
    ``isbn10`` fallback is considered only when the primary value is empty;
    this prevents a stale companion from overriding an invalid or changed
    primary identifier.
    """
    candidate = isbn if isinstance(isbn, str) and isbn.strip() else isbn10
    if not isinstance(candidate, str) or not candidate.strip():
        return None, None

    normalized = normalize_isbn(candidate)
    if validate_isbn10(normalized):
        canonical13 = isbn10_to_isbn13(normalized)
        return canonical13, normalized
    if validate_isbn13(normalized):
        return normalized, isbn13_to_isbn10(normalized)
    return None, None


def isbn13_to_isbn10(isbn13: str) -> str | None:
    if len(isbn13) != 13 or not isbn13.startswith("978"):
        return None
    body = isbn13[3:12]
    total = sum(int(d) * (10 - i) for i, d in enumerate(body))
    check = (11 - (total % 11)) % 11
    check_char = "X" if check == 10 else str(check)
    return body + check_char
