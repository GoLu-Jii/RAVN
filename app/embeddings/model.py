"""MongoDB Atlas-hosted Voyage embedding client."""
from collections import deque
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from math import isfinite, sqrt
import os
from threading import Lock
from time import monotonic, sleep

import httpx

from app.embeddings.constants import EMBEDDING_DIMENSION
from app.weekly.config import WEEKLY_CONFIG, normalize_voyage_endpoint

VOYAGE_REQUESTS_PER_MINUTE = 3
VOYAGE_TOKENS_PER_MINUTE = 10_000
VOYAGE_MAX_REQUEST_TOKENS = 8_000
VOYAGE_RATE_WINDOW_SECONDS = 60.0
VOYAGE_MAX_INPUTS_PER_REQUEST = 64
_TOKEN_ESTIMATE_OVERHEAD = 16


class EmbeddingProviderError(RuntimeError):
    """A safe-to-report error from the hosted embedding provider."""

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


def estimate_embedding_tokens(text: str) -> int:
    """Conservatively estimate input tokens without downloading a local tokenizer."""
    return max(1, len(text.encode("utf-8"))) + _TOKEN_ESTIMATE_OVERHEAD


def split_embedding_batches(
    texts: list[str],
    *,
    max_tokens: int = VOYAGE_MAX_REQUEST_TOKENS,
    max_items: int = VOYAGE_MAX_INPUTS_PER_REQUEST,
) -> Iterator[list[str]]:
    """Split texts below a conservative per-request token estimate and item cap."""
    batch: list[str] = []
    batch_tokens = 0
    for text in texts:
        text_tokens = estimate_embedding_tokens(text)
        if text_tokens > max_tokens:
            raise EmbeddingProviderError(
                f"One embedding input is estimated at {text_tokens} tokens, above the "
                f"safe per-request limit of {max_tokens}."
            )
        if batch and (
            batch_tokens + text_tokens > max_tokens
            or len(batch) >= max_items
        ):
            yield batch
            batch = []
            batch_tokens = 0
        batch.append(text)
        batch_tokens += text_tokens
    if batch:
        yield batch


@dataclass
class _RateReservation:
    started_at: float
    tokens: int


class _VoyageRateLimiter:
    """Serialize per-process requests within the free-tier rolling rate window."""

    def __init__(
        self,
        *,
        clock: Callable[[], float] = monotonic,
        wait: Callable[[float], None] = sleep,
    ) -> None:
        self._clock = clock
        self._wait = wait
        self._lock = Lock()
        self._requests: deque[_RateReservation] = deque()

    def reserve(self, tokens: int) -> _RateReservation:
        if tokens > VOYAGE_TOKENS_PER_MINUTE:
            raise EmbeddingProviderError(
                f"Embedding request estimate {tokens} exceeds the "
                f"{VOYAGE_TOKENS_PER_MINUTE}-token per-minute limit."
            )

        while True:
            with self._lock:
                now = self._clock()
                while (
                    self._requests
                    and now - self._requests[0].started_at >= VOYAGE_RATE_WINDOW_SECONDS
                ):
                    self._requests.popleft()
                used_tokens = sum(request.tokens for request in self._requests)
                if (
                    len(self._requests) < VOYAGE_REQUESTS_PER_MINUTE
                    and used_tokens + tokens <= VOYAGE_TOKENS_PER_MINUTE
                ):
                    reservation = _RateReservation(now, tokens)
                    self._requests.append(reservation)
                    return reservation
                wait_seconds = max(
                    0.01,
                    self._requests[0].started_at + VOYAGE_RATE_WINDOW_SECONDS - now,
                )
            self._wait(wait_seconds)

    def reconcile(self, reservation: _RateReservation, actual_tokens: int | None) -> None:
        if actual_tokens is None:
            return
        with self._lock:
            reservation.tokens = max(0, actual_tokens)


_rate_limiter = _VoyageRateLimiter()


