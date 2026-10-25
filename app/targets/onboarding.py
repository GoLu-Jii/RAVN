from urllib.parse import urljoin
import httpx
from bs4 import BeautifulSoup

import os
from dotenv import load_dotenv
import json

from app.llm import get_llm_client

load_dotenv()


FALLBACK_PATHS = ["/about", "/careers", "/blog"]


_BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}


def normalize_url(url: str | None) -> str | None:
    if not url:
        return url
    url = url.strip()
    if not url:
        return url
    if not (url.startswith("http://") or url.startswith("https://")):
        return f"https://{url}"
    return url


def extract_links(url):
    links = []
    url = normalize_url(url)
    if not url:
        return links

    try:
        response = httpx.get(url, timeout=10.0, headers=_BROWSER_HEADERS, follow_redirects=True)
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
    homepage_url = normalize_url(homepage_url)
    if not homepage_url:
        return []

    urls_to_try = [homepage_url]
    for path in FALLBACK_PATHS:
        urls_to_try.append(urljoin(homepage_url, path))

    for url in urls_to_try:
        page_links = extract_links(url)
        all_links.extend(page_links)

    return all_links



def parse_response(answer_text):
    try:
        text = answer_text.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].startswith("```"):
                lines = lines[:-1]
            text = "\n".join(lines).strip()
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return {"github_url": None, "ats_url": None, "blog_url": None, "web_social_url": None}

    return parsed




def classify_links(links: list):
    link_text = json.dumps(links)

    client = get_llm_client()

    user_prompt = [{"role": "user", "content": link_text}]

    SYSTEM_PROMPT = """You are given a list of links extracted from a company's website.
    Each link has a URL and its visible anchor text, in the form (url, text).

    Your task is to find, from this list, the single best URL for each of these four categories:
    - "github_url": the company's GitHub organization or GitHub profile page
    - "ats_url": a job board or careers page (e.g. Greenhouse, Lever, Ashby, Workable, or a "Careers"/"Jobs" page)
    - "blog_url": the company's engineering or general blog
    - "web_social_url": an official social media page (e.g. Twitter/X, LinkedIn) or product/updates page

    Rules:
    - If no link in the list matches a category, set that field to null. Do not guess.
    - Only pick a link that is actually present in the given list — never invent a URL.
    - Respond with ONLY a JSON object in this exact shape, no other text:
    {"github_url": "url or null", "ats_url": "url or null", "blog_url": "url or null", "web_social_url": "url or null"}
    """

    message = [{"role": "system", "content": SYSTEM_PROMPT}]
    message.extend(user_prompt)


    response = client.chat.completions.create(
        messages=message,
        response_format={"type": "json_object"},
        temperature=0.1,
        max_tokens=1024,
    )

    answer_text = response.choices[0].message.content.strip()

    refined_links = parse_response(answer_text)

    return refined_links
