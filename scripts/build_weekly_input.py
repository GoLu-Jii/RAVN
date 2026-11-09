"""Print the assembled, budget-checked JSON input for a target and Monday week."""
import argparse
from datetime import date
import json

from app.db.database import SessionLocal
from app.weekly.input_builder import WeeklyInputError, build_weekly_input


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", type=int, required=True)
    parser.add_argument("--week", type=date.fromisoformat, required=True, help="Monday in YYYY-MM-DD format")
    args = parser.parse_args()
    db = SessionLocal()
    try:
        try:
            payload = build_weekly_input(db, target_id=args.target, week_start=args.week)
        except WeeklyInputError as exc:
            print(json.dumps({
                "error": exc.reason,
                "estimated_tokens": exc.token_estimate,
                "token_budget": exc.token_budget,
            }, indent=2))
            raise SystemExit(2)
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    finally:
        db.close()


if __name__ == "__main__":
    main()
