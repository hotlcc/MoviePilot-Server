"""安装版本统计的上报过滤和报表筛选测试。"""

import asyncio
from unittest.mock import AsyncMock, Mock

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.cache import cache_manager
from app.models.base import Base
from app.models.usage_statistic import UsageStatistics
from app.schemas.models import UsageStatisticItem
from app.services.usage_statistic import UsageService


def test_usage_report_ignores_requests_without_user_or_version(monkeypatch) -> None:
    """缺少用户 ID 或版本号的上报不写入数据库。"""

    async def run_scenario() -> None:
        record_usage = AsyncMock()
        invalidate_cache = Mock()
        monkeypatch.setattr(UsageService, "_record_usage", record_usage)
        monkeypatch.setattr(UsageService, "_invalidate_statistics_cache", invalidate_cache)
        db = AsyncMock()

        cases = [
            UsageStatisticItem(backend_version="v2.0.0"),
            UsageStatisticItem(user_uid="user-1"),
            UsageStatisticItem(user_uid=" ", backend_version="v2.0.0"),
            UsageStatisticItem(user_uid="user-2", backend_version=" "),
        ]
        for usage in cases:
            result = await UsageService.report_usage(db, usage)
            assert result == {"code": 0, "message": "success"}

        record_usage.assert_not_awaited()
        db.commit.assert_not_awaited()
        invalidate_cache.assert_not_called()

    asyncio.run(run_scenario())


def test_usage_report_records_identified_version(monkeypatch) -> None:
    """同时包含用户 ID 和版本号的上报会累计安装统计。"""

    async def run_scenario() -> None:
        record_usage = AsyncMock()
        invalidate_cache = Mock()
        monkeypatch.setattr(UsageService, "_record_usage", record_usage)
        monkeypatch.setattr(UsageService, "_invalidate_statistics_cache", invalidate_cache)
        db = AsyncMock()
        usage = UsageStatisticItem(
            user_uid="user-1",
            backend_version="v2.0.0",
        )

        result = await UsageService.report_usage(db, usage)

        assert result == {"code": 0, "message": "success"}
        record_usage.assert_awaited_once()
        db.commit.assert_awaited_once()
        invalidate_cache.assert_called_once()

    asyncio.run(run_scenario())


def test_statistics_response_does_not_include_unknown_users(monkeypatch) -> None:
    """统计报表只返回已识别安装数，不再合并 Redis 未知用户。"""

    async def run_scenario() -> None:
        cache_manager.usage_statistic_cache.clear()
        monkeypatch.setattr(
            UsageStatistics,
            "list_backend_version_counts",
            AsyncMock(return_value=[("v2.0.0", 2)]),
        )
        monkeypatch.setattr(
            UsageStatistics,
            "list_frontend_version_counts",
            AsyncMock(return_value=[("v2.0.0", 2)]),
        )
        monkeypatch.setattr(UsageStatistics, "count_all", AsyncMock(return_value=2))
        monkeypatch.setattr(
            UsageStatistics,
            "count_active_since",
            AsyncMock(side_effect=[1, 2, 2]),
        )

        result = await UsageService.get_statistics(AsyncMock())

        assert result["total_users"] == 2
        assert result["backend_versions"] == [{"version": "v2.0.0", "count": 2}]
        assert result["frontend_versions"] == [{"version": "v2.0.0", "count": 2}]
        assert "other_users" not in result
        assert "reported_users" not in result
        cache_manager.usage_statistic_cache.clear()

    asyncio.run(run_scenario())


def test_statistics_exclude_records_without_user_or_version() -> None:
    """报表忽略历史上缺少用户 ID 或所有版本号的记录。"""

    async def run_scenario() -> None:
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)

            async with session_factory() as db:
                db.add_all([
                    UsageStatistics(
                        user_uid="user-1",
                        backend_version="v2.0.0",
                        frontend_version="v2.0.0",
                        last_seen_at="2026-08-05 10:00:00",
                    ),
                    UsageStatistics(
                        user_uid="user-2",
                        frontend_version="v2.0.0",
                        last_seen_at="2026-08-05 10:00:00",
                    ),
                    UsageStatistics(
                        user_uid="user-3",
                        last_seen_at="2026-08-05 10:00:00",
                    ),
                    UsageStatistics(
                        user_uid=" ",
                        backend_version="v2.0.0",
                        last_seen_at="2026-08-05 10:00:00",
                    ),
                ])
                await db.commit()

                assert await UsageStatistics.count_all(db) == 2
                assert await UsageStatistics.count_active_since(
                    db,
                    "2026-08-05 00:00:00",
                ) == 2
                assert await UsageStatistics.list_backend_version_counts(db) == [
                    ("v2.0.0", 1),
                ]
                assert await UsageStatistics.list_frontend_version_counts(db) == [
                    ("v2.0.0", 2),
                ]
        finally:
            await engine.dispose()

    asyncio.run(run_scenario())
