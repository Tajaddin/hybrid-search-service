from __future__ import annotations

import fakeredis
import pytest
import redis


@pytest.fixture
def fake_redis(monkeypatch) -> fakeredis.FakeServer:
    """Every Redis client opened from a URL talks to this in-process server (with Lua, for the rate limiter's
    scripts). Set `connected = False` on it to take Redis down."""
    server = fakeredis.FakeServer()
    monkeypatch.setattr(redis.Redis, "from_url", lambda url, **kw: fakeredis.FakeRedis(server=server, **kw))
    return server
