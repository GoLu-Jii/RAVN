import feedparser
import httpx
import trafilatura


def discover_feed_url(blog_url: str) -> str | None:
    common_paths = ['/feed', '/rss', '/rss.xml', '/atom.xml', '/feed.xml']

    for path in common_paths:
        feed_url = blog_url.rstrip('/') + path
        try:
            response = httpx.get(feed_url, timeout=5)  
        except httpx.RequestError as e:
            print(f"[discover_feed_url] {feed_url} failed: {e}")
            continue

        if response.status_code == 200 and 'xml' in response.headers.get('Content-Type', ''):
            return feed_url

    
    return None


def get_blog_posts(blog_url: str, max_posts: int = 20) -> list[dict]:
    feed_url = discover_feed_url(blog_url)
    if feed_url is None:
        return []

    entries = feedparser.parse(feed_url).entries[:max_posts]

    posts = []

    for entry in entries:
        title = entry.get("title")    
        link = entry.get("link")           
        published = entry.get("published")  

        content = None
        if link:
            try:
                response = httpx.get(link, timeout=10)
                if response.status_code == 200:
                    content = trafilatura.extract(response.text)
            except httpx.RequestError as e:
                print(f"[get_blog_posts] failed to fetch {link}: {e}")

        posts.append({
            "title": title,
            "link": link,
            "published": published,
            "content": content,   
        })

    return posts   