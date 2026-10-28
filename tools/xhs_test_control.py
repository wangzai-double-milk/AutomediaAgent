"""Persistent, shared submission budgets for the user's new-account tests."""

import contextlib
import fcntl
import json
import math
import re
import threading
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import urlsplit

STATE_PATH = Path(__file__).resolve().parent.parent / "tasks" / "test_control.json"
MIN_INTERVAL = 30 * 60
DAILY_LIMIT = 4
PUBLISH_LIMIT = 1
_lock = threading.RLock()


class TestDeferred(RuntimeError):
    def __init__(self, result):
        self.result = result
        super().__init__(result["reason"])


def _now():
    return datetime.now(timezone.utc)


def _initial():
    return {"version": 1, "enabled": True, "background_paused": True,
            "binding": None, "reservations": []}


@contextlib.contextmanager
def _store():
    from tools.xhs_publish import atomic_json
    with _lock:
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(STATE_PATH.with_suffix(".lock"), "a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                state = json.loads(STATE_PATH.read_text()) if STATE_PATH.exists() else _initial()
                if (not isinstance(state, dict) or state.get("version") != 1
                        or type(state.get("enabled")) is not bool
                        or type(state.get("background_paused")) is not bool
                        or not isinstance(state.get("reservations"), list)
                        or not (state.get("binding") is None or isinstance(state["binding"], dict))):
                    raise ValueError("invalid_test_control_state")
                binding = state["binding"]
                if binding is not None and (not re.fullmatch(r"[a-f0-9]{24}", binding.get("owner_id") or "")
                                            or not isinstance(binding.get("nickname"), str)
                                            or not binding["nickname"]):
                    raise ValueError("invalid_test_account_binding")
                for entry in state["reservations"]:
                    if (not isinstance(entry, dict) or entry.get("kind") not in {"write", "draft", "publish"}
                            or not re.fullmatch(r"[a-f0-9]{24}", entry.get("owner_id") or "")
                            or not entry.get("operation") or not entry.get("id")
                            or datetime.fromisoformat(entry["at"]).tzinfo is None):
                        raise ValueError("invalid_test_reservation")
                yield state
                atomic_json(STATE_PATH, state)
            except (ValueError, OSError, KeyError, TypeError) as exc:
                raise TestDeferred({"ok": False, "status": "deferred", "submitted": False,
                                    "reason": "测试控制记录不可读，写操作已暂停。", "retry_after": None}) from exc
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)


def _availability(state, kind, owner=None, account=None):
    base = {"ok": True, "enabled": state["enabled"], "submitted": False,
            "status": "ready", "retry_after": None}
    if not state["enabled"]:
        return base
    bound = state["binding"]
    reason = None
    if not bound:
        reason = "新测试账号尚未绑定；请先登录并读取自己的健康主页。"
    elif owner and owner != bound["owner_id"]:
        reason = "任务账号ID与新测试账号不一致，原任务保持原绑定。"
    elif account and account != bound["nickname"]:
        reason = "任务昵称与绑定账号不一致，请重新读取身份。"
    if reason:
        return {**base, "ok": False, "status": "deferred", "reason": reason}
    from tools.xhs_account import assert_writable
    from tools.xhs_account import AccountRestricted
    try:
        assert_writable(bound["owner_id"])
    except AccountRestricted as exc:
        return {**base, "ok": False, "status": "restricted", "account_access": exc.state,
                "reason": str(exc)}
    now = _now()
    entries = state["reservations"]
    times = [(r, datetime.fromisoformat(r["at"])) for r in entries]
    if any(t > now for _, t in times):
        return {**base, "ok": False, "status": "deferred",
                "reason": "系统时间早于已保存的提交时间，请核对时间后再继续。"}
    delays = []
    # Global interval also survives account switches, tasks and process restarts.
    if times:
        delays.append(max(t for _, t in times) + timedelta(seconds=MIN_INTERVAL))
    daily = sorted(t for r, t in times if r["owner_id"] == bound["owner_id"]
                   and t > now - timedelta(hours=24))
    publications = sorted(t for r, t in times if r["owner_id"] == bound["owner_id"]
                          and r["kind"] == "publish" and t > now - timedelta(hours=24))
    if len(daily) >= DAILY_LIMIT:
        delays.append(daily[-DAILY_LIMIT] + timedelta(hours=24))
    if kind == "publish" and len(publications) >= PUBLISH_LIMIT:
        delays.append(publications[-PUBLISH_LIMIT] + timedelta(hours=24))
    after = max(delays, default=now)
    if after > now:
        return {**base, "ok": False, "status": "deferred",
                "reason": "已到测试提交间隔或滚动24小时额度，当前操作未提交。",
                "retry_after": after.isoformat(), "retry_in_seconds": math.ceil((after - now).total_seconds())}
    return {**base, "owner_id": bound["owner_id"], "nickname": bound["nickname"]}


def preflight(kind="write", owner=None, account=None, transport="desktop"):
    with _store() as state:
        result = _availability(state, kind, owner, account)
        if state["enabled"] and transport == "cdp":
            result = {"ok": False, "status": "deferred", "submitted": False,
                      "reason": "测试期间只使用已验收的desktop通路。", "retry_after": None}
        if not result["ok"]:
            raise TestDeferred(result)
        return result


