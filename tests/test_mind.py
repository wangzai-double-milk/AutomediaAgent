"""Goal orchestration uses isolated stores and no external accounts."""
import asyncio

import pytest

from operations.engine import Engine, THINK, TALK
from operations.mind import REFLECT
from operations.runtime import RuntimeStore
from operations.store import DomainError


def choice(action="draft", objective="整理本地材料", refs=None):
    return {"summary": "核对材料", "reason": "产出可检查的草稿", "hypotheses": [], "unknowns": [],
            "evidence_ids": refs or [], "action": action, "objective": objective, "wait_seconds": None}


def prepared(tmp_path):
    store = RuntimeStore(tmp_path / "goals.sqlite3")
    store.create_goal("整理资料", "产出有据草稿")
    goal = store.claim_goal()
    return store, goal, store.reflection_input(goal)


def test_late_reflection_and_child_are_cancelled_together(tmp_path):
    store, goal, envelope = prepared(tmp_path)
    current = store.apply_reflection(goal, choice(), envelope)
    duplicate = store.apply_reflection(goal, choice(), envelope)
    assert duplicate["child_id"] == current["child_id"]
    store.control_goal(goal["id"], 1, "cancel")
    assert not store.valid(current["child_id"])
    assert store.run_job(current["child_id"])["state"] == "cancelled"
    assert store.apply_reflection(goal, choice(), envelope)["state"] == "cancelled"
    assert len(store.runs()) == 1


def test_correction_arriving_during_reflection_prevents_old_dispatch(tmp_path):
    store, goal, envelope = prepared(tmp_path)
    store.goal_signal(goal["id"], "先等材料", "correction")
    assert store.apply_reflection(goal, choice(), envelope)["state"] == "ready"
    assert not store.runs()
    store.goal_signal(goal["id"], "先等材料", "correction")
    with pytest.raises(DomainError):
        store.goal_signal(goal["id"], "不同信息", "correction")


def test_snapshot_changes_and_ungranted_actions_cannot_dispatch(tmp_path):
    store, goal, envelope = prepared(tmp_path)
    for decision in (choice("douyin_research"), choice(refs=["invented"]), choice("complete")):
        with pytest.raises(DomainError):
            store.apply_reflection(goal, decision, envelope)
    profile = store.character()["profile"]
    profile["persona"] = "隔离新版本"
    store.update_character(1, profile)
    assert store.apply_reflection(goal, choice(), envelope)["state"] == "stale"
    assert not store.runs()


def test_interrupted_child_collects_once_and_requires_resume(tmp_path):
    store, goal, envelope = prepared(tmp_path)
    child = store.apply_reflection(goal, choice(), envelope)["child_id"]
    store.claim(child, "studio-runtime")
    store.recover_owner("studio-runtime")
    store.sweep_goals()
    store.sweep_goals()
    next_goal = store.claim_goal()
    next_input = store.reflection_input(next_goal)
    assert len([e for e in next_input["evidence"] if e["kind"] == "job_result"]) == 1
    assert store.apply_reflection(next_goal, choice(objective="下一步"), next_input)["state"] == "blocked"
    assert len(store.runs()) == 1
    store.control_goal(goal["id"], 1, "resume")
    resumed = store.claim_goal()
    assert store.apply_reflection(resumed, choice(objective="下一步"), store.reflection_input(resumed))["state"] == "running"


def test_repeated_action_waits_and_new_information_wakes_once(tmp_path):
    store, goal, envelope = prepared(tmp_path)
    child = store.apply_reflection(goal, choice(), envelope)["child_id"]
    store.transition_run(child, "completed", result={"draft": {"text": "草稿"}})
    store.sweep_goals()
    following = store.claim_goal()
    assert store.apply_reflection(following, choice(), store.reflection_input(following))["state"] == "waiting"
    assert store.claim_goal() is None
    store.goal_signal(goal["id"], "新的材料", "material")
    assert store.claim_goal()
    assert store.claim_goal() is None
    assert len(store.runs()) == 1


def test_scheduler_completes_goal_without_controller_followup(tmp_path):
    store, goal, _ = prepared(tmp_path)
    store.recover_goals()

    class Gateway:
        async def call(self, method, envelope):
            if method is REFLECT:
                results = [e for e in envelope["evidence"] if e["kind"] == "job_result"]
                return choice("complete" if results else "draft", refs=[e["id"] for e in results]), {}
            if method is THINK:
                return {"summary": "整理", "questions": [], "unknowns": [], "speech_goal": "草稿"}, {}
            if method is TALK:
                return {"text": "本地草稿", "evidence_ids": [], "limitations": [], "commitments": []}, {}
            return {"verdict": "pass", "issues": []}, {}

    async def run():
        engine = Engine(store, Gateway())
        await engine.start()
        try:
            async def completed():
                while store.goal(goal["id"])["state"] != "completed":
                    await asyncio.sleep(.02)
            await asyncio.wait_for(completed(), timeout=5)
            assert store.goal(goal["id"])["turn"] == 2
            assert len(store.runs()) == 1
            assert store.runs()[0]["state"] == "completed"
        finally:
            await engine.close()
    asyncio.run(run())


def test_success_summary_cannot_override_platform_login_receipt(tmp_path):
    from test_runtime import FakeGateway
    store = RuntimeStore(tmp_path / "runtime.sqlite3")
    engine = Engine(store, FakeGateway())
    job = store.create_run("只读观察", "douyin_research")
    store.claim(job["id"], engine.owner)

    async def behavior(job, decision):
        action = store.begin_action(job["id"], "douyin_browse", {"action": "status"})
        store.finish_action(job["id"], action, {"status": "login_required"})
        return {"terminal": {"result": "COMPLETED"}, "task_dir": None}
    engine.behavior = behavior
    asyncio.run(engine.run(job))
    assert store.run_job(job["id"])["state"] == "blocked"


def test_malformed_reflection_repairs_once_without_reexecuting_work(tmp_path):
    store, goal, _ = prepared(tmp_path)

    class Gateway:
        calls = 0

        async def call(self, method, envelope):
            if method is REFLECT:
                self.calls += 1
                if self.calls == 1:
                    return {"summary": "缺失行动字段"}, {}
                assert envelope["correction_required"]
                return choice("wait", objective=""), {}
            return {"verdict": "pass", "issues": []}, {}

    gateway = Gateway()
    asyncio.run(Engine(store, gateway).reflect(goal))
    assert store.goal(goal["id"])["state"] == "waiting"
    assert gateway.calls == 2
    assert not store.runs()
    assert any("validation_error" in event["payload"] for event in store.goal_events(goal["id"]))
