from app.targets.onboarding import discover_candidate_links, classify_links

homepage_url = "https://stripe.com"

links = discover_candidate_links(homepage_url)
print(f"Discovered {len(links)} links\n")

result = classify_links(links)
print(result)