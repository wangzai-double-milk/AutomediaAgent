"""Offline contracts: URLs, pinned tabs, blocked pages and honest read outcomes."""
import copy
from types import SimpleNamespace

import pytest

from tools.douyin import Bridge, Browser, BrowseError, allowed_url, search_url, video_id


@pytest.mark.parametrize("url", [
    "https://www.douyin.com.evil.test/video/123456",
    "https://evil.test/?url=https://www.douyin.com/video/123456",
    "https://www.douyin.com@evil.test/video/123456",
    "https://name:secret@www.douyin.com/video/123456",
    "https://www.douyin.com:8443/video/123456",
    "http://www.douyin.com/video/123456",
    "https://www.douyin.com/messages",
    "https://creator.douyin.com/",
    "file:///tmp/video", "javascript:alert(1)",
])
def test_disallowed_urls(url):
    assert not allowed_url(url)
    assert video_id(url) is None


def test_search_escapes_path_and_video_ids_preserve_precision():
    assert search_url("AI / #?") == "https://www.douyin.com/search/AI%20%2F%20%23%3F?type=video"
    assert video_id("https://www.douyin.com/video/7566123456789012345") == "7566123456789012345"
    assert video_id("https://www.douyin.com/?modal_id=7566123456789012345") == "7566123456789012345"
    with pytest.raises(BrowseError):
        search_url("")


def test_explicit_tab_never_falls_back_to_another_site(monkeypatch):
    import ga
    monkeypatch.setattr(ga, "driver", SimpleNamespace(get_all_sessions=lambda: [
        {"id": "xhs", "url": "https://www.xiaohongshu.com/explore"},
        {"id": "dy", "url": "https://www.douyin.com/"},
    ]))
    with pytest.raises(BrowseError, match="不是"):
        Bridge("xhs")
    token = ga._browser_session.set(None)
    try:
        assert Bridge().tab == "dy"
    finally:
        ga._browser_session.reset(token)


class FakeBridge:
    tab = "douyin-test-tab"
    def __init__(self, states):
        self.states = iter(states)
        self.reads = 0
        self.moves = []
    def read(self, limit):
        self.reads += 1
        return copy.deepcopy(next(self.states))
    def navigate(self, before, target):
        self.moves.append(("navigate", target))
    def next(self, before):
        self.moves.append(("next", None))
    def resume(self, before):
        self.moves.append(("resume", None))


def page(**kwargs):
    return {"ok": True, "status": "ok", "url": "https://www.douyin.com/",
            "items": [], "media": None, **kwargs}


@pytest.mark.parametrize("status", ["verification_required", "restricted", "login_required"])
def test_blocked_page_does_not_navigate_or_retry(status):
    bridge = FakeBridge([page(ok=False, status=status)])
    result = Browser(bridge).run({"action": "search", "keyword": "AI"})
    assert result["status"] == status
    assert bridge.reads == 1 and bridge.moves == []


def test_site_change_and_expected_url_are_checked_before_navigation():
    bridge = FakeBridge([page(url="https://www.xiaohongshu.com/explore")])
    with pytest.raises(BrowseError, match="离开"):
        Browser(bridge).run({"action": "feed"})
    assert bridge.moves == []
    bridge = FakeBridge([page()])
    with pytest.raises(BrowseError, match="不一致"):
        Browser(bridge).run({"action": "feed", "expected_url": "https://www.douyin.com/video/123456"})


def test_player_change_on_same_url_blocks_old_worker():
    bridge = FakeBridge([page(media={"video_id": "222222222"})])
    with pytest.raises(BrowseError, match="另一内容"):
        Browser(bridge).run({"action": "next", "_expected_video_id": "111111111"})
    assert bridge.moves == []


def test_search_waits_for_actual_cards_and_preserves_unknowns():
    target = search_url("AI")
    bridge = FakeBridge([page(), page(url=target, items=[
        {"video_id": "123456789", "title": "真实卡片", "statistics": {"likes": None}}])])
    result = Browser(bridge).run({"action": "search", "keyword": "AI", "limit": 4})
    assert bridge.moves == [("navigate", target)]
    assert result["items"][0]["statistics"]["likes"] is None
    assert result["submitted"] is False and result["account_write"] is False


def test_wrong_video_cannot_satisfy_detail_ready():
    requested = "https://www.douyin.com/video/123456789"
    bridge = FakeBridge([page(), page(url=requested, media={"video_id": "987654321", "ready_state": 4}),
                         page(url=requested, media={"video_id": "123456789", "ready_state": 4})])
    result = Browser(bridge).run({"action": "open", "url": requested})
    assert bridge.reads == 3
    assert result["media"]["video_id"] == "123456789"


