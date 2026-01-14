from datetime import datetime
import html
from aiogram import Router, types
from aiogram.filters import Command
from aiogram.types import URLInputFile
from src.config.settings import get_settings
from src.models import Run, Item, Post
from src.database import async_session_maker
from src.ai.pipeline import run_pipeline, process_manual_url
from src.state import get_state, set_state
from sqlalchemy import select, func, delete
from src.scheduler_manager import scheduler
from src.utils.config_db import set_config, get_config, get_rss_feeds, add_rss_feed, remove_rss_feed
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
        f"🤖 <b>Статус бота</b>\n"
        f"Работает: Да\n"
        f"Интервал: {interval} ч\n"
        f"Записей в БД: {items_count}\n"
        f"Пауза: {is_paused}\n"
    )

    if last_run:
        status_text += f"\n<b>Последний запуск:</b>\nВремя: {last_run.start_time}\nСтатус: {last_run.status}\nИмпортировано: {last_run.items_count}\nЛог: {last_run.log}"
    else:
        status_text += "Запусков пока не было."

    await message.answer(status_text)

@router.message(Command("pause"))
async def cmd_pause(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    await set_state("is_paused", "true")
    await message.answer("⏸ расписание приостановлено.")

@router.message(Command("resume"))
async def cmd_resume(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    await set_state("is_paused", "false")
    await message.answer("▶️ расписание возобновлено.")

@router.message(Command("run"))
async def cmd_run(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    
    await message.answer("🚀 Запуск пайплайна вручную...")
    try:
        count = await run_pipeline(bot=message.bot, allow_auto_publish=False)
        await message.answer(f"✅ Пайплайн завершён. Импортировано: {count}.")
    except Exception as e:
        await message.answer(f"❌ Пайплайн завершился с ошибкой: {html.escape(str(e))}")

@router.message(Command("reroll"))
async def cmd_reroll(message: types.Message):
    if not is_admin(message.from_user.id):
        return
        
    await message.answer("♻️ Перевыбор новости (reroll)...")
    
    # 1. Reject current draft if exists
    async with async_session_maker() as session:
        stmt = select(Post).where(Post.status == "draft").order_by(Post.created_at.desc()).limit(1)
        result = await session.execute(stmt)
        post = result.scalar_one_or_none()
        
        if post:
            post.status = "rejected"
            await session.commit()
            await message.answer(f"🗑 Предыдущий черновик '{html.escape(post.title or '?')}' отклонен.")
    
    # 2. Run pipeline
    try:
        count = await run_pipeline(bot=message.bot, allow_auto_publish=False)
        await message.answer(f"✅ Новая генерация завершена.")
    except Exception as e:
        await message.answer(f"❌ Ошибка при перегенерации: {html.escape(str(e))}")

@router.message(Command("model"))
async def cmd_model(message: types.Message):
    """usage: /model [gemini-2.0-flash] or /model to see current"""
    if not is_admin(message.from_user.id):
        return

    args = message.text.split()
    current = await get_config("llm_model")
    
    if len(args) < 2:
        msg = f"🧠 <b>Текущая модель:</b> {current or 'Auto (Default)'}\n\nВозможные варианты:\n"
        msg += "- gemini-3-flash (top tier)\n"
        msg += "- gemini-2.5-flash-lite (generous free)\n"
        msg += "- gemini-2.5-flash (main free)\n"
        msg += "- gemini-2.0-flash (large context)\n"
        msg += "- gemini-2.0-flash-lite\n"
        msg += "- gemini-1.5-flash\n\n"
        msg += "Для смены: /model &lt;name&gt;"
        await message.answer(msg)
        return

    new_model = args[1].strip()
    await set_config("llm_model", new_model)
    await message.answer(f"✅ Модель переключена на: <b>{html.escape(new_model)}</b>\nПрименится при следующем запуске пайплайна.")

@router.message(Command("add_news"))
async def cmd_add_news(message: types.Message):
    """usage: /add_news https://..."""
    if not is_admin(message.from_user.id):
        return
        
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("Использование: /add_news <url>")
        return
        
    url = parts[1].strip()
    await message.answer(f"🔗 Принято. Обрабатываю ссылку вручную: {url}")
    
    try:
        await process_manual_url(url, message.bot)
    except Exception as e:
        await message.answer(f"❌ Ошибка обработки ссылки: {html.escape(str(e))}")

@router.message(Command("preview"))
async def cmd_preview(message: types.Message):
    if not is_admin(message.from_user.id):
        return
        
    async with async_session_maker() as session:
        stmt = select(Post).where(Post.status == "draft").order_by(Post.created_at.desc()).limit(1)
        result = await session.execute(stmt)
        post = result.scalar_one_or_none()
        
        if not post:
            await message.answer("Черновик не найден.")
            return

        msg_text = f"🆕 <b>Текущий черновик</b>\n\n{post.content}\n\n/publish — опубликовать\n/reject — отклонить"
        try:
            if post.image_url:
                try:
                    if len(msg_text) > 1000:
                        await message.bot.send_photo(chat_id=message.chat.id, photo=_photo_ref(post.image_url))
                        await message.answer(msg_text)
                    else:
                        await message.bot.send_photo(chat_id=message.chat.id, photo=_photo_ref(post.image_url), caption=msg_text)
                except Exception as img_err:
                     await message.answer(f"⚠️ Ошибка показа фото ({html.escape(str(img_err))}). Показываю текст:\n\n{msg_text}")
            else:
                await message.answer(msg_text)
        except Exception as e:
            await message.answer(f"Ошибка показа превью: {html.escape(str(e))}\n\n{msg_text}")

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
                 await message.answer(f"ℹ️ <b>Ручное подтверждение:</b> Игнорируем лимит повтора ({settings.no_repeat_hours} ч). Последний пост был {hours_diff:.1f} ч назад.")

        # Get latest draft
        stmt = select(Post).where(Post.status == "draft").order_by(Post.created_at.desc()).limit(1)
        result = await session.execute(stmt)
        post = result.scalar_one_or_none()
        
        if not post:
            await message.answer("Черновик для публикации не найден.")
            return
            
        # Publish
        try:
            sent_ok = False
            if post.image_url:
                 try:
                     # Check length for caption safety
                     if len(post.content) > 1000:
                          await message.bot.send_photo(chat_id=settings.target_channel_id, photo=_photo_ref(post.image_url))
                          await message.bot.send_message(chat_id=settings.target_channel_id, text=post.content, parse_mode="HTML", disable_web_page_preview=True)
                     else:
                          await message.bot.send_photo(chat_id=settings.target_channel_id, photo=_photo_ref(post.image_url), caption=post.content, parse_mode="HTML")
                     sent_ok = True
                 except Exception as img_err:
                     await message.answer(f"⚠️ Ошибка фото при публикации ({html.escape(str(img_err))}). Публикую только текст...")
                     # Fallback to text
                     await message.bot.send_message(chat_id=settings.target_channel_id, text=post.content, parse_mode="HTML", disable_web_page_preview=True)
                     sent_ok = True
            else:
                await message.bot.send_message(chat_id=settings.target_channel_id, text=post.content, parse_mode="HTML", disable_web_page_preview=True)
                sent_ok = True
            
            if sent_ok:
                post.status = "published"
                post.published_at = datetime.utcnow()
                await session.commit()
                await message.answer(f"✅ Опубликовано в {settings.target_channel_id}")
            else:
                 await message.answer("❌ Не удалось опубликовать (даже текст).")
            
        except Exception as e:
            await message.answer(f"❌ Ошибка публикации: {html.escape(str(e))}")

@router.message(Command("reject"))
async def cmd_reject(message: types.Message):
    if not is_admin(message.from_user.id):
        return
        
    async with async_session_maker() as session:
        stmt = select(Post).where(Post.status == "draft").order_by(Post.created_at.desc()).limit(1)
        result = await session.execute(stmt)
        post = result.scalar_one_or_none()
        
        if not post:
            await message.answer("Черновик для отклонения не найден.")
            return

        post.status = "rejected"
        await session.commit()
        await message.answer("🗑 Черновик отклонён.")

@router.message(Command("set_interval"))
async def cmd_set_interval(message: types.Message):
    """usage: /set_interval 4.5"""
    if not is_admin(message.from_user.id):
        return

    args = message.text.split()
    if len(args) != 2:
        val = await get_config("post_interval_hours", "6")
        await message.answer(f"Текущий интервал: {val} ч.\nИспользование: /set_interval <часы>")
        return

    try:
        hours = float(args[1])
        if hours < 0.1:
             await message.answer("❌ Слишком маленький интервал.")
             return

        await set_config("post_interval_hours", str(hours))
        await message.answer(f"✅ Интервал установлен: {hours} ч. Перезапустите бота для применения.")
    except ValueError:
        await message.answer("❌ Неверное число.")

@router.message(Command("rss_list"))
async def cmd_rss_list(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    
    feeds = await get_rss_feeds()
    if not feeds:
        await message.answer("ℹ️ RSS-ленты не настроены.")
        return
        
    text = "📋 <b>Активные RSS-ленты:</b>\n\n"
    for i, url in enumerate(feeds):
        text += f"{i+1}. {html.escape(url)}\n"
        
    text += "\nИспользуйте /rss_remove &lt;номер&gt; для удаления."
    await message.answer(text, disable_web_page_preview=True)

@router.message(Command("rss_add"))
async def cmd_rss_add(message: types.Message):
    """Использование: /rss_add https://example.com/feed"""
    if not is_admin(message.from_user.id):
        return
        
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("Использование: /rss_add <url>")
        return
        
    url = parts[1].strip()
    if await add_rss_feed(url):
        await message.answer(f"✅ Лента добавлена:\n{html.escape(url)}")
    else:
        await message.answer("⚠️ Лента уже существует.")

@router.message(Command("rss_remove"))
async def cmd_rss_remove(message: types.Message):
    """Использование: /rss_remove 1 ИЛИ /rss_remove https://..."""
    if not is_admin(message.from_user.id):
        return
        
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("Использование: /rss_remove <номер> или <url>")
        return
        
    arg = parts[1].strip()
    feeds = await get_rss_feeds()
    
    target_url = None
    
    # Try index
    if arg.isdigit():
        idx = int(arg) - 1
        if 0 <= idx < len(feeds):
            target_url = feeds[idx]
    else:
        # Try finding by string match
        if arg in feeds:
            target_url = arg
            
    if target_url:
        await remove_rss_feed(target_url)
        await message.answer(f"🗑 Лента удалена:\n{html.escape(target_url)}")
    else:
        await message.answer("❌ Лента не найдена.")

@router.message(Command("clear_cache"))
async def cmd_clear_cache(message: types.Message):
    """Удаляет необработанные записи для получения свежих новостей."""
    if not is_admin(message.from_user.id):
        return

    async with async_session_maker() as session:
        # Delete items that are NOT linked to any post (draft or published)
        # This keeps history of what we posted, but clears "candidates"
        subq = select(Post.item_id).where(Post.item_id.is_not(None))
        stmt = delete(Item).where(Item.id.not_in(subq))
        
        result = await session.execute(stmt)
        deleted = result.rowcount
        await session.commit()
        
    await message.answer(f"🧹 Кэш очищен. Удалено необработанных записей: {deleted}.\nЗапустите /run для немедленного получения новостей.")

@router.message(Command("items"))
async def cmd_items(message: types.Message):
    """View items in DB with pagination. Usage: /items [page]"""
    if not is_admin(message.from_user.id):
        return

    page = 1
    page_size = 10
    
    parts = message.text.split()
    if len(parts) > 1 and parts[1].isdigit():
        page = int(parts[1])
        if page < 1: page = 1

    offset = (page - 1) * page_size

    async with async_session_maker() as session:
        # Get total count
        total_stmt = select(func.count(Item.id))
        total = await session.scalar(total_stmt)
        
        # Get items for page
        stmt = select(Item).order_by(Item.created_at.desc()).offset(offset).limit(page_size)
        result = await session.execute(stmt)
        items = result.scalars().all()

    if not items:
        await message.answer(f"Страница {page} пуста. Всего записей: {total}")
        return

    text = f"📦 <b>Записи в базе</b> (Всего: {total})\n"
    text += f"Страница: {page} (по {page_size} шт)\n\n"

    for item in items:
        status = "✅" if item.is_processed else "🆕"
        # Truncate title
        title = item.title if len(item.title) < 50 else item.title[:47] + "..."
        date_str = item.published_at.strftime("%d.%m %H:%M") if item.published_at else "?"
        text += f"{status} <b>{html.escape(title)}</b>\n"
        text += f"📅 {date_str} | 🔗 <a href='{item.link}'>Ссылка</a>\n"
        text += f"🆔 ID: {item.id}\n\n"

    # Add navigation hint
    next_page = page + 1
    text += f"➡️ Дальше: /items {next_page}"
    
    await message.answer(text, disable_web_page_preview=True)

@router.message(Command("help"))
async def cmd_help(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    
    help_text = (
        "🤖 <b>Команды администратора</b>\n\n"
        "<b>📰 Управление пайплайном:</b>\n"
        "/status — Статус бота и статистика\n"
        "/run — Запустить пайплайн вручную\n"
        "/preview — Показать текущий черновик\n"
        "/publish — Опубликовать черновик (игнорирует лимит повторов)\n"
        "/reject — Отклонить черновик\n\n"
        "<b>⏱️ Управление расписанием:</b>\n"
        "/set_interval &lt;N&gt; — Установить интервал публикаций в часах\n"
        "/pause — Поставить расписание на паузу\n"
        "/resume — Возобновить расписание\n\n"
        "<b>📡 Управление RSS:</b>\n"
        "/rss_list — Показать активные RSS-ленты\n"
        "/rss_add &lt;url&gt; — Добавить RSS-ленту\n"
        "/rss_remove &lt;номер&gt; — Удалить RSS-ленту по номеру из /rss_list\n"
        "/items &lt;стр&gt; — Просмотр записей в БД с пагинацией\n"
        "/clear_cache — Очистить необработанные записи (опубликованные сохраняются)\n\n"
        "<b>🧠 AI настройки:</b>\n"
        "/model — Посмотреть/сменить AI модель\n"
        "/reroll — Отклонить текущий черновик и найти новую новость (перезапуск)\n"
        "/add_news &lt;url&gt; — Вручную обработать новость по ссылке\n\n"
        "<b>ℹ️ Прочее:</b>\n"
        "/help — Показать эту справку"
    )
    await message.answer(help_text)
