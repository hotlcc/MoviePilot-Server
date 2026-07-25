"""
数据库结构初始化服务
"""
from typing import Any

from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncEngine

POSTGRESQL_SCHEMA_LOCK_ID = 2026052501

MEDIA_IDENTITY_COLUMNS = {
    "SUBSCRIBE_STATISTICS": {
        "bangumiid": "INTEGER",
        "anilistid": "INTEGER",
        "media_source": "VARCHAR",
        "media_id": "VARCHAR",
    },
    "SUBSCRIBE_SHARE": {
        "bangumiid": "INTEGER",
        "anilistid": "INTEGER",
        "media_source": "VARCHAR",
        "media_id": "VARCHAR",
    },
}

MEDIA_IDENTITY_INDEXES = {
    "SUBSCRIBE_STATISTICS": {
        "ix_SUBSCRIBE_STATISTICS_bangumiid": ("bangumiid",),
        "ix_SUBSCRIBE_STATISTICS_anilistid": ("anilistid",),
        "ix_SUBSCRIBE_STATISTICS_media_source": ("media_source",),
        "ix_SUBSCRIBE_STATISTICS_media_id": ("media_id",),
        "ix_subscribe_statistics_media_identity": (
            "media_source", "media_id", "season",
        ),
    },
    "SUBSCRIBE_SHARE": {
        "ix_SUBSCRIBE_SHARE_media_source": ("media_source",),
        "ix_SUBSCRIBE_SHARE_media_id": ("media_id",),
        "ix_subscribe_share_media_identity": (
            "media_source", "media_id", "season",
        ),
    },
}


def _ensure_media_identity_schema(connection: Connection) -> None:
    """为存量数据库补齐多数据源字段、索引并回填统一媒体身份。"""
    inspector = inspect(connection)
    table_names = set(inspector.get_table_names())
    for table_name, columns in MEDIA_IDENTITY_COLUMNS.items():
        if table_name not in table_names:
            continue
        existing_columns = {
            column["name"] for column in inspector.get_columns(table_name)
        }
        for column_name, column_type in columns.items():
            if column_name not in existing_columns:
                connection.execute(text(
                    f'ALTER TABLE "{table_name}" '
                    f'ADD COLUMN "{column_name}" {column_type}'
                ))

    inspector = inspect(connection)
    for table_name, indexes in MEDIA_IDENTITY_INDEXES.items():
        if table_name not in table_names:
            continue
        existing_indexes = {
            index["name"] for index in inspector.get_indexes(table_name)
        }
        for index_name, columns in indexes.items():
            if index_name in existing_indexes:
                continue
            column_sql = ", ".join(f'"{column}"' for column in columns)
            connection.execute(text(
                f'CREATE INDEX IF NOT EXISTS "{index_name}" '
                f'ON "{table_name}" ({column_sql})'
            ))

    for table_name in MEDIA_IDENTITY_COLUMNS:
        if table_name not in table_names:
            continue
        for source, id_field in (
                ("themoviedb", "tmdbid"),
                ("douban", "doubanid"),
                ("bangumi", "bangumiid"),
                ("anilist", "anilistid"),
        ):
            connection.execute(text(
                f'UPDATE "{table_name}" '
                f'SET media_source = :source, '
                f'media_id = CAST("{id_field}" AS VARCHAR) '
                f'WHERE (media_source IS NULL OR TRIM(media_source) = \'\' '
                f'OR media_id IS NULL OR TRIM(media_id) = \'\') '
                f'AND "{id_field}" IS NOT NULL'
            ), {"source": source})


async def ensure_database_schema(engine: AsyncEngine, base: Any, is_postgresql: bool) -> None:
    """
    确保当前数据库中存在所有已注册模型表。
    """
    if is_postgresql:
        await ensure_postgresql_schema(engine, base)
        return

    async with engine.begin() as conn:
        await conn.run_sync(base.metadata.create_all)
        await conn.run_sync(_ensure_media_identity_schema)


async def ensure_postgresql_schema(engine: AsyncEngine, base: Any) -> None:
    """
    在PostgreSQL事务级锁保护下创建所有已注册模型表。
    """
    async with engine.begin() as conn:
        await conn.execute(
            text("SELECT pg_advisory_xact_lock(:lock_id)"),
            {"lock_id": POSTGRESQL_SCHEMA_LOCK_ID},
        )
        await conn.run_sync(base.metadata.create_all)
        await conn.run_sync(_ensure_media_identity_schema)
