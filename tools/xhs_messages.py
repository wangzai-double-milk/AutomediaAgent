"""Visible web chat with owner/recipient binding and persistent single-send guards."""

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

from tools import xhs_desktop as desktop
from tools.xhs_publish import TIMEZONE, atomic_json, PublicationJournalError
from tools import xhs_test_control as control


def owner_id(current):
    links = [n for n in current["web"] if n["role"] == "AXLink"
             and desktop.text(n) == "我" and desktop.visible(n)]
    ids = [re.fullmatch(r"/user/profile/([a-f0-9]{24})", urlsplit(n.get("url") or "").path)
           for n in links]
    if len(ids) != 1 or not ids[0]:
        raise RuntimeError("desktop_chat_owner_id_not_observed")
    return ids[0][1]


def composer(current):
    return desktop.choose(current, lambda n: n["role"] == "AXTextArea")


def conversation(current):
    match = re.fullmatch(r"/chat/([a-f0-9]{24})", urlsplit(current.get("page_url") or "").path)
    if not match:
        raise RuntimeError("desktop_chat_recipient_id_not_observed")
    body = composer(current)
    headers = [n for n in current["web"] if n["role"] == "AXStaticText" and desktop.visible(n)
               and desktop.text(n) and n["bounds"]["x"] >= body["bounds"]["x"]
               and n["bounds"]["y"] < body["bounds"]["y"] - 100]
    # Header sits above the actual chat history, at the top of the content pane.
    if not headers:
        raise RuntimeError("desktop_chat_header_not_observed")
    top = min(n["bounds"]["y"] for n in headers)
    headers = [n for n in headers if n["bounds"]["y"] == top]
    if len(headers) != 1:
        raise RuntimeError("desktop_chat_header_ambiguous")
    return {"recipient_id": match[1], "recipient_name": desktop.text(headers[0]),
            "owner_id": owner_id(current), "header": headers[0], "composer": body}


def bubbles(current, state):
    """Only history text within the conversation pane, excluding composer/sidebar."""
    header, body = state["header"], state["composer"]
    return [n for n in current["web"] if n["role"] == "AXStaticText" and desktop.visible(n)
            and n["bounds"]["x"] >= body["bounds"]["x"]
            and header["bounds"]["y"] + 40 < n["bounds"]["y"] < body["bounds"]["y"] - 5
            and desktop.text(n)]


def open_conversation(recipient_id, recipient_name, expected_owner):
    desktop.read_messages()
    current = desktop.snapshot()
    if owner_id(current) != expected_owner:
        raise RuntimeError("desktop_chat_owner_mismatch")
    areas = [n for n in current["web"] if n["role"] == "AXTextArea" and desktop.visible(n)]
    if areas and areas[0].get("value") not in {"", "发消息..."}:
        raise RuntimeError("desktop_chat_unsent_text_present")
    if urlsplit(current.get("page_url") or "").path != "/chat/" + recipient_id:
        desktop.navigate("https://www.xiaohongshu.com/chat/" + recipient_id)
    current = desktop.wait(lambda s: urlsplit(s.get("page_url") or "").path == "/chat/" + recipient_id
                           and any(n["role"] == "AXTextArea" for n in s["web"]), 10)
    state = conversation(current)
    if state["recipient_id"] != recipient_id or state["recipient_name"] != recipient_name or state["owner_id"] != expected_owner:
        raise RuntimeError("desktop_chat_recipient_or_owner_mismatch")
    return current, state


