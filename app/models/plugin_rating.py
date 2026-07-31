"""
插件评分模型
"""
from sqlalchemy import Column, Integer, Numeric, String, UniqueConstraint, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.base import Base, get_id_column


class PluginRating(Base):
    """插件评分明细"""

    __tablename__ = "PLUGIN_RATING"
    __table_args__ = (
        UniqueConstraint("plugin_id", "user_uid", name="uq_plugin_rating_plugin_user"),
    )

    id = get_id_column()
    plugin_id = Column(String, nullable=False, index=True)
    user_uid = Column(String, nullable=False, index=True)
    rating = Column(Numeric(2, 1), nullable=False)
    created_at = Column(String, nullable=False)
    updated_at = Column(String, nullable=False, index=True)

    @classmethod
    async def read(cls, db: AsyncSession, plugin_id: str, user_uid: str):
        """查询指定安装实例对插件的评分明细"""
        result = await db.execute(
            select(cls).where(
                cls.plugin_id == plugin_id,
                cls.user_uid == user_uid,
            )
        )
        return result.scalar_one_or_none()

    @classmethod
    async def list_by_plugins(
            cls,
            db: AsyncSession,
            plugin_ids: list[str],
            user_uid: str,
    ):
        """批量查询指定安装实例对多个插件的评分"""
        if not plugin_ids:
            return []
        result = await db.execute(
            select(cls).where(
                cls.plugin_id.in_(plugin_ids),
                cls.user_uid == user_uid,
            )
        )
        return result.scalars().all()

    @classmethod
    async def aggregate(cls, db: AsyncSession, plugin_id: str):
        """汇总指定插件的平均分和评分人数"""
        result = await db.execute(
            select(func.avg(cls.rating), func.count(cls.id)).where(
                cls.plugin_id == plugin_id
            )
        )
        return result.one()


class PluginRatingSummary(Base):
    """插件评分汇总"""

    __tablename__ = "PLUGIN_RATING_SUMMARY"

    id = get_id_column()
    plugin_id = Column(String, nullable=False, unique=True, index=True)
    average_rating = Column(Numeric(2, 1), nullable=False, default=0)
    rating_count = Column(Integer, nullable=False, default=0)
    updated_at = Column(String, nullable=False)

    @classmethod
    async def read(
            cls,
            db: AsyncSession,
            plugin_id: str,
            for_update: bool = False,
    ):
        """查询评分汇总，并可锁定记录供评分事务更新"""
        statement = select(cls).where(cls.plugin_id == plugin_id)
        if for_update:
            statement = statement.with_for_update()
        result = await db.execute(statement)
        return result.scalar_one_or_none()

    @classmethod
    async def list(cls, db: AsyncSession, plugin_ids: list[str] | None = None):
        """查询全部或指定插件的评分汇总"""
        statement = select(cls)
        if plugin_ids is not None:
            if not plugin_ids:
                return []
            statement = statement.where(cls.plugin_id.in_(plugin_ids))
        result = await db.execute(statement)
        return result.scalars().all()
