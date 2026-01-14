import logging
import asyncio
from datetime import datetime, timedelta
from typing import Optional, Dict, List
from sqlalchemy import select, desc
from sqlalchemy.dialects.postgresql import insert
from aiogram import Bot
from aiogram.types import BufferedInputFile, URLInputFile

from src.database import async_session_maker
from src.models import Run, Item, Post
from src.utils.rss import fetch_rss_entries
from src.config.settings import get_settings
from src.ai.service import LLMService
from src.utils.telegram_html import sanitize_telegram_html, append_footer, channel_id_to_url
from src.utils.article_image import fetch_article_image_url

logger = logging.getLogger(__name__)


def _is_http_url(value: str) -> bool:
    v = (value or "").strip().lower()
    return v.startswith("http://") or v.startswith("https://")


def _balance_candidates_by_source(
    items: List[Item],
    total: int,
    max_per_source: int,
    per_source_caps: Optional[Dict[str, int]] = None,
) -> List[Item]:
    """Pick up to `total` items, spreading across sources.

    Items must be pre-sorted by recency (desc). We keep order within each source
    and select in round-robin to prevent a single RSS from dominating.
    """
    by_source: Dict[str, List[Item]] = {}
    for it in items:
        src = (it.source or "unknown").strip() or "unknown"
        by_source.setdefault(src, []).append(it)

    sources = list(by_source.keys())
    per_source_taken: Dict[str, int] = {s: 0 for s in sources}
    idx: Dict[str, int] = {s: 0 for s in sources}
    caps = per_source_caps or {}

    def _cap_for_source(source_name: str) -> int:
        base = max_per_source
        if not caps:
            return base

        # Exact match first
        if source_name in caps:
            try:
                return int(caps[source_name])
            except Exception:
                return base

        # Case-insensitive substring patterns (e.g. "the verge" matches "The Verge - AI")
        s_low = source_name.lower()
        matched: List[int] = []
        for k, v in caps.items():
            k_low = str(k).strip().lower()
            if not k_low:
                continue
            if k_low in s_low:
                try:
                    matched.append(int(v))
                except Exception:
                    continue
        return min(matched) if matched else base

    out: List[Item] = []
    made_progress = True
    while len(out) < total and made_progress:
        made_progress = False
        for s in sources:
            if len(out) >= total:
                break
            cap = min(max_per_source, _cap_for_source(s))
            if cap < 1:
                cap = 1
            if per_source_taken[s] >= cap:
                continue
            if idx[s] >= len(by_source[s]):
                continue
            out.append(by_source[s][idx[s]])
            idx[s] += 1
            per_source_taken[s] += 1
            made_progress = True
    return out

