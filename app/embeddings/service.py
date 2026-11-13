"""Incrementally persist embeddings without allowing model failures to break collection."""
import hashlib
import logging
from collections.abc import Callable
from math import isfinite

from sqlalchemy import inspect
from sqlalchemy.exc import NoSuchTableError
from sqlalchemy.orm import Session

from app.embeddings.model import (
    EMBEDDING_DIMENSION,
    EmbeddingProviderError,
    embed_texts,
    split_embedding_batches,
)
from app.embeddings.text import PREP_VERSION, build_embedding_text
from app.events.models import Event, EventEmbedding
from app.weekly.config import WEEKLY_CONFIG


logger = logging.getLogger(__name__)
EmbeddingFunction = Callable[[list[str]], list[list[float]]]


class EmbeddingSchemaError(RuntimeError):
    """The database vector column does not match the hosted model output."""


def _validate_storage_schema(db: Session) -> None:
    bind = db.get_bind()
    if bind.dialect.name != "postgresql":
        return

    try:
        columns = inspect(bind).get_columns(EventEmbedding.__tablename__)
    except NoSuchTableError as exc:
        raise EmbeddingSchemaError(
            "The event_embeddings table is missing; run `python -m alembic upgrade head`."
        ) from exc

    embedding_column = next(
        (column for column in columns if column["name"] == "embedding"),
        None,
    )
    dimension = (
        getattr(embedding_column["type"], "dim", None)
        if embedding_column is not None
        else None
    )
    if dimension != EMBEDDING_DIMENSION:
        actual = f"vector({dimension})" if dimension is not None else "an unknown type"
        raise EmbeddingSchemaError(
            f"PostgreSQL event_embeddings.embedding is {actual}; this app requires "
            f"vector({EMBEDDING_DIMENSION}). Run `python -m alembic upgrade head` "
            "against the same database before retrying."
        )


def embed_missing_events(
    db: Session,
    target_id: int | None = None,
    *,
    batch_size: int = 64,
    embedder: EmbeddingFunction | None = None,
    progress_callback: Callable[[dict], None] | None = None,
) -> dict:
    """Embed missing or text-changed non-baseline events in resumable batches."""
    if batch_size < 1:
        raise ValueError("batch_size must be at least 1.")
    embedder = embedder or embed_texts
    model_id = WEEKLY_CONFIG.embed_model_id
    prep_version = WEEKLY_CONFIG.prep_version or PREP_VERSION
    embedded = 0
    already_current = 0

    try:
        _validate_storage_schema(db)
        query = db.query(Event).filter(Event.is_baseline.is_(False))
        if target_id is not None:
            query = query.filter(Event.target_id == target_id)
        events = query.order_by(Event.id.asc()).all()
        existing_rows = db.query(EventEmbedding).filter_by(
            model_id=model_id, prep_version=prep_version
        ).all()
        existing_by_event = {row.event_id: row for row in existing_rows}

        pending = []
        for event in events:
            text = build_embedding_text(event)
            text_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
            existing = existing_by_event.get(event.id)
            if existing is not None and existing.text_hash == text_hash:
                already_current += 1
                continue
            pending.append((event, text, text_hash, existing))

        total_pending = len(pending)
        if not pending:
            return {
                "status": "no_work",
                "target_id": target_id,
                "total_events": len(events),
                "embedded": 0,
                "already_current": already_current,
                "pending": 0,
            }

        text_batches = split_embedding_batches(
            [item[1] for item in pending],
            max_items=batch_size,
        )
        pending_offset = 0
        for batch_number, text_batch in enumerate(text_batches, start=1):
            batch = pending[pending_offset:pending_offset + len(text_batch)]
            pending_offset += len(text_batch)
            try:
                vectors = embedder(text_batch)
                if len(vectors) != len(batch):
                    raise ValueError("Embedder returned a different number of vectors than inputs.")
                for (event, _, text_hash, row), vector in zip(batch, vectors):
                    if len(vector) != EMBEDDING_DIMENSION:
                        raise ValueError(f"Expected {EMBEDDING_DIMENSION}-dimensional vectors.")
                    if not all(isfinite(value) for value in vector):
                        raise ValueError("Embedder returned non-finite embedding values.")
                    if row is None:
                        row = EventEmbedding(
                            event_id=event.id,
                            model_id=model_id,
                            dimension=EMBEDDING_DIMENSION,
                            prep_version=prep_version,
                            text_hash=text_hash,
                            embedding=vector,
                        )
                        db.add(row)
                    else:
                        row.dimension = EMBEDDING_DIMENSION
                        row.text_hash = text_hash
                        row.embedding = vector
                db.commit()
                embedded += len(batch)
                if progress_callback:
                    progress_callback({
                        "batch": batch_number,
                        "embedded": embedded,
                        "total_pending": total_pending,
                    })
            except Exception as exc:
                db.rollback()
                logger.exception("Embedding batch %s failed", batch_number)
                report = {
                    "status": "partial" if embedded else "failed",
                    "target_id": target_id,
                    "total_events": len(events),
                    "embedded": embedded,
                    "already_current": already_current,
                    "pending": total_pending - embedded,
                    "error_type": type(exc).__name__,
                }
                if isinstance(exc, EmbeddingProviderError):
                    report["error_message"] = str(exc)
                    report["retryable"] = exc.retryable
                return report

        return {
            "status": "success",
            "target_id": target_id,
            "total_events": len(events),
            "embedded": embedded,
            "already_current": already_current,
            "pending": 0,
        }
    except EmbeddingSchemaError as exc:
        db.rollback()
        logger.error("Embedding storage schema check failed: %s", exc)
        return {
            "status": "failed",
            "target_id": target_id,
            "embedded": embedded,
            "already_current": already_current,
            "error_type": type(exc).__name__,
            "error_message": str(exc),
        }
    except EmbeddingProviderError as exc:
        db.rollback()
        logger.exception("Could not prepare event embeddings")
        return {
            "status": "failed",
            "target_id": target_id,
            "embedded": embedded,
            "already_current": already_current,
            "error_type": type(exc).__name__,
            "error_message": str(exc),
            "retryable": exc.retryable,
        }
    except Exception as exc:
        db.rollback()
        logger.exception("Could not prepare event embeddings")
        return {
            "status": "failed",
            "target_id": target_id,
            "embedded": embedded,
            "already_current": already_current,
            "error_type": type(exc).__name__,
        }
