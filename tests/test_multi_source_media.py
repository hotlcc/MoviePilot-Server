"""多识别源统计、分享和数据库升级测试。"""

import asyncio

from sqlalchemy import inspect, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Base, SubscribeStatistics
from app.schemas.models import (
    MediaRecognizeShareItem,
    SubscribeShareItem,
    SubscribeStatisticItem,
)
from app.services.database_schema import ensure_database_schema
from app.services.media_recognize_share import MediaRecognizeShareService
from app.services.subscribe_share import SubscribeShareService
from app.services.subscribe_statistic import SubscribeService


def test_schemas_keep_all_media_source_fields() -> None:
    """中心服务请求模型不得静默丢弃统一身份和扩展数据源 ID。"""
    payload = {
        "bangumiid": 42,
        "anilistid": 84,
        "media_source": "plugin-anime",
        "media_id": "subject-126",
    }

    assert SubscribeStatisticItem(**payload).model_dump().items() >= payload.items()
    assert SubscribeShareItem(**payload).model_dump().items() >= payload.items()
    recognize = MediaRecognizeShareItem(
        keyword="Test",
        type="tv",
        **payload,
    )
    assert recognize.model_dump().items() >= payload.items()


def test_statistics_separate_source_namespaces_and_keep_season_zero() -> None:
    """相同数值 ID 的不同数据源和第 0 季必须分别统计。"""

    async def run_scenario() -> None:
        """在隔离的内存数据库中执行多源统计场景。"""
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        async with session_factory() as session:
            for source, media_id in (
                    ("themoviedb", "42"),
                    ("douban", "42"),
                    ("plugin-anime", "42"),
            ):
                result = await SubscribeService.add_subscribe(
                    session,
                    SubscribeStatisticItem(
                        name=f"{source} title",
                        type="电视剧",
                        media_source=source,
                        media_id=media_id,
                        season=0,
                    ),
                )
                assert result["code"] == 0

            records = (
                await session.execute(select(SubscribeStatistics))
            ).scalars().all()
            assert len(records) == 3
            assert {record.media_source for record in records} == {
                "themoviedb", "douban", "plugin-anime",
            }
            assert {record.season for record in records} == {0}
        await engine.dispose()

    asyncio.run(run_scenario())


def test_share_keeps_original_source_without_tmdb() -> None:
    """无 TMDB ID 的分享应按分享者原始数据源保存和返回。"""

    async def run_scenario() -> None:
        """在隔离数据库中创建并读取自定义源分享。"""
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        async with session_factory() as session:
            result = await SubscribeShareService.create_share(
                session,
                SubscribeShareItem(
                    share_title="自定义源分享",
                    share_user="tester",
                    name="Custom Movie",
                    type="电影",
                    media_source="plugin-metadata",
                    media_id="movie-7",
                ),
            )
            assert result["code"] == 0
            shares = await SubscribeShareService.get_shares(session)
            assert shares[0]["media_source"] == "plugin-metadata"
            assert shares[0]["media_id"] == "movie-7"
            assert shares[0]["tmdbid"] is None
        await engine.dispose()

    asyncio.run(run_scenario())


def test_media_recognize_share_keeps_custom_source_and_season_zero() -> None:
    """共享识别规范化必须保留自定义源身份和特别季。"""
    item = MediaRecognizeShareService._normalize_item_dict({
        "keyword": "Custom Show",
        "type": "tv",
        "season": 0,
        "media_source": "plugin-metadata",
        "media_id": "show-9",
    })

    assert item["media_source"] == "plugin-metadata"
    assert item["media_id"] == "show-9"
    assert item["season"] == 0
    assert MediaRecognizeShareService._build_cache_key(
        "Custom Show", "tv", season=0
    ).endswith("|0")


def test_schema_upgrade_adds_and_backfills_media_identity() -> None:
    """旧 SQLite 数据库启动时应自动补列，并按原有数据源 ID 回填。"""

    async def run_scenario() -> None:
        """构造旧表并执行幂等结构升级。"""
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as connection:
            await connection.execute(text(
                'CREATE TABLE "SUBSCRIBE_STATISTICS" ('
                "id INTEGER PRIMARY KEY, name VARCHAR NOT NULL, type VARCHAR, "
                "tmdbid INTEGER, doubanid VARCHAR, season INTEGER, count INTEGER)"
            ))
            await connection.execute(text(
                'CREATE TABLE "SUBSCRIBE_SHARE" ('
                "id INTEGER PRIMARY KEY, share_title VARCHAR NOT NULL, "
                "name VARCHAR NOT NULL, tmdbid INTEGER, doubanid VARCHAR, "
                "season INTEGER)"
            ))
            await connection.execute(text(
                'INSERT INTO "SUBSCRIBE_STATISTICS" '
                "(id, name, type, doubanid, season, count) "
                "VALUES (1, 'Legacy', '电影', '42', NULL, 1)"
            ))
            await connection.execute(text(
                'INSERT INTO "SUBSCRIBE_SHARE" '
                "(id, share_title, name, doubanid, season) "
                "VALUES (1, 'Legacy Share', 'Legacy', '84', NULL)"
            ))

        await ensure_database_schema(engine, Base, is_postgresql=False)

        async with engine.begin() as connection:
            columns = await connection.run_sync(
                lambda sync_connection: {
                    column["name"]
                    for column in inspect(sync_connection).get_columns(
                        "SUBSCRIBE_STATISTICS"
                    )
                }
            )
            assert {"bangumiid", "anilistid", "media_source", "media_id"} <= columns
            row = (
                await connection.execute(text(
                    'SELECT media_source, media_id FROM "SUBSCRIBE_STATISTICS" '
                    "WHERE id = 1"
                ))
            ).one()
            assert tuple(row) == ("douban", "42")
            share_row = (
                await connection.execute(text(
                    'SELECT media_source, media_id FROM "SUBSCRIBE_SHARE" '
                    "WHERE id = 1"
                ))
            ).one()
            assert tuple(share_row) == ("douban", "84")
        await engine.dispose()

    asyncio.run(run_scenario())