def verify_browser(owner=None, account=None):
    """Check the main site's stable ID as well as the creator nickname.

    Existing tabs are selected without navigating or discarding open forms.
    """
    from tools import xhs_desktop as d
    from tools.xhs_messages import owner_id
    with _store() as state:
        if not state["enabled"]:
            return
        binding = dict(state["binding"] or {})
    if not binding:
        raise TestDeferred({"ok": False, "status": "deferred", "submitted": False,
                            "reason": "新测试账号尚未绑定。", "retry_after": None})
    with d._lock:
        original = d.snapshot()
        creator = urlsplit(original.get("page_url") or "").hostname == "creator.xiaohongshu.com"
        try:
            current = d.select_site()
            observed = owner_id(current)
            if observed != binding["owner_id"] or (owner and owner != observed):
                raise TestDeferred({"ok": False, "status": "deferred", "submitted": False,
                                    "reason": "浏览器登录账号ID已变化，已停止写操作。", "retry_after": None})
            from tools.xhs_account import assert_writable
            assert_writable(observed)
        finally:
            if creator or account:
                current = d.select_site(True)
        try:
            if (creator or account) and (d.identity(current) != binding["nickname"]
                                         or (account and account != d.identity(current))):
                raise TestDeferred({"ok": False, "status": "deferred", "submitted": False,
                                    "reason": "创作中心账号标签与绑定账号不一致。", "retry_after": None})
        finally:
            if account and not creator:
                d.select_site()


def reserve(kind, operation, owner=None, account=None):
    """Claim before any submission journal; failures/unknown results retain it."""
    preflight(kind, owner, account)
    verify_browser(owner, account)
    with _store() as state:
        result = _availability(state, kind, owner, account)
        if not result["ok"]:
            raise TestDeferred(result)
        if not state["enabled"]:
            return None
        # Reusing a claimed operation after a crash never authorizes a replay.
        if any(r["operation"] == operation for r in state["reservations"]):
            raise TestDeferred({"ok": False, "status": "deferred", "submitted": False,
                                "reason": "此操作已经领取提交额度，请读取原结果；禁止重放。",
                                "retry_after": None})
        receipt = {"id": uuid.uuid4().hex, "kind": kind, "operation": operation,
                   "owner_id": state["binding"]["owner_id"], "at": _now().isoformat()}
        state["reservations"].append(receipt)
        return receipt


def status():
    try:
        with _store() as state:
            available = _availability(state, "write")
            publication = _availability(state, "publish")
            return {**state, "write_ready": available, "publish_ready": publication,
                    "limits": {"min_interval_seconds": MIN_INTERVAL, "rolling_24h": DAILY_LIMIT,
                               "publish_24h": PUBLISH_LIMIT},
                    "message": "项目内部测试上限，不是平台公布的安全阈值。"}
    except TestDeferred as exc:
        return {**exc.result, "enabled": True, "background_paused": True}
    except Exception as exc:
        from tools.xhs_account import AccountRestricted
        if isinstance(exc, AccountRestricted):
            return {"enabled": True, "background_paused": True, "status": "restricted",
                    "account_access": exc.state}
        raise


def queue_ready(item=None, background=False):
    with _store() as state:
        if background and state["background_paused"]:
            return {"ok": False, "status": "deferred", "submitted": False,
                    "reason": "测试期间后台发布已暂停。", "retry_after": None}
        payload = (item or {}).get("publication", {})
        if state["enabled"] and not (item or {}).get("owner_id"):
            return {"ok": False, "status": "deferred", "submitted": False,
                    "reason": "旧队列没有稳定账号ID，不会自动改绑或领取。", "retry_after": None}
        return _availability(state, "publish", (item or {}).get("owner_id"), payload.get("expected_account"))


def generic_guard(tool_name, args):
    """Raw JS/keys cannot substitute for the budgeted XHS operation tools."""
    with _store() as state:
        if not state["enabled"]:
            return None
    if tool_name == "web_desktop" and args.get("action", "snapshot") in {
            "snapshot", "capture", "status", "tabs"}:
        return None
    if tool_name not in {"web_execute_js", "web_visual_click", "web_visual_fill", "web_desktop"}:
        return None
    return {"ok": False, "status": "deferred", "submitted": False, "retry_after": None,
            "reason": "换号测试期间，浏览器写操作请使用有账号绑定与频率控制的小红书业务工具。"}


def operate(args):
    action = args.get("action", "status")
    if action == "status":
        return {"ok": True, "test_control": status()}
    if action == "pause":
        with _store() as state:
            state["background_paused"] = True
        return {"ok": True, "test_control": status()}
    if action != "bind":
        raise ValueError("测试账号action必须为status/bind/pause")
    owner, nickname = args.get("owner_id"), args.get("nickname")
    if not re.fullmatch(r"[a-f0-9]{24}", owner or "") or not nickname:
        raise ValueError("bind需主页真实owner_id与nickname")
    from tools import xhs_desktop as d, xhs_account as access
    from tools.xhs_read_desktop import Actions
    from tools.xhs_messages import owner_id
    with d._lock:
        current = d.select_site()
        if owner_id(current) != owner:
            raise ValueError("新测试账号ID与实际登录身份不符")
        profile = Actions().user_profile(owner)
        if profile["basic_info"]["nickname"] != nickname:
            raise ValueError("新测试账号昵称未在自己的主页准确读取")
        observed = access.status().get("observations", {}).get(owner, {})
        if observed.get("status") != "available" or observed.get("writes_blocked"):
            raise access.AccountRestricted(observed)
        current = d.select_site(True)
        if d.identity(current) != nickname:
            raise ValueError("主站与创作中心账号不一致")
        if d.editor(current)["has_editor"] or any(
                n.get("placeholder") == "请输入合集名称" for n in current.get("web", [])):
            raise ValueError("创作中心存在未完成表单；请先保留并正常离开，再绑定新账号")
        with _store() as state:
            access.activate(owner)
            state["binding"] = {"owner_id": owner, "nickname": nickname,
                                "profile_url": profile["evidence_url"], "bound_at": _now().isoformat()}
            state["background_paused"] = True
    return {"ok": True, "test_control": status()}
