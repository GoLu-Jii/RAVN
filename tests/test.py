from app.kundali.builder import list_repos

# Organization-type account
print("=== Testing Organization account ===")
org_repos = list_repos("https://github.com/vercel", limit=5)
for r in org_repos:
    print(r)

# User-type account (pick any solo dev's public GitHub URL)
print("\n=== Testing User account ===")
user_repos = list_repos("https://github.com/torvalds", limit=5)
for r in user_repos:
    print(r)