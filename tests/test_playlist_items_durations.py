"""playlist_items(include_durations=...) with `server.call` stubbed: no key, network or cache."""
import copy
import json

import anyio
import pytest

from youtube import server

PL = "PLtest"


def item(video_id=None, n=0):
    i = {"id": f"item{n}", "snippet": {"title": f"t{n}", "position": n}}
    if video_id is not None:
        i["contentDetails"] = {"videoId": video_id}
    return i


def duration(video_id):
    return f"PT{len(video_id)}M{sum(map(ord, video_id)) % 60}S"


@pytest.fixture
def stub(monkeypatch):
    """Record every call(); serve canned playlistItems and videos responses."""
    state = {"calls": [], "page": None, "missing": set()}

    def fake_call(resource, oauth, ttl=None, **params):
        state["calls"].append((resource, oauth, ttl, params))
        if resource == "playlistItems":
            return copy.deepcopy(state["page"])
        assert resource == "videos"
        ids = params["id"].split(",")
        return {
            "items": [
                {"id": v, "contentDetails": {"duration": duration(v)}}
                for v in ids if v not in state["missing"]
            ]
        }

    monkeypatch.setattr(server, "call", fake_call)
    return state


def run(args):
    result = anyio.run(server.build_server().call_tool, "playlist_items", args)
    return json.loads(result[0].text)


def page(items):
    return {"items": items, "nextPageToken": "NEXT", "pageInfo": {"totalResults": 321}}


PLAYLIST_CALL = (
    "playlistItems", True, 300,
    {"part": "snippet,contentDetails", "playlistId": PL, "maxResults": 50, "pageToken": None},
)


@pytest.mark.parametrize("args", [{}, {"include_durations": False}])
def test_default_makes_one_call_and_adds_no_duration(stub, args):
    stub["page"] = page([item("a", 0), item("b", 1), item(None, 2)])
    out = run({"playlist_id": PL, **args})
    assert stub["calls"] == [PLAYLIST_CALL]
    assert out == server._page(stub["page"], stub["page"]["items"])
    assert all("duration" not in i for i in out["items"])


def test_true_adds_duration_to_every_item(stub):
    stub["page"] = page([item("a", 0), item("bb", 1), item("ccc", 2)])
    out = run({"playlist_id": PL, "include_durations": True})
    assert [i["duration"] for i in out["items"]] == [duration(v) for v in ("a", "bb", "ccc")]
    assert out["next_page_token"] == "NEXT"
    assert out["total_results"] == 321
    assert stub["calls"][0] == PLAYLIST_CALL


def test_mixed_page_nulls_missing_and_keeps_every_item(stub):
    stub["page"] = page(
        [item("a", 0), item("gone", 1), item(None, 2), item("dup", 3), item("b", 4), item("dup", 5)]
    )
    stub["missing"] = {"gone"}
    out = run({"playlist_id": PL, "include_durations": True})
    assert len(out["items"]) == 6
    assert [i["id"] for i in out["items"]] == [f"item{n}" for n in range(6)]
    assert [i["duration"] for i in out["items"]] == [
        duration("a"), None, None, duration("dup"), duration("b"), duration("dup"),
    ]
    for got, sent in zip(out["items"], stub["page"]["items"], strict=True):
        assert {k: v for k, v in got.items() if k != "duration"} == sent


def test_lookups_batch_50_ids_per_cached_call(stub):
    ids = [f"v{n:03d}" for n in range(120)]
    stub["page"] = page([item(v, n) for n, v in enumerate(ids)])
    out = run({"playlist_id": PL, "include_durations": True})
    videos = [c for c in stub["calls"] if c[0] == "videos"]
    assert len(videos) == 3
    seen = []
    for _, oauth, ttl, params in videos:
        assert oauth is False
        assert ttl is None
        assert params["part"] == "contentDetails"
        batch = params["id"].split(",")
        assert len(batch) <= 50
        seen += batch
    assert sorted(seen) == ids
    assert [i["duration"] for i in out["items"]] == [duration(v) for v in ids]


def test_schema_exposes_include_durations():
    tools = anyio.run(server.build_server().list_tools)
    (tool,) = [t for t in tools if t.name == "playlist_items"]
    prop = tool.inputSchema["properties"]["include_durations"]
    assert prop["type"] == "boolean"
    assert prop["default"] is False


def test_empty_page_makes_no_videos_call(stub):
    stub["page"] = {"items": [], "pageInfo": {"totalResults": 0}}
    out = run({"playlist_id": PL, "include_durations": True})
    assert stub["calls"] == [PLAYLIST_CALL]
    assert out == {"items": [], "next_page_token": None, "total_results": 0}
