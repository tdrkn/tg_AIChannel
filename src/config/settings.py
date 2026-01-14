from functools import lru_cache
from typing import Optional

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
    admin_user_ids: list[int] = []

    # App Settings
    auto_publish: bool = False
    moderation: bool = True
    
    # Limits
    max_items_per_run: int = 80
    candidates_for_llm: int = 30
    candidate_pool_multiplier: int = 5
    max_candidates_per_source: int = 6
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

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


@lru_cache
def get_settings():
    return Settings()
