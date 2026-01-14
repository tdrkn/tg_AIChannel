from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from src.database import async_session_maker
from src.models import Config

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