def operate(args, task):
    action = args.get("action", "list")
    if action not in {"list", "read", "prepare", "send", "clear"}:
        raise ValueError("action 必须是list/read/prepare/send/clear")
    if action == "send":
        from tools.xhs_account import assert_writable
        assert_writable()
    if args.get("transport", "desktop") not in {"desktop", "auto"}:
        raise RuntimeError("CDP_chat_not_validated; use_desktop")
    with desktop._lock:
        if action == "list":
            result = desktop.read_messages()
            current = desktop.snapshot()
            return {**result, "owner_id": owner_id(current), "count": None,
                    "message": "当前页可见会话，未提取稳定分页ID。"}
        recipient_id, recipient_name, expected_owner = (
            args.get("recipient_id"), args.get("recipient_name"), args.get("owner_id"))
        if not all(isinstance(v, str) and re.fullmatch(r"[a-f0-9]{24}", v)
                   for v in (recipient_id, expected_owner)) or not recipient_name:
            raise ValueError("需真实owner_id、recipient_id和recipient_name")
        if action == "clear":
            current = desktop.snapshot()
            state = conversation(current)
            if (state["recipient_id"] != recipient_id or state["recipient_name"] != recipient_name
                    or state["owner_id"] != expected_owner or state["composer"].get("value") != args.get("text")):
                raise RuntimeError("desktop_chat_clear_target_or_text_changed")
            desktop.act("fill", current, state["composer"], text="")
            desktop.wait(lambda s: composer(s).get("value") in {"", "发消息..."}, 4)
            return {"ok": True, "status": "cleared", "submitted": False}
        text = args.get("text")
        if action in {"prepare", "send"} and (not isinstance(text, str) or not text.strip() or len(text) > 500
                                              or "\n" in text or "\r" in text):
            raise ValueError("消息需1–500字单行文本；多行发送未验收")
        directory = task.ensure_task(hint="xhs_messages")
        path = Path(directory) / "message_attempts.json"
        try:
            states = json.loads(path.read_text()) if path.exists() else {}
            if not isinstance(states, dict) or any(not isinstance(v, dict) or
                type(v.get("verified")) is not bool for v in states.values()):
                raise ValueError("invalid_message_journal")
        except (OSError, ValueError) as exc:
            raise PublicationJournalError("私信记录不可读；禁止自动重复发送。") from exc
        op = hashlib.sha256(json.dumps([expected_owner, recipient_id, text], ensure_ascii=False).encode()).hexdigest()
        if action == "send" and op in states:
            return {"ok": states[op]["verified"], "status": "ok" if states[op]["verified"] else "unverified",
                    "skipped": True, "attempt_id": op, "previous": states[op]}
        if action in {"prepare", "send"}:
            control.preflight(owner=expected_owner)
            control.verify_browser(owner=expected_owner)
        current, state = open_conversation(recipient_id, recipient_name, expected_owner)
        if action == "read":
            return {"ok": True, "recipient_id": recipient_id, "recipient_name": recipient_name,
                    "owner_id": expected_owner, "messages": [desktop.text(n) for n in bubbles(current, state)],
                    "structured": False, "message_ids": None, "evidence_url": current["page_url"]}
        before = [desktop.text(n) for n in bubbles(current, state)]
        if text in before:
            return {"ok": False, "status": "needs_review", "submitted": False,
                    "reason": "当前历史中已有完全相同消息；不自动重复发送"}
        desktop.act("fill", current, state["composer"], text=text)
        current = desktop.wait(lambda s: composer(s).get("value") == text, 4)
        state = conversation(current)
        if action == "prepare":
            evidence = {"ok": True, "status": "prepared", "submitted": False,
                        "recipient_id": recipient_id, "recipient_name": recipient_name,
                        "owner_id": expected_owner, "text": text, "evidence_url": current["page_url"]}
            atomic_json(Path(directory) / "message_preview.json", evidence)
            return evidence
        if (state["recipient_id"] != recipient_id or state["recipient_name"] != recipient_name
                or state["owner_id"] != expected_owner or not state["composer"].get("focused")):
            raise RuntimeError("desktop_chat_send_identity_or_focus_changed")
        control.reserve("write", "message:" + op, owner=expected_owner)
        states[op] = {"verified": False, "state": "submitting", "recipient_id": recipient_id,
                      "recipient_name": recipient_name, "owner_id": expected_owner, "text": text,
                      "submitted_at": datetime.now(TIMEZONE).isoformat()}
        atomic_json(path, states)
        task.append_trace("message_submit", states[op])
        # Exactly one Enter in the ID-bound, focused chat composer.
        assert_writable()
        desktop.mac_browser.operate({"action": "key", "key": "enter"})
        try:
            current = desktop.wait(lambda s: composer(s).get("value") in {"", "发消息..."}
                                   and text in [desktop.text(n) for n in bubbles(s, conversation(s))], 10)
            if any(re.search(r"发送失败|消息未送达|无法发送", desktop.text(n)) for n in current["web"]):
                raise RuntimeError("desktop_chat_send_failed")
            # Reopen the server-backed conversation; composer/sidebar text is not evidence.
            desktop.navigate("https://www.xiaohongshu.com/chat?channel_type=message_pc_page")
            current, state = open_conversation(recipient_id, recipient_name, expected_owner)
            current = desktop.wait(lambda s: text in [desktop.text(n) for n in bubbles(s, conversation(s))], 10)
            verified = True
            states[op]["evidence_url"] = current["page_url"]
        except Exception as exc:
            verified = False
            states[op]["reason"] = str(exc)
        states[op].update(verified=verified, state="sent" if verified else "uncertain")
        atomic_json(path, states)
        task.append_trace("message_result", states[op])
        return {"ok": verified, "status": "ok" if verified else "unverified", "attempt_id": op,
                "result": states[op], "delivered": None,
                "message": "已回读重新打开后的消息历史；不代表对方已收到或已读。" if verified
                else "发送结果不确定，禁止自动重复发送。"}
