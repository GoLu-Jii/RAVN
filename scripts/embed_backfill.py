"""Resume embedding any missing or changed non-baseline events."""
import argparse

from app.db.database import SessionLocal
from app.embeddings.service import embed_missing_events


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", type=int, help="Only process this target ID")
    parser.add_argument("--batch-size", type=int, default=64)
    args = parser.parse_args()

    def report_progress(progress: dict) -> None:
        print(
            f"Batch {progress['batch']}: embedded {progress['embedded']} / "
            f"{progress['total_pending']} pending events"
        )

    db = SessionLocal()
    try:
        result = embed_missing_events(
            db,
            target_id=args.target,
            batch_size=args.batch_size,
            progress_callback=report_progress,
        )
        print(result)
        if result["status"] in {"failed", "partial"}:
            raise SystemExit(1)
    finally:
        db.close()


if __name__ == "__main__":
    main()
