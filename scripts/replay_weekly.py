"""Replay recent closed weeks and write a Markdown evaluation report."""
import argparse
from datetime import datetime
from pathlib import Path

from app.db.database import SessionLocal
from app.weekly.replay import render_replay_report, replay_weeks


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", type=int, required=True)
    parser.add_argument("--weeks", type=int, required=True, help="Number of closed weeks to replay")
    parser.add_argument(
        "--live",
        action="store_true",
        help="Use the configured Groq model; default uses a fake no-signal client",
    )
    parser.add_argument("--output", type=Path, help="Markdown report path")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        replay = replay_weeks(db, target_id=args.target, weeks=args.weeks, live=args.live)
        report = render_replay_report(replay)
    finally:
        db.close()

    output = args.output or Path(
        f"weekly_replay_target_{args.target}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
    )
    output.write_text(report, encoding="utf-8")
    print(f"Replay report written to {output.resolve()}")


if __name__ == "__main__":
    main()
