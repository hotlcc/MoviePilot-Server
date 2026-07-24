"""
订阅统计模型
"""
from typing import Optional

from sqlalchemy import Column, Integer, String, Float, Index, or_, select, delete, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.base import Base, get_id_column
from app.schemas.models import SortType


class SubscribeStatistics(Base):
    """
    订阅统计
    """
    __tablename__ = "SUBSCRIBE_STATISTICS"

    id = get_id_column()
    # 标题
    name = Column(String, nullable=False)
    # 年份
    year = Column(String)
    # 类型
    type = Column(String, index=True)
    # 媒体编号
    tmdbid = Column(Integer, index=True)
    imdbid = Column(String)
    tvdbid = Column(Integer)
    doubanid = Column(String, index=True)
    bangumiid = Column(Integer, index=True)
    anilistid = Column(Integer, index=True)
    media_source = Column(String, index=True)
    media_id = Column(String, index=True)
    # genre_ids,分隔
    genre_ids = Column(String)
    # 季号
    season = Column(Integer)
    # 海报
    poster = Column(String)
    # 背景图
    backdrop = Column(String)
    # 评分，float
    vote = Column(Float)
    # 简介
    description = Column(String)
    # 订阅人次
    count = Column(Integer)

    __table_args__ = (
        Index(
            "ix_subscribe_statistics_media_identity",
            "media_source",
            "media_id",
            "season",
        ),
    )

    async def create(self, db: AsyncSession):
        db.add(self)
        await db.commit()
        await db.refresh(self)

    @classmethod
    async def read(
            cls,
            db: AsyncSession,
            media_source: str,
            media_id: str,
            season: Optional[int],
    ):
        """按数据源、原生 ID 和季号读取唯一统计记录。"""
        query = select(cls).where(
            cls.media_source == media_source,
            cls.media_id == str(media_id),
        )
        if season is None:
            query = query.where(cls.season.is_(None))
        else:
            query = query.where(cls.season == season)
        result = await db.execute(query)
        return result.scalars().first()

    async def update(self, db: AsyncSession, payload: dict):
        payload = {k: v for k, v in payload.items() if v is not None}
        for key, value in payload.items():
            if hasattr(self, key):
                setattr(self, key, value)
        await db.commit()
        await db.refresh(self)

    @classmethod
    async def delete(cls, db: AsyncSession, sid: int):
        await db.execute(
            delete(cls).where(cls.id == sid)
        )
        await db.commit()

    @classmethod
    async def list(cls, db: AsyncSession, stype: str, page: int = 1, count: int = 30, genre_id: int = None,
                   min_rating: float = None, max_rating: float = None, sort_type: SortType = SortType.COUNT):
        query = select(cls).where(cls.type == stype)

        # 如果提供了genre_id，则添加genre_ids过滤条件
        if genre_id is not None:
            # 使用正确的分隔符匹配，避免部分数字匹配问题
            query = query.where(
                or_(
                    cls.genre_ids == str(genre_id),  # 只有一个genre_id的情况
                    cls.genre_ids.like(f'{genre_id},%'),  # 开头的genre_id
                    cls.genre_ids.like(f'%,{genre_id},%'),  # 中间的genre_id
                    cls.genre_ids.like(f'%,{genre_id}')  # 结尾的genre_id
                )
            )

        # 如果提供了评分范围，则添加评分过滤条件
        if min_rating is not None:
            query = query.where(cls.vote >= min_rating)
        if max_rating is not None:
            query = query.where(cls.vote <= max_rating)

        # 根据排序类型添加排序
        if sort_type == SortType.COUNT:
            query = query.order_by(desc(cls.count))
        elif sort_type == SortType.RATING:
            query = query.order_by(desc(cls.vote))
        elif sort_type == SortType.TIME:
            # 改为按年份倒序、人数倒序
            query = query.order_by(desc(cls.year), desc(cls.count))
        else:
            # 默认按人数倒序
            query = query.order_by(desc(cls.count))

        result = await db.execute(
            query
            .offset((page - 1) * count)
            .limit(count)
        )
        return result.scalars().all()

    def dict(self):
        return {c.name: getattr(self, c.name, None) for c in self.__table__.columns}
