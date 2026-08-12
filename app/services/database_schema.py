"""
数据库结构初始化服务
"""
from typing import Any

from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.media import (
    MEDIA_SOURCE_ALIASES as MEDIA_SOURCE_ENUM_ALIASES,
    MEDIA_SOURCE_VALUES,
    media_identity_check_sql,
)

# 跨版本启动必须竞争同一把锁，避免滚动发布期间并发修改订阅表结构。
POSTGRESQL_SCHEMA_LOCK_ID = 2026052501

SUBSCRIBE_UNIFIED_COLUMNS = {
    "SUBSCRIBE_STATISTICS": {
        "media_source": "VARCHAR",
        "media_id": "VARCHAR",
        "music_type": "VARCHAR",
        "total_tracks": "INTEGER",
    },
    "SUBSCRIBE_SHARE": {
        "media_source": "VARCHAR",
        "media_id": "VARCHAR",
        "music_type": "VARCHAR",
        "total_tracks": "INTEGER",
    },
}

LEGACY_MEDIA_ID_COLUMNS = (
    ("themoviedb", "tmdbid"),
    ("douban", "doubanid"),
    ("bangumi", "bangumiid"),
    ("anilist", "anilistid"),
    ("imdb", "imdbid"),
    ("tvdb", "tvdbid"),
)

