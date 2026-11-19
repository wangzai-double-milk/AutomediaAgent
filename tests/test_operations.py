"""Planning invariants, with isolated SQLite and an explicitly fake model gateway."""
import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from operations.methods import METHODS, versions
from operations.planner import ModelError, Planner, validate
from operations.store import DomainError, Store


def prepared(tmp_path):
    store = Store(tmp_path / "operations.sqlite3")
    account = store.create_account("隔离验收账号")
    return store, account["id"]


def memory(store, account_id, content="本人拍了番茄鸡蛋面"):
    return store.add_memory(account_id, dict(kind="experience", content=content,
                                            source="本人原始记录（测试）", occurred_at="2026-10-29"))


def test_profile_cas_locks_and_history_survive_reopen(tmp_path):
    store, account_id = prepared(tmp_path)
    store.update_profile(account_id, 1, {"identity": {"value": "本人", "locked": True}})
    with pytest.raises(DomainError, match="其他窗口"):
        store.update_profile(account_id, 1, {"voice": {"value": "旧编辑"}})
    with pytest.raises(DomainError, match="锁定"):
        store.update_profile(account_id, 2, {"identity": {"value": "AI覆盖"}}, actor="agent")
    store.update_profile(account_id, 2, {"voice": {"value": "直接"}}, actor="agent")
    reopened = Store(store.path)
    assert reopened.account(account_id)["profile"]["identity"]["value"] == "本人"
    assert [row["revision"] for row in reopened.history(account_id)] == [3, 2, 1]


def test_memory_scope_literal_search_retraction_and_expiry(tmp_path):
    store, a = prepared(tmp_path)
    b = store.create_account("另一账号")["id"]
    m = memory(store, a, "番茄记录：20%")
    memory(store, b, "番茄记录：私有")
    assert len(store.memories(a, "番茄")) == 1
    assert len(store.memories(a, "%")) == 1
    assert len(store.memories(a, "_")) == 0
    with pytest.raises(DomainError):
        store.retract_memory(b, m)
    job, _ = store.create_job(a, "写计划", "key", "", versions())
    store.retract_memory(a, m)
    rev = store.account(a)["memory_revision"]
    store.retract_memory(a, m)
    assert store.account(a)["memory_revision"] == rev  # duplicate retract is a no-op
    assert not store.memories(a)
    assert store.memories(a, include_inactive=True)[0]["state"] == "retracted"
    assert store.job(a, job["id"])["outdated"]
    future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    store.add_memory(a, dict(kind="observation", content="营业时间", source="测试来源",
                             occurred_at="今天", valid_until=future))
    with store.connect(True) as db:
        db.execute("UPDATE memories SET valid_until='2000-01-01T00:00:00+00:00' WHERE account_id=?", (a,))
    assert not store.memories(a)


def test_duplicate_request_and_active_job_are_transactional(tmp_path):
    store, a = prepared(tmp_path)
    job, created = store.create_job(a, "第一任务", "key", "", versions())
    same, created_again = store.create_job(a, "第一任务", "key", "", versions())
    assert created and not created_again and job["id"] == same["id"]
    with pytest.raises(DomainError, match="不同任务"):
        store.create_job(a, "改过任务", "key", "", versions())
    with pytest.raises(DomainError, match="正在生成"):
        Store(store.path).create_job(a, "第二任务", "key2", "", versions())
    store.recover_interrupted()
    assert store.job(a, job["id"])["state"] == "interrupted"
    assert store.create_job(a, "第二任务", "key2", "", versions())[1]


def test_second_process_cannot_recover_live_jobs(tmp_path):
    store, a = prepared(tmp_path)
    other = Store(store.path)
    store.acquire_owner()
    try:
        with pytest.raises(RuntimeError, match="另一工作台"):
            other.acquire_owner()
    finally:
        store.release_owner()
    other.acquire_owner()
    other.release_owner()


