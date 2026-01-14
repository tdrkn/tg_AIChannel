from functools import lru_cache
import json
from typing import Optional

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    bot_token: str
    google_api_key: str
    target_channel_id: str
    news_rss_urls: Optional[str] = None  # Comma-separated RSS URLs
    post_template: Optional[str] = None  # e.g., "Title: {title}\nSummary: {summary}\nLink: {link}"
    
    # AI Persona
    system_prompt: Optional[str] = None

    # Database
    postgres_user: str = "postgres"
    postgres_password: str = "postgres"
    postgres_db: str = "aichannel"
    postgres_host: str = "db"
    postgres_port: int = 5432

    # OpenAI (for Images)
    openai_api_key: Optional[str] = None
    openai_image_model: str = "gpt-image-1"

    # Images pipeline
    # - "search": do not generate; find a relevant image URL via search
    # - "openai": generate via OpenAI only
    # - "auto": try OpenAI, then fall back to search
    image_source: str = "search"
    image_search_max_results: int = 8
    image_search_safesearch: str = "moderate"  # off|moderate|strict
    image_search_retries: int = 2
    image_search_retry_delay_sec: float = 0.7
    
    # Admin
    admin_user_ids: list[int] = Field(default_factory=list)

    # App Settings
    auto_publish: bool = False
    moderation: bool = True
    
    # Limits
    max_items_per_run: int = 80
    candidates_for_llm: int = 30
    candidate_pool_multiplier: int = 5
    max_candidates_per_source: int = 6
    source_candidate_caps: dict[str, int] = Field(default_factory=dict)
    source_cooldown_hours: int = 6
    source_cooldown_cap_penalty: int = 2
    web_search_calls_limit: int = 3
    llm_timeout: int = 30
    http_timeout: int = 10
    no_repeat_hours: int = 2

    # Post style controls
    post_max_chars: int = 900
    post_max_emojis: int = 4

    # Default Template
    post_template: str = (
        "<b>{title}</b>\n\n"
        "{summary}\n\n"
        "Главное:\n"
        "• {bullet_1}\n"
        "• {bullet_2}\n"
        "• {bullet_3}\n\n"
        "Источники: {sources}"
    )

    @property
    def database_url(self) -> str:
        return f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"

    # NOTE: We disable automatic JSON-decoding for complex fields so that we can
    # support both JSON and simple comma-separated formats from .env.
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", enable_decoding=False)

    @field_validator("admin_user_ids", mode="before")
    @classmethod
    def _parse_admin_user_ids(cls, v):
        """Supports JSON list ([1,2]) and convenient '1,2' formats."""
        if v is None or v == "":
            return []
        if isinstance(v, list):
            out: list[int] = []
            for x in v:
                try:
                    out.append(int(x))
                except Exception:
                    continue
            return out
        if isinstance(v, str):
            s = v.strip()
            if not s:
                return []
            if s.startswith("["):
                try:
                    parsed = json.loads(s)
                    if isinstance(parsed, list):
                        return [int(x) for x in parsed if str(x).strip()]
                except Exception:
                    return []
            # Comma/space separated: 123,456 or 123 456
            parts = [p.strip() for p in s.replace(" ", ",").split(",") if p.strip()]
            out: list[int] = []
            for part in parts:
                try:
                    out.append(int(part))
                except Exception:
                    continue
            return out
        return []

    @field_validator("source_candidate_caps", mode="before")
    @classmethod
    def _parse_source_candidate_caps(cls, v):
        """Supports both JSON and a convenient 'name=4,name2=2' format."""
        if v is None or v == "":
            return {}
        if isinstance(v, dict):
            return {str(k): int(vv) for k, vv in v.items()}
        if isinstance(v, str):
            s = v.strip()
            if not s:
                return {}

            # JSON style: {"The Verge": 4, "vc.ru": 6}
            if s.startswith("{"):
                parsed = json.loads(s)
                if isinstance(parsed, dict):
                    return {str(k): int(vv) for k, vv in parsed.items()}
                return {}

            # Convenient style: The Verge=4,vc.ru=6,TechCrunch:3
            out: dict[str, int] = {}
            parts = [p.strip() for p in s.split(",") if p.strip()]
            for part in parts:
                if "=" in part:
                    k, vv = part.split("=", 1)
                elif ":" in part:
                    k, vv = part.split(":", 1)
                else:
                    continue
                key = k.strip().strip('"').strip("'")
                val = vv.strip().strip('"').strip("'")
                if not key:
                    continue
                try:
                    out[key] = int(float(val))
                except Exception:
                    continue
            return out
        return {}


@lru_cache
def get_settings():
    return Settings()
