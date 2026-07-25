"""媒体身份处理工具。"""

from typing import Any, Optional, Tuple


MEDIA_SOURCE_ALIASES = {
    "tmdb": "themoviedb",
    "themoviedb": "themoviedb",
    "douban": "douban",
    "bangumi": "bangumi",
    "anilist": "anilist",
}

MEDIA_SOURCE_ID_FIELDS = {
    "themoviedb": "tmdbid",
    "douban": "doubanid",
    "bangumi": "bangumiid",
    "anilist": "anilistid",
}


def normalize_media_source(source: Optional[str]) -> Optional[str]:
    """规范化媒体数据源名称，同时保留插件自定义数据源。"""
    if not source:
        return None
    normalized = str(source).strip().casefold()
    return MEDIA_SOURCE_ALIASES.get(normalized, normalized or None)


def _get_value(item: Any, field: str) -> Any:
    """从字典或模型对象读取媒体身份字段。"""
    if isinstance(item, dict):
        return item.get(field)
    return getattr(item, field, None)


def _normalize_media_id(media_id: Any) -> Optional[str]:
    """将数据源原生 ID 统一转换为非空字符串。"""
    if media_id is None:
        return None
    value = str(media_id).strip()
    return value or None


def resolve_media_identity(item: Any) -> Tuple[Optional[str], Optional[str]]:
    """
    解析媒体主身份，显式统一身份优先，兼容字段仅用于旧客户端回退。

    :param item: 字典或包含媒体身份字段的模型对象
    :return: 数据源名称与该数据源原生 ID
    """
    source = normalize_media_source(_get_value(item, "media_source"))
    media_id = _normalize_media_id(_get_value(item, "media_id"))
    if source and media_id:
        return source, media_id

    if source in MEDIA_SOURCE_ID_FIELDS:
        media_id = _normalize_media_id(
            _get_value(item, MEDIA_SOURCE_ID_FIELDS[source])
        )
        if media_id:
            return source, media_id

    for fallback_source, field in MEDIA_SOURCE_ID_FIELDS.items():
        media_id = _normalize_media_id(_get_value(item, field))
        if media_id:
            return fallback_source, media_id
    return None, None
