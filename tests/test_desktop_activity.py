"""Desktop Agent Live telemetry tests."""

import json
import os
import sys
import tempfile
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from frontends import desktop_bridge  # noqa: E402
from frontends.desktop_bridge import AgentManager, Session  # noqa: E402


def _trace(task_dir, step, payload):
    with open(os.path.join(task_dir, "trace.log"), "a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": "12:00:00", "step": step, "payload": payload}, ensure_ascii=False) + "\n")


def test_activity_aggregates_learning_and_convergence_metrics():
    with tempfile.TemporaryDirectory() as task_dir:
        manager = AgentManager()
        session = Session(id="sess-test", status="running")
        session.agent = types.SimpleNamespace(task_dir=task_dir)
        manager.sessions[session.id] = session

        _trace(task_dir, "research_search_round", {"round_id": "r1", "keyword": "trend"})
        _trace(task_dir, "research_search_round", {"round_id": "r2", "keyword": "pain point"})
        for index in range(5):
            _trace(task_dir, "research_item_read", {"item_id": f"note-{index}"})
            _trace(task_dir, "research_knowledge_delta", {
                "item_id": f"note-{index}",
                "platform": "xiaohongshu",
                "has_new": index == 0,
                "new_points": ["前三秒先展示结果"] if index == 0 else [],
                "revised_points": [],
            })

        result = manager.activity(session.id)

        assert result["metrics"]["searchRounds"] == 2
        assert result["metrics"]["itemsRead"] == 5
        assert result["metrics"]["consecutiveNoNew"] == 4
        assert result["knowledge"][0]["text"] == "前三秒先展示结果"
        assert result["current"]["label"] == "更新知识增量"


def test_browser_url_removes_query_tokens():
    safe = AgentManager._safe_browser_url(
        "https://www.xiaohongshu.com/explore/note-id?xsec_token=secret&source=feed"
    )
    assert safe == "https://www.xiaohongshu.com/explore/note-id"


def test_internal_trace_names_are_presented_as_customer_friendly_actions():
    known = AgentManager._event_view({
        "ts": "12:00:00",
        "step": "search_viral_nav",
        "payload": {"keyword": "减脂早餐"},
    })
    unknown = AgentManager._event_view({
        "ts": "12:00:01",
        "step": "internal_pipeline_edge",
        "payload": {},
    })

    assert known["label"] == "前往新的搜索结果"
    assert known["detail"] == "减脂早餐"
    assert unknown["label"] == "正在推进任务"
    assert "_" not in unknown["label"]


def test_platform_status_distinguishes_ready_and_login_required():
    class FakeDriver:
        default_session_id = "xhs"

        @staticmethod
        def get_all_sessions():
            return [
                {"id": "xhs", "url": "https://www.xiaohongshu.com/explore", "title": "小红书"},
                {"id": "dy", "url": "https://www.douyin.com/", "title": "抖音"},
            ]

        @staticmethod
        def execute_js(code, timeout=0, session_id=None):
            if session_id == "xhs":
                return {"data": json.dumps({"loggedIn": True, "nickname": "测试用户"})}
            return {"data": json.dumps({"loggedIn": False, "loginVisible": True})}

    original_ga = sys.modules.get("ga")
    sys.modules["ga"] = types.SimpleNamespace(driver=FakeDriver())
    try:
        manager = AgentManager()
        result = manager.platform_status(force=True)
    finally:
        if original_ga is None:
            sys.modules.pop("ga", None)
        else:
            sys.modules["ga"] = original_ga

    assert result["browserConnected"] is True
    assert result["overallReady"] is False
    assert result["platforms"]["xiaohongshu"]["state"] == "ready"
    assert result["platforms"]["xiaohongshu"]["nickname"] == "测试用户"
    assert result["platforms"]["douyin"]["state"] == "login_required"


def test_platform_status_requires_tabs_when_platforms_are_not_open():
    class EmptyDriver:
        @staticmethod
        def get_all_sessions():
            return []

    original_ga = sys.modules.get("ga")
    sys.modules["ga"] = types.SimpleNamespace(driver=EmptyDriver())
    try:
        manager = AgentManager()
        result = manager.platform_status(force=True)
    finally:
        if original_ga is None:
            sys.modules.pop("ga", None)
        else:
            sys.modules["ga"] = original_ga

    assert result["browserConnected"] is False
    assert result["platforms"]["xiaohongshu"]["state"] == "open_required"
    assert result["platforms"]["douyin"]["state"] == "open_required"


