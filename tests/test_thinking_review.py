"""Exercise review revisions, bounded rejection and cancellation without models."""
import asyncio

import pytest

from automedia.application.runtime import RuntimeStore
from automedia.thinking.engine import Engine, THINK, TALK, REVIEW


@pytest.mark.parametrize("passes,cancel,state,attempts", [
    (True, False, "completed", 2),
    (False, False, "blocked", 3),
    (True, True, "cancelled", 1),
])
def test_thinking_owns_bounded_draft_revision(tmp_path, passes, cancel, state, attempts):
    store = RuntimeStore(tmp_path / "review.sqlite3")
    job = store.create_run("表达温和且准确", "draft")
    class Gateway:
        talks = 0
        reviews = 0
        async def call(self, method, envelope):
            if method is THINK:
                return {"summary": "判断", "questions": [], "unknowns": [], "speech_goal": "温和表达"}, {}
            if method is TALK:
                self.talks += 1
                if self.talks > 1:
                    assert envelope["previous_draft"]["text"] == "候选表达"
                    assert envelope["revision_request"] == ["语气太锋利，改得温和"]
                return {"text": "候选表达", "evidence_ids": [], "limitations": [], "commitments": []}, {}
            assert method is REVIEW and method["domain"] == "thinking"
            self.reviews += 1
            if cancel:
                store.cancel_run(job["id"])
            return ({"verdict": "pass", "issues": []} if passes and self.reviews > 1 else
                    {"verdict": "revise", "issues": ["语气太锋利，改得温和"]}), {}
    gateway = Gateway()
    engine = Engine(store, gateway)
    store.claim(job["id"], engine.owner)
    asyncio.run(engine.run(job))
    assert store.run_job(job["id"])["state"] == state
    assert gateway.talks == attempts
    reviews = [e for e in store.events(job["id"]) if e["kind"] == "review"]
    assert len(reviews) == attempts and all(e["domain"] == "thinking" for e in reviews)
