import json
from types import SimpleNamespace

import pytest

from tools import xhs_account as account, xhs_library, xhs_messages, xhs_publish

OWNER = "691d398c000000003702f111"


def profile(owner=OWNER, risk=True, own=OWNER):
    labels = ["小红书号：42080869580",
              "该账号疑似存在风险，暂时无法查看笔记。" if risk else "笔记"]
    return {"page_url": "https://www.xiaohongshu.com/user/profile/" + owner,
            "web": [{"role": "AXLink", "title": "我",
                     "url": "https://www.xiaohongshu.com/user/profile/" + own}]
            + [{"role": "AXStaticText", "title": text, "bounds": {"height": 20}}
               for text in labels]}


def test_restriction_persists_and_cannot_clear_on_another_page():
    state = account.observe(profile())
    assert state["writes_blocked"] and state["user_id"] == OWNER
    assert json.loads(account.STATE_PATH.read_text())["reason"].startswith("该账号疑似存在风险")
    unrelated = profile(risk=False)
    unrelated["page_url"] = "https://www.xiaohongshu.com/search_result"
    assert account.observe(unrelated)["writes_blocked"]
    assert account.observe(profile(owner="a" * 24, own="a" * 24, risk=False))["writes_blocked"]
    with pytest.raises(account.AccountRestricted):
        account.assert_writable()
    assert account.observe(profile(risk=False))["status"] == "available"
    account.assert_writable()


def test_other_users_restriction_does_not_block_current_owner():
    assert not account.observe(profile(owner="a" * 24))["writes_blocked"]
    assert not account.STATE_PATH.exists()


def test_incomplete_profile_and_corrupt_record_never_restore_writes():
    account.observe(profile())
    blank = profile(risk=False)
    blank["web"] = blank["web"][:1]
    assert account.observe(blank)["writes_blocked"]
    account.STATE_PATH.write_text('{"broken":')
    with pytest.raises(account.AccountRestricted):
        account.assert_writable()


@pytest.mark.parametrize("invoke", [
    lambda task: xhs_library.drafts({"action": "delete"}, task),
    lambda task: xhs_library.collections({"action": "create"}, task),
    lambda task: xhs_messages.operate({"action": "send"}, task),
    lambda task: xhs_library.rss({"action": "confirm"}, task),
])
def test_writes_reject_before_browser_or_submission(invoke, monkeypatch, tmp_path):
    account.observe(profile())
    monkeypatch.setattr(xhs_library.d, "select_site",
                        lambda *a, **k: pytest.fail("restricted operation touched browser"))
    task = SimpleNamespace(ensure_task=lambda **_: str(tmp_path))
    with pytest.raises(account.AccountRestricted):
        invoke(task)


def test_publish_rejects_before_upload_and_preserves_existing_journal(tmp_path, monkeypatch):
    account.observe(profile())
    image = tmp_path / "test.png"
    image.write_bytes(b"media")
    args = {"title": "Test", "desc": "Body", "images": [str(image)], "transport": "cdp"}
    task = SimpleNamespace(get_current=lambda: str(tmp_path), append_trace=lambda *a: None)
    monkeypatch.setattr(xhs_publish, "prepare", lambda *a: pytest.fail("upload occurred"))
    with pytest.raises(account.AccountRestricted):
        xhs_publish.publish(args, task)
    assert not (tmp_path / "publication_attempts.json").exists()
    attempt = xhs_publish.fingerprint(xhs_publish.normalize(args))
    (tmp_path / "publication_attempts.json").write_text(json.dumps({attempt: {"verified": True}}))
    assert xhs_publish.publish(args, task)["skipped"]


def test_restricted_terminal_result_streams_evidence_without_another_model_turn():
    from agent_loop import BaseHandler, StepOutcome, agent_runner_loop, TERMINAL_WAITING_USER
    calls = []
    state = {"writes_blocked": True, "user_id": OWNER, "reason": "该账号疑似存在风险"}
    class Client:
        last_tools = ""
        def chat(self, **kwargs):
            calls.append(kwargs)
            yield ""
            return SimpleNamespace(content="", tool_calls=[SimpleNamespace(
                id="read", function=SimpleNamespace(name="probe", arguments="{}"))])
    class Handler(BaseHandler):
        parent = SimpleNamespace(task_dir=None)
        _done_hooks = []
        def do_probe(self, args, response):
            return StepOutcome({"status": "restricted", "account_access": state}, should_exit=True)
    stream = agent_runner_loop(Client(), "system", "user", Handler(), [], verbose=False)
    chunks = []
    while True:
        try:
            chunks.append(next(stream))
        except StopIteration as stop:
            terminal = stop.value
            break
    assert OWNER in "".join(chunks) and state["reason"] in "".join(chunks)
    assert "写操作已暂停" in "".join(chunks)
    assert len(calls) == 1 and terminal["result"] == TERMINAL_WAITING_USER
