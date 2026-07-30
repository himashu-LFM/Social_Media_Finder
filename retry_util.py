"""
retry_util.py  —  Shared HTTP retry/backoff for external APIs
=============================================================

A single helper so Serper, OpenAI and Apify calls survive transient failures
and rate limits (HTTP 429 / 5xx / timeouts) instead of silently degrading a row
to "Not Found". Uses exponential backoff and honours a Retry-After header when
present. Concurrency is what makes rate limits likely, so this is the safety net
that keeps parallel runs accurate.
"""

from __future__ import annotations

import time
from typing import Any

import requests
from requests.adapters import HTTPAdapter

# Status codes worth retrying (transient / rate limit).
_RETRY_STATUS = {429, 500, 502, 503, 504}

# Shared pooled session so concurrent calls REUSE keep-alive connections instead
# of opening a fresh TLS handshake each time. A burst of ~20 simultaneous new
# handshakes is what triggered the SSL "UNEXPECTED_EOF" resets; pooling plus the
# per-service concurrency caps (see serper_service / wikidata_lookup) prevents it.
_SESSION = requests.Session()
_adapter = HTTPAdapter(pool_connections=16, pool_maxsize=16, max_retries=0)
_SESSION.mount("https://", _adapter)
_SESSION.mount("http://", _adapter)


def request_with_retry(
    method: str,
    url: str,
    *,
    retries: int = 4,
    backoff: float = 2.0,
    timeout: float = 30,
    **kwargs: Any,
) -> requests.Response:
    """
    Perform an HTTP request with exponential backoff on transient errors.

    Retries on connection errors, timeouts, and 429/5xx responses. Raises the
    last error only after exhausting retries; otherwise returns the Response
    (which the caller still checks / raises for status as usual).
    """
    last_exc: Exception | None = None
    response: requests.Response | None = None

    for attempt in range(retries + 1):
        try:
            response = _SESSION.request(method, url, timeout=timeout, **kwargs)
            if response.status_code in _RETRY_STATUS and attempt < retries:
                _sleep(response, attempt, backoff)
                continue
            return response
        except (requests.Timeout, requests.ConnectionError) as exc:
            # Includes SSLError / connection resets — retry with exponential backoff.
            last_exc = exc
            if attempt < retries:
                time.sleep(min(backoff ** attempt, 30))
                continue
            raise

    if response is not None:
        return response
    assert last_exc is not None
    raise last_exc


def _sleep(response: requests.Response, attempt: int, backoff: float) -> None:
    """Back off, respecting a Retry-After header when the server sends one."""
    retry_after = response.headers.get("Retry-After")
    delay = backoff ** attempt
    if retry_after:
        try:
            delay = max(delay, float(retry_after))
        except ValueError:
            pass
    time.sleep(min(delay, 30))  # never wait more than 30s per attempt
