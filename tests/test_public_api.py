"""Every public tool against the real YouTube Data API, through the real stdio server.

Reference data is stable, publicly-known fact (e.g. the first video ever uploaded), not
values copied from a response and echoed back.
"""
import pytest
from helpers import LAUNCHERS, list_tool_names, run_tools

ZOO = "jNQXAC9IVRw"  # "Me at the zoo", the first YouTube video (2005)
ZOO_UPLOADER = "UC4QobU6STFB0P71PMvOGN5A"
YT_CHANNEL = "UCBR8-60-B28hp2BmDPdntcQ"  # YouTube's official channel
EXPECTED_TOOLS = {
    "my_channel", "get_channel", "list_uploads", "get_videos", "get_comments",
    "my_playlists", "playlist_items", "my_subscriptions", "search",
}


def one(env, tool, **args):
    return run_tools([(tool, args)], env)[0]


@pytest.mark.parametrize("name", LAUNCHERS)
def test_both_launchers_serve_every_tool(env, name):
    assert list_tool_names(env, LAUNCHERS[name]) == EXPECTED_TOOLS


# ---- get_channel -----------------------------------------------------------------------


def test_get_channel_by_id(env):
    d = one(env, "get_channel", channel_id=YT_CHANNEL).data
    (ch,) = d["items"]
    assert ch["id"] == YT_CHANNEL
    assert ch["snippet"]["title"] == "YouTube"
    assert int(ch["statistics"]["subscriberCount"]) > 1_000_000
    assert ch["contentDetails"]["relatedPlaylists"]["uploads"] == "UU" + YT_CHANNEL[2:]


def test_get_channel_by_handle_resolves_to_same_channel(env):
    d = one(env, "get_channel", handle="@YouTube").data
    assert [c["id"] for c in d["items"]] == [YT_CHANNEL]


def test_get_channel_requires_an_argument(env):
    r = one(env, "get_channel")
    assert r.is_error and "channel_id or handle" in r.text


def test_get_channel_unknown_handle_returns_nothing(env):
    d = one(env, "get_channel", handle="@this-handle-should-not-exist-zzqx-91827364").data
    assert d["items"] == []


# ---- get_videos ------------------------------------------------------------------------


def test_get_videos_first_youtube_video(env):
    d = one(env, "get_videos", ids=[ZOO]).data
    assert d["count"] == 1
    v = d["items"][0]
    assert v["id"] == ZOO
    assert v["snippet"]["title"] == "Me at the zoo"
    assert v["snippet"]["channelId"] == ZOO_UPLOADER
    assert v["snippet"]["publishedAt"].startswith("2005-04-24")
    assert int(v["statistics"]["viewCount"]) > 100_000_000
    assert v["contentDetails"]["duration"] == "PT19S"


def test_get_videos_unknown_id_is_absent_not_an_error(env):
    d = one(env, "get_videos", ids=["AAAAAAAAAAA"]).data
    assert d == {"items": [], "count": 0}


def test_get_videos_mixed_known_and_unknown(env):
    d = one(env, "get_videos", ids=["AAAAAAAAAAA", ZOO]).data
    assert [v["id"] for v in d["items"]] == [ZOO]


def test_get_videos_empty_list(env):
    assert one(env, "get_videos", ids=[]).data == {"items": [], "count": 0}


def test_get_videos_batches_past_the_50_id_api_limit(env):
    """The API rejects >50 ids per call, so >50 results proves real batching worked."""
    first = one(env, "list_uploads", channel_id=YT_CHANNEL, max_results=50).data
    second = one(env, "list_uploads", channel_id=YT_CHANNEL, max_results=50,
                 page_token=first["next_page_token"]).data
    ids = [i["video_id"] for i in first["items"] + second["items"]]
    assert len(ids) == len(set(ids)) == 100
    d = one(env, "get_videos", ids=ids).data
    assert d["count"] > 50
    assert {v["id"] for v in d["items"]} <= set(ids)


# ---- list_uploads ----------------------------------------------------------------------


