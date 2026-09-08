"""Retry/backoff utilities for transient external failures (Phase 5.3).

- Exponential backoff with configurable base/max, bounded, no tight loops
- Distinguishes transient vs permanent errors (no infinite retry for deterministic failures)
- Central config via config.MAX_RETRIES etc., no new dependency
"""
import time
import logging
from typing import Callable, Type, Tuple, Any

import config

log = logging.getLogger(__name__)

# Permanent errors that should NOT be retried (deterministic, will always fail)
# These are imported lazily to avoid circular imports, but we check by name/message
PERMANENT_ERROR_SUBSTRINGS = [
    "EmptyLinkError",
    "InvalidLinkError",
    "Invalid YouTube URL",
    "File not found",
    "Not a file",
    "TranscriptNotFoundError",
    "No transcript",
    "Subtitles are disabled",
    "private_key",
    "invalid credentials",
    "Invalid API key",
    "API key not set",
    "GEMINI_API_KEY not set",
    "OPENAI_API_KEY not set",
    "No valid Google credentials",
    "No Drive OAuth client",
    "appears to be a service-account JSON, not OAuth",
]

TRANSIENT_SUBSTRINGS = [
    "429",
    "Too Many Requests",
    "rateLimitExceeded",
    "quotaExceeded",
    "500",
    "502",
    "503",
    "504",
    "Internal Server Error",
    "Bad Gateway",
    "Service Unavailable",
    "Gateway Timeout",
    "timeout",
    "Timeout",
    "ConnectionError",
    "Connection reset",
    "NetworkError",
    "network failure",
    "temporary",
    "transient",
    "HttpError 429",
    "HttpError 500",
    "HttpError 502",
    "HttpError 503",
    "HttpError 504",
]

def is_transient_error(exc: Exception) -> bool:
    """Return True if error is likely transient and worth retrying."""
    name = type(exc).__name__
    msg = str(exc)

    # Check for permanent first (explicit)
    for sub in PERMANENT_ERROR_SUBSTRINGS:
        if sub.lower() in msg.lower() or sub.lower() in name.lower():
            # But check if also contains transient marker that overrides permanent
            # e.g., "InvalidLink" is permanent even if message contains "timeout" word, but we treat InvalidLink as permanent
            # So permanent takes precedence
            return False

    # Check transient markers
    for sub in TRANSIENT_SUBSTRINGS:
        if sub.lower() in msg.lower() or sub.lower() in name.lower():
            return True

    # Check by exception type for known transient types
    transient_types = ("Timeout", "ConnectionError", "HttpError", "APIError", "ServiceUnavailable", "RateLimit")
    for t in transient_types:
        if t.lower() in name.lower():
            return True

    # Default: treat as permanent to avoid hammering
    return False

def get_retry_delay(attempt: int) -> float:
    """Exponential backoff: base * 2^attempt, bounded by max."""
    base = float(config.RETRY_BASE_DELAY_SECONDS)
    max_delay = float(config.RETRY_MAX_DELAY_SECONDS)
    # attempt 0 => base, 1 => 2*base, 2 => 4*base, etc.
    delay = base * (2 ** attempt)
    if delay > max_delay:
        delay = max_delay
    return delay

def retry_operation(
    func: Callable[[], Any],
    max_retries: int = None,
    is_transient: Callable[[Exception], bool] = None,
    operation_name: str = "operation",
) -> Any:
    """Execute func with retry for transient failures.

    Args:
        func: callable with no args that performs the operation
        max_retries: overrides config.MAX_RETRIES if set
        is_transient: custom function to decide if error is transient
        operation_name: for logging

    Returns:
        result of func

    Raises:
        last exception if all retries exhausted or error is permanent
    """
    if max_retries is None:
        max_retries = int(config.MAX_RETRIES)
    if is_transient is None:
        is_transient = is_transient_error

    last_exc = None
    for attempt in range(max_retries + 1):
        try:
            return func()
        except Exception as e:
            last_exc = e
            # Check if should retry
            if not is_transient(e):
                log.info("Retry: %s failed with permanent error, not retrying: %s: %s", operation_name, type(e).__name__, e)
                raise
            if attempt >= max_retries:
                log.warning("Retry: %s failed after %d retries (transient): %s: %s", operation_name, max_retries, type(e).__name__, e)
                raise
            delay = get_retry_delay(attempt)
            log.info("Retry: %s transient failure (attempt %d/%d): %s: %s — retrying in %.1fs", operation_name, attempt + 1, max_retries, type(e).__name__, e, delay)
            time.sleep(delay)
    # Should not reach here, but raise last
    if last_exc:
        raise last_exc
    raise RuntimeError(f"Retry logic error for {operation_name}")

def should_retry_error(exc: Exception) -> bool:
    """Public helper for tests and callers."""
    return is_transient_error(exc)
