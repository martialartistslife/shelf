"""Shared safety boundary for optional metadata-provider calls."""

import logging

logger = logging.getLogger(__name__)


async def safe_lookup(provider: str, isbn: str, operation):
    """Return a provider result while keeping failures inside the cascade."""
    try:
        return await operation()
    except Exception:
        logger.warning("%s metadata lookup failed for ISBN %s", provider, isbn, exc_info=True)
        return None
