import sys
import os

# Handle UTF-8 / emojis on Windows console
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# Ensure project root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.kundali.builder import get_recent_commits

owner = sys.argv[1] if len(sys.argv) > 1 else "stripe"
repo = sys.argv[2] if len(sys.argv) > 2 else "stripe-python"

print(f"\nFetching recent commits for {owner}/{repo} (last 45 days)...\n")
commits = get_recent_commits(owner, repo, days=45, limit=50)

if not commits:
    print(f"No commits found or error fetching commits for {owner}/{repo}.")
    sys.exit(0)

print(f"Found {len(commits)} commits:\n" + "=" * 80)

for i, c in enumerate(commits, 1):
    print(f"\n[{i}] Date: {c['date']} | SHA: {c['sha']}")
    print("-" * 80)
    print(c['message'].strip())
    print("=" * 80)