def _normalize_response(data: object, expected_count: int) -> list[list[float]]:
    if not isinstance(data, list) or len(data) != expected_count:
        raise EmbeddingProviderError("Voyage returned a different number of vectors than inputs.")
    if all(isinstance(item, dict) and isinstance(item.get("index"), int) for item in data):
        data = sorted(data, key=lambda item: item["index"])

    normalized = []
    try:
        vectors = [item["embedding"] for item in data]
        for vector in vectors:
            values = [float(value) for value in vector]
            if len(values) != EMBEDDING_DIMENSION:
                raise EmbeddingProviderError(
                    f"Voyage returned {len(values)} dimensions; expected {EMBEDDING_DIMENSION}."
                )
            if not all(isfinite(value) for value in values):
                raise EmbeddingProviderError("Voyage returned non-finite embedding values.")
            norm = sqrt(sum(value * value for value in values))
            if not isfinite(norm) or norm == 0:
                raise EmbeddingProviderError("Voyage returned a zero or invalid vector norm.")
            normalized.append([value / norm for value in values])
    except (KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, EmbeddingProviderError):
            raise
        raise EmbeddingProviderError("Voyage returned malformed embedding vectors.") from exc
    return normalized


def _post_embedding_request(texts: list[str], api_key: str) -> tuple[list[list[float]], int | None]:
    estimated_tokens = sum(estimate_embedding_tokens(text) for text in texts)
    reservation = _rate_limiter.reserve(estimated_tokens)
    try:
        response = httpx.post(
            normalize_voyage_endpoint(WEEKLY_CONFIG.voyage_endpoint),
            headers={
                "Authorization": "Bearer " + api_key,
                "Content-Type": "application/json",
            },
            json={
                "input": texts,
                "model": WEEKLY_CONFIG.embed_model_id,
                "input_type": "document",
                "output_dimension": EMBEDDING_DIMENSION,
                "output_dtype": "float",
            },
            timeout=60.0,
        )
        response.raise_for_status()
        payload = response.json()
    except httpx.HTTPStatusError as exc:
        status_code = exc.response.status_code
        if status_code in {401, 403}:
            message = (
                f"Voyage embeddings request failed with HTTP {status_code}; "
                "check that VOYAGE_API_KEY is valid and authorized for this endpoint."
            )
        elif status_code == 429:
            message = (
                "Voyage embeddings request failed with HTTP 429; "
                "the request/token rate limit or account quota may have been reached."
            )
        else:
            message = f"Voyage embeddings request failed with HTTP {status_code}."
        raise EmbeddingProviderError(
            message,
            retryable=status_code == 429 or status_code >= 500 or status_code == 408,
        ) from exc
    except httpx.HTTPError as exc:
        raise EmbeddingProviderError(
            f"Voyage embeddings request failed: {type(exc).__name__}.",
            retryable=True,
        ) from exc
    except (KeyError, TypeError, ValueError) as exc:
        raise EmbeddingProviderError("Voyage returned an invalid embeddings response.") from exc

    if not isinstance(payload, dict) or "data" not in payload:
        raise EmbeddingProviderError("Voyage returned an invalid embeddings response.")
    usage = payload.get("usage")
    actual_tokens = usage.get("total_tokens") if isinstance(usage, dict) else None
    if not isinstance(actual_tokens, int) or isinstance(actual_tokens, bool):
        actual_tokens = None
    else:
        _rate_limiter.reconcile(reservation, actual_tokens)
    return _normalize_response(payload["data"], len(texts)), actual_tokens


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Embed event documents while staying within Voyage's free-tier request limits."""
    if not texts:
        return []
    api_key = os.getenv("VOYAGE_API_KEY", "").strip()
    if not api_key:
        raise EmbeddingProviderError("VOYAGE_API_KEY is not configured.")

    vectors = []
    for batch in split_embedding_batches(texts):
        batch_vectors, _ = _post_embedding_request(batch, api_key)
        vectors.extend(batch_vectors)
    return vectors
