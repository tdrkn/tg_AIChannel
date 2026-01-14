from duckduckgo_search import DDGS
from typing import List, Dict
import logging

logger = logging.getLogger(__name__)

def web_search(query: str, max_results: int = 3) -> List[Dict[str, str]]:
    """
    Performs a web search using DuckDuckGo.
    Returns a list of results with 'title', 'href', 'body'.
    """
    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=max_results))
            return [
                {
                    "title": r.get("title", ""),
                    "href": r.get("href", ""),
                    "body": r.get("body", "")
                }
                for r in results
            ]
    except Exception as e:
        logger.error(f"Search failed for query '{query}': {e}")
        return []


def image_search(query: str, max_results: int = 8, safesearch: str = "moderate") -> List[Dict[str, str]]:
    """Performs an image search and returns raw result dicts.

    NOTE: Results are third-party URLs; availability/hotlinking may vary.
    """
    if not query:
        return []

    try:
        with DDGS() as ddgs:
            results = list(ddgs.images(query, max_results=max_results, safesearch=safesearch))
            cleaned: List[Dict[str, str]] = []
            for r in results:
                cleaned.append(
                    {
                        "title": r.get("title", ""),
                        "image": r.get("image", ""),
                        "thumbnail": r.get("thumbnail", ""),
                        "url": r.get("url", ""),
                        "source": r.get("source", ""),
                    }
                )
            return cleaned
    except Exception as e:
        logger.error(f"Image search failed for query '{query}': {e}")
        return []
