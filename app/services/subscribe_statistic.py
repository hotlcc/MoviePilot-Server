"""
订阅统计服务
"""
from typing import Dict, Any, List

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cache import cache_manager
from app.core.media import build_legacy_media_identity, resolve_media_identity
from app.models import SubscribeStatistics
from app.schemas.models import SubscribeStatisticItem, SortType


class SubscribeService:
    """订阅统计服务类"""

    @staticmethod
    def _fill_media_identity(
            subscribe: SubscribeStatisticItem,
    ) -> tuple[str | None, str | None]:
        """规范化统计项的统一媒体身份。"""
        media_source, media_id = resolve_media_identity(subscribe)
        subscribe.media_source = media_source
        subscribe.media_id = media_id
        return media_source, media_id

    @staticmethod
    async def add_subscribe(db: AsyncSession, subscribe: SubscribeStatisticItem) -> Dict[str, Any]:
        """添加订阅统计"""
        media_source, media_id = SubscribeService._fill_media_identity(subscribe)
        if not media_source or not media_id or not subscribe.type or not subscribe.name:
            return {"code": 1, "message": "元数据不完整"}

        # 查询数据库中是否存在
        sub = await SubscribeStatistics.read(
            db,
            media_source=media_source,
            media_id=media_id,
            season=subscribe.season,
        )

        # 如果不存在则创建
        if not sub:
            sub = SubscribeStatistics(**subscribe.storage_payload(), count=1)
            await sub.create(db)
        # 如果存在则更新
        else:
            await sub.update(db, {"count": (sub.count or 0) + 1})

        cache_manager.statistic_cache.clear()

        return {"code": 0, "message": "success"}

    @staticmethod
    async def done_subscribe(db: AsyncSession, subscribe: SubscribeStatisticItem) -> Dict[str, Any]:
        """完成订阅更新统计"""
        media_source, media_id = SubscribeService._fill_media_identity(subscribe)
        if not media_source or not media_id:
            return {"code": 1, "message": "媒体身份不完整"}
        # 查询数据库中是否存在
        sub = await SubscribeStatistics.read(
            db,
            media_source=media_source,
            media_id=media_id,
            season=subscribe.season,
        )

        # 如果存在则更新
        if sub:
            if sub.count <= 1:
                await sub.delete(db, sub.id)
            else:
                await sub.update(db, {"count": sub.count - 1})

        cache_manager.statistic_cache.clear()

        return {"code": 0, "message": "success"}

    @staticmethod
    async def batch_report_subscribes(db: AsyncSession, subscribes: List[SubscribeStatisticItem]) -> Dict[str, Any]:
        """批量添加订阅统计"""
        for subscribe in subscribes:
            media_source, media_id = SubscribeService._fill_media_identity(subscribe)
            if not media_source or not media_id or not subscribe.type or not subscribe.name:
                continue

            sub = await SubscribeStatistics.read(
                db,
                media_source=media_source,
                media_id=media_id,
                season=subscribe.season,
            )
            if not sub:
                sub = SubscribeStatistics(**subscribe.storage_payload(), count=1)
                db.add(sub)
            else:
                sub.count = (sub.count or 0) + 1

        await db.commit()
        cache_manager.statistic_cache.clear()
        return {"code": 0, "message": "success"}

    @staticmethod
    async def get_statistics(db: AsyncSession, stype: str, page: int = 1, count: int = 30, genre_id: int = None,
                             min_rating: float = None, max_rating: float = None, sort_type: SortType = SortType.COUNT) -> List[Dict[str, Any]]:
        """查询订阅统计"""
        cache_key = f"subscribe_{stype}_{page}_{count}_{genre_id}_{min_rating}_{max_rating}_{sort_type}"
        cached_data = cache_manager.statistic_cache.get(cache_key)

        if cached_data is None:
            statistics = await SubscribeStatistics.list(db, stype=stype, page=page, count=count, genre_id=genre_id,
                                                        min_rating=min_rating, max_rating=max_rating, sort_type=sort_type)
            cached_data = [
                build_legacy_media_identity(statistic.dict())
                for statistic in statistics
            ]
            cache_manager.statistic_cache.set(cache_key, cached_data)

        return cached_data
