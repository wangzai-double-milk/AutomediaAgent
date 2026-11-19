"""Content-to-publication invariants in isolated stores and fake platform transports."""
import asyncio
import copy
from types import SimpleNamespace

import pytest
from PIL import Image

from operations.content import CONTENT_TALK, CONTENT_REVIEW, PUBLISH_TOOLS
from operations.engine import Engine, THINK
from operations.runtime import RuntimeStore
from operations.store import DomainError


@pytest.fixture
def content_case(tmp_path, monkeypatch):
    from tools import xhs_test_control
    store = RuntimeStore(tmp_path / "ops.sqlite3")
    image = tmp_path / "image.png"
    Image.new("RGB", (40, 40), "white").save(image)
    binding = {"owner_id": "a" * 24, "nickname": "隔离账号"}
    monkeypatch.setattr(xhs_test_control, "status", lambda: {"binding": {**binding, "bound_at": "test"}})
    account = store.create_account("本地档案")
    inputs = {"objective": "写一篇建议", "account_id": account["id"],
              "assets": [{"path": str(image), "source": "隔离生成"}],
              "sources": [{"content": "先保存原始资料，再核对来源。", "source": "测试建议"}],
              "binding": binding, "request_key": "one"}
    return store, inputs, image


class Gateway:
    async def call(self, method, envelope):
        if method is THINK:
            return {"summary": "整理建议", "questions": [], "unknowns": [], "speech_goal": "写建议"}, {}
        if method is CONTENT_TALK:
            return {"title": "先核对，再表达", "desc": "建议先保存原始资料，再核对来源。",
                    "tags": [], "source_ids": ["source-1"], "limitations": []}, {}
        assert method is CONTENT_REVIEW
        return {"verdict": "pass", "issues": []}, {}


def ready(case):
    store, inputs, _ = case
    row = store.create_content(**inputs)
    job = store.run_job(row["draft_job"])
    engine = Engine(store, Gateway())
    assert store.claim(job["id"], engine.owner)
    asyncio.run(engine.run(job))
    store.collect_content()
    row = store.content(row["id"])
    assert row["state"] == "ready"
    return store, row


def queued(case):
    store, row = ready(case)
    job = store.queue_content(row["id"], row["fingerprint"])
    store.claim(job["id"], "studio-runtime")
    return store, row, job


def test_content_deduplication_conflicts_and_frozen_inputs(content_case):
    store, inputs, image = content_case
    row = store.create_content(**inputs)
    assert store.create_content(**inputs)["id"] == row["id"]
    changed = copy.deepcopy(inputs)
    changed["objective"] = "另一个目标"
    with pytest.raises(DomainError):
        store.create_content(**changed)
    Image.new("RGB", (40, 40), "blue").save(image)
    with pytest.raises(DomainError):
        store.create_content(**inputs)
    assert store.content(row["id"])["brief"]["assets"][0]["sha256"] == row["brief"]["assets"][0]["sha256"]


@pytest.mark.parametrize("change", ["character", "account", "asset", "cancel", "payload", "binding"])
def test_final_submit_rechecks_all_frozen_authority(content_case, change, monkeypatch):
    from tools import xhs_test_control
    store, row, job = queued(content_case)
    args = copy.deepcopy(row["payload"])
    store.check_content_publish(job["id"], args)
    if change == "character":
        profile = store.character()["profile"]; profile["persona"] = "变更"
        store.update_character(1, profile)
    elif change == "account":
        store.update_profile(job["context"]["account_id"], 1, {"audience": {"value": "变化"}})
    elif change == "asset":
        Image.new("RGB", (40, 40), "blue").save(content_case[2])
    elif change == "cancel":
        store.cancel_content(row["id"])
    elif change == "payload":
        args["visibility"] = "public"
    else:
        monkeypatch.setattr(xhs_test_control, "status", lambda: {"binding": {"owner_id": "b" * 24, "nickname": "另一账号"}})
    with pytest.raises(DomainError):
        store.check_content_publish(job["id"], args, claim=True)
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM content_submissions").fetchone()[0] == 0


def test_once_claim_crash_recovery_and_late_completion(content_case):
    store, row, job = queued(content_case)
    store.check_content_publish(job["id"], row["payload"], claim=True)
    with pytest.raises(DomainError):
        store.check_content_publish(job["id"], row["payload"], claim=True)
    store.recover_owner("studio-runtime")
    store.collect_content()
    assert store.content(row["id"])["state"] == "unknown"
    assert store.queue_content(row["id"], row["fingerprint"])["id"] == job["id"]
    assert store.run_job(job["id"])["state"] == "interrupted"
    result = {"ok": True, "visibility": "self", "attempt_id": row["fingerprint"],
              "note_id": "platform-note", "independent_verification": {"verified": True}}
    assert store.finish_content_publish(job["id"], result)["state"] == "verified"
    assert store.run_job(job["id"])["state"] == "interrupted"


def test_restart_does_not_resume_unclaimed_queued_write(content_case):
    store, row = ready(content_case)
    job = store.queue_content(row["id"], row["fingerprint"])
    store.recover_content()
    assert store.content(row["id"])["state"] == "blocked"
    assert not store.claim(job["id"], "studio-runtime")


@pytest.mark.parametrize("independent", [False, True])
def test_acknowledgement_alone_is_not_verified(content_case, independent):
    store, row, job = queued(content_case)
    store.check_content_publish(job["id"], row["payload"], claim=True)
    result = {"ok": True, "visibility": "self", "attempt_id": row["fingerprint"],
              "note_id": "platform-note", "independent_verification": {"verified": independent}}
    assert store.finish_content_publish(job["id"], result)["state"] == ("verified" if independent else "unknown")


def test_cross_brief_identical_publication_is_not_replayed(content_case):
    store, row, job = queued(content_case)
    store.check_content_publish(job["id"], row["payload"], claim=True)
    content_case[1]["request_key"] = "second"
    _, second, second_job = queued(content_case)
    with pytest.raises(DomainError):
        store.check_content_publish(second_job["id"], second["payload"], claim=True)


def test_publish_worker_cannot_use_general_publisher(content_case):
    from ga import GenericAgentHandler
    from agent_loop import exhaust
    store, row, job = queued(content_case)
    handler = GenericAgentHandler(SimpleNamespace(runtime=store, runtime_job=job, allowed_tools=PUBLISH_TOOLS))
    result = exhaust(handler.dispatch("xhs_publish_note", row["payload"], None))
    assert result.should_exit and result.data["status"] == "denied"


def test_stop_at_last_desktop_boundary_prevents_press(monkeypatch):
    from tools import xhs_desktop as d
    from tools import xhs_publish as p
    monkeypatch.setattr(d, "snapshot", lambda: {"web": [{"text": "仅自己可见"}]})
    monkeypatch.setattr(d, "identity", lambda _: "account")
    monkeypatch.setattr(d, "editor", lambda _: {})
    monkeypatch.setattr(p, "content_matches", lambda *a: True)
    monkeypatch.setattr(d, "text", lambda n: n["text"])
    monkeypatch.setattr(d, "visible", lambda n: True)
    monkeypatch.setattr(d, "schedule_state", lambda _: {"enabled": False})
    monkeypatch.setattr(d, "choose", lambda *a: {})
    calls = []
    monkeypatch.setattr(d, "act", lambda *a: calls.append(a))
    def stop():
        raise DomainError("任务已停止")
    with pytest.raises(DomainError):
        d.submit({"visibility": "self", "schedule_at": None}, "account", before_submit=stop)
    assert not calls
