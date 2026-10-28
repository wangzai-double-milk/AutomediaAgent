"""Persist actual account restrictions; never retry a write via another transport."""

import json
import re
import threading
import contextlib
import fcntl
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

STATE_PATH = Path(__file__).resolve().parent.parent / "tasks" / "account_access.json"
_lock = threading.RLock()
RISK_TEXT = re.compile(r"该账号疑似存在风险|账号(?:已被|被)?封禁|账号异常|账号受限|账号存在风险")


class AccountRestricted(RuntimeError):
    def __init__(self, state):
        self.state = state
        super().__init__("小红书账号访问受限，已停止账号写操作；请通过平台处理限制后重新读取自己的主页。")


@contextlib.contextmanager
def _exclusive():
    with _lock:
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(STATE_PATH.with_suffix(".lock"), "a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)


def status(user_id=None):
    with _lock:
        if not STATE_PATH.exists():
            return {"status": "unknown", "writes_blocked": bool(user_id), "user_id": user_id}
        try:
            state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
            if not isinstance(state, dict) or type(state.get("writes_blocked")) is not bool:
                raise ValueError("invalid_account_access_state")
            for collection in ("accounts", "observations"):
                records = state.get(collection, {})
                if not isinstance(records, dict) or any(
                    not isinstance(v, dict) or type(v.get("writes_blocked")) is not bool
                    or v.get("user_id") != k for k, v in records.items()):
                    raise ValueError("invalid_account_history")
            if user_id:
                return state.get("accounts", {}).get(user_id) or (
                    state if state.get("user_id") == user_id else
                    {"status": "unknown", "writes_blocked": True, "user_id": user_id})
            return state
        except (OSError, ValueError):
            return {"status": "needs_review", "writes_blocked": True,
                    "reason": "账号限制记录不可读，不能据此恢复写操作。"}


def assert_writable(user_id=None):
    state = status(user_id)
    if state["writes_blocked"]:
        raise AccountRestricted(state)


def observe(current):
    """Only the logged-in user's actual main-site profile can clear a restriction."""
    url = urlsplit(current.get("page_url") or "")
    if url.hostname != "www.xiaohongshu.com":
        return status()
    nodes = current.get("web", [])
    def text(n):
        return str(n.get("value") or n.get("title") or "")
    own = [urlsplit(n.get("url") or "").path for n in nodes
           if n.get("role") == "AXLink" and text(n) == "我"]
    if len(own) != 1 or url.path.rstrip("/") != own[0].rstrip("/"):
        return status()
    match = re.fullmatch(r"/user/profile/([a-f0-9]{24})", own[0])
    if not match:
        return status()
    labels = [text(n) for n in nodes if n.get("role") in {"AXStaticText", "AXHeading"}
              and (n.get("bounds") or {}).get("height", 0) >= 2]
    risk = next((line for line in labels if RISK_TEXT.search(line)), None)
    with _exclusive():
        before = status()
        if before.get("status") == "needs_review":
            return before
        accounts = dict(before.get("accounts", {}))
        observations = dict(before.get("observations", {}))
        if before.get("user_id"):
            accounts.setdefault(before["user_id"], {
                k: v for k, v in before.items() if k not in {"accounts", "observations"}})
        owner = match[1]
        prior = accounts.get(owner, {})
        healthy = "笔记" in labels and any(line.startswith("小红书号：") for line in labels)
        if risk:
            state = {"status": "restricted", "writes_blocked": True,
                     "user_id": owner, "reason": risk, "evidence_url": current["page_url"]}
        elif healthy:
            state = {"status": "available", "writes_blocked": False, "user_id": owner,
                     "reason": "同一账号主页已恢复笔记入口且未显示风险提示。",
                     "evidence_url": current["page_url"]}
        else:
            return before
        if all(prior.get(key) == value for key, value in state.items()) and owner in observations:
            return before
        from tools.xhs_publish import atomic_json
        state["observed_at"] = datetime.now(timezone.utc).isoformat()
        observations[owner] = state
        # Only a previously known owner can recover automatically. A different
        # healthy profile is evidence for an explicit bind, not an active switch.
        if risk or owner in accounts:
            accounts[owner] = state
        active = before.get("user_id")
        top = state if active == owner or (risk and not active) else {
            k: v for k, v in before.items() if k not in {"accounts", "observations"}}
        saved = {**top, "accounts": accounts, "observations": observations}
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        atomic_json(STATE_PATH, saved)
        return saved


def activate(user_id):
    """Explicit switch backed by a freshly observed, healthy own profile."""
    from tools.xhs_publish import atomic_json
    with _exclusive():
        before = status()
        state = before.get("observations", {}).get(user_id)
        if not state or state.get("status") != "available" or state.get("writes_blocked"):
            raise AccountRestricted(state or before)
        accounts = dict(before.get("accounts", {}))
        accounts[user_id] = state
        saved = {**state, "accounts": accounts, "observations": before["observations"]}
        atomic_json(STATE_PATH, saved)
        return saved
