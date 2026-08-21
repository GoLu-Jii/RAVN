"""
Test script for app/kundali/builder.py — GitHub parsing layer.
Run from project root: python -m tests.test_kundali_github
(or however you're invoking your test scripts, per your existing pattern)
"""

from datetime import datetime, timedelta, timezone

from app.kundali.builder import (
    extract_account_name,
    get_account_type,
    list_repos,
    get_commit_activity,
    get_recent_commits,
    get_languages,
)


def test_extract_account_name():
    print("\n=== extract_account_name ===")
    cases = [
        ("https://github.com/vercel", "vercel"),
        ("https://github.com/vercel/", "vercel"),
        ("https://github.com/torvalds", "torvalds"),
    ]
    for url, expected in cases:
        result = extract_account_name(url)
        status = "OK" if result == expected else "FAIL"
        print(f"[{status}] {url} -> {result} (expected {expected})")


def test_get_account_type():
    print("\n=== get_account_type ===")
    org_type = get_account_type("vercel")
    user_type = get_account_type("torvalds")
    print(f"vercel -> {org_type} (expected Organization)")
    print(f"torvalds -> {user_type} (expected User)")


def test_list_repos():
    print("\n=== list_repos ===")
    org_repos = list_repos("https://github.com/vercel", limit=5)
    print(f"Org account: got {len(org_repos)} repos (expected 5)")
    for r in org_repos:
        print(" ", r)

    user_repos = list_repos("https://github.com/torvalds", limit=5)
    print(f"User account: got {len(user_repos)} repos (expected 5)")
    for r in user_repos:
        print(" ", r)


def test_get_commit_activity():
    print("\n=== get_commit_activity ===")

    # cached / likely-200 case
    activity = get_commit_activity("vercel", "next.js")
    if activity is not None:
        print(f"vercel/next.js -> {len(activity)} weeks (expected 52)")
    else:
        print("vercel/next.js -> None (unexpected — check printed reason above)")

    # 404 / fail-fast case, timed to confirm no wasted retries
    import time
    start = time.time()
    bad = get_commit_activity("torvalds", "this-repo-does-not-exist-12345")
    elapsed = time.time() - start
    print(f"nonexistent repo -> {bad}, took {elapsed:.2f}s (should be near-instant)")


def test_get_recent_commits():
    print("\n=== get_recent_commits ===")
    commits = get_recent_commits("vercel", "next.js", days=45, limit=20)
    print(f"Got {len(commits)} commits (expected <= 20)")

    cutoff = datetime.now(timezone.utc) - timedelta(days=45)
    all_within_window = True
    for c in commits:
        commit_date = datetime.strptime(c["date"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        if commit_date < cutoff:
            print(f"  OUT OF WINDOW: {c['sha']} at {c['date']}")
            all_within_window = False
    print(f"All commits within 45-day window: {all_within_window}")

    small_batch = get_recent_commits("vercel", "next.js", days=45, limit=3)
    print(f"limit=3 -> got {len(small_batch)} (expected 3)")
    for c in commits[:3]:
        print(c)


def test_get_languages():
    print("\n=== get_languages ===")
    next_langs = get_languages("vercel", "next.js")
    print(f"vercel/next.js languages: {next_langs}")

    linux_langs = get_languages("torvalds", "linux")
    print(f"torvalds/linux languages: {linux_langs}")


if __name__ == "__main__":
    test_extract_account_name()
    test_get_account_type()
    test_list_repos()
    test_get_commit_activity()
    test_get_recent_commits()
    test_get_languages()
    print("\n=== All tests completed ===")