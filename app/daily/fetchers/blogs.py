import feedparser
import httpx
import trafilatura
from bs4 import BeautifulSoup
from urllib.parse import urljoin


_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}


def discover_feed_url(blog_url: str) -> str | None:
    """
    Attempts to discover an RSS/Atom feed URL for a given blog:
    1. Tests blog_url itself (in case an RSS feed was passed directly).
    2. Inspects HTML <head> for <link rel="alternate" type="application/rss+xml">.
    3. Probes common feed paths (/feed, /rss, /feed.xml, etc.).
    """
    if not blog_url:
        return None

    # 1. Check if blog_url itself is already an RSS/XML feed
    try:
        r = httpx.get(blog_url, headers=_HEADERS, timeout=6, follow_redirects=True)
        content_type = r.headers.get("content-type", "").lower()
        if r.status_code == 200 and any(t in content_type for t in ["xml", "rss", "atom"]):
            return blog_url

        # 2. Check HTML head for <link rel="alternate" type="application/rss+xml" ...>
        if r.status_code == 200 and "html" in content_type:
            soup = BeautifulSoup(r.text, "html.parser")
            for link_tag in soup.find_all("link", rel=lambda x: x and "alternate" in x):
                t = link_tag.get("type", "").lower()
                if "rss" in t or "atom" in t or "xml" in t:
                    feed_href = link_tag.get("href")
                    if feed_href:
                        return urljoin(blog_url, feed_href)
    except httpx.RequestError as e:
        print(f"[discover_feed_url] initial check for {blog_url} failed: {e}")

    # 3. Probe common feed subpaths
    common_paths = ["/feed", "/rss", "/rss.xml", "/atom.xml", "/feed.xml", "/blog/feed.xml"]
    base_url = blog_url.rstrip("/")
    for path in common_paths:
        feed_url = base_url + path
        try:
            res = httpx.get(feed_url, headers=_HEADERS, timeout=5, follow_redirects=True)
            ct = res.headers.get("content-type", "").lower()
            if res.status_code == 200 and any(t in ct for t in ["xml", "rss", "atom"]):
                return feed_url
        except httpx.RequestError:
            continue

    return None


def _extract_posts_from_html(blog_url: str, max_posts: int = 15) -> list[dict]:
    """
    Fallback extractor when no RSS/Atom feed exists. Scrapes recent article cards
    or post links directly from the blog HTML.
    """
    try:
        response = httpx.get(blog_url, headers=_HEADERS, timeout=10, follow_redirects=True)
        if response.status_code != 200:
            return []
    except httpx.RequestError as e:
        print(f"[_extract_posts_from_html] failed to fetch {blog_url}: {e}")
        return []

    soup = BeautifulSoup(response.text, "html.parser")
    posts = []
    seen_urls = set()

    for a in soup.find_all("a"):
        href = a.get("href", "")
        if not href or href.startswith("#") or href.startswith("mailto:"):
            continue

        full_url = urljoin(blog_url, href)
        if full_url in seen_urls:
            continue

        # Look for article-like links (contains /blog/, /news/, /posts/, /insights/, etc.)
        is_article_link = any(p in full_url.lower() for p in ["/blog/", "/news/", "/posts/", "/insights/", "/article/"])
        if not is_article_link:
            continue

        # Skip landing pages themselves
        if full_url.rstrip("/") in [blog_url.rstrip("/"), blog_url.rstrip("/") + "/blog"]:
            continue

        text = a.get_text(" ", strip=True)
        # Filter out short labels like 'Read more', dates, category tags
        if len(text) < 18 or text.lower() in ["read more", "learn more", "view post"]:
            continue
        # Filter out pure dates like "October 22, 2026"
        if text.replace(",", "").replace(" ", "").isdigit():
            continue

        seen_urls.add(full_url)
        posts.append({
            "title": text,
            "link": full_url,
            "published": None,
            "content": None,
        })

        if len(posts) >= max_posts:
            break

    # Extract snippet text for the top 3 posts using trafilatura
    for post in posts[:3]:
        try:
            art_res = httpx.get(post["link"], headers=_HEADERS, timeout=8, follow_redirects=True)
            if art_res.status_code == 200:
                extracted = trafilatura.extract(art_res.text)
                if extracted:
                    post["content"] = extracted[:600]
        except Exception:
            pass

    return posts


def get_blog_posts(blog_url: str, max_posts: int = 15) -> list[dict]:
    """
    Primary blog post fetcher:
    - Uses discovered RSS/Atom feed if available.
    - Falls back to HTML article extraction if no feed is found.
    """
    if not blog_url:
        return []

    feed_url = discover_feed_url(blog_url)
    if feed_url:
        try:
            entries = feedparser.parse(feed_url).entries[:max_posts]
            posts = []
            for entry in entries:
                title = entry.get("title")
                link = entry.get("link")
                published = entry.get("published")

                content = None
                if link:
                    try:
                        response = httpx.get(link, headers=_HEADERS, timeout=8, follow_redirects=True)
                        if response.status_code == 200:
                            extracted = trafilatura.extract(response.text)
                            if extracted:
                                content = extracted[:600]
                    except httpx.RequestError as e:
                        print(f"[get_blog_posts] failed to fetch {link}: {e}")

                posts.append({
                    "title": title,
                    "link": link,
                    "published": published,
                    "content": content,
                })

            if posts:
                return posts
        except Exception as e:
            print(f"[get_blog_posts] feedparser failed for {feed_url}: {e}")

    # Fallback to direct HTML article extraction
    return _extract_posts_from_html(blog_url, max_posts=max_posts)