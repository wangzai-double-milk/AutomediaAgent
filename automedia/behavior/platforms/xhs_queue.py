"""A durable content queue. Interrupted submits require reconciliation, not replay."""

import contextlib
# Platform implementation; legacy tools imports share this module.
import json
import uuid
from datetime import datetime
from pathlib import Path

from tools.xhs_publish import TIMEZONE, atomic_json, normalize, fingerprint


@contextlib.contextmanager
def _store(task):
    import fcntl
    root = Path(task.TASKS_ROOT)
    root.mkdir(parents=True, exist_ok=True)
    path = root / "content_queue.json"
    with open(root / ".content_queue.lock", "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            items = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
            if not isinstance(items, list):
                raise ValueError("queue_store_invalid")
            yield items
            atomic_json(path, items)
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def enqueue(args, task):
    payload = dict(args.get("publication") or {})
    from tools import xhs_test_control as control
    test_state = control.status()
    if test_state.get("enabled"):
        bound = test_state.get("binding") or {}
        if (not bound or payload.get("owner_id") != bound.get("owner_id")
                or payload.get("expected_account") != bound.get("nickname")
                or payload.get("transport") not in {"desktop", "auto", None}):
            raise ValueError("新测试队列需明确提供绑定owner_id、expected_account和desktop通路")
        payload["transport"] = "desktop"
    if payload.get("preview") or payload.get("save_draft"):
        raise ValueError("发布队列不接受 preview/save_draft；请单独预填或保存草稿")
    payload.setdefault("visibility", "self")
    if payload.get("transport") == "desktop" and not payload.get("expected_account"):
        raise ValueError("desktop 队列必须绑定 expected_account")
    spec = normalize(payload)
    identity = fingerprint(spec)
    run_after = args.get("run_after") or datetime.now(TIMEZONE).isoformat()
    when = datetime.fromisoformat(run_after.replace("Z", "+00:00"))
    if when.tzinfo is None:
        when = when.replace(tzinfo=TIMEZONE)
    task_dir = task.ensure_task(hint="content_queue")
    with _store(task) as items:
        existing = next((item for item in items if item["fingerprint"] == identity
                         and item["state"] != "cancelled"), None)
        if existing:
            if existing.get("owner_id") != payload.get("owner_id"):
                raise ValueError("相同内容已有其他账号队列；不能改绑")
            return {"ok": True, "skipped": True, "item": existing}
        item = {"id": uuid.uuid4().hex, "fingerprint": identity,
                "state": "queued", "run_after": when.astimezone(TIMEZONE).isoformat(),
                "publication": payload, "spec": spec, "task_dir": task_dir,
                "owner_id": payload.get("owner_id"),
                "created_at": datetime.now(TIMEZONE).isoformat(), "attempts": 0}
        items.append(item)
    task.append_trace("queue_enqueued", {"id": item["id"], "run_after": item["run_after"]})
    return {"ok": True, "item": item}


def list_items(task, state=None):
    with _store(task) as items:
        return {"ok": True, "items": [dict(item) for item in items if not state or item["state"] == state]}


def cancel(item_id, task):
    with _store(task) as items:
        item = next((item for item in items if item["id"] == item_id), None)
        if not item:
            return {"ok": False, "reason": "queue_item_not_found"}
        if item["state"] != "queued":
            return {"ok": False, "reason": "only_queued_item_can_be_cancelled", "state": item["state"]}
        item["state"] = "cancelled"
        return {"ok": True, "item": dict(item)}


def _update(item_id, task, **values):
    with _store(task) as items:
        item = next(item for item in items if item["id"] == item_id)
        item.update(values, updated_at=datetime.now(TIMEZONE).isoformat())
        return dict(item)


def run_due(task, limit=1, background=False):
    """Claim before browser work; a crashed `running` item is never automatically retried."""
    if not isinstance(limit, int) or not 1 <= limit <= 5:
        raise ValueError("limit 必须为 1–5")
    results = []
    deferred = None
    from tools import xhs_publish
    from tools import xhs_test_control as control
    from ga import _browser_action_lock
    for _ in range(limit):
        # Inspect readiness before claiming. Disconnected/mismatched work stays
        # queued, with zero attempts. The shared browser lock spans this check.
        with _browser_action_lock:
            with _store(task) as items:
                item = None
                for candidate in items:
                    if (candidate["state"] != "queued" or
                            datetime.fromisoformat(candidate["run_after"]) > datetime.now(TIMEZONE)):
                        continue
                    try:
                        ready = control.queue_ready(candidate, background=background)
                    except Exception as exc:
                        from tools.xhs_account import AccountRestricted
                        if isinstance(exc, AccountRestricted):
                            ready = {"ok": False, "status": "restricted", "reason": str(exc),
                                     "account_access": exc.state}
                        else:
                            raise
                    if not ready["ok"]:
                        deferred = ready
                        continue
                    if publication_ready(candidate["publication"], activate=True):
                        try:
                            control.verify_browser(candidate.get("owner_id"),
                                                   candidate["publication"].get("expected_account"))
                        except control.TestDeferred as exc:
                            deferred = exc.result
                            continue
                        item = candidate
                        break
                if not item:
                    break
                item.update(state="running", attempts=item["attempts"] + 1)
                claimed = dict(item)
        previous = task.get_current()
        task.set_current(claimed["task_dir"])
        try:
            # File changes after enqueue are detected before any browser action.
            current_spec = normalize(claimed["publication"])
            if fingerprint(current_spec) != claimed["fingerprint"]:
                raise ValueError("媒体或文案已变更，请重新排队")
            with _browser_action_lock:
                result = xhs_publish.publish(claimed["publication"], task)
            state = "done" if result.get("ok") else "uncertain"
        except Exception as exc:
            # If the publication journal contains an attempt, do not release for retry.
            path = Path(claimed["task_dir"]) / "publication_attempts.json"
            try:
                submitted = claimed["fingerprint"] in json.loads(path.read_text()) if path.exists() else False
            except (OSError, ValueError):
                submitted = True
            result = exc.result if isinstance(exc, control.TestDeferred) else {"ok": False, "reason": str(exc)}
            state = "uncertain" if submitted else "queued" if isinstance(exc, control.TestDeferred) else "failed"
        finally:
            task.set_current(previous)
        saved = _update(claimed["id"], task, state=state, result=result,
                        attempts=claimed["attempts"] - 1 if state == "queued" else claimed["attempts"])
        if state == "queued":
            deferred = result
            break
        results.append(saved)
        if state == "uncertain":
            break
    return {"ok": all(item["state"] == "done" for item in results) and not deferred, "processed": len(results),
            **({**deferred, "deferred": deferred} if deferred else {}),
            "results": results, "message": "running/uncertain 不会自动重发，需要作品管理页核对。"}


def publication_ready(payload, activate=False):
    """Only due work may select an existing creator tab; preserve open forms."""
    import ga
    from urllib.parse import urlsplit
    from tools.xhs_account import status
    if status()["writes_blocked"]:
        return False
    from tools import xhs_test_control as control
    try:
        control.preflight("publish", payload.get("owner_id"), payload.get("expected_account"),
                          payload.get("transport", "auto"))
    except control.TestDeferred:
        return False
    requested = payload.get("transport", "auto")
    if control.status().get("enabled"):
        requested = "desktop"
    sessions = ga.driver.get_all_sessions() if ga.driver is not None else []
    connected = any(urlsplit(str(s.get("url") or "")).hostname in
                    {"www.xiaohongshu.com", "creator.xiaohongshu.com"} for s in sessions)
    if requested != "desktop" and connected:
        if payload.get("expected_account") or payload.get("audio") or payload.get("longform_body"):
            return False
        return True
    if requested == "cdp" or not payload.get("expected_account"):
        return False
    try:
        from tools.xhs_desktop import snapshot, identity, editor, select_site
        current = snapshot()
        if urlsplit(current.get("page_url") or "").hostname != "creator.xiaohongshu.com":
            if not activate:
                return False
            # Do not take focus from an unfinished XHS composer.
            if (urlsplit(current.get("page_url") or "").hostname == "www.xiaohongshu.com"
                    and any(n["role"] == "AXTextArea" and n.get("value") for n in current["web"])):
                return False
            current = select_site(True)
        return (identity(current) == payload["expected_account"] and not editor(current)["has_editor"]
                and not current.get("account_access", {}).get("writes_blocked")
                and not any(n["role"] == "AXTextArea" for n in current["web"])
                and not any(n["role"] == "AXHeading" and n.get("title") == "草稿箱" for n in current["web"]))
    except Exception:
        return False


class QueueWorker:
    """One daemon per task store; waits interruptibly and leaves disconnected work queued."""

    def __init__(self, task, interval=15):
        import threading
        self.task, self.interval = task, interval
        self.stop_event = threading.Event()
        self.thread = None
        self.status = {"running": False, "connected": False, "last_result": None, "error": None}

    def start(self):
        import threading
        if self.thread and self.thread.is_alive():
            return
        self.stop_event.clear()
        self.thread = threading.Thread(target=self._loop, name="automedia-content-queue", daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        # Browser operation may be in progress; never interrupt a submitting item.

    def _loop(self):
        import fcntl
        root = Path(self.task.TASKS_ROOT)
        root.mkdir(parents=True, exist_ok=True)
        with open(root / ".content_queue.worker.lock", "a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                self.status.update(running=False, error="another_queue_worker_is_active")
                return
            self.status["running"] = True
            try:
                while not self.stop_event.is_set():
                    try:
                        import ga
                        from tools import xhs_test_control as control
                        test_state = control.status()
                        if test_state.get("background_paused", True):
                            self.status.update(connected=False, paused=True, error=None)
                            self.stop_event.wait(self.interval)
                            continue
                        self.status["paused"] = False
                        # Do not trigger first_init_driver's blocking discovery loop.
                        with _store(self.task) as items:
                            queued = [item for item in items if item["state"] == "queued"
                                      and datetime.fromisoformat(item["run_after"]) <= datetime.now(TIMEZONE)]
                        connected = any(publication_ready(item["publication"]) for item in queued)
                        self.status.update(connected=connected, error=None)
                        if queued:
                            result = run_due(self.task, background=True)
                            if result["processed"]:
                                self.status["last_result"] = {
                                    "processed": result["processed"],
                                    "states": [item["state"] for item in result["results"]],
                                    "at": datetime.now(TIMEZONE).isoformat(),
                                }
                    except Exception as exc:
                        self.status["error"] = str(exc)
                    self.stop_event.wait(self.interval)
            finally:
                self.status["running"] = False
                fcntl.flock(lock, fcntl.LOCK_UN)


def retry(item_id, task):
    with _store(task) as items:
        item = next((item for item in items if item["id"] == item_id), None)
        if not item or item["state"] != "failed":
            return {"ok": False, "reason": "only_pre_submit_failure_can_retry"}
        item["state"] = "queued"
        return {"ok": True, "item": dict(item)}


def reconcile(item_id, note_id, task):
    from tools import xhs_manage, xhs_publish
    with _store(task) as items:
        item = next((dict(item) for item in items if item["id"] == item_id), None)
    if not item or item["state"] not in {"uncertain", "running"}:
        return {"ok": False, "reason": "item_does_not_need_reconciliation"}
    directory = Path(item["task_dir"])
    journal_path = directory / "publication_attempts.json"
    try:
        journal = json.loads(journal_path.read_text())
        attempt = journal[item["fingerprint"]]
        if not isinstance(attempt, dict) or not attempt.get("submitted_at"):
            raise ValueError("submission_evidence_missing")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise xhs_publish.PublicationJournalError("发布记录不可读或缺少提交时间，禁止将队列标记完成。") from exc
    transport = attempt.get("transport") or item["publication"].get("transport", "auto")
    if transport == "desktop":
        from tools import xhs_desktop as d
        from ga import _browser_action_lock
        account = item["publication"].get("expected_account")
        if not account or attempt.get("account") != account:
            return {"ok": False, "reason": "queue_account_binding_missing_or_mismatched"}
        # Persisted spec permits read-only recovery even after a temporary media
        # file has gone away or the originally valid schedule is now in the past.
        spec = item.get("spec") or attempt.get("spec")
        if spec is None:
            spec = normalize(item["publication"], now=datetime.fromisoformat(attempt["submitted_at"]))
        try:
            with _browser_action_lock, d._lock:
                d.select_site(True)
                if d.editor()["has_editor"]:
                    raise RuntimeError("desktop_unsaved_editor_present")
                independent = d.verify_publication(spec, account, attempt["submitted_at"],
                                                   expected_id=xhs_manage._id(note_id))
        except Exception as exc:
            result = {"ok": False, "status": "needs_review", "reason": str(exc),
                      "item_id": item_id, "state": item["state"], "submitted": False}
            atomic_json(directory / "queue_reconciliation.json", result)
            return result
        attempt.update(initial_reason=attempt.get("initial_reason") or attempt.get("reason"),
                       verified=True, state="submitted", reconciled=True, note_id=note_id,
                       independent_verification=independent)
        atomic_json(journal_path, journal)
        result = {"ok": True, "verified": True, "status": "ok", "note_id": note_id,
                  "independent_verification": independent, "submitted": False, "reconciled": True,
                  "media_pixels_verified": False}
        atomic_json(directory / "queue_reconciliation.json", result)
        saved = _update(item_id, task, state="done", result=result)
        task.append_trace("queue_reconciled", {"item_id": item_id, "note_id": note_id})
        return {"ok": True, "status": "ok", "item": saved, "submitted": False}
    opened = xhs_manage.open_area("notes")
    if not opened.get("ok"):
        return opened
    card = xhs_manage._note_card(xhs_manage._id(note_id), opened["tab_id"])
    if not isinstance(card, dict) or not card.get("ok"):
        return {"ok": False, "reason": "published_note_not_found"}
    if item["publication"]["title"] not in card.get("text", "").splitlines():
        return {"ok": False, "reason": "note_title_not_matched", "card": card}
    # Finding a title alone cannot prove assets/body/schedule match.
    return {"ok": True, "status": "needs_review", "item_id": item_id, "candidate_note_id": note_id,
            "card": card, "state": item["state"], "evidence_url": opened["evidence_url"],
            "message": "已找到标题相符作品；仍需核对媒体、正文、可见性和定时，队列保留原状态。"}