MEDIA_SOURCE_ALIASES = {
    alias: source.value for alias, source in MEDIA_SOURCE_ENUM_ALIASES.items()
}
MEDIA_IDENTITY_INDEXES = {
    "SUBSCRIBE_STATISTICS": {
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

MEDIA_IDENTITY_CONSTRAINTS = {
    "SUBSCRIBE_STATISTICS": "ck_subscribe_statistics_media_identity",
    "SUBSCRIBE_SHARE": "ck_subscribe_share_media_identity",
}


def _ensure_media_identity_constraints(
        connection: Connection,
        table_names: set[str],
) -> None:
    """为存量订阅表补齐数据库级统一媒体身份原子约束。"""
    dialect_name = connection.dialect.name
    for table_name, constraint_name in MEDIA_IDENTITY_CONSTRAINTS.items():
        if table_name not in table_names:
            continue
        if dialect_name == "postgresql":
            existing_constraints = {
                constraint.get("name")
                for constraint in inspect(connection).get_check_constraints(table_name)
            }
            if constraint_name in existing_constraints:
                continue
            connection.execute(text(
                f'ALTER TABLE "{table_name}" '
                f'ADD CONSTRAINT "{constraint_name}" '
                f'CHECK ({media_identity_check_sql()})'
            ))
            continue

        if dialect_name != "sqlite":
            continue
        # SQLite 不支持 ALTER TABLE ADD CHECK，存量表用等价触发器兜底。
        trigger_prefix = constraint_name.removeprefix("ck_")
        insert_trigger = f"trg_{trigger_prefix}_insert"
        update_trigger = f"trg_{trigger_prefix}_update"
        check_sql = media_identity_check_sql("NEW")
        connection.execute(text(
            f'CREATE TRIGGER IF NOT EXISTS "{insert_trigger}" '
            f'BEFORE INSERT ON "{table_name}" '
            f'FOR EACH ROW WHEN NOT ({check_sql}) '
            "BEGIN SELECT RAISE(ABORT, 'invalid media identity'); END"
        ))
        connection.execute(text(
            f'CREATE TRIGGER IF NOT EXISTS "{update_trigger}" '
            f'BEFORE UPDATE OF media_source, media_id ON "{table_name}" '
            f'FOR EACH ROW WHEN NOT ({check_sql}) '
            "BEGIN SELECT RAISE(ABORT, 'invalid media identity'); END"
        ))


def _ensure_subscribe_identity_schema(connection: Connection) -> None:
    """迁移订阅表身份字段：补列、回填、规范来源并删除旧专用 ID 列。"""
    inspector = inspect(connection)
    table_names = set(inspector.get_table_names())
    for table_name, columns in SUBSCRIBE_UNIFIED_COLUMNS.items():
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

    for table_name in SUBSCRIBE_UNIFIED_COLUMNS:
        if table_name not in table_names:
            continue
        existing_columns = {
            column["name"] for column in inspect(connection).get_columns(table_name)
        }

        for alias, source in MEDIA_SOURCE_ALIASES.items():
            connection.execute(text(
                f'UPDATE "{table_name}" SET media_source = :source '
                f'WHERE LOWER(TRIM(media_source)) = :alias'
            ), {"source": source, "alias": alias})

        allowed_source_params = {
            f"allowed_source_{index}": source
            for index, source in enumerate(MEDIA_SOURCE_VALUES)
        }
        allowed_source_sql = ", ".join(
            f":allowed_source_{index}" for index in range(len(MEDIA_SOURCE_VALUES))
        )
        invalid_identity_sql = (
            "media_source IS NULL OR TRIM(media_source) = '' "
            "OR media_id IS NULL OR TRIM(media_id) IN ('', '0') "
            f"OR LOWER(TRIM(media_source)) NOT IN ({allowed_source_sql})"
        )
        for source, id_field in LEGACY_MEDIA_ID_COLUMNS:
            if id_field not in existing_columns:
                continue
            connection.execute(text(
                f'UPDATE "{table_name}" '
                f'SET media_source = :source, '
                f'media_id = TRIM(CAST("{id_field}" AS VARCHAR)) '
                f'WHERE ({invalid_identity_sql}) '
                f'AND "{id_field}" IS NOT NULL '
                f'AND TRIM(CAST("{id_field}" AS VARCHAR)) NOT IN (\'\', \'0\')'
            ), {"source": source, **allowed_source_params})

        # 统一身份必须成对存在且来源固定；无法从旧列补齐的身份不再保留。
        connection.execute(text(
            f'UPDATE "{table_name}" '
            'SET media_source = NULL, media_id = NULL '
            f'WHERE {invalid_identity_sql}'
        ), allowed_source_params)
        connection.execute(text(
            f'UPDATE "{table_name}" SET media_id = TRIM(media_id) '
            'WHERE media_id IS NOT NULL'
        ))

        legacy_columns = {
            column for _, column in LEGACY_MEDIA_ID_COLUMNS
            if column in existing_columns
        }
        if legacy_columns:
            for index in inspect(connection).get_indexes(table_name):
                index_name = index.get("name")
                index_columns = set(index.get("column_names") or [])
                if index_name and index_columns.intersection(legacy_columns):
                    connection.execute(text(
                        f'DROP INDEX IF EXISTS "{index_name}"'
                    ))
            for column_name in legacy_columns:
                connection.execute(text(
                    f'ALTER TABLE "{table_name}" '
                    f'DROP COLUMN "{column_name}"'
                ))

    for table_name, indexes in MEDIA_IDENTITY_INDEXES.items():
        if table_name not in table_names:
            continue
        existing_indexes = {
            index["name"] for index in inspect(connection).get_indexes(table_name)
        }
        for index_name, columns in indexes.items():
            if index_name in existing_indexes:
                continue
            column_sql = ", ".join(f'"{column}"' for column in columns)
            connection.execute(text(
                f'CREATE INDEX IF NOT EXISTS "{index_name}" '
                f'ON "{table_name}" ({column_sql})'
            ))

    _ensure_media_identity_constraints(connection, table_names)

async def ensure_database_schema(engine: AsyncEngine, base: Any, is_postgresql: bool) -> None:
    """
    确保当前数据库中存在所有已注册模型表。
    """
    if is_postgresql:
        await ensure_postgresql_schema(engine, base)
        return

    async with engine.begin() as conn:
        await conn.run_sync(base.metadata.create_all)
        await conn.run_sync(_ensure_subscribe_identity_schema)


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
        await conn.run_sync(_ensure_subscribe_identity_schema)
