import json
from datetime import datetime, timedelta

import ga
from tools import task, xhs_queue as queue, xhs_desktop as desktop, xhs_account as account
from tools.xhs_publish import TIMEZONE


def test_future_items_never_probe_or_select_browser(monkeypatch, tmp_path):
    monkeypatch.setattr(task, "TASKS_ROOT", str(tmp_path))
    item = {"id": "future", "state": "queued", "attempts": 0, "publication": {},
            "run_after": (datetime.now(TIMEZONE) + timedelta(days=1)).isoformat()}
    (tmp_path / "content_queue.json").write_text(json.dumps([item]))
    probes = []
    monkeypatch.setattr(queue, "publication_ready", lambda *a, **k: probes.append(k) or True)
    assert queue.run_due(task)["processed"] == 0
    assert not probes
    assert json.loads((tmp_path / "content_queue.json").read_text())[0]["attempts"] == 0


def test_due_item_stays_queued_with_zero_attempts_when_restricted(monkeypatch, tmp_path):
    monkeypatch.setattr(task, "TASKS_ROOT", str(tmp_path))
    account.STATE_PATH.write_text(json.dumps({"status": "restricted", "writes_blocked": True}))
    item = {"id": "due", "state": "queued", "attempts": 0, "publication": {},
            "run_after": (datetime.now(TIMEZONE) - timedelta(minutes=1)).isoformat()}
    (tmp_path / "content_queue.json").write_text(json.dumps([item]))
    monkeypatch.setattr(desktop, "snapshot", lambda: (_ for _ in ()).throw(AssertionError("browser activated")))
    assert queue.run_due(task)["processed"] == 0
    saved = json.loads((tmp_path / "content_queue.json").read_text())[0]
    assert saved["state"] == "queued" and saved["attempts"] == 0


def test_only_due_readiness_can_select_creator_and_preserves_composers(monkeypatch):
    monkeypatch.setattr(ga, "driver", None)
    active = {"page_url": "http://127.0.0.1:14169", "web": []}
    creator = {"page_url": "https://creator.xiaohongshu.com/new/note-manager", "web": []}
    selections = []
    monkeypatch.setattr(desktop, "snapshot", lambda: active)
    monkeypatch.setattr(desktop, "select_site", lambda *a: selections.append(a) or creator)
    monkeypatch.setattr(desktop, "identity", lambda s: "Owner" if s is creator else None)
    monkeypatch.setattr(desktop, "editor", lambda s: {"has_editor": False})
    payload = {"transport": "desktop", "expected_account": "Owner"}
    assert not queue.publication_ready(payload)
    assert not selections
    assert queue.publication_ready(payload, activate=True)
    assert selections == [(True,)]
    active.update(page_url="https://www.xiaohongshu.com/chat",
                  web=[{"role": "AXTextArea", "value": "unfinished"}])
    assert not queue.publication_ready(payload, activate=True)
    assert len(selections) == 1
