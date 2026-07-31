"""
插件评分 API 路由
"""
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.deps import get_db
from app.schemas.models import PluginRatingItem, PluginRatingResult
from app.services.plugin_rating import PluginRatingService

router = APIRouter()
USER_UID_HEADER = "X-MoviePilot-User-Uid"


@router.get("/rating", response_model=dict[str, PluginRatingResult])
async def plugin_ratings(
        plugin_ids: Annotated[str | None, Query()] = None,
        user_uid: Annotated[str | None, Header(alias=USER_UID_HEADER)] = None,
        db: AsyncSession = Depends(get_db),
):
    """批量查询插件评分结果"""
    requested_ids = plugin_ids.split(",") if plugin_ids is not None else None
    return await PluginRatingService.get_ratings(db, requested_ids, user_uid)


@router.get("/rating/{pid}", response_model=PluginRatingResult)
async def plugin_rating(
        pid: str,
        user_uid: Annotated[str | None, Header(alias=USER_UID_HEADER)] = None,
        db: AsyncSession = Depends(get_db),
):
    """查询单个插件评分结果"""
    return await PluginRatingService.get_rating(db, pid, user_uid)


@router.post("/rating/{pid}", response_model=PluginRatingResult)
async def rate_plugin(
        pid: str,
        payload: PluginRatingItem,
        user_uid: Annotated[
            str,
            Header(alias=USER_UID_HEADER, min_length=1, max_length=128),
        ],
        db: AsyncSession = Depends(get_db),
):
    """新增或更新当前安装实例的插件评分"""
    return await PluginRatingService.rate_plugin(
        db,
        pid,
        user_uid,
        payload.rating,
    )
