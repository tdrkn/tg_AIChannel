from datetime import datetime
from aiogram import Router, types
from aiogram.filters import Command
from aiogram.types import URLInputFile
from src.config.settings import get_settings
from src.models import Run, Item, Post
from src.database import async_session_maker
from src.ai.pipeline import run_pipeline
from src.state import get_state, set_state
from sqlalchemy import select, func
from src.scheduler_manager import scheduler
from src.utils.config_db import set_config, get_config
from src.tasks import periodic_job
import logging

router = Router()
settings = get_settings()
logger = logging.getLogger(__name__)


def _photo_ref(value: str):
    v = (value or "").strip()
    if v.lower().startswith("http://") or v.lower().startswith("https://"):
        return URLInputFile(v)
    return v

def is_admin(user_id: int) -> bool:
    is_allowed = user_id in settings.admin_user_ids
    if not is_allowed:
        logger.warning(f"Unauthorized access attempt by {user_id}. Allowed: {settings.admin_user_ids}")
    return is_allowed

@router.message(Command("status"))
async def cmd_status(message: types.Message):
    if not is_admin(message.from_user.id):
        return

    is_paused = await get_state("is_paused", "false")
    interval = await get_config("post_interval_hours", "6")

    async with async_session_maker() as session:
        # Check last run
        result = await session.execute(select(Run).order_by(Run.start_time.desc()).limit(1))
        last_run = result.scalar_one_or_none()
        
        # Check items count
        items_count = await session.scalar(select(func.count(Item.id)))

    status_text = (
        f"🤖 <b>Bot Status</b>\n"
        f"Alive: Yes\n"
        f"Interval: {interval}h\n"
        f"Items in DB: {items_count}\n"
        f"Paused: {is_paused}\n"
    )

    if last_run:
        status_text += f"\n<b>Last Run:</b>\nTime: {last_run.start_time}\nStatus: {last_run.status}\nItems Ingested: {last_run.items_count}\nLog: {last_run.log}"
    else:
        status_text += "No runs yet."

    await message.answer(status_text)

