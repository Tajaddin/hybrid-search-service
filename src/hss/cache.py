"""A small key-value layer: Redis when configured and reachable, an in-process dict otherwise (tests, and a
degraded mode that keeps the API answering when Redis is down)."""

from __future__ import annotations

import json
import threading
import time
from typing import Any


class Store:
    def __init__(self, redis_url: str | None) -> None:
        self.backend = "memory"
        self._mem: dict[str, tuple[float | None, str]] = {}
        self._lists: dict[str, list[str]] = {}
        self._lock = threading.Lock()
        self._redis = None
        if redis_url:
            try:
                import redis

                client = redis.Redis.from_url(
                    redis_url, socket_connect_timeout=1, socket_timeout=1, decode_responses=True
                )
                client.ping()
                self._redis = client
                self.backend = "redis"
            except Exception:
                self._redis = None

    # ---- simple values ---------------------------------------------------------------------------------
    def get_json(self, key: str) -> Any | None:
        raw = self._get(key)
        return json.loads(raw) if raw is not None else None

    def set_json(self, key: str, value: Any, ttl_s: int | None) -> None:
        self._set(key, json.dumps(value, default=str), ttl_s)

    def _get(self, key: str) -> str | None:
        if self._redis is not None:
            try:
                raw = self._redis.get(key)
                return None if raw is None else str(raw)
            except Exception:
                return None
        with self._lock:
            item = self._mem.get(key)
            if item is None:
                return None
            expires, raw = item
            if expires is not None and expires < time.time():
                del self._mem[key]
                return None
            return raw

    def _set(self, key: str, raw: str, ttl_s: int | None) -> None:
        if self._redis is not None:
            try:
                self._redis.set(key, raw, ex=ttl_s)
            except Exception:
                return
            return
        with self._lock:
            self._mem[key] = (time.time() + ttl_s if ttl_s else None, raw)

    # ---- bounded lists (impressions) ----------------------------------------------------------------------
    def push(self, key: str, value: str, cap: int = 20_000) -> None:
        if self._redis is not None:
            try:
                pipe = self._redis.pipeline()
                pipe.lpush(key, value)
                pipe.ltrim(key, 0, cap - 1)
                pipe.execute()
            except Exception:
                return
            return
        with self._lock:
            lst = self._lists.setdefault(key, [])
            lst.insert(0, value)
            del lst[cap:]

    def items(self, key: str, limit: int = 20_000) -> list[str]:
        if self._redis is not None:
            try:
                return [str(x) for x in self._redis.lrange(key, 0, limit - 1)]
            except Exception:
                return []
        with self._lock:
            return list(self._lists.get(key, [])[:limit])

    def ping(self) -> bool:
        if self._redis is None:
            return self.backend == "memory"
        try:
            return bool(self._redis.ping())
        except Exception:
            return False
