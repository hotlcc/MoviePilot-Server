"""媒体身份处理工具。"""

from enum import Enum
from typing import Any, Optional, Tuple


class MediaSource(str, Enum):
    """MoviePilot 全链路允许使用的媒体主身份来源。"""

    TMDB = "themoviedb"
    Douban = "douban"
    Bangumi = "bangumi"
    AniList = "anilist"
    IMDb = "imdb"
    TVDB = "tvdb"
    MusicBrainz = "musicbrainz"
    TheAudioDB = "theaudiodb"
    DoubanMusic = "doubanmusic"
    Bilibili = "bilibili"
    MangoTV = "mangguodiscover"
    MiguVideo = "migu"
    TencentVideo = "tencentvideodiscover"

    def __str__(self) -> str:
        """返回 API 与数据库使用的规范来源值。"""
        return self.value


# 数据库约束和迁移必须与 API 枚举使用同一份来源集合。
MEDIA_SOURCE_VALUES = tuple(source.value for source in MediaSource)


def media_identity_check_sql(column_prefix: str = "") -> str:
    """生成数据库使用的统一媒体身份原子约束表达式。"""
    prefix = f"{column_prefix}." if column_prefix else ""
    source_column = f"{prefix}media_source"
    id_column = f"{prefix}media_id"
    allowed_sources = ", ".join(
        f"'{source.replace(chr(39), chr(39) * 2)}'"
        for source in MEDIA_SOURCE_VALUES
    )
    return (
        f"(({source_column} IS NULL AND {id_column} IS NULL) OR "
        f"({source_column} IS NOT NULL AND {id_column} IS NOT NULL AND "
        f"{source_column} IN ({allowed_sources}) AND "
        f"TRIM({id_column}) <> '' AND TRIM({id_column}) <> '0'))"
    )


MEDIA_SOURCE_ALIASES = {
    "tmdb": MediaSource.TMDB,
    "themoviedb": MediaSource.TMDB,
    "douban": MediaSource.Douban,
    "bangumi": MediaSource.Bangumi,
    "anilist": MediaSource.AniList,
    "imdb": MediaSource.IMDb,
    "tvdb": MediaSource.TVDB,
    "musicbrainz": MediaSource.MusicBrainz,
    "theaudiodb": MediaSource.TheAudioDB,
    "audio_db": MediaSource.TheAudioDB,
    "doubanmusic": MediaSource.DoubanMusic,
    "douban_music": MediaSource.DoubanMusic,
    "bilibili": MediaSource.Bilibili,
    "mangguodiscover": MediaSource.MangoTV,
    "mango_tv": MediaSource.MangoTV,
    "migu": MediaSource.MiguVideo,
    "migu_video": MediaSource.MiguVideo,
    "tencentvideodiscover": MediaSource.TencentVideo,
    "tencent_video": MediaSource.TencentVideo,
}

LEGACY_MEDIA_SOURCE_ID_FIELDS = {
    MediaSource.TMDB: "tmdbid",
    MediaSource.Douban: "doubanid",
    MediaSource.Bangumi: "bangumiid",
    MediaSource.AniList: "anilistid",
    MediaSource.IMDb: "imdbid",
    MediaSource.TVDB: "tvdbid",
}

MEDIA_SOURCE_PREFIXES = {
    MediaSource.TMDB: "tmdb",
    MediaSource.Douban: "douban",
    MediaSource.Bangumi: "bangumi",
    MediaSource.AniList: "anilist",
    MediaSource.IMDb: "imdb",
    MediaSource.TVDB: "tvdb",
    MediaSource.MusicBrainz: "musicbrainz",
    MediaSource.TheAudioDB: "theaudiodb",
    MediaSource.DoubanMusic: "doubanmusic",
    MediaSource.Bilibili: "bilibili",
    MediaSource.MangoTV: "mangguodiscover",
    MediaSource.MiguVideo: "migu",
    MediaSource.TencentVideo: "tencentvideodiscover",
}


def normalize_media_source(source: Optional[MediaSource | str]) -> Optional[MediaSource]:
    """将来源别名规范化为固定枚举，未知来源返回 None。"""
    if not source:
        return None
    if isinstance(source, MediaSource):
        return source
    normalized = str(source).strip().casefold()
    return MEDIA_SOURCE_ALIASES.get(normalized)


