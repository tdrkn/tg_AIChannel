from datetime import datetime
from sqlalchemy import Column, Integer, String, DateTime, Text, Boolean, ARRAY
from src.database import Base

class Item(Base):
    __tablename__ = "items"

    id = Column(Integer, primary_key=True)
    title = Column(String, nullable=False)
    link = Column(String, unique=True, nullable=False)
    summary = Column(Text, nullable=True)
    published_at = Column(DateTime)
    source = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Flags for processing
    is_processed = Column(Boolean, default=False)

class Run(Base):
    __tablename__ = "runs"

    id = Column(Integer, primary_key=True)
    start_time = Column(DateTime, default=datetime.utcnow)
    end_time = Column(DateTime, nullable=True)
    status = Column(String)  # success, failed
    items_count = Column(Integer, default=0)
    log = Column(Text, nullable=True)

class Post(Base):
    __tablename__ = "posts"

    id = Column(Integer, primary_key=True)
    run_id = Column(Integer, nullable=True) # Link to the run that generated it
    item_id = Column(Integer, nullable=True) # Link to the source item
    
    title = Column(String)
    content = Column(Text)
    image_prompt = Column(Text, nullable=True)
    image_url = Column(String, nullable=True)
    
    status = Column(String, default="draft") # draft, published, rejected
    created_at = Column(DateTime, default=datetime.utcnow)
    published_at = Column(DateTime, nullable=True)

class Config(Base):
    __tablename__ = "configs"
    key = Column(String, primary_key=True)
    value = Column(String, nullable=False)
