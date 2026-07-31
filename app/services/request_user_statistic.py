"""
请求用户统计服务
"""
import asyncio
import hashlib
import hmac
import logging
import time
from contextlib import suppress
from typing import Optional

from cacheout import Cache
from fastapi import Request

from app.core.config import settings
from app.db.redis import get_redis

logger = logging.getLogger(__name__)


class RequestUserStatisticService:
    """请求用户统计服务类"""

    UNKNOWN_USERS_KEY = f"{settings.REDIS_KEY_PREFIX}:usage:request_users:unknown"
    REPORTED_USERS_KEY = f"{settings.REDIS_KEY_PREFIX}:usage:request_users:reported"
    REQUEST_FINGERPRINT_STATE_KEY = "request_user_fingerprint"
    REPORT_USER_UID_HEADER = "X-MoviePilot-User-Uid"
    RECENT_FINGERPRINT_CACHE_TTL = 3600
    RECENT_FINGERPRINT_CACHE = Cache(maxsize=65536, ttl=RECENT_FINGERPRINT_CACHE_TTL)
    RECORD_RETRY_TTL = 60
    RECORD_QUEUE_MAXSIZE = 4096
    RECORD_BATCH_SIZE = 256
    RECORD_BATCH_WAIT = 0.02
    ERROR_LOG_INTERVAL = 60
    RECORD_UNKNOWN_USERS_SCRIPT = """
    local reported = redis.call('SMISMEMBER', KEYS[2], unpack(ARGV))
    local unknown = {}
    for index, fingerprint in ipairs(ARGV) do
        if reported[index] == 0 then
            unknown[#unknown + 1] = fingerprint
        end
    end
    if #unknown == 0 then
        return 0
    end
    return redis.call('SADD', KEYS[1], unpack(unknown))
    """
    _record_queue: Optional[asyncio.Queue[str]] = None
    _record_worker: Optional[asyncio.Task] = None
    _last_error_logged_at = 0.0
    _suppressed_errors = 0

    @classmethod
    async def start(cls) -> None:
        """启动当前进程的请求用户批量写入任务。"""
        if cls._record_worker and not cls._record_worker.done():
            return

        cls._record_queue = asyncio.Queue(maxsize=cls.RECORD_QUEUE_MAXSIZE)
        cls._record_worker = asyncio.create_task(
            cls._record_loop(),
            name="request-user-statistic",
        )

    @classmethod
    async def stop(cls) -> None:
        """停止批量写入任务，并尽量刷新队列中的指纹。"""
        queue = cls._record_queue
        worker = cls._record_worker
        if queue is None or worker is None:
            return

        try:
            await asyncio.wait_for(queue.join(), timeout=2)
        except asyncio.TimeoutError:
            logger.warning("Request user statistic queue did not drain before shutdown")

        worker.cancel()
        with suppress(asyncio.CancelledError):
            await worker
        cls._record_queue = None
        cls._record_worker = None

    @classmethod
    async def _record_loop(cls) -> None:
        """将短时间内的用户指纹合并为一次 Redis 写入。"""
        queue = cls._record_queue
        if queue is None:
            return

        while True:
            first_fingerprint = await queue.get()
            fingerprints = [first_fingerprint]
            try:
                await asyncio.sleep(cls.RECORD_BATCH_WAIT)
                while len(fingerprints) < cls.RECORD_BATCH_SIZE:
                    try:
                        fingerprints.append(queue.get_nowait())
                    except asyncio.QueueEmpty:
                        break

                await cls._record_fingerprints(set(fingerprints))
            except asyncio.CancelledError:
                raise
            except Exception as err:
                cls._remember_fingerprints(
                    set(fingerprints),
                    ttl=cls.RECORD_RETRY_TTL,
                )
                cls._log_record_error(err)
            finally:
                for _ in fingerprints:
                    queue.task_done()

    @classmethod
    def _log_record_error(cls, err: Exception) -> None:
        """限制 Redis 异常时的日志频率，避免过载被进一步放大。"""
        now = time.monotonic()
        if now - cls._last_error_logged_at < cls.ERROR_LOG_INTERVAL:
            cls._suppressed_errors += 1
            return

        suppressed = cls._suppressed_errors
        cls._last_error_logged_at = now
        cls._suppressed_errors = 0
        if suppressed:
            logger.warning(
                "Record request users skipped: %s (%s similar errors suppressed)",
                err,
                suppressed,
            )
        else:
            logger.warning("Record request users skipped: %s", err)

    @staticmethod
    def should_skip_request(request: Request) -> bool:
        """
        判断当前请求是否需要跳过用户统计。
        """
        if request.method in {"OPTIONS", "HEAD"}:
            return True

        return request.url.path in {
            "/",
            "/usage/statistic",
            "/favicon.ico",
        }

    @staticmethod
    def _is_recent_fingerprint(fingerprint: str) -> bool:
        """
        判断用户指纹是否已在本进程短时间内登记过。
        """
        return RequestUserStatisticService.RECENT_FINGERPRINT_CACHE.get(fingerprint) is not None

    @staticmethod
    def _remember_fingerprints(
            fingerprints: set[str],
            ttl: Optional[int] = None,
    ) -> None:
        """
        记录本进程短时间内已经处理过的用户指纹。
        """
        for fingerprint in fingerprints:
            RequestUserStatisticService.RECENT_FINGERPRINT_CACHE.set(
                fingerprint,
                True,
                ttl=ttl,
            )

    @classmethod
    def enqueue_request_user(cls, request: Request) -> None:
        """将未近期处理的请求用户放入有界队列。"""
        fingerprint = cls._prepare_request_fingerprint(request)
        if not fingerprint:
            return

        queue = cls._record_queue
        if queue is None:
            return

        cls._remember_fingerprints({fingerprint})
        try:
            queue.put_nowait(fingerprint)
        except asyncio.QueueFull:
            cls._remember_fingerprints({fingerprint}, ttl=cls.RECORD_RETRY_TTL)
            cls._log_record_error(RuntimeError("request user statistic queue is full"))

    @staticmethod
    def _get_client_ip(request: Request) -> str:
        """
        获取 Cloudflare 转发后的真实客户端 IP。
        """
        cf_ip = request.headers.get("CF-Connecting-IP")
        if cf_ip:
            return cf_ip.strip()

        forwarded_for = request.headers.get("X-Forwarded-For")
        if forwarded_for:
            return forwarded_for.split(",")[0].strip()

        return request.client.host if request.client else ""

    @staticmethod
    def _build_hashed_fingerprint(raw_value: str) -> str:
        """
        将用户识别信息转换为不可逆指纹。
        """
        salt = settings.REDIS_KEY_PREFIX.encode("utf-8")
        return hmac.new(salt, raw_value.encode("utf-8"), hashlib.sha256).hexdigest()

    @staticmethod
    def _build_user_uid_fingerprint(user_uid: Optional[str]) -> Optional[str]:
        """
        根据安装用户唯一 ID 生成用户指纹。
        """
        if not user_uid:
            return None

        return RequestUserStatisticService._build_hashed_fingerprint(f"uid\0{user_uid}")

    @staticmethod
    def _build_source_fingerprint(request: Request) -> Optional[str]:
        """
        根据客户端来源生成兼容旧客户端的用户指纹。
        """
        client_ip = RequestUserStatisticService._get_client_ip(request)
        user_agent = request.headers.get("User-Agent", "")
        if not client_ip and not user_agent:
            return None

        return RequestUserStatisticService._build_hashed_fingerprint(
            f"request\0{client_ip}\0{user_agent}"
        )

    @staticmethod
    def _build_request_fingerprint(request: Request) -> Optional[str]:
        """
        根据请求头或客户端来源生成用户指纹。
        """
        user_uid = request.headers.get(RequestUserStatisticService.REPORT_USER_UID_HEADER)
        if user_uid:
            return RequestUserStatisticService._build_user_uid_fingerprint(user_uid.strip())

        return RequestUserStatisticService._build_source_fingerprint(request)

    @classmethod
    def _prepare_request_fingerprint(cls, request: Request) -> Optional[str]:
        """生成请求指纹，并过滤本进程近期已处理的用户。"""
        if cls.should_skip_request(request):
            return None

        fingerprint = cls._build_request_fingerprint(request)
        if not fingerprint:
            return None

        setattr(request.state, cls.REQUEST_FINGERPRINT_STATE_KEY, fingerprint)
        if cls._is_recent_fingerprint(fingerprint):
            return None
        return fingerprint

    @classmethod
    async def _record_fingerprints(cls, fingerprints: set[str]) -> None:
        """将一批未上报安装版本的用户指纹写入 Redis。"""
        if not fingerprints:
            return

        redis = get_redis()
        await redis.eval(
            cls.RECORD_UNKNOWN_USERS_SCRIPT,
            2,
            cls.UNKNOWN_USERS_KEY,
            cls.REPORTED_USERS_KEY,
            *fingerprints,
        )

    @staticmethod
    async def record_request_user(request: Request) -> None:
        """
        将未上报安装版本的请求用户登记到 Redis。
        """
        fingerprint = RequestUserStatisticService._prepare_request_fingerprint(request)
        if not fingerprint:
            return

        RequestUserStatisticService._remember_fingerprints({fingerprint})
        try:
            await RequestUserStatisticService._record_fingerprints({fingerprint})
        except Exception:
            RequestUserStatisticService._remember_fingerprints(
                {fingerprint},
                ttl=RequestUserStatisticService.RECORD_RETRY_TTL,
            )
            raise

    @staticmethod
    async def safe_record_request_user(request: Request) -> None:
        """
        安全登记请求用户，避免统计异常影响正常响应。
        """
        try:
            await RequestUserStatisticService.record_request_user(request)
        except Exception as err:
            RequestUserStatisticService._log_record_error(err)

    @staticmethod
    async def mark_request_user_reported(
            request: Optional[Request],
            user_uid: Optional[str] = None,
    ) -> None:
        """
        将当前请求用户从“未知”分类移动到已上报安装版本分类。
        """
        fingerprints = set()
        user_uid_fingerprint = RequestUserStatisticService._build_user_uid_fingerprint(user_uid)
        if user_uid_fingerprint:
            fingerprints.add(user_uid_fingerprint)

        if request is not None:
            request_fingerprint = getattr(
                request.state,
                RequestUserStatisticService.REQUEST_FINGERPRINT_STATE_KEY,
                None,
            ) or RequestUserStatisticService._build_request_fingerprint(request)
            if request_fingerprint:
                fingerprints.add(request_fingerprint)
            source_fingerprint = RequestUserStatisticService._build_source_fingerprint(request)
            if source_fingerprint:
                fingerprints.add(source_fingerprint)

        if not fingerprints:
            return

        redis = get_redis()
        async with redis.pipeline(transaction=True) as pipe:
            pipe.sadd(RequestUserStatisticService.REPORTED_USERS_KEY, *fingerprints)
            pipe.srem(RequestUserStatisticService.UNKNOWN_USERS_KEY, *fingerprints)
            await pipe.execute()
        RequestUserStatisticService._remember_fingerprints(fingerprints)

    @staticmethod
    async def count_other_users() -> int:
        """
        统计尚未上报安装版本的请求用户数量。
        """
        redis = get_redis()
        return await redis.scard(RequestUserStatisticService.UNKNOWN_USERS_KEY)
