from urllib.parse import urljoin
import httpx
from bs4 import BeautifulSoup

import os
from groq import Groq
from dotenv import load_dotenv
import json

load_dotenv()


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



def parse_response(answer_text):
    try:
        parsed = json.loads(answer_text)
    except json.JSONDecodeError:
        return {"github_url": None, "ats_url": None, "blog_url": None, "web_social_url": None}

    return parsed




def classify_links(links: list):
    link_text = json.dumps(links)

    api = os.environ.get("GROQ_API_KEY")
    if not api:
        raise ValueError("API KEY not found!!!!")
    
    client = Groq(
        api_key=api,
    )

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
        model="llama-3.3-70b-versatile",
        messages=message,
        response_format={"type": "json_object"},
        temperature=0.1,
        max_tokens=1024,
    )

    answer_text = response.choices[0].message.content.strip()

    refined_links = parse_response(answer_text)

    return refined_links
