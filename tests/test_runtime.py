"""Isolated runtime invariants; no platform writes or production character data."""
import asyncio
import copy
from types import SimpleNamespace

import pytest

from operations.runtime import RuntimeStore
from operations.store import DomainError
from operations.engine import Engine, THINK, TALK
from operations.broker import acquire_set, resources, try_model


def test_character_is_shared_versioned_and_invalidates_frozen_jobs(tmp_path):
    store = RuntimeStore(tmp_path / "ops.sqlite3")
    account = store.create_account("测试账号")
    job = store.create_run("写本地草稿", "draft", account["id"])
    original = copy.deepcopy(job["context"]["character"])
    profile = store.character()["profile"]
    profile["voice"]["description"] = "测试声音，不是真实账号设定"
    changed = store.update_character(1, profile)
    assert (changed["revision"], changed["voice_revision"], changed["appearance_revision"]) == (2, 2, 1)
    assert store.run_job(job["id"])["context"]["character"] == original
    assert not store.valid(job["id"])
    with pytest.raises(DomainError):
        store.update_character(1, profile)
    assert RuntimeStore(store.path).character() == changed


def test_runtime_does_not_override_existing_planner_store(tmp_path):
    from operations.methods import versions
    store = RuntimeStore(tmp_path / "ops.sqlite3")
    account = store.create_account("隔离账号")["id"]
    plan, _ = store.create_job(account, "计划", "plan-key", "", versions())
    assert store.job(account, plan["id"])["context"]["character"]["revision"] == 1
    store.create_run("研究", "draft")
    assert len(store.jobs(account)) == len(store.runs()) == 1
    profile = store.character()["profile"]
    profile["persona"] = "新角色"
    store.update_character(1, profile)
    assert store.job(account, plan["id"])["outdated"]


def test_dedupe_claim_cancellation_and_unknown_recovery(tmp_path):
    store = RuntimeStore(tmp_path / "ops.sqlite3")
    job = store.create_run("研究", "douyin_research", request_key="one")
    assert store.create_run("研究", "douyin_research", request_key="one")["id"] == job["id"]
    with pytest.raises(DomainError):
        store.create_run("改目标", "douyin_research", request_key="one")
    assert store.claim(job["id"], "scheduler")
    assert not store.claim(job["id"], "second-worker")
    action = store.begin_action(job["id"], "douyin_browse", {"action": "status"})
    store.recover_owner("unrelated")
    assert store.run_job(job["id"])["state"] == "thinking"
    store.recover_owner("scheduler")
    assert store.run_job(job["id"])["state"] == "interrupted"
    with store.connect() as db:
        assert db.execute("SELECT state FROM runtime_actions WHERE id=?", (action,)).fetchone()[0] == "unknown"
    queued = store.create_run("取消", "draft")
    assert store.cancel_run(queued["id"])["state"] == "cancelled"
    assert not store.claim(queued["id"], "scheduler")
    assert not store.valid(queued["id"])


def test_receipts_are_separate_from_model_intent(tmp_path):
    store = RuntimeStore(tmp_path / "ops.sqlite3")
    job = store.create_run("研究", "douyin_research")
    store.claim(job["id"], "scheduler")
    a = store.begin_action(job["id"], "douyin_browse", {"action": "status"})
    store.finish_action(job["id"], a, {"status": "login_required", "submitted": False})
    events = store.events(job["id"])
    assert [e["kind"] for e in events] == ["request", "action_intent", "action_receipt", "observation"]
    assert events[-2]["payload"]["result"]["submitted"] is False


