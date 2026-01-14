from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from src.database import async_session_maker
from src.models import Config
from src.config.settings import get_settings
import json

async def get_config(key: str, default: str = None) -> str:
    async with async_session_maker() as session:
        result = await session.execute(select(Config.value).where(Config.key == key))
        val = result.scalar_one_or_none()
        return val if val is not None else default

async def set_config(key: str, value: str):
    async with async_session_maker() as session:
        stmt = insert(Config).values(key=key, value=value).on_conflict_do_update(
            index_elements=['key'],
            set_={'value': value}
        )
        await session.execute(stmt)
        await session.commit()

async def get_rss_feeds() -> list[str]:
    """Get active RSS feeds from DB, falling back to .env and syncing if DB is empty."""
    val = await get_config("rss_urls")
    if val:
        try:
            return json.loads(val)
        except:
            return [x.strip() for x in val.split(',') if x.strip()]
    
    # Fallback/Init
    settings = get_settings()
    feeds = []
    if settings.news_rss_urls:
         feeds = [x.strip() for x in settings.news_rss_urls.split(',') if x.strip()]
    
    # Save to DB for future management
    if feeds:
        await set_config("rss_urls", json.dumps(feeds))
    
    return feeds

async def add_rss_feed(url: str) -> bool:
    feeds = await get_rss_feeds()
    if url in feeds:
        return False
    feeds.append(url)
    await set_config("rss_urls", json.dumps(feeds))
    return True

async def remove_rss_feed(url: str) -> bool:
    feeds = await get_rss_feeds()
    if url not in feeds:
        return False
    feeds.remove(url)
    await set_config("rss_urls", json.dumps(feeds))
    return True

