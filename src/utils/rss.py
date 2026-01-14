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

async def fetch_rss_entries(rss_urls: List[str], hours: int = 6) -> tuple[List[Dict], List[str]]:
    """
    Fetch and parse RSS feeds. 
    Returns:
        1. List of entries (title/summary/link) filtered by 'hours'.
        2. List of source names (titles) that were successfully fetched (active sources).
    """
    entries = []
    active_sources = set()
    since_time = datetime.utcnow() - timedelta(hours=hours)
    
    async with httpx.AsyncClient(follow_redirects=True) as client:
        tasks = [fetch_feed_content(client, url) for url in rss_urls]
        contents = await asyncio.gather(*tasks)

    for content, url in zip(contents, rss_urls):
        if not content:
            continue
        
        # Offload parsing to thread to avoid blocking loop
        try:
            feed = await asyncio.to_thread(feedparser.parse, content)
        except Exception as e:
            logger.error(f"Error parsing feed content for {url}: {e}")
            continue

        # Record this source as active
        if hasattr(feed, "feed") and feed.feed.get("title"):
            active_sources.add(feed.feed.get("title"))
        else:
            # Fallback to domain or url if no title
            active_sources.add(url)

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
            
            source_name = feed.feed.get("title", url) if hasattr(feed, "feed") else url
            
            entries.append({
                "title": entry.get("title", ""),
                "link": entry.get("link", ""),
                "summary": entry.get("summary", "") or entry.get("description", ""),
                "published_at": published,
                "source": source_name
            })
            
    logger.info(f"Fetched {len(entries)} items from {len(rss_urls)} feeds. Active sources detected: {len(active_sources)}")
    return entries, list(active_sources)
