import httpx
import trafilatura


def get_web_social_content(web_social_url: str) -> dict | None:
    if not web_social_url:
        return None

    try:
        response = httpx.get(web_social_url, timeout=10.0)
    except httpx.RequestError:
        return None

    if response.status_code != 200:
        return None

    content = trafilatura.extract(response.text)
    
    if content is None or len(content) < 50:
        return None

    return {"url": web_social_url, "content": content}