def test_resource_set_releases_partial_acquisition_and_reserves_talk(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOMEDIA_RESOURCE_DIR", str(tmp_path / "locks"))
    with resources("b"):
        assert acquire_set(("a", "b")) is None
        free = acquire_set(("a",))
        assert free
        free[0].close()
    handles = [try_model("thinking") for _ in range(6)]
    try:
        assert all(handles)
        assert try_model("thinking") is None
        urgent = try_model("talk")
        assert urgent is not None
        urgent.close()
    finally:
        for h in handles:
            h.close()


class FakeGateway:
    async def call(self, method, envelope):
        if method is THINK:
            return {"summary": "检查证据", "questions": [], "unknowns": [], "speech_goal": "本地总结"}, {}
        if method is TALK:
            return {"text": "只读工具返回，尚未发送。", "evidence_ids": [r["id"] for r in envelope["receipts"]],
                    "limitations": [], "commitments": []}, {}
        return {"verdict": "pass", "issues": []}, {}


def test_three_domain_workflow_preserves_blocked_behavior(tmp_path):
    store = RuntimeStore(tmp_path / "ops.sqlite3")
    engine = Engine(store, FakeGateway())
    job = engine.submit({"kind": "douyin_research", "objective": "检查抖音"})
    store.claim(job["id"], engine.owner)
    async def fake_behavior(job, decision):
        a = store.begin_action(job["id"], "douyin_browse", {"action": "status"})
        store.finish_action(job["id"], a, {"status": "login_required"})
        return {"terminal": {"result": "WAITING_USER"}, "task_dir": None}
    engine.behavior = fake_behavior
    asyncio.run(engine.run(job))
    result = store.run_job(job["id"])
    assert result["state"] == "blocked"
    assert result["result"]["platform_sent"] is False
    assert {e["domain"] for e in store.events(job["id"])} == {"behavior", "thinking", "talk"}


@pytest.mark.parametrize("status", ["live_preview", "unidentified_player", "not_ready", "partial"])
def test_report_completion_cannot_override_terminal_evidence(tmp_path, status):
    store = RuntimeStore(tmp_path / "ops.sqlite3")
    engine = Engine(store, FakeGateway())
    job = engine.submit({"kind": "douyin_research", "objective": "严格边界"})
    store.claim(job["id"], engine.owner)
    async def behavior(job, decision):
        a = store.begin_action(job["id"], "douyin_browse", {"action": "next"})
        store.finish_action(job["id"], a, {"status": status})
        return {"terminal": {"result": "COMPLETED"}, "task_dir": None}
    engine.behavior = behavior
    asyncio.run(engine.run(job))
    assert store.run_job(job["id"])["state"] == "blocked"


def test_stop_before_tool_execution_is_durable_and_blocks_completion(tmp_path):
    from ga import GenericAgentHandler
    from agent_loop import exhaust
    store = RuntimeStore(tmp_path / "ops.sqlite3")
    engine = Engine(store, FakeGateway())
    job = engine.submit({"kind": "douyin_research", "objective": "工具边界"})
    store.claim(job["id"], engine.owner)
    async def behavior(job, decision):
        handler = GenericAgentHandler(SimpleNamespace(runtime=store, runtime_job=job, allowed_tools=set()))
        outcome = exhaust(handler.dispatch("web_execute_js", {}, None))
        assert outcome.should_exit
        return {"terminal": {"result": "COMPLETED"}, "task_dir": None}
    engine.behavior = behavior
    asyncio.run(engine.run(job))
    assert store.run_job(job["id"])["state"] == "blocked"
    assert any(e["kind"] == "worker_stopped" for e in store.events(job["id"]))


def test_late_model_result_cannot_complete_changed_character(tmp_path):
    store = RuntimeStore(tmp_path / "ops.sqlite3")
    class MutatingGateway(FakeGateway):
        async def call(self, method, envelope):
            result = await super().call(method, envelope)
            if method is THINK:
                p = store.character()["profile"]; p["persona"] = "更新"
                store.update_character(1, p)
            return result
    engine = Engine(store, MutatingGateway())
    job = engine.submit({"kind": "draft", "objective": "草稿"})
    store.claim(job["id"], engine.owner)
    asyncio.run(engine.run(job))
    assert store.run_job(job["id"])["state"] == "stale"
    assert not any(e["domain"] == "talk" for e in store.events(job["id"]))


def test_read_worker_denies_code_and_arbitrary_files(tmp_path):
    from ga import GenericAgentHandler
    from agent_loop import exhaust
    from operations.engine import READ_TOOLS
    parent = SimpleNamespace(allowed_tools=READ_TOOLS)
    handler = GenericAgentHandler(parent, cwd=str(tmp_path))
    for tool, args in (("code_run", {}), ("file_read", {"path": "/etc/hosts"}), ("web_execute_js", {})):
        result = exhaust(handler.dispatch(tool, args, None))
        assert result.should_exit and result.data["status"] == "denied"


def test_behavior_terminal_does_not_leak_response_objects_into_talk():
    import json
    from operations.engine import terminal_summary
    value = terminal_summary({"result": "COMPLETED", "data": SimpleNamespace(thinking="private")})
    assert "private" not in json.dumps(value)
    assert value["result"] == "COMPLETED"


def test_resources_are_shared_with_another_process(tmp_path, monkeypatch):
    import subprocess
    import sys
    monkeypatch.setenv("AUTOMEDIA_RESOURCE_DIR", str(tmp_path / "process-locks"))
    with resources("desktop"):
        result = subprocess.check_output([sys.executable, "-c",
            "from operations.broker import acquire_set; print(acquire_set(['desktop']) is None)"], text=True)
        assert result.strip() == "True"


def test_cancel_during_model_call_keeps_task_cancelled(tmp_path):
    store = RuntimeStore(tmp_path / "ops.sqlite3")
    async def run():
        entered = asyncio.Event()
        class WaitingGateway:
            async def call(self, method, envelope):
                entered.set()
                await asyncio.Event().wait()
        engine = Engine(store, WaitingGateway())
        job = engine.submit({"kind": "draft", "objective": "取消测试"})
        store.claim(job["id"], engine.owner)
        task = asyncio.create_task(engine.run(job))
        engine.active[job["id"]] = task
        await entered.wait()
        engine.cancel(job["id"])
        await task
        assert store.run_job(job["id"])["state"] == "cancelled"
        assert not any(e["domain"] == "talk" for e in store.events(job["id"]))
    asyncio.run(run())