def test_list_uploads_shape_pagination_and_order(env):
    p1 = one(env, "list_uploads", channel_id=YT_CHANNEL, max_results=10).data
    assert len(p1["items"]) == 10
    assert p1["next_page_token"]
    assert p1["total_results"] > 100
    for item in p1["items"]:
        assert set(item) == {"video_id", "title", "published_at", "position"}
        assert len(item["video_id"]) == 11
    dates = [i["published_at"] for i in p1["items"] if i["published_at"]]
    assert dates == sorted(dates, reverse=True), "uploads should be newest first"

    p2 = one(env, "list_uploads", channel_id=YT_CHANNEL, max_results=10,
             page_token=p1["next_page_token"]).data
    assert len(p2["items"]) == 10
    assert not {i["video_id"] for i in p1["items"]} & {i["video_id"] for i in p2["items"]}


def test_list_uploads_clamps_max_results(env):
    assert len(one(env, "list_uploads", channel_id=YT_CHANNEL, max_results=500).data["items"]) == 50
    assert len(one(env, "list_uploads", channel_id=YT_CHANNEL, max_results=0).data["items"]) == 1


def test_list_uploads_of_the_zoo_uploader_contains_the_zoo_video(env):
    d = one(env, "list_uploads", channel_id=ZOO_UPLOADER, max_results=50).data
    assert ZOO in {i["video_id"] for i in d["items"]}


def test_list_uploads_unknown_channel(env):
    r = one(env, "list_uploads", channel_id="UC_x5XG1OV2P6uZZ5FSM9Tttw_nonexistent")
    assert r.is_error and "Channel not found" in r.text


# ---- get_comments ----------------------------------------------------------------------


def test_get_comments_shape_and_clamp(env):
    d = one(env, "get_comments", video_id=ZOO, max_results=5).data
    assert len(d["items"]) == 5 and d["next_page_token"]
    for c in d["items"]:
        assert set(c) == {"thread_id", "author", "text", "likes", "published_at", "reply_count"}
        assert c["text"] and c["author"]
    assert len(one(env, "get_comments", video_id=ZOO, max_results=1000).data["items"]) == 100


def test_get_comments_time_order_is_newest_first_and_pages_advance(env):
    """order=time: Google may put a pinned comment first, then newest-first. The video is
    live (new comments every minute), so pages can shift by an item or two between calls."""
    p1 = one(env, "get_comments", video_id=ZOO, max_results=20, order="time").data
    dates = [c["published_at"] for c in p1["items"]][1:]  # [0] may be the pinned comment
    assert dates == sorted(dates, reverse=True)
    p2 = one(env, "get_comments", video_id=ZOO, max_results=20, order="time",
             page_token=p1["next_page_token"]).data
    assert len(p2["items"]) == 20
    assert p2["next_page_token"] != p1["next_page_token"]
    overlap = {c["thread_id"] for c in p1["items"]} & {c["thread_id"] for c in p2["items"]}
    assert len(overlap) <= 2, f"pages overlap by {len(overlap)}: pagination token not advancing"
    assert max(c["published_at"] for c in p2["items"]) <= min(dates)


def test_get_comments_unknown_video_is_a_clean_error(env, real_key):
    r = one(env, "get_comments", video_id="AAAAAAAAAAA")
    assert r.is_error
    assert "404" in r.text and "could not be found" in r.text


def test_no_error_message_ever_contains_the_api_key(env, real_key):
    """Regression: HttpError's text includes the request URL with `key=<API key>`."""
    results = run_tools(
        [
            ("get_comments", {"video_id": "AAAAAAAAAAA"}),
            ("get_comments", {"video_id": ZOO, "order": "not-a-real-order"}),
            ("get_videos", {"ids": [ZOO], }),
            ("list_uploads", {"channel_id": "UCnonexistent"}),
            ("search", {"q": "x", "type": "not-a-type"}),
        ],
        env,
    )
    assert sum(r.is_error for r in results) >= 3
    for r in results:
        assert real_key not in r.text and "key=" not in r.text


# ---- search (costs 100 quota units per run; runs every time) -------------------------------------------------


def test_search_finds_the_zoo_video(env):
    d = one(env, "search", q="Me at the zoo jawed", type="video", max_results=10).data
    assert d["items"], "search returned nothing"
    assert ZOO in {i["id"].get("videoId") for i in d["items"]}
