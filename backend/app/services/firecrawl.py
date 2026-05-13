import os
from firecrawl import FirecrawlApp

_app: FirecrawlApp | None = None

EDUCATIONAL_SITES = [
    "khanacademy.org",
    "youtube.com",
    "mit.edu",
    "coursera.org",
]


def get_app() -> FirecrawlApp:
    global _app
    if _app is None:
        _app = FirecrawlApp(api_key=os.environ["FIRECRAWL_API_KEY"])
    return _app


def search_videos(topic_name: str, max_results: int = 10) -> list[dict]:
    app = get_app()
    site_filter = " OR ".join(f"site:{s}" for s in EDUCATIONAL_SITES)
    query = f"{topic_name} tutorial explained ({site_filter})"

    results = app.search(query, limit=max_results)
    videos = []

    for r in results.get("data", []):
        url = r.get("url", "")
        # Only keep video URLs
        if any(
            pat in url
            for pat in ["youtube.com/watch", "khanacademy.org/", "youtu.be/"]
        ):
            videos.append(
                {
                    "url": url,
                    "title": r.get("title", ""),
                    "description": r.get("description", ""),
                    "platform": _detect_platform(url),
                }
            )

    return videos


def scrape_page(url: str) -> dict:
    app = get_app()
    result = app.scrape_url(url, formats=["markdown"])
    return {
        "url": url,
        "content": result.get("markdown", ""),
        "metadata": result.get("metadata", {}),
    }


def _detect_platform(url: str) -> str:
    if "youtube.com" in url or "youtu.be" in url:
        return "youtube"
    if "khanacademy.org" in url:
        return "khan_academy"
    if "mit.edu" in url:
        return "mit_ocw"
    return "other"
