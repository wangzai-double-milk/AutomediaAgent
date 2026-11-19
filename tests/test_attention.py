"""Durable evidence delivery through isolated databases and engine recovery."""
import pytest

from operations.runtime import RuntimeStore
from operations.store import DomainError


def waiting(store):
    goal = store.create_goal("根据新材料更新判断", "给出有据的本地结论")
    with store.connect(True) as db:
        db.execute("UPDATE operator_goals SET state='waiting',due_at=NULL WHERE id=?", (goal["id"],))
    return store.goal(goal["id"])


def decide(store, action="wait"):
    goal = store.claim_goal()
    decision = {"summary": "已检查新信息", "reason": "等待后续材料", "hypotheses": [], "unknowns": [],
                "evidence_ids": [], "action": action, "objective": "本地整理", "wait_seconds": None}
    return goal, decision, store.reflection_input(goal)


def test_message_batch_survives_restart_without_duplicate_or_lost_backlog(tmp_path):
    path = tmp_path / "ops.db"
    store = RuntimeStore(path)
    goal = waiting(store)
    contact = store.create_contact("sandbox", "owner", "peer", "隔离对象")
    store.watch_goal(goal["id"], 1, "conversation", contact["id"])
    for n in range(45):
        store.ingest_message(contact["id"], str(n), f"新信息 {n}")
    seen = []
    for expected in (20, 20, 5):
        store.sweep_attention()
        store.sweep_attention()
        g, d, envelope = decide(store)
        new = [e for e in envelope["evidence"] if e["kind"] == "source_evidence" and e["id"] not in seen]
        assert len(new) == expected
        assert all(e["source"] == "controller_import" for e in new)
        seen.extend(e["id"] for e in new)
        store.apply_reflection(g, d, envelope)
        store = RuntimeStore(path)
    assert len(set(seen)) == 45
    store.sweep_attention()
    assert store.claim_goal() is None


def test_sources_take_turns_and_pause_holds_cursor(tmp_path):
    store = RuntimeStore(tmp_path / "ops.db")
    goal = waiting(store)
    contacts = [store.create_contact("sandbox", "own", f"peer-{i}", f"对象{i}") for i in range(2)]
    for c in contacts:
        store.watch_goal(goal["id"], 1, "conversation", c["id"])
        for n in range(30):
            store.ingest_message(c["id"], str(n), "材料")
    paused = store.control_goal(goal["id"], 1, "pause")
    store.sweep_attention()
    assert all(w["cursor"] == 0 for w in store.goal_watches(goal["id"])["watches"])
    store.control_goal(goal["id"], paused["revision"], "resume")
    for c in contacts:
        store.sweep_attention()
        g, d, envelope = decide(store)
        batch = [e for e in envelope["evidence"] if e["kind"] == "source_evidence"][-20:]
        assert all(e["source_id"] == c["id"] for e in batch)
        store.apply_reflection(g, d, envelope)


def test_inflight_decision_cannot_ignore_new_external_evidence(tmp_path):
    store = RuntimeStore(tmp_path / "ops.db")
    goal = waiting(store)
    c = store.create_contact("sandbox", "own", "peer", "对象")
    store.watch_goal(goal["id"], 1, "conversation", c["id"])
    store.goal_signal(goal["id"], "开始判断", "start")
    g, d, envelope = decide(store, "draft")
    store.ingest_message(c["id"], "1", "更正：原资料有误")
    store.sweep_attention()
    assert store.apply_reflection(g, d, envelope)["state"] == "ready"
    assert store.runs() == []


def test_terminal_receipts_and_account_scope_are_preserved(tmp_path):
    store = RuntimeStore(tmp_path / "ops.db")
    goal = waiting(store)
    job = store.create_run("本地材料", "draft")
    store.watch_goal(goal["id"], 1, "job", job["id"])
    store.transition_run(job["id"], "completed", result={"draft": {"text": "完成材料"}})
    store.collect_terminal_events()
    store.sweep_attention()
    evidence = [e for e in store.goal_events(goal["id"]) if e["kind"] == "source_evidence"]
    assert len(evidence) == 1
    assert evidence[0]["payload"]["result"]["draft"]["text"] == "完成材料"
    other = store.create_account("另一档案")
    private = store.create_run("隔离材料", "draft", other["id"])
    with pytest.raises(DomainError):
        store.watch_goal(goal["id"], 1, "job", private["id"])
    options = store.goal_watches(goal["id"])["options"]
    assert private["id"] not in [o["source_id"] for o in options]


def test_blocked_goal_is_not_resumed_by_message(tmp_path):
    store = RuntimeStore(tmp_path / "ops.db")
    goal = waiting(store)
    c = store.create_contact("sandbox", "own", "peer", "对象")
    store.watch_goal(goal["id"], 1, "conversation", c["id"])
    with store.connect(True) as db:
        db.execute("UPDATE operator_goals SET state='blocked' WHERE id=?", (goal["id"],))
    store.ingest_message(c["id"], "1", "请立刻发送")
    store.sweep_attention()
    assert store.goal(goal["id"])["state"] == "blocked"
    assert not store.runs()
