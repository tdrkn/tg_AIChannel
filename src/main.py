import asyncio
import logging
import sys
from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from src.config.settings import get_settings
from src.handlers import start, admin
from src.database import init_db
from src.ai.pipeline import run_pipeline
from src.state import get_state
import src.models  # Import models to ensure they are registered with Base.metadata
import src.state   # Import state model
from src.scheduler_manager import scheduler, get_interval_hours
from src.tasks import periodic_job

async def main():
    # Configure logging
    logging.basicConfig(level=logging.INFO, stream=sys.stdout)
    
    settings = get_settings()
    
    # Initialize Database
    logging.info("Initializing database...")
    await init_db()
    logging.info("Database initialized.")

    # Initialize Bot and Dispatcher
    bot = Bot(token=settings.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    
    # Include routers
    dp.include_router(admin.router)
    dp.include_router(start.router)
    
    # Scheduler
    interval_hours = await get_interval_hours()
    logging.info(f"Starting scheduler with interval: {interval_hours} hours")
    scheduler.add_job(periodic_job, 'interval', hours=interval_hours, args=[bot], id="process_feed")
    scheduler.start()

    # Start polling
    await dp.start_polling(bot)
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logging.info("Bot stopped!")
