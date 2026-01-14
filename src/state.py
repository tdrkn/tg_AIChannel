from sqlalchemy import Column, Integer, String, Boolean
from src.database import Base

class SystemState(Base):
    __tablename__ = "system_state"
    
    id = Column(Integer, primary_key=True)
    key = Column(String, unique=True, index=True)
    value = Column(String)
    is_active = Column(Boolean, default=True)

# Helper to get/set state
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
import src.database as db

async def get_state(key: str, default: str = "false") -> str:
    async with db.async_session_maker() as session:
        result = await session.execute(select(SystemState).where(SystemState.key == key))
        state = result.scalar_one_or_none()
        return state.value if state else default

async def set_state(key: str, value: str):
    async with db.async_session_maker() as session:
        stmt = insert(SystemState).values(key=key, value=value).on_conflict_do_update(
            index_elements=['key'],
            set_=dict(value=value)
        )
        await session.execute(stmt)
        await session.commit()
