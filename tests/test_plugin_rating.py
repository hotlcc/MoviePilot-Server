"""插件评分持久化与 API 测试。"""

import asyncio

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.plugin_rating import router
from app.core.cache import cache_manager
from app.db.deps import get_db
from app.models import Base, PluginRating, PluginRatingSummary
from app.schemas.models import PluginRatingItem
from app.services.plugin_rating import PluginRatingService


def test_rating_schema_accepts_tenths_and_rejects_out_of_range() -> None:
    """评分请求只接受 0.1 到 5.0 之间的一位小数。"""
    assert PluginRatingItem(rating=4.3).rating == 4.3

    for invalid_rating in (0, 4.25, 5.1):
        try:
            PluginRatingItem(rating=invalid_rating)
        except ValidationError:
            continue
        raise AssertionError(f"评分 {invalid_rating} 应校验失败")


def test_rating_api_persists_details_summary_and_user_update() -> None:
    """评分 API 应保存明细、汇总平均分，并允许同一实例更新评分。"""

    async def run_scenario() -> None:
        """在隔离内存数据库中执行完整评分路由场景。"""
        cache_manager.plugin_rating_cache.clear()
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)

        async def override_get_db():
            """为路由提供隔离数据库会话。"""
            async with session_factory() as session:
                yield session

        app = FastAPI()
        app.include_router(router, prefix="/plugin")
        app.dependency_overrides[get_db] = override_get_db
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            missing_header = await client.post(
                "/plugin/rating/DemoPlugin",
                json={"rating": 4.3},
            )
            assert missing_header.status_code == 422

            first = await client.post(
                "/plugin/rating/DemoPlugin",
                headers={"X-MoviePilot-User-Uid": "user-1"},
                json={"rating": 4.3},
            )
            assert first.status_code == 200
            assert first.json() == {
                "plugin_id": "DemoPlugin",
                "average_rating": 4.3,
                "rating_count": 1,
                "user_rating": 4.3,
            }

            second = await client.post(
                "/plugin/rating/demoplugin",
                headers={"X-MoviePilot-User-Uid": "user-2"},
                json={"rating": 4.0},
            )
            assert second.status_code == 200
            assert second.json()["average_rating"] == 4.2
            assert second.json()["rating_count"] == 2

            updated = await client.post(
                "/plugin/rating/DemoPlugin",
                headers={"X-MoviePilot-User-Uid": "user-1"},
                json={"rating": 5.0},
            )
            assert updated.status_code == 200
            assert updated.json()["average_rating"] == 4.5
            assert updated.json()["rating_count"] == 2

            detail = await client.get(
                "/plugin/rating/DemoPlugin",
                headers={"X-MoviePilot-User-Uid": "user-1"},
            )
            assert detail.json()["user_rating"] == 5.0

            batch = await client.get(
                "/plugin/rating",
                params={"plugin_ids": "DemoPlugin,UnknownPlugin"},
            )
            assert batch.json()["DemoPlugin"]["average_rating"] == 4.5
            assert batch.json()["UnknownPlugin"]["rating_count"] == 0

        async with session_factory() as session:
            details = (await session.execute(select(PluginRating))).scalars().all()
            summaries = (
                await session.execute(select(PluginRatingSummary))
            ).scalars().all()
            assert len(details) == 2
            assert len(summaries) == 1
            assert float(summaries[0].average_rating) == 4.5
            assert summaries[0].rating_count == 2

        await engine.dispose()
        cache_manager.plugin_rating_cache.clear()

    asyncio.run(run_scenario())


def test_rating_query_is_cached_and_invalidated_after_rating() -> None:
    """公共汇总和实例评分应命中缓存，评分写入后必须刷新对应缓存。"""

    async def run_scenario() -> None:
        """验证缓存命中和评分事务后的精确失效。"""
        cache_manager.plugin_rating_cache.clear()
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)

        async with session_factory() as session:
            await PluginRatingService.rate_plugin(
                session,
                "CachePlugin",
                "user-1",
                4.3,
            )
            cache_manager.plugin_rating_cache.clear()
            first = await PluginRatingService.get_rating(
                session,
                "CachePlugin",
                "user-1",
            )
            assert first.average_rating == 4.3
            assert first.user_rating == 4.3

            summary = await PluginRatingSummary.read(session, "cacheplugin")
            summary.average_rating = 1.0
            detail = await PluginRating.read(session, "cacheplugin", "user-1")
            detail.rating = 1.0
            await session.commit()

            cached = await PluginRatingService.get_rating(
                session,
                "CachePlugin",
                "user-1",
            )
            assert cached.average_rating == 4.3
            assert cached.user_rating == 4.3

            await PluginRatingService.rate_plugin(
                session,
                "CachePlugin",
                "user-1",
                5.0,
            )
            refreshed = await PluginRatingService.get_rating(
                session,
                "CachePlugin",
                "user-1",
            )
            assert refreshed.average_rating == 5.0
            assert refreshed.user_rating == 5.0

        await engine.dispose()
        cache_manager.plugin_rating_cache.clear()

    asyncio.run(run_scenario())
