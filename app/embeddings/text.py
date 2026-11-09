"""Prepare short, deterministic event text before embedding."""
import json
import re


PREP_VERSION = "v1"
MAX_WORDS = 300


def _value(event, key: str, default=None):
    if isinstance(event, dict):
        return event.get(key, default)
    return getattr(event, key, default)


def _compact(value: str | None) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def _opening(text: str, max_words: int = MAX_WORDS) -> str:
    """Trim on whitespace boundaries; never cut a word in half."""
    words = _compact(text).split()
    return " ".join(words[:max_words])


def _github_parts(event) -> tuple[str, str]:
    raw_content = _value(event, "raw_content", "")
    try:
        payload = json.loads(raw_content) if isinstance(raw_content, str) else {}
    except (json.JSONDecodeError, TypeError):
        payload = {}
    title = _value(event, "title", "") or "GitHub commit"
    repo = payload.get("repo") or title.removeprefix("Commit in ")
    message = payload.get("message") or _value(event, "one_liner", "")
    message_opening = " ".join(str(message).splitlines()[:3])
    return _compact(str(repo)), _compact(message_opening)


def _ats_description(raw_content: str) -> str:
    match = re.search(r"(?:Description|Requirements):\s*(.*)", raw_content, flags=re.IGNORECASE | re.DOTALL)
    if match:
        return match.group(1)
    lines = [
        line for line in raw_content.splitlines()
        if not line.strip().lower().startswith(("title:", "location:", "url:"))
    ]
    return " ".join(lines)


def build_embedding_text(event, max_words: int = MAX_WORDS) -> str:
    """Build source-specific v1 text; source context keeps short snippets interpretable."""
    source = _value(event, "source_type", "unknown")
    title = _compact(_value(event, "title", ""))
    raw_content = _value(event, "raw_content", "") or ""

    if source == "github":
        repo, message = _github_parts(event)
        prepared = f"Repository: {repo}\nCommit: {message}"
    elif source == "ats":
        location_match = re.search(
            r"^Location:\s*(.*)$", raw_content, re.MULTILINE | re.IGNORECASE
        )
        location = _compact(location_match.group(1)) if location_match else ""
        description = _ats_description(raw_content)
        prepared = f"Job title: {title}"
        if location:
            prepared += f"\nLocation: {location}"
        if description:
            prepared += f"\nDescription: {description}"
    elif source == "blog":
        prepared = f"Article title: {title}\nOpening: {raw_content}"
    elif source == "web_social":
        url = _compact(_value(event, "url", ""))
        snippet = raw_content or _value(event, "one_liner", "")
        prepared = f"Page update: {title}\nURL: {url}\nChanged text: {snippet}"
    else:
        prepared = f"{source}: {title}\n{raw_content or _value(event, 'one_liner', '')}"

    return _opening(prepared, max_words=max_words)