async def run_pipeline(bot: Optional[Bot] = None):
    settings = get_settings()
    llm_service = LLMService()
    logger.info("Starting pipeline run...")
    
    if bot and settings.admin_user_ids:
        await bot.send_message(settings.admin_user_ids[0], "🕵️‍♂️ Pipeline started: Fetching RSS feeds...")

    async with async_session_maker() as session:
        # 1. Create Run record
        new_run = Run(status="running")
        session.add(new_run)
        await session.commit()
        await session.refresh(new_run)
        
        try:
            # 2. Fetch RSS
            rss_urls = settings.news_rss_urls.split(",") if settings.news_rss_urls else []
            items_data = []
            saved_count = 0
            
            if rss_urls:
                items_data = await fetch_rss_entries(rss_urls, hours=6)
                
                if bot and settings.admin_user_ids:
                     await bot.send_message(settings.admin_user_ids[0], f"📥 Fetched {len(items_data)} items. Filtering...")

                # Filter limits
                if len(items_data) > settings.max_items_per_run:
                    # Sort by published (just in case) and slice
                    items_data.sort(key=lambda x: x['published_at'] or datetime.min, reverse=True)
                    items_data = items_data[:settings.max_items_per_run]

                # 3. Save Items (Dedup by link)
                for item in items_data:
                    stmt = insert(Item).values(
                        title=item['title'],
                        link=item['link'],
                        summary=item['summary'],
                        published_at=item['published_at'],
                        source=item['source']
                    ).on_conflict_do_nothing(index_elements=['link'])
                    
                    result = await session.execute(stmt)
                    if result.rowcount > 0:
                        saved_count += 1
                await session.commit()
            
            new_run.items_count = saved_count
            logger.info(f"Ingested {saved_count} items.")
            
            # 4. Select Candidate
            # Get fresh items from DB (last 24 hours), excluding items already posted/drafted
            
            # Subquery to find item_ids that are already in Posts
            used_items_subquery = select(Post.item_id).where(Post.item_id.is_not(None))
            
            pool_limit = max(settings.candidates_for_llm * settings.candidate_pool_multiplier, settings.candidates_for_llm)
            stmt = select(Item).where(
                Item.published_at >= datetime.utcnow() - timedelta(hours=24),
                Item.id.not_in(used_items_subquery)
            ).order_by(Item.published_at.desc()).limit(pool_limit)
            
            candidates_result = await session.execute(stmt)
            pool = candidates_result.scalars().all()

            # Cooldown: if a source was published recently, reduce its quota in the candidate set.
            per_source_caps: Dict[str, int] = dict(settings.source_candidate_caps or {})
            try:
                cooldown_hours = int(settings.source_cooldown_hours)
                penalty = int(settings.source_cooldown_cap_penalty)
            except Exception:
                cooldown_hours = 6
                penalty = 2

            if cooldown_hours > 0 and penalty > 0:
                since = datetime.utcnow() - timedelta(hours=cooldown_hours)
                stmt_recent = (
                    select(Item.source)
                    .join(Post, Post.item_id == Item.id)
                    .where(Post.status == "published", Post.published_at >= since)
                    .order_by(Post.published_at.desc())
                )
                recent_res = await session.execute(stmt_recent)
                recent_sources = {((s or "unknown").strip() or "unknown") for (s,) in recent_res.all()}
                if recent_sources:
                    for s in recent_sources:
                        base = int(per_source_caps.get(s, settings.max_candidates_per_source))
                        per_source_caps[s] = max(1, base - penalty)

            candidates = _balance_candidates_by_source(
                pool,
                total=settings.candidates_for_llm,
                max_per_source=settings.max_candidates_per_source,
                per_source_caps=per_source_caps,
            )
            
            candidates_dicts = [
                {"title": c.title, "source": c.source, "summary": c.summary, "link": c.link, "id": c.id}
                for c in candidates
            ]
            
            if not candidates_dicts:
                raise Exception("No candidates found for processing.")

            selection = await llm_service.select_winner(candidates_dicts)
            winner_idx = selection.get("winner_index", 0)
            if winner_idx >= len(candidates_dicts):
                 winner_idx = 0
            
            winner_item = candidates_dicts[winner_idx]
            if bot and settings.admin_user_ids:
                 await bot.send_message(settings.admin_user_ids[0], f"🏆 Winner selected: {winner_item['title']}\n🕵️‍♀️ Researching...")
            search_queries = selection.get("search_queries", [])
            
            if bot and settings.admin_user_ids:
                 await bot.send_message(settings.admin_user_ids[0], "✍️ Generating post content and image...")

            logger.info(f"Winner selected: {winner_item['title']}")
            
            # 5. Research
            research_context = await llm_service.research_topic(search_queries)
            
            # 6. Generate Content & Image
            content_data = await llm_service.generate_post(winner_item, research_context)
            post_text = content_data.get("post_text", "")
            image_prompt = content_data.get("image_prompt", "")

            # If Google quota is exceeded, don't create a draft post with an error text.
            # Mark the run as failed and notify admin to retry later.
            if "Google AI Quota" in (post_text or ""):
                logger.warning("Google AI quota exceeded; skipping draft creation.")
                new_run.status = "failed"
                new_run.end_time = datetime.utcnow()
                new_run.log = "Google AI quota exceeded during post generation"
                await session.commit()

                if bot and settings.admin_user_ids:
                    admin_id = settings.admin_user_ids[0]
                    await bot.send_message(
                        chat_id=admin_id,
                        text=(
                            "⚠️ <b>Google AI Quota</b> — лимит исчерпан, пост не сгенерирован.\n\n"
                            "Попробуйте позже (или увеличьте квоту/смените ключ)."
                        ),
                    )
                return saved_count

            # Telegram HTML is strict; sanitize LLM output and append a guaranteed-valid footer.
            post_text = sanitize_telegram_html(post_text)
            post_text = append_footer(
                post_text,
                source_url=winner_item.get("link"),
                channel_url=channel_id_to_url(settings.target_channel_id),
            )

            if not image_prompt:
                logger.warning("No image_prompt produced by LLM; skipping image step.")
            
            image_url = None
            # Prefer article image directly (more relevant + stable)
            article_link = winner_item.get("link")
            if article_link:
                image_url = await fetch_article_image_url(article_link, timeout_sec=settings.http_timeout)

            # Fallback to our normal image pipeline if no article image is available
            if not image_url and image_prompt:
                image_url = await llm_service.generate_image(image_prompt)
            
            # 7. Save Draft Post
            new_post = Post(
                run_id=new_run.id,
                item_id=winner_item['id'],
                title=winner_item['title'],
                content=post_text,
                image_prompt=image_prompt,
                image_url=image_url,
                status="draft"
            )
            session.add(new_post)
            
            new_run.status = "success"
            new_run.end_time = datetime.utcnow()
            new_run.log = f"Ingested {saved_count}. Winner: {winner_item['title']}. Draft created."
            
            await session.commit()
            
            # 8. Notify Admin / Auto Publish
            if bot and settings.admin_user_ids:
                admin_id = settings.admin_user_ids[0]
                msg_text = f"🆕 <b>Draft Generated</b>\n\n{post_text}\n\n/publish - to publish\n/reject - to discard"
                
                try:
                    if image_url:
                        photo_ref = URLInputFile(image_url) if _is_http_url(image_url) else image_url
                        # Split caption if too long (Telegram limit 1024 for captions)
                        if len(msg_text) > 1000:
                            sent = await bot.send_photo(chat_id=admin_id, photo=photo_ref)
                            await bot.send_message(chat_id=admin_id, text=msg_text)
                        else:
                            sent = await bot.send_photo(chat_id=admin_id, photo=photo_ref, caption=msg_text)

                        # Persist Telegram file_id for reliability (URLs can expire / block hotlinks)
                        try:
                            if sent and sent.photo:
                                file_id = sent.photo[-1].file_id
                                new_post.image_url = file_id
                                await session.commit()
                        except Exception as ex2:
                            logger.warning(f"Failed to persist telegram file_id: {ex2}")
                    else:
                        if not image_prompt:
                            await bot.send_message(chat_id=admin_id, text=f"ℹ️ No image prompt was generated for this post.\n\n{msg_text}")
                        else:
                            await bot.send_message(chat_id=admin_id, text=f"⚠️ Image step failed (search+generation).\n\n{msg_text}")
                except Exception as ex:
                    logger.error(f"Failed to send preview to admin: {ex}")
                    # Fallback to text only if photo fails
                    await bot.send_message(chat_id=admin_id, text=f"⚠️ Error sending photo: {ex}\n\n{msg_text}")


            return saved_count

        except Exception as e:
            logger.error(f"Pipeline failed: {e}", exc_info=True)
            new_run.status = "failed"
            new_run.end_time = datetime.utcnow()
            new_run.log = str(e)
            await session.commit()
            raise e
