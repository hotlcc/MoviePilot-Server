"""媒体身份处理工具。"""

import re
from enum import Enum
from typing import Any, Optional, Tuple

from pydantic import GetJsonSchemaHandler
from pydantic_core import CoreSchema


MEDIA_SOURCE_IDENTIFIER_PATTERN = r"^[a-z][a-z0-9._-]{0,63}$"
_MEDIA_SOURCE_IDENTIFIER_RE = re.compile(MEDIA_SOURCE_IDENTIFIER_PATTERN)


class MediaSource(str, Enum):
    """内置媒体来源常量，并兼容主程序插件注册的扩展来源。"""

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

    @classmethod
    def _missing_(cls, value: object) -> Optional["MediaSource"]:
        """将格式合法的插件来源解析为动态枚举成员。"""
        if not isinstance(value, str):
            return None
        normalized = value.strip().casefold()
        known_member = cls._value2member_map_.get(normalized)
        if known_member:
            return known_member
        if not _MEDIA_SOURCE_IDENTIFIER_RE.fullmatch(normalized):
            return None
        member = str.__new__(cls, normalized)
        member._name_ = normalized
        member._value_ = normalized
        cls._value2member_map_.setdefault(normalized, member)
        return cls._value2member_map_[normalized]

    @classmethod
    def __get_pydantic_json_schema__(
            cls, core_schema: CoreSchema, handler: GetJsonSchemaHandler,
    ) -> dict:
        """在 OpenAPI 中公开扩展标识格式和内置来源示例。"""
        schema = handler(core_schema)
        schema.pop("enum", None)
        schema["pattern"] = MEDIA_SOURCE_IDENTIFIER_PATTERN
        schema["examples"] = [source.value for source in cls]
        return schema


def media_identity_check_sql(column_prefix: str = "") -> str:
    """生成允许插件扩展来源且保证身份成对的数据库约束表达式。"""
    prefix = f"{column_prefix}." if column_prefix else ""
    source_column = f"{prefix}media_source"
    id_column = f"{prefix}media_id"
    return (
        f"(({source_column} IS NULL AND {id_column} IS NULL) OR "
        f"({source_column} IS NOT NULL AND {id_column} IS NOT NULL AND "
        f"TRIM({source_column}) <> '' AND "
        f"{source_column} = LOWER(TRIM({source_column})) AND "
        f"LENGTH({source_column}) <= 64 AND "
        f"{source_column} NOT LIKE '%:%' AND "
        f"{source_column} NOT LIKE '% %' AND "
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
    """将内置别名或插件扩展标识规范化为 MediaSource。"""
    if not source:
        return None
    if isinstance(source, MediaSource):
        return source
    normalized = str(source).strip().casefold()
    builtin_source = MEDIA_SOURCE_ALIASES.get(normalized)
    if builtin_source:
        return builtin_source
    try:
        return MediaSource(normalized)
    except ValueError:
        return None


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
        "mediaid": f"{MEDIA_SOURCE_PREFIXES.get(source, source.value)}:{media_id}",
    }