class FakeGateway:
    def __init__(self, store=None, account_id=None, mutate_at_review=False, fail=False):
        self.entered = set()
        self.both_started = asyncio.Event()
        self.review_envelope = None
        self.store, self.account_id = store, account_id
        self.mutate_at_review, self.fail = mutate_at_review, fail

    async def call(self, method, envelope):
        name = next(key for key, value in METHODS.items() if value is method)
        if name in ("content", "diagnosis"):
            self.entered.add(name)
            if len(self.entered) == 2:
                self.both_started.set()
            await asyncio.wait_for(self.both_started.wait(), 1)  # proves fan-out, not timing guess
            if self.fail and name == "diagnosis":
                raise ModelError("隔离模型失败")
        base = dict(summary="隔离产物", next_actions=["补充证据"])
        if name == "content":
            return {**base, "ideas": []}, {}
        if name == "diagnosis":
            return {**base, "decision": "wait", "action_recommended": False,
                    "evidence_ids": [], "missing": ["数据"], "observation_plan": []}, {}
        self.review_envelope = envelope
        if self.mutate_at_review:
            self.store.update_profile(self.account_id, 1, {"voice": {"value": "人工刚改的口吻"}})
        return {**base, "case_id": envelope["case_id"], "verdict": "pass", "issues": []}, {}


@pytest.mark.parametrize("mutate,expected", [(False, "completed"), (True, "stale")])
def test_parallel_branches_complete_case_and_late_revision_gate(tmp_path, mutate, expected):
    store, a = prepared(tmp_path)
    m = memory(store, a)

    async def run():
        gateway = FakeGateway(store, a, mutate_at_review=mutate)
        planner = Planner(store, gateway)
        job = planner.start(a, "根据记忆做明日计划", "key")
        task = planner.tasks[job["id"]]
        await task
        result = store.job(a, job["id"])
        assert result["state"] == expected
        assert result["executable"] is False
        assert set(result["branches"]) == {"content", "diagnosis"}
        envelope = gateway.review_envelope
        assert envelope["case_id"] == job["id"] and envelope["context"]["memories"][0]["id"] == m
        assert envelope["context"]["profile_revision"] == 1 and "objective" in envelope
        assert envelope["allowed_actions"] == ["local_plan_only"]
    asyncio.run(run())


def test_partial_failure_preserved_and_cancel_is_terminal(tmp_path):
    store, a = prepared(tmp_path)

    async def run():
        planner = Planner(store, FakeGateway(fail=True))
        job = planner.start(a, "失败测试", "key")
        await planner.tasks[job["id"]]
        result = store.job(a, job["id"])
        assert result["state"] == "failed"
        assert result["branches"]["content"]["state"] == "completed"
        assert result["review"] is None
        second = planner.start(a, "取消测试", "key2")
        task = planner.tasks[second["id"]]
        planner.cancel(a, second["id"])
        await asyncio.gather(task, return_exceptions=True)
        store.transition(a, second["id"], "completed")  # late worker cannot overwrite cancellation
        assert store.job(a, second["id"])["state"] == "cancelled"
    asyncio.run(run())


def test_invalid_references_and_wait_cannot_recommend_action():
    ctx = {"case_id": "same-case", "memories": []}
    output = dict(summary="等一等", next_actions=[], decision="wait", action_recommended=True,
                  evidence_ids=[], missing=[], observation_plan=[])
    with pytest.raises(ModelError, match="相矛盾"):
        validate("diagnosis", output, ctx)
    output.update(action_recommended=False, evidence_ids=["foreign-account-memory"])
    with pytest.raises(ModelError, match="不存在"):
        validate("diagnosis", output, ctx)
    with pytest.raises(ModelError, match="案例"):
        validate("review", dict(summary="通过", next_actions=[], case_id="other-case",
                                verdict="pass", issues=[]), ctx)


def test_http_contracts_and_origin_boundary(tmp_path, monkeypatch):
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer
    from frontends.desktop_bridge import cors_middleware
    from operations.api import install
    monkeypatch.setenv("AUTOMEDIA_OPERATIONS_DB", str(tmp_path / "http.sqlite3"))

    async def run():
        app = web.Application(middlewares=[cors_middleware])
        install(app, tmp_path)
        async with TestClient(TestServer(app)) as client:
            denied = await client.post("/ops/accounts", json={"name": "不应创建"},
                                       headers={"Origin": "https://foreign.example"})
            assert denied.status == 403
            malformed = await client.post("/ops/accounts", data="[]")
            assert malformed.status == 400
            created = await client.post("/ops/accounts", json={"name": "接口验收"})
            a = (await created.json())["id"]
            saved = await client.post(f"/ops/{a}/profile", json={
                "expected_revision": 1, "fields": {"identity": {"value": "人工身份"}}})
            assert saved.status == 200
            conflict = await client.post(f"/ops/{a}/profile", json={
                "expected_revision": 1, "fields": {"identity": {"value": "旧值"}}})
            assert conflict.status == 409
            assert (await client.get("/ops/unknown/plans")).status == 404
            assert (await client.post("/ops/accounts", data=b"x" * 65537)).status == 400
    asyncio.run(run())
