"""Retry constants and backoff computation for HTTP scrapers."""

from __future__ import annotations

import random

import requests

MAX_RETRIES = 3
RETRY_BACKOFFS = [5, 15, 45]
MAX_RETRY_AFTER = 120.0


def compute_backoff(attempt: int, resp: requests.Response | None = None) -> float:
    """Compute backoff seconds with jitter. Honors Retry-After when present."""
    if resp is not None:
        retry_after = resp.headers.get("Retry-After")
        if retry_after:
            try:
                # Honour the server, within reason: a huge value must not stall a run.
                return min(float(retry_after), MAX_RETRY_AFTER)
            except ValueError:
                pass  # HTTP-date form — ignore, fall through
    base = RETRY_BACKOFFS[min(attempt, len(RETRY_BACKOFFS) - 1)]
    return base * (1 + random.random() * 0.3)
