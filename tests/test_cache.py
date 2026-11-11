"""The real SQLite cache in front of the real API."""
import sqlite3
import stat
import time

import pytest

from youtube import server

ZOO = "jNQXAC9IVRw"


@pytest.fixture
def cache(tmp_path, monkeypatch, real_key):
    monkeypatch.setattr(server, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(server, "CACHE_DB", tmp_path / "cache" / "cache.sqlite")
    monkeypatch.setattr(server, "_clients", {})
    return server.CACHE_DB


def rows(db_path):
    with sqlite3.connect(db_path) as db:
        return db.execute("SELECT k, t, v FROM c").fetchall()


def test_second_identical_call_is_served_from_cache(cache):
    first = server.call("videos", False, part="snippet", id=ZOO)
    (row,) = rows(cache)
    time.sleep(1.1)
    second = server.call("videos", False, part="snippet", id=ZOO)
    (row2,) = rows(cache)
    assert second == first
    assert row2[1] == row[1], "cache row timestamp changed: the API was called again"


def test_ttl_zero_forces_a_real_refetch(cache):
    server.call("videos", False, part="snippet", id=ZOO)
    t1 = rows(cache)[0][1]
    time.sleep(1.1)
    server.call("videos", False, part="snippet", id=ZOO, ttl=0)
    assert len(rows(cache)) == 1
    assert rows(cache)[0][1] > t1


def test_expired_entry_is_refetched(cache, monkeypatch):
    server.call("videos", False, part="snippet", id=ZOO, ttl=3600)
    t1 = rows(cache)[0][1]
    time.sleep(1.1)
    server.call("videos", False, part="snippet", id=ZOO, ttl=1)  # entry is >1s old
    assert rows(cache)[0][1] > t1


def test_different_parameters_get_separate_entries(cache):
    a = server.call("videos", False, part="snippet", id=ZOO)
    b = server.call("videos", False, part="statistics", id=ZOO)
    assert len(rows(cache)) == 2
    assert "statistics" in b["items"][0] and "statistics" not in a["items"][0]


def test_none_params_are_dropped_from_the_request_and_key(cache):
    server.call("videos", False, part="snippet", id=ZOO, pageToken=None)
    server.call("videos", False, part="snippet", id=ZOO)
    assert len(rows(cache)) == 1


def test_errors_are_not_cached_and_do_not_leak_the_key(cache, real_key):
    with pytest.raises(RuntimeError) as e:
        server.call("commentThreads", False, part="snippet", videoId="AAAAAAAAAAA")
    assert "404" in str(e.value)
    assert real_key not in str(e.value) and "key=" not in str(e.value)
    assert rows(cache) == []


def test_cache_permissions(cache):
    server.call("videos", False, part="snippet", id=ZOO)
    assert stat.S_IMODE(cache.stat().st_mode) == 0o600
    assert stat.S_IMODE(cache.parent.stat().st_mode) == 0o700


def test_cache_survives_across_processes(tmp_path, env):
    """Two separate real server processes share the on-disk cache."""
    from helpers import run_tools

    (r1,) = run_tools([("get_videos", {"ids": [ZOO]})], env)
    db = tmp_path / "cache" / "youtube-mcp" / "cache.sqlite"
    (row,) = rows(db)
    time.sleep(1.1)
    (r2,) = run_tools([("get_videos", {"ids": [ZOO]})], env)
    assert r1.text == r2.text
    assert rows(db)[0][1] == row[1]


def test_deleting_the_cache_is_harmless(tmp_path, env):
    from helpers import run_tools

    (r1,) = run_tools([("get_videos", {"ids": [ZOO]})], env)
    (tmp_path / "cache" / "youtube-mcp" / "cache.sqlite").unlink()
    (r2,) = run_tools([("get_videos", {"ids": [ZOO]})], env)
    assert r2.data["items"][0]["id"] == ZOO
