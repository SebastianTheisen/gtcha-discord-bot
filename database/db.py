from pathlib import Path
from typing import Optional, List
from datetime import datetime
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload
from loguru import logger
from .models import Base, Banner, DiscordThread, PackHistory, Medal


class Database:
    def __init__(self, database_path: Path):
        self.database_path = database_path
        database_path.parent.mkdir(parents=True, exist_ok=True)
        self.engine = create_async_engine(f"sqlite+aiosqlite:///{database_path}", echo=False)
        self.async_session = async_sessionmaker(self.engine, class_=AsyncSession, expire_on_commit=False)

    async def init_db(self) -> None:
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        logger.info(f"Datenbank initialisiert: {self.database_path}")

    async def get_banner_by_pack_id(self, pack_id: int) -> Optional[Banner]:
        async with self.async_session() as session:
            result = await session.execute(
                select(Banner)
                .options(selectinload(Banner.discord_thread))
                .where(Banner.pack_id == pack_id)
            )
            return result.scalar_one_or_none()

    async def get_all_active_banners(self) -> List[Banner]:
        async with self.async_session() as session:
            result = await session.execute(
                select(Banner)
                .options(selectinload(Banner.discord_thread))
                .where(Banner.is_active == True)
            )
            return list(result.scalars().all())

    async def create_banner(self, **kwargs) -> Banner:
        async with self.async_session() as session:
            banner = Banner(**kwargs)
            session.add(banner)
            await session.commit()
            await session.refresh(banner)
            return banner

    async def update_banner(self, pack_id: int, **kwargs) -> Optional[Banner]:
        async with self.async_session() as session:
            result = await session.execute(select(Banner).where(Banner.pack_id == pack_id))
            banner = result.scalar_one_or_none()
            if banner:
                for key, value in kwargs.items():
                    setattr(banner, key, value)
                banner.updated_at = datetime.utcnow()
                await session.commit()
                await session.refresh(banner)
            return banner

    async def mark_banner_inactive(self, pack_id: int) -> Optional[Banner]:
        return await self.update_banner(pack_id, is_active=False)

    async def get_all_pack_ids(self) -> List[int]:
        async with self.async_session() as session:
            result = await session.execute(select(Banner.pack_id))
            return [row[0] for row in result.fetchall()]

    async def get_thread_by_thread_id(self, thread_id: int) -> Optional[DiscordThread]:
        async with self.async_session() as session:
            result = await session.execute(
                select(DiscordThread)
                .options(selectinload(DiscordThread.banner))
                .where(DiscordThread.thread_id == thread_id)
            )
            return result.scalar_one_or_none()

    async def create_discord_thread(
        self,
        banner_id: int,
        thread_id: int,
        channel_id: int,
        starter_message_id: Optional[int] = None
    ) -> DiscordThread:
        async with self.async_session() as session:
            discord_thread = DiscordThread(
                banner_id=banner_id,
                thread_id=thread_id,
                channel_id=channel_id,
                starter_message_id=starter_message_id
            )
            session.add(discord_thread)
            await session.commit()
            await session.refresh(discord_thread)
            return discord_thread

    async def mark_thread_expired(self, thread_id: int) -> None:
        async with self.async_session() as session:
            await session.execute(
                update(DiscordThread)
                .where(DiscordThread.thread_id == thread_id)
                .values(is_expired=True)
            )
            await session.commit()

    async def add_pack_history(self, banner_id: int, old_count: int, new_count: int) -> PackHistory:
        async with self.async_session() as session:
            history = PackHistory(banner_id=banner_id, old_count=old_count, new_count=new_count)
            session.add(history)
            await session.commit()
            return history

    async def is_tier_claimed(self, thread_id: int, tier: str) -> bool:
        async with self.async_session() as session:
            result = await session.execute(
                select(Medal).where(Medal.thread_id == thread_id, Medal.tier == tier)
            )
            return result.scalar_one_or_none() is not None

    async def claim_medal(self, thread_id: int, tier: str, user_id: int) -> Optional[Medal]:
        if await self.is_tier_claimed(thread_id, tier):
            return None
        async with self.async_session() as session:
            medal = Medal(thread_id=thread_id, tier=tier, user_id=user_id)
            session.add(medal)
            await session.commit()
            await session.refresh(medal)
            return medal

    async def get_stats(self) -> dict:
        async with self.async_session() as session:
            active = await session.execute(select(Banner).where(Banner.is_active == True))
            total = await session.execute(select(Banner))
            threads = await session.execute(select(DiscordThread))
            medals = await session.execute(select(Medal))
            return {
                "active_banners": len(list(active.scalars().all())),
                "total_banners": len(list(total.scalars().all())),
                "total_threads": len(list(threads.scalars().all())),
                "total_medals": len(list(medals.scalars().all())),
            }
