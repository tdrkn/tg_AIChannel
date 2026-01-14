import feedparser
import httpx
import asyncio
from datetime import datetime, timedelta
from time import mktime
from typing import Dict, List, Optional
import logging

logger = logging.getLogger(__name__)

async def fetch_feed_content(client: httpx.AsyncClient, url: str) -> Optional[str]:
    try:
        response = await client.get(url, timeout=10.0, follow_redirects=True)
        response.raise_for_status()
        return response.text
    except Exception as e:
        logger.error(f"Error fetching {url}: {e}")
        return None

async def fetch_rss_entries(rss_urls: List[str], hours: int = 6) -> List[Dict]:
    """
    Fetch and parse RSS feeds. Returns list of entries with title/summary/link.
    Filters entries older than 'hours' hours.
    """
    entries = []
    since_time = datetime.utcnow() - timedelta(hours=hours)
    
    async with httpx.AsyncClient(follow_redirects=True) as client:
        tasks = [fetch_feed_content(client, url) for url in rss_urls]
        contents = await asyncio.gather(*tasks)

    for content, url in zip(contents, rss_urls):
        if not content:
            continue
        
        feed = feedparser.parse(content)
        # Check bozo but sometimes valid feeds trigger it
        if feed.bozo:
             logger.warning(f"Feed {url} potential issue: {feed.bozo_exception}")

        for entry in feed.entries:
            published = None
            # feedparser parses time into struct_time (UTC usually)
            if hasattr(entry, 'published_parsed') and entry.published_parsed:
                 published = datetime.fromtimestamp(mktime(entry.published_parsed))
            elif hasattr(entry, 'updated_parsed') and entry.updated_parsed:
                 published = datetime.fromtimestamp(mktime(entry.updated_parsed))
            
            # If no date, use current time or skip? For now, we accept it if we can't determine
            if published:
                 if published < since_time:
                     continue
            
            entries.append({
                "title": entry.get("title", ""),
                "link": entry.get("link", ""),
                "summary": entry.get("summary", "") or entry.get("description", ""),
                "published_at": published,
                "source": feed.feed.get("title", url)
            })
            
    logger.info(f"Fetched {len(entries)} items from {len(rss_urls)} feeds.")
    return entries
