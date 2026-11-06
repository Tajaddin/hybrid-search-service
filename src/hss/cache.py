"""A small key-value layer: Redis when configured and reachable, an in-process dict otherwise (tests, and a
degraded mode that keeps the API answering while Redis is down). An unreachable Redis is tried again every
`retry_s` seconds, and the in-process dict keeps at most `max_entries` values."""

from __future__ import annotations

import json
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from typing import Any

import redis


class Store:
    def __init__(self, redis_url: str | None, retry_s: float = 5.0, max_entries: int = 10_000) -> None:
        self._mem: OrderedDict[str, tuple[float | None, str]] = OrderedDict()
        self._lists: dict[str, list[str]] = {}
        self._lock = threading.Lock()
        self._max_entries = max_entries
        self._retry_s = retry_s
        self._redis = (
            redis.Redis.from_url(redis_url, socket_connect_timeout=1, socket_timeout=1, decode_responses=True)
            if redis_url
            else None
        )
        self._up = False
        self._retry_at = 0.0
        self._connected()

    @property
    def backend(self) -> str:
        return "redis" if self._up else "memory"

    def _connected(self) -> redis.Redis | None:
        """The Redis client while it answers; None means use memory. A down Redis gets one ping per retry_s."""
        if self._up or self._redis is None:
            return self._redis
        if time.monotonic() < self._retry_at:
            return None
        try:
            self._up = bool(self._redis.ping())
        except (redis.RedisError, OSError):
            self._up = False
        if not self._up:
            self._retry_at = time.monotonic() + self._retry_s
            return None
        return self._redis

    def _failed(self, exc: Exception) -> None:
        """A dropped connection or a timeout marks Redis down until the next retry. Any other error, such as a
        full Redis refusing a write, sends only this call to memory: reads still go to Redis."""
        if isinstance(exc, (redis.ConnectionError, redis.TimeoutError, OSError)):
            self._up = False
            self._retry_at = time.monotonic() + self._retry_s

    # ---- simple values ---------------------------------------------------------------------------------
    def get_json(self, key: str) -> Any | None:
        raw = self._get(key)
        return json.loads(raw) if raw is not None else None

    def set_json(self, key: str, value: Any, ttl_s: int | None) -> None:
        self._set(key, json.dumps(value, default=str), ttl_s)

    def update_json(self, key: str, change: Callable[[Any], Any], ttl_s: int | None) -> Any | None:
        """Read, change and write one value as a single step, so concurrent updates from any worker all land
        (Redis: WATCH and MULTI, retried when another client wrote in between). None when the key is absent."""
        if (client := self._connected()) is not None:

            def update(pipe: Any) -> Any | None:
                raw = pipe.get(key)
                if raw is None:
                    return None
                value = change(json.loads(raw))
                pipe.multi()
                pipe.set(key, json.dumps(value, default=str), ex=ttl_s)
                return value

            try:
                return client.transaction(update, key, value_from_callable=True)
            except (redis.RedisError, OSError) as exc:
                self._failed(exc)
        with self._lock:
            raw = self._mem_get(key)
            if raw is None:
                return None
            value = change(json.loads(raw))
            self._mem_set(key, json.dumps(value, default=str), ttl_s)
            return value

    def _get(self, key: str) -> str | None:
        if (client := self._connected()) is not None:
            try:
                raw = client.get(key)
                return None if raw is None else str(raw)
            except (redis.RedisError, OSError) as exc:
                self._failed(exc)
        with self._lock:
            return self._mem_get(key)

    def _set(self, key: str, raw: str, ttl_s: int | None) -> None:
        if (client := self._connected()) is not None:
            try:
                client.set(key, raw, ex=ttl_s)
                return
            except (redis.RedisError, OSError) as exc:
                self._failed(exc)
        with self._lock:
            self._mem_set(key, raw, ttl_s)

    # ---- the in-process dict; callers hold the lock -------------------------------------------------------
    def _mem_get(self, key: str) -> str | None:
        item = self._mem.get(key)
        if item is None:
            return None
        expires, raw = item
        if expires is not None and expires < time.time():
            del self._mem[key]
            return None
        return raw

    def _mem_set(self, key: str, raw: str, ttl_s: int | None) -> None:
        self._mem[key] = (time.time() + ttl_s if ttl_s else None, raw)
        self._mem.move_to_end(key)
        while len(self._mem) > self._max_entries:
            self._mem.popitem(last=False)

    # ---- bounded lists (impressions) ----------------------------------------------------------------------
    def push(self, key: str, value: str, cap: int = 20_000) -> None:
        if (client := self._connected()) is not None:
            try:
                pipe = client.pipeline()
                pipe.lpush(key, value)
                pipe.ltrim(key, 0, cap - 1)
                pipe.execute()
                return
            except (redis.RedisError, OSError) as exc:
                self._failed(exc)
        with self._lock:
            lst = self._lists.setdefault(key, [])
            lst.insert(0, value)
            del lst[cap:]

    def items(self, key: str, limit: int = 20_000) -> list[str]:
        if (client := self._connected()) is not None:
            try:
                return [str(x) for x in client.lrange(key, 0, limit - 1)]
            except (redis.RedisError, OSError) as exc:
                self._failed(exc)
        with self._lock:
            return list(self._lists.get(key, [])[:limit])

    def ping(self) -> bool:
        """True for memory by configuration; a configured Redis must answer."""
        if self._redis is None:
            return True
        if (client := self._connected()) is not None:
            try:
                return bool(client.ping())
            except (redis.RedisError, OSError) as exc:
                self._failed(exc)
        return False
