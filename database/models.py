from datetime import datetime
from sqlalchemy import Column, Integer, String, Boolean, DateTime, BigInteger, ForeignKey, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(DeclarativeBase):
    pass


class Banner(Base):
    __tablename__ = "banners"

    id = Column(Integer, primary_key=True, autoincrement=True)
    pack_id = Column(Integer, unique=True, nullable=False, index=True)
    category = Column(String(50), nullable=False, index=True)
    title = Column(String(255), nullable=True)
    best_hit = Column(String(255), nullable=True)
    price_coins = Column(Integer, nullable=True)
    entries_per_day = Column(Integer, nullable=True)
    total_packs = Column(Integer, nullable=True)
    current_packs = Column(Integer, nullable=True)
    sale_end_date = Column(String(50), nullable=True)
    image_url = Column(Text, nullable=True)
    detail_page_url = Column(Text, nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    discord_thread = relationship("DiscordThread", back_populates="banner", uselist=False)
    pack_history = relationship("PackHistory", back_populates="banner")

    @property
    def thread_title(self) -> str:
        return f"{self.price_coins or 0}c {self.best_hit or 'Unknown'} / x{self.entries_per_day or 0} / {self.total_packs or 0}"

    @property
    def expired_thread_title(self) -> str:
        return f"[ABGELAUFEN] {self.thread_title}"


class DiscordThread(Base):
    __tablename__ = "discord_threads"

    id = Column(Integer, primary_key=True, autoincrement=True)
    banner_id = Column(Integer, ForeignKey("banners.id"), nullable=False, unique=True)
    thread_id = Column(BigInteger, nullable=False, unique=True, index=True)
    channel_id = Column(BigInteger, nullable=False)
    starter_message_id = Column(BigInteger, nullable=True)
    is_expired = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    banner = relationship("Banner", back_populates="discord_thread")
    medals = relationship("Medal", back_populates="discord_thread")


class PackHistory(Base):
    __tablename__ = "pack_history"

    id = Column(Integer, primary_key=True, autoincrement=True)
    banner_id = Column(Integer, ForeignKey("banners.id"), nullable=False, index=True)
    old_count = Column(Integer, nullable=False)
    new_count = Column(Integer, nullable=False)
    changed_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    banner = relationship("Banner", back_populates="pack_history")


class Medal(Base):
    __tablename__ = "medals"

    id = Column(Integer, primary_key=True, autoincrement=True)
    thread_id = Column(BigInteger, ForeignKey("discord_threads.thread_id"), nullable=False, index=True)
    tier = Column(String(2), nullable=False)
    user_id = Column(BigInteger, nullable=False)
    claimed_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (UniqueConstraint("thread_id", "tier", name="unique_thread_tier"),)

    discord_thread = relationship("DiscordThread", back_populates="medals")