def test_next_is_single_action_and_requires_changed_identity():
    bridge = FakeBridge([page(media={"video_id": "123456"}),
                         page(media={"video_id": "123456"}), page(media={"video_id": "987654"})])
    result = Browser(bridge).run({"action": "next"})
    assert result["changed"] is True
    assert bridge.moves == [("next", None)]
    search = FakeBridge([page(url=search_url("AI"), media={"video_id": "123456"})])
    with pytest.raises(BrowseError, match="推荐流"):
        Browser(search).run({"action": "next"})


def test_empty_dom_is_not_reported_as_no_results():
    result = Browser(FakeBridge([page()])).run({"action": "read"})
    assert not result["ok"] and result["status"] == "no_extractable_content"


def test_sop_gate_blocks_before_browser_calls(tmp_path):
    import ga
    handler = ga.GenericAgentHandler(SimpleNamespace(), cwd=str(tmp_path))
    assert handler._xhs_sop_gate("douyin_browse", {}) is not None
    path = str((ga.Path(handler.project_root) / "skills/hard_skills/douyin_browse/SKILL.md").resolve())
    handler.working["read_sops"] = {path}
    assert handler._xhs_sop_gate("douyin_browse", {}) is None


@pytest.mark.parametrize("action", ["status", "feed", "search", "next"])
def test_research_live_stop_precedes_all_navigation(action):
    bridge = FakeBridge([page(media={"video_id": "123456", "content_kind": "live_preview"})])
    result = Browser(bridge).run({"action": action, "keyword": "AI", "_research_stops": True})
    assert result["status"] == "live_preview"
    assert bridge.moves == []


def test_research_unknown_player_stops_even_with_page_video_id():
    bridge = FakeBridge([page(url="https://www.douyin.com/video/123456", media={"video_id": None})])
    result = Browser(bridge).run({"action": "feed", "_research_stops": True})
    assert result["status"] == "unidentified_player"
    assert bridge.moves == []


def test_player_fallback_requires_both_title_and_asset_change():
    def player(path, title):
        import json
        return page(media={"player_key": json.dumps([path, title]), "video_id": None})
    bridge = FakeBridge([player("/a", "first"), player("/b", "first"), player("/b", "second")])
    result = Browser(bridge).run({"action": "next", "_research_stops": True})
    assert result["changed"] is True and result["previous_video_id"] is None
    assert bridge.reads == 3 and bridge.moves == [("next", None)]


def test_live_after_next_returns_blocked_instead_of_changed():
    bridge = FakeBridge([page(media={"video_id": "123456", "content_kind": "video"}),
                         page(media={"video_id": "987654", "content_kind": "live_preview"})])
    result = Browser(bridge).run({"action": "next", "_research_stops": True})
    assert result["status"] == "live_preview" and result["changed"] is False


def test_terminal_observation_blocks_following_tool_in_same_response():
    import ga
    from agent_loop import exhaust
    handler = ga.GenericAgentHandler(SimpleNamespace(allowed_tools={"analyze_media"}))
    handler.working["douyin_stopped"] = {"status": "live_preview"}
    outcome = exhaust(handler.dispatch("analyze_media", {}, None))
    assert outcome.should_exit and outcome.data["status"] == "live_preview"


def test_diagnostic_capture_handles_cdp_unwrapped_payload():
    import base64
    bridge = Bridge.__new__(Bridge)
    bridge.tab = "isolated"
    png = b"\x89PNG\r\n\x1a\nisolated"
    bridge.ga = SimpleNamespace(cdp_send=lambda *a, **k: base64.b64encode(png).decode())
    assert bridge.capture() == png
    bridge.ga = SimpleNamespace(cdp_send=lambda *a, **k: {"error": "disconnected"})
    with pytest.raises(BrowseError):
        bridge.capture()


def test_idle_pause_blocks_next_and_requires_verified_resume():
    paused = page(idle_pause=True, media={"video_id": "123456", "paused": True})
    bridge = FakeBridge([paused])
    with pytest.raises(BrowseError, match="resume"):
        Browser(bridge).run({"action": "next"})
    assert bridge.moves == []
    bridge = FakeBridge([paused, page(idle_pause=False, media={"video_id": "123456", "paused": False})])
    result = Browser(bridge).run({"action": "resume", "_research_stops": True})
    assert result["resumed"] and bridge.moves == [("resume", None)]
