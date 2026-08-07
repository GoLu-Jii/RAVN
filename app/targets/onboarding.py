from urllib.parse import urljoin
import httpx
from bs4 import BeautifulSoup

import os
from groq import Groq


FALLBACK_PATHS = ["/about", "/careers", "/blog"]


def extract_links(url):
    links = []

    try:
        response = httpx.get(url, timeout=10.0)
    except httpx.RequestError:
        return links

    if response.status_code >= 400:
        return links

    soup = BeautifulSoup(response.text, "html.parser")

    for anchor in soup.find_all("a"):
        href = anchor.get("href")
        if not href:
            continue

        absolute_url = urljoin(url, href)
        text = anchor.get_text(strip=True)
        links.append((absolute_url, text))

    return links


def discover_candidate_links(homepage_url):
    all_links = []

    urls_to_try = [homepage_url]
    for path in FALLBACK_PATHS:
        urls_to_try.append(urljoin(homepage_url, path))

    for url in urls_to_try:
        page_links = extract_links(url)
        all_links.extend(page_links)

    return all_links