def _get_value(item: Any, field: str) -> Any:
    """从字典或模型对象读取媒体身份字段。"""
    if isinstance(item, dict):
        return item.get(field)
    return getattr(item, field, None)


def _normalize_media_id(media_id: Any) -> Optional[str]:
    """将数据源原生 ID 转为非空字符串，并忽略旧客户端的零哨兵。"""
    if media_id is None:
        return None
    value = str(media_id).strip()
    return None if value in {"", "0"} else value


def _normalize_legacy_media_id(media_id: Any) -> Optional[str]:
    """规范旧字段 ID，并把历史客户端使用的 0 视为未提供。"""
    return _normalize_media_id(media_id)


def parse_media_key(media_key: Any) -> Tuple[Optional[MediaSource], Optional[str]]:
    """解析旧客户端使用的 ``source:id`` 复合媒体键。"""
    if not media_key or ":" not in str(media_key):
        return None, None
    source_name, media_id = str(media_key).split(":", 1)
    source = normalize_media_source(source_name)
    normalized_id = _normalize_legacy_media_id(media_id)
    if not source or not normalized_id:
        return None, None
    return source, normalized_id


def resolve_media_identity(item: Any) -> Tuple[Optional[MediaSource], Optional[str]]:
    """
    从统一字段解析媒体主身份，来源和 ID 必须同时有效。

    :param item: 字典或包含媒体身份字段的模型对象
    :return: 数据源名称与该数据源原生 ID
    """
    source = normalize_media_source(_get_value(item, "media_source"))
    media_id = _normalize_media_id(_get_value(item, "media_id"))
    if source and media_id:
        return source, media_id
    return None, None


def resolve_legacy_media_identity(
        item: Any,
) -> Tuple[Optional[MediaSource], Optional[str]]:
    """在旧客户端输入和存量数据边界解析旧媒体身份字段。"""
    source, media_id = resolve_media_identity(item)
    if source and media_id:
        return source, media_id

    declared_source = normalize_media_source(
        _get_value(item, "media_source") or _get_value(item, "source")
    )
    if declared_source:
        legacy_field = LEGACY_MEDIA_SOURCE_ID_FIELDS.get(declared_source)
        media_id = _normalize_legacy_media_id(
            _get_value(item, legacy_field) if legacy_field else None
        )
        if media_id:
            return declared_source, media_id

        # 部分旧版使用 source + 原生 mediaid，而订阅接口使用 source:id。
        raw_media_key = _get_value(item, "mediaid")
        parsed_source, parsed_id = parse_media_key(raw_media_key)
        if parsed_source and parsed_id:
            return parsed_source, parsed_id
        media_id = _normalize_legacy_media_id(raw_media_key)
        if media_id:
            return declared_source, media_id

    source, media_id = parse_media_key(_get_value(item, "mediaid"))
    if source and media_id:
        return source, media_id
    for fallback_source, field in LEGACY_MEDIA_SOURCE_ID_FIELDS.items():
        media_id = _normalize_legacy_media_id(_get_value(item, field))
        if media_id:
            return fallback_source, media_id
    return None, None


def build_legacy_media_identity(item: Any) -> dict[str, Any]:
    """
    为中心服务响应补充旧版客户端仍会读取的媒体身份字段。

    返回值只用于 API 输出，不得写回数据库或 Redis。
    """
    payload = dict(item) if isinstance(item, dict) else {}
    source, media_id = resolve_media_identity(item)
    legacy_fields = {
        field: None for field in LEGACY_MEDIA_SOURCE_ID_FIELDS.values()
    }
    if not source or not media_id:
        return {**payload, **legacy_fields, "mediaid": None}

    legacy_field = LEGACY_MEDIA_SOURCE_ID_FIELDS.get(source)
    if legacy_field:
        legacy_value: Any = media_id
        if source in {
            MediaSource.TMDB,
            MediaSource.Bangumi,
            MediaSource.AniList,
            MediaSource.TVDB,
        }:
            legacy_value = int(media_id) if media_id.isdigit() else None
        legacy_fields[legacy_field] = legacy_value

    return {
        **payload,
        "media_source": source.value,
        "media_id": media_id,
        **legacy_fields,
        "mediaid": f"{MEDIA_SOURCE_PREFIXES[source]}:{media_id}",
    }
