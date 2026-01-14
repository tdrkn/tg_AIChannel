import json
import logging
import asyncio
import re
from typing import List, Dict, Any, Optional
import google.generativeai as genai
from openai import AsyncOpenAI
from google.api_core import exceptions

from src.config.settings import get_settings
from src.utils.search import web_search, image_search

logger = logging.getLogger(__name__)

class LLMService:
    def __init__(self):
        self.settings = get_settings()
        if self.settings.google_api_key:
            genai.configure(api_key=self.settings.google_api_key)
            
            # Prioritized list of models to try
            self.model_names = [
                'gemini-3-flash-preview',
                'gemini-2.5-flash', 
                'gemini-2.0-flash', 
                'gemini-1.5-flash-latest',
                'gemini-pro'
            ]
            self.current_model_name = self.model_names[0]
            self.text_model = genai.GenerativeModel(self.current_model_name)
            logger.info(f"Initialized with model: {self.current_model_name}")
        else:
            logger.warning("GOOGLE_API_KEY not set. text generation will fail.")
            self.text_model = None

        if self.settings.openai_api_key:
            self.openai_client = AsyncOpenAI(api_key=self.settings.openai_api_key)
        else:
            logger.warning("OPENAI_API_KEY not set. Image generation will fail.")
            self.openai_client = None

    async def _generate_with_retry(self, prompt: str, retries: int = 3, delay: int = 5) -> str:
        """Helper to retry generation on 429 errors or switch models."""
        for attempt in range(retries):
            try:
                response = await self.text_model.generate_content_async(prompt)
                return response.text
            except (exceptions.ResourceExhausted, exceptions.NotFound) as e:
                logger.warning(f"Model {self.current_model_name} failed: {e}. Attempt {attempt + 1}/{retries}")
                
                # Try to switch to next model
                current_idx = -1
                if self.current_model_name in self.model_names:
                    current_idx = self.model_names.index(self.current_model_name)
                
                if current_idx + 1 < len(self.model_names):
                    self.current_model_name = self.model_names[current_idx + 1]
                    logger.info(f"Switching to fallback model: {self.current_model_name}")
                    self.text_model = genai.GenerativeModel(self.current_model_name)
                    # Don't sleep, just try immediately with new model
                    continue
                else:
                    # No more models, wait and retry current (last) model
                    logger.warning("All models exhausted or hitting limits. Sleeping...")
                    await asyncio.sleep(delay)
                    delay *= 2
            except Exception as e:
                logger.error(f"Generation error: {e}")
                raise e
        raise Exception("Max retries exceeded for text generation.")

    async def select_winner(self, items: List[Dict]) -> Dict[str, Any]:
        """
        Selects the best news item from the list.
        Returns JSON: { "winner_index": int, "reasoning": str, "search_queries": [str] }
        """
        if not items:
            return {}
        
        if not self.text_model:
             return {"winner_index": 0, "reasoning": "No LLM Configured", "search_queries": []}

        prompt = """
        You are an expert news editor for a Telegram channel about AI and Tech.
        Review the following news candidates and select ONE "winner" that is most impactful, interesting, and fresh.
        Output ONLY valid JSON.
        
        Candidates:
        """
        # Limit candidates
        limit = self.settings.candidates_for_llm
        candidates = items[:limit]
        
        for i, item in enumerate(candidates):
            # Limit item text to save tokens
            title = item.get('title', '')
            source = item.get('source', '')
            prompt += f"\n[{i}] {title} (Source: {source})"

        prompt += """
        
        Return JSON format:
        {
            "winner_index": <int>,
            "reasoning": "<short explanation why>",
            "search_queries": ["<query1>", "<query2>"]
        }
        The search queries will be used to find more details about the event.
        """

        try:
            text = await self._generate_with_retry(prompt)
            text = text.strip()
            # Clean up markdown if present
            if text.startswith("```json"):
                text = text.replace("```json", "").replace("```", "")
            if text.startswith("```"):
                text = text.replace("```", "")
            return json.loads(text)
        except Exception as e:
            logger.error(f"Error selecting winner: {e}")
            # Fallback: pick first
            return {"winner_index": 0, "reasoning": "Fallback selection", "search_queries": [items[0]['title']]}

    async def research_topic(self, queries: List[str]) -> str:
        """
        Executes web searches and summarizes facts.
        """
        consolidated_text = ""
        
        if not queries:
            return ""

        limit = self.settings.web_search_calls_limit
        for q in queries[:limit]:
            results = web_search(q, max_results=2)
            for r in results:
                consolidated_text += f"\nSource: {r['title']}\nURL: {r['href']}\nContent: {r['body']}\n"
        
        if not consolidated_text:
            return "No additional info found from web search."
            
        return consolidated_text

    async def generate_post(self, winner_item: Dict, research_context: str) -> Dict[str, str]:
        """
        Generates the final post text and image prompt.
        """
        template = self.settings.post_template
        
        if not self.text_model:
             return {
                "post_text": f"<b>{winner_item.get('title')}</b>\n\n{winner_item.get('summary')}\n\n{winner_item.get('link')}",
                "image_prompt": ""
            }

        # Use system prompt from .env or fallback to default
        system_instruction = self.settings.system_prompt
        if not system_instruction:
            system_instruction = """
            ACT AS A PROFESSIONAL RUSSIAN TECH JOURNALIST.
            GOAL: Write a high-quality Telegram post in RUSSIAN based on the provided news and research.
            Tone: Professional, informative, slightly engaging.
            Structure: Headline, Body, Key Takeaways.
            """

        prompt = f"""
        SYSTEM INSTRUCTIONS:
        {system_instruction}
        
        INPUT DATA:
        Title: {winner_item.get('title')}
        Original Summary: {winner_item.get('summary')}
        Link: {winner_item.get('link')}
        Source Name: {winner_item.get('source')}
        Research Facts (Additional Context): {research_context}
        
        TASK:
        Generate a Telegram post ('post_text') content based on the SYSTEM INSTRUCTIONS and INPUT DATA.
        Make it short and clean:
        - target length: 450–900 chars total (hard max: {self.settings.post_max_chars})
        - NO long intros, NO повторов, NO канцелярита
        - 2–3 коротких абзаца + 3 буллита (не больше)
        - emoji budget: максимум {self.settings.post_max_emojis} эмодзи на весь пост, только по делу (не в каждом предложении)
        - keep HTML valid; DO NOT output any <a href=...> links (footer will be appended automatically)
        Also generate an 'image_prompt' in English using these GUIDELINES:
        "minimalist conceptual illustration for a viral news post.
        single main object, centered.
        visual metaphor reflecting the core emotion of the news.
        object: a simple, recognizable symbol related to the topic.
        action/state: exaggerated but clean metaphor (pressure, trap, loss, control, tension).
        background: solid neutral color (black, white, light gray or muted color).
        style: ultra minimal, flat illustration, modern tech aesthetics.
        composition: lots of empty space, strong contrast.
        no text, no logos, no people, no watermark.
        designed to be eye-catching in a social media feed."
           
        STRICT OUTPUT FORMAT (JSON):
        {{
            "post_text": "YOUR_CONTENT_HERE",
            "image_prompt": "IMAGE_PROMPT_HERE"
        }}
        """
        
        try:
            text = await self._generate_with_retry(prompt)
            text = text.strip()
            if text.startswith("```json"):
                text = text.replace("```json", "").replace("```", "")
            if text.startswith("```"):
                text = text.replace("```", "")
            data = json.loads(text)
            post_text = self._polish_post_text(
                data.get("post_text", ""),
                max_chars=self.settings.post_max_chars,
                max_emojis=self.settings.post_max_emojis,
            )
            data["post_text"] = post_text
            return data
        except Exception as e:
            logger.error(f"Error generating post after retries: {e}", exc_info=True)
            # Fail gracefully but informatively
            return {
                "post_text": f"⚠️ <b>Ошибка генерации поста (Google AI Quota).</b>\n\nНе удалось перевести новость: <b>{winner_item.get('title')}</b>\n\nПопробуйте позже.",
                "image_prompt": ""
            }

    async def generate_image(self, prompt: str) -> Optional[str]:
        """
        Generates image using OpenAI Images API.
        Tries the configured model first and falls back to safer defaults.
        """
        if not prompt:
            logger.warning("Image prompt empty")
            return None

        source_mode = (self.settings.image_source or "").lower().strip()

        # New behavior requested: search twice, then generate.
        # - image_source=search: search-first -> (if not found) OpenAI fallback
        # - image_source=openai: OpenAI only
        # - image_source=auto: OpenAI first -> (if not found) search fallback
        if source_mode == "search":
            found = await self._search_image_url_with_retries(prompt)
            if found:
                return found
            # If still nothing, fall back to generation
            if not self.openai_client:
                logger.warning("OPENAI_CLIENT not set; cannot fall back to generation.")
                return None
            return await self._generate_image_openai(prompt)

        if not self.openai_client:
            logger.warning("OPENAI_CLIENT not set. Falling back to image search.")
            return await self._search_image_url_with_retries(prompt)
            
        logger.info(f"Generating image with prompt: {prompt[:100]}...")
        
        openai_url = await self._generate_image_openai(prompt)
        if openai_url:
            return openai_url

        if source_mode == "auto":
            logger.info("Falling back to image search (image_source=auto).")
            return await self._search_image_url_with_retries(prompt)

        return None

    async def _generate_image_openai(self, prompt: str) -> Optional[str]:
        """Generate image via OpenAI with model fallbacks."""
        if not self.openai_client:
            return None

        preferred_model = (self.settings.openai_image_model or "gpt-image-1").strip()
        model_candidates = [preferred_model, "gpt-image-1-mini", "dall-e-3"]

        tried: set[str] = set()
        force_next_model: Optional[str] = None

        for attempt in range(3):
            if force_next_model:
                current_model = force_next_model
                force_next_model = None
            else:
                current_model = next((m for m in model_candidates if m and m not in tried), model_candidates[-1])
            tried.add(current_model)

            request_kwargs: Dict[str, Any] = {
                "model": current_model,
                "prompt": prompt,
                "size": "1024x1024",
                "n": 1,
            }
            if current_model == "dall-e-3":
                request_kwargs["quality"] = "standard"

            try:
                response = await self.openai_client.images.generate(**request_kwargs)
                url = response.data[0].url
                logger.info(f"Image generated successfully using {current_model}.")
                return url
            except Exception as e:
                error_msg = str(e)
                logger.warning(f"Image generation attempt {attempt + 1} failed with {current_model}: {e}")

                lowered = error_msg.lower()
                if "403" in lowered or "must be verified" in lowered or "verify organization" in lowered:
                    logger.warning(
                        "OpenAI denied access to gpt-image-1* (403, org verification required). Falling back to dall-e-3."
                    )
                    force_next_model = "dall-e-3"

                if "model" in lowered and ("not found" in lowered or "does not exist" in lowered or "invalid" in lowered):
                    logger.warning("Model appears unavailable; trying next fallback model.")

                await asyncio.sleep(2 * (attempt + 1))

        logger.error("All OpenAI image generation attempts failed.")
        return None

    async def _search_image_url_with_retries(self, prompt: str) -> Optional[str]:
        """Try image search multiple times before giving up."""
        retries = max(1, int(self.settings.image_search_retries))
        delay = float(self.settings.image_search_retry_delay_sec)

        for attempt in range(retries):
            url = self._search_image_url(prompt)
            if url:
                return url
            if attempt + 1 < retries:
                logger.warning("Image search returned no usable URL; retrying (%d/%d)...", attempt + 1, retries)
                await asyncio.sleep(delay)
        return None

    @staticmethod
    def _polish_post_text(text: str, max_chars: int, max_emojis: int) -> str:
        t = (text or "").strip()
        t = re.sub(r"\n{3,}", "\n\n", t)
        t = re.sub(r"[ \t]{2,}", " ", t)

        # Soft emoji limiting: if it's extremely emoji-heavy, remove extra emoji-only runs.
        if max_emojis > 0:
            emoji_like = re.findall(r"[\U0001F300-\U0001FAFF\u2600-\u26FF\u2700-\u27BF]", t)
            if len(emoji_like) > max_emojis * 3:
                t = re.sub(r"([\U0001F300-\U0001FAFF\u2600-\u26FF\u2700-\u27BF]){2,}", "", t)

        if max_chars and len(t) > max_chars:
            # Try to preserve footer links if present
            footer_idx = t.rfind("<a href=")
            if footer_idx != -1:
                footer_start = t.rfind("\n", 0, footer_idx)
                footer_start = footer_start if footer_start != -1 else footer_idx
                footer = t[footer_start:].strip()
                body_budget = max_chars - len(footer) - 2
                body_budget = max(0, body_budget)
                body = t[:footer_start].strip()
                if len(body) > body_budget:
                    body = body[:body_budget].rstrip()
                    body = body.rsplit(" ", 1)[0] if " " in body else body
                    body = body.rstrip(".,;:-")
                    body = body + "…"
                t = f"{body}\n\n{footer}" if body else footer
            else:
                t = t[:max_chars].rstrip()
                t = t.rsplit(" ", 1)[0] if " " in t else t
                t = t.rstrip(".,;:-") + "…"

        return t

    def _search_image_url(self, prompt: str) -> Optional[str]:
        """Find a relevant image URL via search, using the same prompt text."""
        prompt_clean = " ".join((prompt or "").split())
        base_query = self._prompt_to_image_query(prompt_clean)
        fallback_query = self._fallback_image_query(prompt_clean)

        queries: List[str] = []
        for q in [base_query, base_query.replace(" illustration", ""), fallback_query, prompt_clean[:140]]:
            q = (q or "").strip()
            if not q:
                continue
            if q not in queries:
                queries.append(q)

        for q in queries:
            logger.info(
                "Searching image for query=%r (safesearch=%s, max_results=%s)",
                q,
                self.settings.image_search_safesearch,
                self.settings.image_search_max_results,
            )
            results = image_search(
                q,
                max_results=self.settings.image_search_max_results,
                safesearch=self.settings.image_search_safesearch,
            )
            logger.info("Image search returned %d results", len(results))

            for r in results:
                url = (r.get("image") or r.get("thumbnail") or "").strip()
                if url.startswith("http://") or url.startswith("https://"):
                    logger.info(f"Found image via search: {url}")
                    return url

        logger.warning("No usable image URL found via search (all query variants exhausted).")
        return None

    @staticmethod
    def _prompt_to_image_query(prompt: str) -> str:
        """Shrink the style-heavy prompt into a shorter search query."""
        p = (prompt or "").strip()
        if not p:
            return ""

        # Prefer the first clause; prompts tend to be "subject, style, constraints".
        head = p.split(".")[0]
        head = head.split("\n")[0]
        head = head.strip()

        # Remove common style boilerplate to improve search relevance.
        for phrase in [
            "minimalist conceptual illustration",
            "minimalist conceptual",
            "ultra minimal",
            "flat illustration",
            "modern tech",
            "no text",
            "no logos",
            "no watermark",
            "single main object",
            "centered",
        ]:
            head = head.replace(phrase, "")

        head = " ".join(head.replace(",", " ").split())
        if len(head) < 3:
            head = LLMService._fallback_image_query(p)

        # Add intent hint for better results.
        head = head.strip()
        return f"{head} illustration" if head else ""

    @staticmethod
    def _fallback_image_query(prompt: str) -> str:
        """A safer fallback query if prompt cleaning removes too much."""
        words = [w for w in (prompt or "").replace(",", " ").split() if w]
        core = " ".join(words[:10])
        return core.strip()
