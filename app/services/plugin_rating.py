"""
插件评分服务
"""
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cache import cache_manager
from app.models import PluginRating, PluginRatingSummary
from app.schemas.models import PluginRatingResult


class PluginRatingService:
    """插件评分服务"""

    RATING_QUANTUM = Decimal("0.1")
    RATING_CACHE_PREFIX = "plugin_rating"
    USER_RATING_CACHE_PREFIX = "plugin_user_rating"

    @staticmethod
    def _plugin_key(plugin_id: str) -> str:
        """生成大小写无关的插件评分存储键"""
        return plugin_id.strip().lower()

    @staticmethod
    def _round_rating(rating: Decimal) -> Decimal:
        """按四舍五入规则将评分统一到一位小数"""
        return rating.quantize(
            PluginRatingService.RATING_QUANTUM,
            rounding=ROUND_HALF_UP,
        )

    @staticmethod
    def _cache_key(plugin_key: str) -> str:
        """生成插件公共评分汇总缓存键"""
        return f"{PluginRatingService.RATING_CACHE_PREFIX}:{plugin_key}"

    @staticmethod
    def _user_rating_cache_key(plugin_key: str, user_uid: str) -> str:
        """生成当前安装实例评分缓存键"""
        return (
            f"{PluginRatingService.USER_RATING_CACHE_PREFIX}:"
            f"{plugin_key}:{user_uid}"
        )

    @staticmethod
    def _summary_payload(summary: PluginRatingSummary | None) -> dict[str, float | int]:
        """将评分汇总转换为可缓存的基础数据"""
        return {
            "average_rating": float(summary.average_rating) if summary else 0.0,
            "rating_count": summary.rating_count if summary else 0,
        }

    @staticmethod
    async def _get_summary_payloads(
            db: AsyncSession,
            plugin_keys: list[str],
    ) -> dict[str, dict[str, float | int]]:
        """优先从缓存批量读取评分汇总，并一次查询所有缺失项"""
        payloads: dict[str, dict[str, float | int]] = {}
        missing_keys = []
        for plugin_key in plugin_keys:
            cached_payload = cache_manager.plugin_rating_cache.get(
                PluginRatingService._cache_key(plugin_key)
            )
            if cached_payload is None:
                missing_keys.append(plugin_key)
            else:
                payloads[plugin_key] = cached_payload

        if missing_keys:
            summaries = await PluginRatingSummary.list(db, missing_keys)
            summary_by_key = {item.plugin_id: item for item in summaries}
            for plugin_key in missing_keys:
                payload = PluginRatingService._summary_payload(
                    summary_by_key.get(plugin_key)
                )
                payloads[plugin_key] = payload
                cache_manager.plugin_rating_cache.set(
                    PluginRatingService._cache_key(plugin_key),
                    payload,
                )
        return payloads

    @staticmethod
    async def _get_user_ratings(
            db: AsyncSession,
            plugin_keys: list[str],
            user_uid: str,
    ) -> dict[str, float | None]:
        """优先从缓存批量读取当前安装实例评分，并一次查询所有缺失项"""
        ratings: dict[str, float | None] = {}
        missing_keys = []
        for plugin_key in plugin_keys:
            cached_payload = cache_manager.plugin_rating_cache.get(
                PluginRatingService._user_rating_cache_key(
                    plugin_key,
                    user_uid,
                )
            )
            if cached_payload is None:
                missing_keys.append(plugin_key)
            else:
                ratings[plugin_key] = cached_payload["user_rating"]

        if missing_keys:
            details = await PluginRating.list_by_plugins(
                db,
                missing_keys,
                user_uid,
            )
            detail_by_key = {item.plugin_id: item for item in details}
            for plugin_key in missing_keys:
                detail = detail_by_key.get(plugin_key)
                user_rating = float(detail.rating) if detail else None
                ratings[plugin_key] = user_rating
                cache_manager.plugin_rating_cache.set(
                    PluginRatingService._user_rating_cache_key(
                        plugin_key,
                        user_uid,
                    ),
                    {"user_rating": user_rating},
                )
        return ratings

    @staticmethod
    async def _save_rating(
            db: AsyncSession,
            plugin_id: str,
            user_uid: str,
            rating: Decimal,
    ) -> PluginRatingResult:
        """在同一事务内写入评分明细并重算汇总"""
        plugin_key = PluginRatingService._plugin_key(plugin_id)
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        summary = await PluginRatingSummary.read(
            db,
            plugin_key,
            for_update=True,
        )
        if summary is None:
            summary = PluginRatingSummary(
                plugin_id=plugin_key,
                average_rating=Decimal("0.0"),
                rating_count=0,
                updated_at=now,
            )
            db.add(summary)
            await db.flush()

        detail = await PluginRating.read(db, plugin_key, user_uid)
        if detail is None:
            detail = PluginRating(
                plugin_id=plugin_key,
                user_uid=user_uid,
                rating=rating,
                created_at=now,
                updated_at=now,
            )
            db.add(detail)
        else:
            detail.rating = rating
            detail.updated_at = now
        await db.flush()

        average_rating, rating_count = await PluginRating.aggregate(db, plugin_key)
        rounded_average = PluginRatingService._round_rating(
            Decimal(str(average_rating or 0))
        )
        summary.average_rating = rounded_average
        summary.rating_count = int(rating_count or 0)
        summary.updated_at = now
        await db.commit()
        cache_manager.plugin_rating_cache.delete(
            PluginRatingService._cache_key(plugin_key)
        )
        cache_manager.plugin_rating_cache.set(
            PluginRatingService._user_rating_cache_key(plugin_key, user_uid),
            {"user_rating": float(rating)},
        )

        return PluginRatingResult(
            plugin_id=plugin_id,
            average_rating=float(rounded_average),
            rating_count=summary.rating_count,
            user_rating=float(rating),
        )

    @staticmethod
    async def rate_plugin(
            db: AsyncSession,
            plugin_id: str,
            user_uid: str,
            rating: float,
    ) -> PluginRatingResult:
        """新增或更新安装实例对插件的评分"""
        normalized_rating = PluginRatingService._round_rating(Decimal(str(rating)))
        try:
            return await PluginRatingService._save_rating(
                db,
                plugin_id,
                user_uid,
                normalized_rating,
            )
        except IntegrityError:
            # 首次评分并发创建汇总记录时，唯一键冲突后重试即可复用已创建的汇总行锁。
            await db.rollback()
            return await PluginRatingService._save_rating(
                db,
                plugin_id,
                user_uid,
                normalized_rating,
            )

    @staticmethod
    async def get_rating(
            db: AsyncSession,
            plugin_id: str,
            user_uid: str | None = None,
    ) -> PluginRatingResult:
        """查询单个插件的评分汇总和当前安装实例评分"""
        plugin_key = PluginRatingService._plugin_key(plugin_id)
        summary_payloads = await PluginRatingService._get_summary_payloads(
            db,
            [plugin_key],
        )
        summary_payload = summary_payloads[plugin_key]
        user_ratings = (
            await PluginRatingService._get_user_ratings(
                db,
                [plugin_key],
                user_uid,
            )
            if user_uid
            else {}
        )
        return PluginRatingResult(
            plugin_id=plugin_id,
            average_rating=float(summary_payload["average_rating"]),
            rating_count=int(summary_payload["rating_count"]),
            user_rating=user_ratings.get(plugin_key),
        )

    @staticmethod
    async def get_ratings(
            db: AsyncSession,
            plugin_ids: list[str] | None = None,
            user_uid: str | None = None,
    ) -> dict[str, PluginRatingResult]:
        """批量查询插件评分，并保留请求中的插件 ID 写法"""
        requested_ids = list(dict.fromkeys(
            plugin_id.strip()
            for plugin_id in (plugin_ids or [])
            if plugin_id.strip()
        ))
        plugin_keys = [
            PluginRatingService._plugin_key(plugin_id)
            for plugin_id in requested_ids
        ]
        if plugin_ids is None:
            summaries = await PluginRatingSummary.list(db)
            plugin_keys = [item.plugin_id for item in summaries]
            for summary in summaries:
                cache_manager.plugin_rating_cache.set(
                    PluginRatingService._cache_key(summary.plugin_id),
                    PluginRatingService._summary_payload(summary),
                )
        summary_payloads = await PluginRatingService._get_summary_payloads(
            db,
            plugin_keys,
        )
        user_ratings = (
            await PluginRatingService._get_user_ratings(
                db,
                plugin_keys,
                user_uid,
            )
            if user_uid
            else {}
        )

        response_ids = requested_ids if plugin_ids is not None else plugin_keys
        results = {}
        for response_id in response_ids:
            plugin_key = PluginRatingService._plugin_key(response_id)
            summary_payload = summary_payloads.get(plugin_key, {})
            results[response_id] = PluginRatingResult(
                plugin_id=response_id,
                average_rating=float(summary_payload.get("average_rating", 0.0)),
                rating_count=int(summary_payload.get("rating_count", 0)),
                user_rating=user_ratings.get(plugin_key),
            )
        return results