def test_workspace_collects_reference_edit_and_publish_draft():
    with tempfile.TemporaryDirectory() as task_dir:
        refs_dir = os.path.join(task_dir, "viral_refs")
        os.makedirs(refs_dir)
        reference = os.path.join(refs_dir, "00.webp")
        original = os.path.join(task_dir, "original.png")
        edited = os.path.join(task_dir, "edited_round1.png")
        for path in (reference, original, edited):
            with open(path, "wb") as file:
                file.write(b"image")
        with open(os.path.join(task_dir, "viral_meta.json"), "w", encoding="utf-8") as file:
            json.dump({
                "keyword": "咖啡探店",
                "covers": [{
                    "local_path": reference,
                    "title": "杭州咖啡店合集",
                    "likes": "952",
                    "note_url": "https://www.xiaohongshu.com/explore/note-1?token=secret",
                }],
            }, file, ensure_ascii=False)

        records = [
            {"step": "task_init", "payload": {"original_image": original}},
            {"step": "search_viral_done", "payload": {"keyword": "咖啡探店"}},
            {"step": "image_edit_round_1_done", "payload": {"output": edited}},
            {"step": "publish_draft_start", "payload": {
                "images": [edited],
                "title": "杭州这家咖啡店值得去",
                "desc": "完整正文",
                "tags": ["#杭州咖啡"],
            }},
        ]
        workspace = AgentManager()._workspace_view("sess-test", task_dir, records)

        assert workspace["mode"] == "publish"
        assert workspace["references"][0]["title"] == "杭州咖啡店合集"
        assert workspace["references"][0]["likes"] == "952"
        assert workspace["assets"][0]["role"] == "publish"
        assert workspace["draft"]["title"] == "杭州这家咖啡店值得去"
        assert workspace["draft"]["desc"] == "完整正文"
        assert workspace["focus"]["role"] == "publish"


def test_workspace_paths_cannot_escape_task_directory():
    with tempfile.TemporaryDirectory() as task_dir, tempfile.NamedTemporaryFile() as outside:
        root = os.path.realpath(task_dir)
        assert AgentManager._inside_task(
            __import__("pathlib").Path(root), outside.name
        ) is None


def test_uploaded_video_becomes_video_prompt_attachment():
    upload_dir = desktop_bridge._UPLOAD_DIR
    upload_dir.mkdir(parents=True, exist_ok=True)
    video = upload_dir / "media-0123456789abcdef.mp4"
    video.write_bytes(b"video")
    try:
        prompt, ids = desktop_bridge.normalize_prompt("分析这个视频", [{
            "id": "media-0123456789abcdef",
            "path": str(video),
            "kind": "video",
        }])
        assert ids == ["media-0123456789abcdef"]
        assert f"[video:{video.resolve()}]" in prompt
    finally:
        video.unlink(missing_ok=True)


def test_untrusted_attachment_path_is_ignored():
    with tempfile.NamedTemporaryFile(suffix=".mp4") as outside:
        prompt, ids = desktop_bridge.normalize_prompt("分析", [{
            "id": "media-outside",
            "path": outside.name,
            "kind": "video",
        }])
    assert ids == []
    assert "[video:" not in prompt


def test_browser_windows_include_regular_web_tabs():
    class FakeDriver:
        default_session_id = "xhs"

        @staticmethod
        def get_all_sessions():
            return [
                {"id": "xhs", "url": "https://www.xiaohongshu.com/explore", "title": "小红书"},
                {"id": "dy", "url": "https://www.douyin.com/", "title": "抖音"},
                {"id": "web", "url": "https://example.com/article", "title": "行业报告"},
            ]

    manager = AgentManager()
    manager._browser_driver = lambda: FakeDriver()
    manager._browser_preview_cache = {
        "xhs": {"data": "x", "updatedAt": 123.0},
        "dy": {"data": "x", "updatedAt": 123.0},
        "web": {"data": "x", "updatedAt": 123.0},
    }
    windows = manager.browser_windows()

    assert [item["platform"] for item in windows] == ["xiaohongshu", "douyin", "web"]
    assert windows[0]["active"] is True
    assert all(item["previewReady"] for item in windows)


def _main():
    tests = [value for name, value in sorted(globals().items()) if name.startswith("test_") and callable(value)]
    for test in tests:
        test()
        print(f"  PASS  {test.__name__}")
    print(f"\n{len(tests)} passed, 0 failed (total {len(tests)})")


if __name__ == "__main__":
    _main()
