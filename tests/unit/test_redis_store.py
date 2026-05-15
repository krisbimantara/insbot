"""Unit tests for RedisSessionStore using fakeredis.

Tests cover all public methods: get_session, save_session, delete_session,
add_pending, remove_pending, replace_pending, list_pending, ping.
"""

from __future__ import annotations

from datetime import datetime, timezone

import fakeredis.aioredis as fakeredis
import pytest

from bot.adapters.redis_store import RedisSessionStore, _pending_key, _session_key
from bot.domain.models import MotorMeta, Phase, Session


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_redis():
    """Return a fakeredis async client with decode_responses=True."""
    return fakeredis.FakeRedis(decode_responses=True)


@pytest.fixture
def store(fake_redis):
    return RedisSessionStore(fake_redis, ttl=86400)


def _make_session(telegram_id: str = "111", motor_id: str = "PJ-001") -> Session:
    return Session(
        telegram_id=telegram_id,
        motor_id=motor_id,
        tipe_inspeksi="Inspeksi",
        phase=Phase.SELECTED,
        motor_meta=MotorMeta(
            name=motor_id,
            nopol="B1234XY",
            merk="Honda",
            model="Vario",
            tahun="2020",
            warna="Merah",
        ),
    )


# ---------------------------------------------------------------------------
# get_session / save_session
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_session_returns_none_when_missing(store):
    result = await store.get_session("999", "PJ-999")
    assert result is None


@pytest.mark.asyncio
async def test_save_and_get_session_round_trip(store):
    session = _make_session()
    await store.save_session(session)
    loaded = await store.get_session(session.telegram_id, session.motor_id)
    assert loaded is not None
    assert loaded.telegram_id == session.telegram_id
    assert loaded.motor_id == session.motor_id
    assert loaded.tipe_inspeksi == session.tipe_inspeksi
    assert loaded.phase == session.phase


@pytest.mark.asyncio
async def test_save_session_sets_ttl(store, fake_redis):
    session = _make_session()
    await store.save_session(session)
    key = _session_key(session.telegram_id, session.motor_id)
    ttl = await fake_redis.ttl(key)
    assert ttl > 0


@pytest.mark.asyncio
async def test_save_session_overwrites_existing(store):
    session = _make_session()
    await store.save_session(session)

    updated = session.model_copy(update={"phase": Phase.CHECKLIST})
    await store.save_session(updated)

    loaded = await store.get_session(session.telegram_id, session.motor_id)
    assert loaded is not None
    assert loaded.phase == Phase.CHECKLIST


@pytest.mark.asyncio
async def test_save_session_preserves_answers(store):
    session = _make_session()
    session = session.model_copy(update={"answers": {"kepala": "Baik", "sayap_dalam": "Cukup"}})
    await store.save_session(session)

    loaded = await store.get_session(session.telegram_id, session.motor_id)
    assert loaded is not None
    assert loaded.answers == {"kepala": "Baik", "sayap_dalam": "Cukup"}


# ---------------------------------------------------------------------------
# delete_session
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_session_removes_key(store):
    session = _make_session()
    await store.save_session(session)
    await store.delete_session(session.telegram_id, session.motor_id)
    result = await store.get_session(session.telegram_id, session.motor_id)
    assert result is None


@pytest.mark.asyncio
async def test_delete_session_idempotent(store):
    """Deleting a non-existent key should not raise."""
    await store.delete_session("nonexistent", "PJ-000")  # should not raise


# ---------------------------------------------------------------------------
# add_pending / remove_pending / list_pending
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_add_pending_adds_motor(store):
    await store.add_pending("111", "PJ-001")
    result = await store.list_pending("111")
    assert result == {"PJ-001"}


@pytest.mark.asyncio
async def test_add_pending_idempotent(store):
    """Adding the same motor twice should not create duplicates."""
    await store.add_pending("111", "PJ-001")
    await store.add_pending("111", "PJ-001")
    result = await store.list_pending("111")
    assert result == {"PJ-001"}


@pytest.mark.asyncio
async def test_add_pending_multiple_motors(store):
    await store.add_pending("111", "PJ-001")
    await store.add_pending("111", "PJ-002")
    result = await store.list_pending("111")
    assert result == {"PJ-001", "PJ-002"}


@pytest.mark.asyncio
async def test_add_pending_sets_ttl(store, fake_redis):
    await store.add_pending("111", "PJ-001")
    key = _pending_key("111")
    ttl = await fake_redis.ttl(key)
    assert ttl > 0


@pytest.mark.asyncio
async def test_remove_pending_removes_motor(store):
    await store.add_pending("111", "PJ-001")
    await store.add_pending("111", "PJ-002")
    await store.remove_pending("111", "PJ-001")
    result = await store.list_pending("111")
    assert result == {"PJ-002"}


@pytest.mark.asyncio
async def test_remove_pending_idempotent(store):
    """Removing a non-existent member should not raise."""
    await store.remove_pending("111", "PJ-999")  # should not raise


@pytest.mark.asyncio
async def test_list_pending_returns_empty_set_when_missing(store):
    result = await store.list_pending("nonexistent")
    assert result == set()


# ---------------------------------------------------------------------------
# replace_pending
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_replace_pending_replaces_all(store):
    await store.add_pending("111", "PJ-001")
    await store.add_pending("111", "PJ-002")
    await store.replace_pending("111", ["PJ-003", "PJ-004"])
    result = await store.list_pending("111")
    assert result == {"PJ-003", "PJ-004"}


@pytest.mark.asyncio
async def test_replace_pending_with_empty_list_clears_set(store):
    await store.add_pending("111", "PJ-001")
    await store.replace_pending("111", [])
    result = await store.list_pending("111")
    assert result == set()


@pytest.mark.asyncio
async def test_replace_pending_sets_ttl(store, fake_redis):
    await store.replace_pending("111", ["PJ-001"])
    key = _pending_key("111")
    ttl = await fake_redis.ttl(key)
    assert ttl > 0


@pytest.mark.asyncio
async def test_replace_pending_no_ttl_when_empty(store, fake_redis):
    """When replacing with empty list, key should not exist (no TTL)."""
    await store.add_pending("111", "PJ-001")
    await store.replace_pending("111", [])
    key = _pending_key("111")
    exists = await fake_redis.exists(key)
    assert exists == 0


# ---------------------------------------------------------------------------
# ping
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ping_returns_true_when_redis_available(store):
    result = await store.ping()
    assert result is True


# ---------------------------------------------------------------------------
# Key isolation — different telegram_ids don't interfere
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sessions_isolated_by_telegram_id(store):
    s1 = _make_session(telegram_id="111", motor_id="PJ-001")
    s2 = _make_session(telegram_id="222", motor_id="PJ-001")
    await store.save_session(s1)
    await store.save_session(s2)

    loaded1 = await store.get_session("111", "PJ-001")
    loaded2 = await store.get_session("222", "PJ-001")
    assert loaded1 is not None and loaded1.telegram_id == "111"
    assert loaded2 is not None and loaded2.telegram_id == "222"


@pytest.mark.asyncio
async def test_pending_isolated_by_telegram_id(store):
    await store.add_pending("111", "PJ-001")
    await store.add_pending("222", "PJ-002")

    assert await store.list_pending("111") == {"PJ-001"}
    assert await store.list_pending("222") == {"PJ-002"}
