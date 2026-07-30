"""외부 조회용 비동기 TTL 캐시.

동일한 필지·좌표를 여러 세션이나 후속 질문에서 다시 조회할 때 네트워크 요청을
반복하지 않는다. 진행 중인 동일 요청도 하나로 합쳐서 동시에 같은 API를 두 번
호출하지 않는다. 반환값은 복제해 진단 단계의 후속 가공이 캐시 원본을 바꾸지
못하게 한다.
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import time
from collections import OrderedDict
from functools import wraps
from typing import Any, Callable


def _cache_key(args: tuple, kwargs: dict) -> str:
    payload = json.dumps(
        [args, sorted(kwargs.items())],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode()
    return hashlib.sha256(payload).hexdigest()


def async_ttl_cache(*, ttl_seconds: float = 300, maxsize: int = 512):
    """비동기 함수 결과와 동일 키의 진행 중 요청을 함께 캐시한다."""

    def decorate(func: Callable):
        values: OrderedDict[str, tuple[float, Any]] = OrderedDict()
        inflight: dict[str, asyncio.Task] = {}
        lock = asyncio.Lock()

        @wraps(func)
        async def wrapped(*args, **kwargs):
            key = _cache_key(args, kwargs)
            now = time.monotonic()
            async with lock:
                cached = values.get(key)
                if cached and cached[0] > now:
                    values.move_to_end(key)
                    return copy.deepcopy(cached[1])
                if cached:
                    values.pop(key, None)
                task = inflight.get(key)
                if task is None:
                    task = asyncio.create_task(func(*args, **kwargs))
                    inflight[key] = task

            try:
                result = await asyncio.shield(task)
            finally:
                if task.done():
                    async with lock:
                        inflight.pop(key, None)

            async with lock:
                values[key] = (time.monotonic() + ttl_seconds, copy.deepcopy(result))
                values.move_to_end(key)
                while len(values) > maxsize:
                    values.popitem(last=False)
            return copy.deepcopy(result)

        def cache_clear() -> None:
            values.clear()
            inflight.clear()

        wrapped.cache_clear = cache_clear
        return wrapped

    return decorate
