"""Exercise publish orchestration without touching a browser or account."""

import json
from types import SimpleNamespace
from pathlib import Path

import pytest

import ga
from agent_loop import exhaust
from tools import task, xhs_publish


@pytest.fixture
def publication_environment(monkeypatch, tmp_path):
    parent = SimpleNamespace(task_dir=str(tmp_path), verbose=False)
    handler = ga.GenericAgentHandler(parent, cwd=str(tmp_path))
    response = SimpleNamespace(content="", thinking="")
    sop = Path(handler.project_root) / "skills/hard_skills/xhs_publish/SKILL.md"
    exhaust(handler.do_file_read({"path": str(sop)}, response))
    image = tmp_path / "image.png"
    image.write_bytes(b"fixture-image")
    calls = []
    monkeypatch.setattr(ga, "ensure_xhs_tab", lambda **_: {"ok": True, "tab_id": "101"})
    uploads = []
    monkeypatch.setattr(ga, "cdp_upload_files_to_input",
                        lambda *a, **k: uploads.append((a, k)) or {"ok": True})
    monkeypatch.setattr(xhs_publish.browser, "navigate", lambda *a, **k: None)
    args = {"images": [str(image)], "title": "Test title", "desc": "Test body", "transport": "cdp"}
    def execute(script, **kwargs):
        if "schedule_row_ambiguous" in script:
            value = {"ok": True, "enabled": False}
        elif "has_editor:" in script:
            value = {"has_editor": True, "title": args["title"], "desc": args["desc"],
                     "plain_desc": args["desc"], "chips": [], "errors": [], "busy": False}
        else:
            value = {"ok": True}
        return {"js_return": json.dumps(value)}
    monkeypatch.setattr(ga, "web_execute_js", execute)
    monkeypatch.setattr(ga, "cdp_set_xhs_visibility",
                        lambda *a, **k: {"ok": True, "now": ga._XHS_VIS_TEXT["self"]})
    monkeypatch.setattr(ga, "cdp_find_xhs_publish_bar_btn", lambda **_: {"ok": True, "x": 10, "y": 20})
    monkeypatch.setattr(ga, "cdp_mouse_click", lambda *a, **k: calls.append((a, k)) or {})
    monkeypatch.setattr(ga, "wait_xhs_publish_result", lambda **_: {"verified": True})
    try:
        yield handler, response, args, calls, uploads
    finally:
        task.set_current(None)


@pytest.mark.parametrize("visible,verified,expected,clicks_expected", [
    (True, True, "ok", 1),
    (True, False, "unverified", 1),
    (False, False, "error", 0),
])
def test_publish_self_visibility_and_acknowledgement(
        monkeypatch, publication_environment, visible, verified, expected, clicks_expected):
    handler, response, args, calls, uploads = publication_environment
    levels = []
    monkeypatch.setattr(ga, "cdp_set_xhs_visibility", lambda *a, **k: {
        "requested": levels.append(a[0]),
        "ok": visible, "now": ga._XHS_VIS_TEXT["self"] if visible else "PUBLIC",
    })
    monkeypatch.setattr(ga, "wait_xhs_publish_result", lambda **_: {"verified": verified})
    result = exhaust(handler.dispatch("xhs_publish_note", dict(args), response))
    assert result.data["status"] == expected
    assert levels == ["self"]
    assert len(uploads) == 1
    assert len(calls) == clicks_expected
    if clicks_expected:
        assert calls[0][1]["switch_tab_id"] == "101"
        repeated = exhaust(handler.dispatch("xhs_publish_note", dict(args), response))
        assert len(calls) == 1
        assert len(uploads) == 1
        assert repeated.data["skipped"] is True
        if not verified:
            assert repeated.should_exit is True
    if expected == "unverified":
        assert result.should_exit is True
        assert result.next_prompt == ""


