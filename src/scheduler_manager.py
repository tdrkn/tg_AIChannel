from apscheduler.schedulers.asyncio import AsyncIOScheduler
from src.utils.config_db import get_config

scheduler = AsyncIOScheduler()

async def get_interval_hours():
    val = await get_config("post_interval_hours", "2")
    try:
        return float(val)
    except:
        return 2.0

def get_scheduler():
    return scheduler
