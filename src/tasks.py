import logging
from aiogram import Bot
from src.state import get_state
from src.ai.pipeline import run_pipeline

async def periodic_job(bot: Bot):
    # Check if paused
    try:
        is_paused = await get_state("is_paused", "false")
        if is_paused == "true":
            logging.info("Scheduler: Auto-publish is PAUSED. Skipping run.")
            return
        
        await run_pipeline(bot=bot, allow_auto_publish=True)
    except Exception as e:
        logging.error(f"CRITICAL: Periodic job failure: {e}", exc_info=True)