@pytest.mark.parametrize("verified", [True, False])
def test_publish_retains_legacy_submission_guard(publication_environment, tmp_path, verified):
    handler, response, args, calls, uploads = publication_environment
    spec = xhs_publish.normalize(args)
    identity = xhs_publish.legacy_fingerprint(spec)
    (tmp_path / "publication_attempts.json").write_text(
        json.dumps({identity: {"verified": verified, "tab_id": "101"}}), encoding="utf-8")
    result = exhaust(handler.dispatch("xhs_publish_note", dict(args), response))
    assert result.data["skipped"] is True
    assert result.data["status"] == ("ok" if verified else "unverified")
    assert result.should_exit is (not verified)
    assert calls == uploads == []


@pytest.mark.parametrize("record", ['{"unfinished":', '[]'])
def test_publish_unreadable_journal_stops_agent(publication_environment, tmp_path, record):
    handler, response, args, calls, uploads = publication_environment
    (tmp_path / "publication_attempts.json").write_text(record, encoding="utf-8")
    result = exhaust(handler.dispatch("xhs_publish_note", dict(args), response))
    assert result.data["status"] == "unverified"
    assert result.should_exit is True
    assert result.next_prompt == ""
    assert calls == uploads == []


def test_publish_expected_account_required_before_upload(publication_environment):
    handler, response, args, calls, uploads = publication_environment
    result = exhaust(handler.dispatch(
        "xhs_publish_note", dict(args, expected_account="requested-account"), response))
    assert result.data["status"] == "error"
    assert "account" in result.data["msg"].lower()
    assert calls == uploads == []


def test_prepare_never_submits_or_creates_attempt(publication_environment, tmp_path):
    handler, response, args, calls, uploads = publication_environment
    result = exhaust(handler.dispatch("xhs_prepare_note", dict(args), response))
    assert result.data["status"] == "prepared"
    assert result.data["submitted"] is False
    assert len(uploads) == 1
    assert calls == []
    assert not (tmp_path / "publication_attempts.json").exists()
    assert (tmp_path / "publication_preview.json").exists()


@pytest.mark.parametrize("url,success,verified", [
    ("https://creator.xiaohongshu.com/publish/success", False, True),
    ("https://creator.xiaohongshu.com/publish/publish", True, True),
    ("https://example.test/publish/success", True, False),
    ("https://creator.xiaohongshu.com/publish/publish", False, False),
])
def test_publish_acknowledgement_requires_creator_evidence(monkeypatch, url, success, verified):
    clock = iter([0, 0.1, 2])
    monkeypatch.setattr(ga, "time", SimpleNamespace(monotonic=lambda: next(clock), sleep=lambda _: None))
    monkeypatch.setattr(ga, "web_execute_js", lambda *a, **k: {
        "js_return": json.dumps({"url": url, "success": success}),
    })
    assert ga.wait_xhs_publish_result("101", timeout=1)["verified"] is verified


def test_browser_tab_persists_for_each_agent(monkeypatch, tmp_path):
    sent = []
    class Driver:
        default_session_id = "101"
        def get_all_sessions(self):
            return [{"id": "101"}, {"id": "202"}]
        def execute_js(self, code, session_id=None, **kwargs):
            sent.append(session_id)
            return {"data": "ok"}
    monkeypatch.setattr(ga, "driver", Driver())
    monkeypatch.setattr(ga.simphtml, "execute_js_rich",
                        lambda code, driver, **kwargs: driver.execute_js(code))
    response = SimpleNamespace(content="")
    a = ga.GenericAgentHandler(SimpleNamespace(task_dir=None, verbose=False), cwd=str(tmp_path))
    b = ga.GenericAgentHandler(SimpleNamespace(task_dir=None, verbose=False), cwd=str(tmp_path))
    for handler, tab in [(a, "101"), (b, "202"), (a, None)]:
        exhaust(handler.dispatch("web_execute_js", {"script": "1", "switch_tab_id": tab}, response))
    assert sent == ["101", "202", "101"]
