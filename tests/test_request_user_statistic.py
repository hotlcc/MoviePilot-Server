"""请求用户统计的去重、批处理和中间件测试。"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from starlette.requests import Request

import app.services.request_user_statistic as request_user_module
from app.services.request_user_statistic import RequestUserStatisticService
from main import RequestUserStatisticMiddleware


def _request(user_uid: str, path: str = "/recognize/share") -> Request:
    """构造只包含统计所需字段的 ASGI 请求。"""
    return Request({
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "https",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": [
            (b"x-moviepilot-user-uid", user_uid.encode()),
            (b"user-agent", b"MoviePilot-Test"),
        ],
        "client": ("127.0.0.1", 12345),
        "server": ("movie-pilot.org", 443),
        "state": {},
    })


def _reset_service_state() -> None:
    """清理进程级统计状态，避免测试间互相影响。"""
    RequestUserStatisticService.RECENT_FINGERPRINT_CACHE.clear()
    RequestUserStatisticService._record_queue = None
    RequestUserStatisticService._record_worker = None
    RequestUserStatisticService._last_error_logged_at = 0
    RequestUserStatisticService._suppressed_errors = 0


def test_request_users_are_deduplicated_and_batched(monkeypatch) -> None:
    """重复用户只入队一次，短时间内的新用户合并写入。"""

    async def run_scenario() -> None:
        _reset_service_state()
        redis = SimpleNamespace(eval=AsyncMock(return_value=2))
        monkeypatch.setattr(request_user_module, "get_redis", lambda: redis)
        monkeypatch.setattr(RequestUserStatisticService, "RECORD_BATCH_WAIT", 0)

        await RequestUserStatisticService.start()
        try:
            RequestUserStatisticService.enqueue_request_user(_request("user-1"))
            RequestUserStatisticService.enqueue_request_user(_request("user-1"))
            RequestUserStatisticService.enqueue_request_user(_request("user-2"))
            await asyncio.wait_for(
                RequestUserStatisticService._record_queue.join(),
                timeout=1,
            )
        finally:
            await RequestUserStatisticService.stop()

        redis.eval.assert_awaited_once()
        arguments = redis.eval.await_args.args
        assert arguments[1:4] == (
            2,
            RequestUserStatisticService.UNKNOWN_USERS_KEY,
            RequestUserStatisticService.REPORTED_USERS_KEY,
        )
        assert len(set(arguments[4:])) == 2

    asyncio.run(run_scenario())


def test_middleware_only_enqueues_successful_http_responses(monkeypatch) -> None:
    """ASGI 中间件忽略失败响应和非 HTTP 请求。"""

    async def run_scenario() -> None:
        enqueue = Mock()
        monkeypatch.setattr(
            RequestUserStatisticService,
            "enqueue_request_user",
            enqueue,
        )

        async def successful_app(scope, receive, send):
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"{}"})

        async def failed_app(scope, receive, send):
            await send({"type": "http.response.start", "status": 500, "headers": []})
            await send({"type": "http.response.body", "body": b"{}"})

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        sent_messages = []

        async def send(message):
            sent_messages.append(message)

        success_request = _request("success-user")
        await RequestUserStatisticMiddleware(successful_app)(
            success_request.scope,
            receive,
            send,
        )
        failed_request = _request("failed-user")
        await RequestUserStatisticMiddleware(failed_app)(
            failed_request.scope,
            receive,
            send,
        )

        enqueue.assert_called_once()
        assert enqueue.call_args.args[0].headers[
            RequestUserStatisticService.REPORT_USER_UID_HEADER
        ] == "success-user"
        assert len(sent_messages) == 4

    asyncio.run(run_scenario())