@router.message(Command("pause"))
async def cmd_pause(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    await set_state("is_paused", "true")
    await message.answer("⏸ scheduler paused.")

@router.message(Command("resume"))
async def cmd_resume(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    await set_state("is_paused", "false")
    await message.answer("▶️ scheduler resumed.")

@router.message(Command("run"))
async def cmd_run(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    
    await message.answer("🚀 Pipeline started manually...")
    try:
        count = await run_pipeline(bot=message.bot)
        await message.answer(f"✅ Pipeline finished. Ingested {count} items.")
    except Exception as e:
        await message.answer(f"❌ Pipeline failed: {str(e)}")

@router.message(Command("preview"))
async def cmd_preview(message: types.Message):
    if not is_admin(message.from_user.id):
        return
        
    async with async_session_maker() as session:
        stmt = select(Post).where(Post.status == "draft").order_by(Post.created_at.desc()).limit(1)
        result = await session.execute(stmt)
        post = result.scalar_one_or_none()
        
        if not post:
            await message.answer("No active draft found.")
            return

        msg_text = f"🆕 <b>Current Draft</b>\n\n{post.content}\n\n/publish - to publish\n/reject - to discard"
        try:
            if post.image_url:
                if len(msg_text) > 1000:
                    await message.bot.send_photo(chat_id=message.chat.id, photo=_photo_ref(post.image_url))
                    await message.answer(msg_text)
                else:
                    await message.bot.send_photo(chat_id=message.chat.id, photo=_photo_ref(post.image_url), caption=msg_text)
            else:
                await message.answer(msg_text)
        except Exception as e:
             await message.answer(f"Error showing preview: {e}\n\n{msg_text}")

@router.message(Command("publish"))
async def cmd_publish(message: types.Message):
    if not is_admin(message.from_user.id):
        return
        
    async with async_session_maker() as session:
        # Check NO_REPEAT_HOURS (Warn only)
        stmt_last_pub = select(Post).where(Post.status == "published").order_by(Post.published_at.desc()).limit(1)
        last_pub_res = await session.execute(stmt_last_pub)
        last_pub = last_pub_res.scalar_one_or_none()
        
        if last_pub and last_pub.published_at:
            hours_diff = (datetime.utcnow() - last_pub.published_at).total_seconds() / 3600
            if hours_diff < settings.no_repeat_hours:
                 await message.answer(f"ℹ️ <b>Manual Override:</b> Ignoring No-Repeat limit ({settings.no_repeat_hours}h). Last post was {hours_diff:.1f}h ago.")

        # Get latest draft
        stmt = select(Post).where(Post.status == "draft").order_by(Post.created_at.desc()).limit(1)
        result = await session.execute(stmt)
        post = result.scalar_one_or_none()
        
        if not post:
            await message.answer("No draft found to publish.")
            return
            
        # Publish
        try:
            if post.image_url:
                 # Check length for caption safety
                 if len(post.content) > 1000:
                      await message.bot.send_photo(chat_id=settings.target_channel_id, photo=_photo_ref(post.image_url))
                      await message.bot.send_message(chat_id=settings.target_channel_id, text=post.content, parse_mode="HTML", disable_web_page_preview=True)
                 else:
                      await message.bot.send_photo(chat_id=settings.target_channel_id, photo=_photo_ref(post.image_url), caption=post.content, parse_mode="HTML")
            else:
                await message.bot.send_message(chat_id=settings.target_channel_id, text=post.content, parse_mode="HTML", disable_web_page_preview=True)
            
            post.status = "published"
            post.published_at = datetime.utcnow()
            await session.commit()
            
            await message.answer(f"✅ Published to {settings.target_channel_id}")
            
        except Exception as e:
            await message.answer(f"❌ Failed to publish: {e}")

@router.message(Command("reject"))
async def cmd_reject(message: types.Message):
    if not is_admin(message.from_user.id):
        return
        
    async with async_session_maker() as session:
        stmt = select(Post).where(Post.status == "draft").order_by(Post.created_at.desc()).limit(1)
        result = await session.execute(stmt)
        post = result.scalar_one_or_none()
        
        if not post:
            await message.answer("No draft found to reject.")
            return

        post.status = "rejected"
        await session.commit()
        await message.answer("🗑 Draft rejected.")

@router.message(Command("set_interval"))
async def cmd_set_interval(message: types.Message):
    """usage: /set_interval 4.5"""
    if not is_admin(message.from_user.id):
        return

    args = message.text.split()
    if len(args) != 2:
        val = await get_config("post_interval_hours", "6")
        await message.answer(f"Current interval: {val} hours.\nUsage: /set_interval <hours>")
        return

    try:
        hours = float(args[1])
        if hours < 0.1:
             await message.answer("❌ Interval too small.")
             return

        await set_config("post_interval_hours", str(hours))
        
        # Reschedule
        try:
            scheduler.reschedule_job("process_feed", trigger='interval', hours=hours)
            await message.answer(f"✅ Interval updated to {hours} hours. Scheduler updated.")
        except Exception as se:
            try:
                scheduler.add_job(periodic_job, 'interval', hours=hours, args=[message.bot], id="process_feed", replace_existing=True)
                await message.answer(f"✅ Interval updated to {hours} hours. Job recreated.")
            except Exception as e2:
                 await message.answer(f"⚠️ Saved to DB, but scheduler update failed: {se} / {e2}. Restart bot to apply.")

    except ValueError:
        await message.answer("❌ Invalid number.")

@router.message(Command("help"))
async def cmd_help(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    
    help_text = (
        "🤖 <b>Admin Commands</b>\n\n"
        "<b>/status</b> — Check bot health, stats & interval\n"
        "<b>/run</b> — Manually trigger a pipeline run\n"
        "<b>/preview</b> — View the currently pending draft\n"
        "<b>/publish</b> — Publish draft (ignores repeat limit)\n"
        "<b>/reject</b> — Discard draft\n"
        "<b>/set_interval N</b> — Set auto-post interval to N hours\n"
        "<b>/pause</b> — Pause schedule\n"
        "<b>/resume</b> — Resume schedule\n"
    )
    await message.answer(help_text)
