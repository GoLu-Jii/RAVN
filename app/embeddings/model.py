"""Lazy local sentence-transformer loader and normalized embedding function."""
from functools import lru_cache
from math import sqrt

from app.weekly.config import WEEKLY_CONFIG


EMBEDDING_DIMENSION = 384


@lru_cache(maxsize=None)
def _load_model(model_id: str):
    # Importing this module must not download or initialize the local model.
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(model_id, device="cpu")


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Embed text with the pinned local model and L2-normalize each vector."""
    if not texts:
        return []
    model = _load_model(WEEKLY_CONFIG.embed_model_id)
    encoded = model.encode(texts, convert_to_numpy=True, show_progress_bar=False)
    vectors = encoded.tolist() if hasattr(encoded, "tolist") else encoded
    if len(vectors) != len(texts):
        raise ValueError("Embedding model returned a different number of vectors than inputs.")

    normalized = []
    for vector in vectors:
        values = [float(value) for value in vector]
        if len(values) != EMBEDDING_DIMENSION:
            raise ValueError(f"Expected {EMBEDDING_DIMENSION}-dimensional vectors.")
        norm = sqrt(sum(value * value for value in values))
        if norm == 0:
            raise ValueError("Embedding model returned a zero vector.")
        normalized.append([value / norm for value in values])
    return normalized
