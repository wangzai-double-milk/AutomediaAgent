"""One-shot desktop interactions with owner binding and independent readback."""

import hashlib
# Platform implementation; legacy tools imports share this module.
import json
import re
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from tools import xhs_desktop as d, xhs_read_desktop as read
from tools.xhs_messages import owner_id
from tools.xhs_publish import atomic_json, TIMEZONE, PublicationJournalError
from tools import xhs_test_control as test_control


def engagement(current, action):
    scoped, _, body = read.note_scope(current)
    parents = {n["ref"]: n for n in scoped["web"]}
    row = parents[parents[body["parent"]]["parent"]]
    children = [n for n in scoped["web"] if n.get("parent") == row["ref"] and n["role"] == "AXGroup"]
    pairs = []
    for group in children:
        nodes = d.descendants(scoped, group["ref"])
        images = [n for n in nodes if n["role"] == "AXImage" and d.visible(n)]
        counts = [n for n in nodes if n["role"] == "AXStaticText" and re.fullmatch(r"[\d.]+万?", d.text(n))]
        if len(images) == len(counts) == 1:
            pairs.append((group, images[0], counts[0]))
    if len(pairs) != 3:
        raise RuntimeError("desktop_engagement_row_not_identified")
    pairs.sort(key=lambda pair: pair[0]["bounds"]["x"])
    group, icon, count = pairs[0 if action == "like" else 1]
    return group, icon, d.text(count)


def icon_state(current, action, directory, label):
    """Read colored selected icons versus visible monochrome outlines.

    Geometry is tied to the real detail composer and its three icon/count pairs.
    Actual website heart/star order was visually verified; unknown pixels stop.
    """
    from PIL import Image
    group, icon, count = engagement(current, action)
    path = directory / (label + "_" + action + "_" + uuid4().hex[:8] + ".png")
    d.act("capture", current, icon, path=str(path))
    with Image.open(path) as image:
        pixels = list(image.convert("RGB").getdata())
    colored = sum(r > 165 and r - b > 65 and (
        r - g > 55 if action == "like" else g > 95 and g - b > 45) for r, g, b in pixels) / len(pixels)
    dark = sum(max(r, g, b) < 140 for r, g, b in pixels) / len(pixels)
    if colored > .1:
        selected = True
    elif colored < .005 and dark > .035:
        selected = False
    else:
        raise RuntimeError("desktop_engagement_icon_state_unknown")
    return {"selected": selected, "colored_fraction": colored, "outline_fraction": dark,
            "count_text": count, "evidence_path": str(path)}, group


def reopen(url):
    d.navigate("https://www.xiaohongshu.com/explore")
    d.navigate(url)
    note_id = read.stable_id(url, "note")
    def ready(s):
        if read.stable_id(s.get("page_url"), "note") != note_id:
            return False
        try:
            return bool(read.read_detail(s, note_id)["note"]["desc"])
        except RuntimeError:
            return False
    return d.wait(ready, 12)


def journal(directory):
    path = directory / "interaction_attempts.json"
    try:
        states = json.loads(path.read_text()) if path.exists() else {}
        if not isinstance(states, dict) or any(not isinstance(v, dict) or
                                              type(v.get("verified")) is not bool for v in states.values()):
            raise ValueError("invalid_interaction_journal")
    except (OSError, ValueError) as exc:
        raise PublicationJournalError("互动记录不可读；禁止自动重复提交。") from exc
    return path, states


def reply_target(current, args):
    scoped, _, _ = read.note_scope(current)
    matches = [c for c in read.comments(scoped, include_refs=True)
               if c["author_id"] == args.get("user_id") and c["content"] == args.get("target_content")]
    if len(matches) != 1:
        raise RuntimeError("desktop_reply_target_author_and_fulltext_not_unique")
    return scoped, matches[0]


def reply_preview(current, target):
    scoped, _, composer = read.note_scope(current)
    nodes = {n["ref"]: n for n in scoped["web"]}
    ancestor = composer.get("parent")
    while ancestor in nodes and len(d.descendants(scoped, ancestor)) < len(scoped["web"]):
        labels = {d.text(n) for n in d.descendants(scoped, ancestor) if n["role"] == "AXStaticText"}
        if "回复 " + target["author"] in labels and target["content"] in labels:
            return True
        ancestor = nodes[ancestor].get("parent")
    return False


def verified_reply(current, args, owner):
    try:
        scoped, target = reply_target(current, args)
    except RuntimeError:
        return False
    nodes = {n["ref"]: n for n in scoped["web"]}
    original = nodes.get(target.get("_thread_ref"))
    if not original or not original.get("parent"):
        return False
    # Actual top-level thread: original wrapper, then an indented reply wrapper
    # under their common parent. Never accept a matching comment elsewhere in
    # the full comments list as a reply to this target.
    siblings = [n for n in scoped["web"] if n.get("parent") == original["parent"] and n["role"] == "AXGroup"]
    box = original.get("bounds") or {}
    replies = [n for n in siblings if n["ref"] != original["ref"] and n.get("bounds")
               and n["bounds"]["x"] >= box.get("x", 99999) + 20
               and n["bounds"]["y"] >= box.get("y", 0) + box.get("height", 0) - 2]
    for wrapper in replies:
        thread = d.descendants(scoped, wrapper["ref"])
        if any(c["author_id"] == owner and c["content"] == args["content"]
               for c in read.comments({**scoped, "web": thread}, include_refs=True)):
            return True
    return False


def operate(args, task):
    action = args.get("action")
    if action not in {"like", "favorite", "follow", "comment", "prepare_comment", "clear_comment", "reply", "prepare_reply"}:
        raise ValueError("unknown_desktop_interaction")
    if action in {"like", "favorite", "follow", "comment", "reply"}:
        from tools.xhs_account import assert_writable
        assert_writable()
    expected = args.get("owner_id")
    if not isinstance(expected, str) or not re.fullmatch(r"[a-f0-9]{24}", expected):
        raise ValueError("desktop互动需真实owner_id绑定当前账号")
    if action in {"reply", "prepare_reply"} and (not re.fullmatch(r"[a-f0-9]{24}", args.get("user_id") or "")
                                               or not args.get("target_content")):
        raise ValueError("desktop reply requires observed user_id and exact target_content; comment IDs remain null")
    directory = Path(task.ensure_task(hint="desktop_interaction")).resolve()
    path, states = journal(directory)
    op = hashlib.sha256(json.dumps({k: v for k, v in args.items() if not k.startswith("_")},
                                  sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    if action not in {"prepare_comment", "prepare_reply", "clear_comment"} and op in states:
        return {"ok": states[op]["verified"], "status": "ok" if states[op]["verified"] else "unverified",
                "skipped": True, "attempt_id": op, "previous": states[op]}
    if action != "clear_comment":
        test_control.preflight(owner=expected)
        test_control.verify_browser(owner=expected)
    with d._lock:
        current = read.open_detail(args.get("feed_id", ""), args.get("xsec_token", ""), args.get("xsec_source"))
        if owner_id(current) != expected:
            raise RuntimeError("desktop_interaction_owner_mismatch")
        scoped, _, composer = read.note_scope(current)
        url = current["page_url"]
        if action == "clear_comment":
            if composer.get("value") != args.get("content"):
                raise RuntimeError("desktop_clear_comment_text_changed")
            d.act("fill", scoped, composer, text="")
            d.wait(lambda s: read.note_scope(s)[2].get("value") == "", 4)
            return {"ok": True, "status": "cleared", "submitted": False}
        if composer.get("value"):
            raise RuntimeError("desktop_unsent_comment_present")
        if action in {"like", "favorite"}:
            before, control = icon_state(current, action, directory, op + "_before")
            wanted = not bool(args.get("remove", False))
            if before["selected"] == wanted:
                return {"ok": True, "verified": True, "skipped": True, "state": wanted, "evidence": before}
        elif action == "follow":
            controls = [n for n in scoped["web"] if n["role"] == "AXButton" and d.visible(n)
                        and d.text(n) in {"关注", "已关注", "互相关注", "取消关注"}]
            if len(controls) != 1:
                raise RuntimeError("desktop_note_follow_state_unknown")
            control = controls[0]
            note = read.read_detail(current, args["feed_id"])["note"]
            if note["author_id"] != args.get("user_id") or note["author_id"] == expected:
                raise RuntimeError("desktop_follow_author_mismatch")
            before = {"selected": d.text(control) != "关注", "label": d.text(control)}
            wanted = not bool(args.get("remove", False))
            if before["selected"] == wanted:
                return {"ok": True, "verified": True, "skipped": True, "state": wanted, "evidence": before}
        else:
            content = args.get("content")
            if not isinstance(content, str) or not content.strip() or len(content) > 500:
                raise ValueError("comment requires 1–500 characters")
            loaded = read.read_detail(current, args["feed_id"])["comments"]
            if (verified_reply(current, args, expected) if action in {"reply", "prepare_reply"} else
                    any(c["content"] == content and c["author_id"] == expected for c in loaded)):
                if op in states and not states[op]["verified"]:
                    states[op].update(initial_reason=states[op].get("initial_reason") or states[op].get("reason"),
                                      verified=True, state="done", reconciled=True, after=loaded)
                    atomic_json(path, states)
                return {"ok": True, "skipped": True, "verified": True, "reason": "existing_matching_own_comment"}
            before = {"loaded_comment_count": len(loaded)}
            wanted = None
        if action not in {"prepare_comment", "prepare_reply"} and op in states:
            if states[op]["verified"] and action in {"like", "favorite", "follow"}:
                return {"ok": False, "status": "needs_review", "submitted": False,
                        "attempt_id": op, "previous": states[op],
                        "reason": "同一操作已核对完成但当前状态已改变；新的意图需新的operation_id"}
            return {"ok": states[op]["verified"], "status": "ok" if states[op]["verified"] else "unverified",
                    "skipped": True, "attempt_id": op, "previous": states[op]}
        if action in {"comment", "prepare_comment", "reply", "prepare_reply"}:
            target = None
            if action in {"reply", "prepare_reply"}:
                scoped, target = reply_target(current, args)
                if not reply_preview(current, target):
                    target_scope = {**scoped, "web": d.descendants(scoped, target["_ref"])}
                    reply = d.choose(target_scope, lambda n: n["role"] == "AXStaticText" and d.text(n) == "回复")
                    d.act("press", current, reply)
                    current = d.wait(lambda s: reply_preview(s, target), 5)
                    scoped, _, composer = read.note_scope(current)
            # AXValue can change a collapsed composer without its click handler.
            # Enter the actual editing mode before setting text.
            placeholders = [n for n in scoped["web"] if n["role"] == "AXStaticText"
                            and d.text(n) == "说点什么..." and d.visible(n)]
            if len(placeholders) == 1:
                d.act("press", scoped, placeholders[0])
            elif not any(n["role"] == "AXButton" and d.text(n) == "发送"
                         and (n.get("bounds") or {}).get("height", 0) >= 2 for n in scoped["web"]):
                raise RuntimeError("desktop_comment_activation_control_missing")
            current = d.wait(lambda s: any(
                n["role"] == "AXButton" and d.text(n) == "发送"
                and (n.get("bounds") or {}).get("height", 0) >= 2
                for n in read.note_scope(s)[0]["web"]), 5)
            scoped, _, composer = read.note_scope(current)
            if not composer.get("focused"):
                raise RuntimeError("desktop_comment_not_focused_after_activation")
            # A real input event keeps the platform's editing mode and send
            # enabled state in sync; AXValue alone does not run its handlers.
            d.act("type", scoped, composer, text=content, replace=True)
            current = d.wait(lambda s: read.note_scope(s)[2].get("value") == content, 5)
            scoped, _, composer = read.note_scope(current)
            if action in {"prepare_comment", "prepare_reply"}:
                result = {"ok": True, "status": "prepared", "submitted": False, "owner_id": expected,
                          "feed_id": args["feed_id"], "content": content, "evidence_url": url}
                if target:
                    result["reply_to"] = {k: v for k, v in target.items() if not k.startswith("_")}
                atomic_json(directory / "comment_preview.json", result)
                return result
            buttons = [n for n in scoped["web"] if n["role"] == "AXButton" and d.text(n) == "发送"]
            if len(buttons) == 1 and not d.visible(buttons[0]):
                d.act("reveal", current, buttons[0])
                current = d.wait(lambda s: any(n["role"] == "AXButton" and d.text(n) == "发送"
                                             and d.visible(n) for n in read.note_scope(s)[0]["web"]), 4)
                scoped, _, composer = read.note_scope(current)
            if composer.get("value") != content:
                raise RuntimeError("desktop_comment_text_changed_before_send")
            if target and not reply_preview(current, target):
                raise RuntimeError("desktop_reply_preview_changed_before_send")
            control = d.choose(scoped, lambda n: n["role"] == "AXButton" and d.text(n) == "发送")
        else:
            # Capture consumed the previous refs; restore the same semantic control.
            current = d.snapshot()
            if action in {"like", "favorite"}:
                control = engagement(current, action)[0]
            else:
                scoped, _, _ = read.note_scope(current)
                control = d.choose(scoped, lambda n: n["role"] == "AXButton"
                                   and d.text(n) == before["label"])
        if owner_id(current) != expected or current.get("page_url") != url:
            raise RuntimeError("desktop_interaction_target_changed")
        test_control.reserve("write", "interact:" + op, owner=expected)
        states[op] = {"verified": False, "state": "submitting", "action": action,
                      "owner_id": expected, "feed_id": args["feed_id"], "before": before,
                      "wanted": wanted, "content": args.get("content"),
                      "reply_to": {k: v for k, v in target.items() if not k.startswith("_")} if
                                  action == "reply" else None,
                      "submitted_at": datetime.now(TIMEZONE).isoformat()}
        atomic_json(path, states)
        task.append_trace("interaction_submit", states[op])
        # One actual click. Never click a second time after an uncertain response.
        from tools.xhs_account import assert_writable
        assert_writable()
        d.act("press", current, control)
        try:
            if action in {"like", "favorite"}:
                # AXImage changes during the selected icon animation. Wait for
                # the real row, then verify the persisted state on a fresh page.
                d.wait(lambda s: read.stable_id(s.get("page_url"), "note") == args["feed_id"]
                       and any(n["role"] == "AXTextArea" for n in s["web"]), 6)
            elif action == "follow":
                label = "关注" if not wanted else "已关注"
                d.wait(lambda s: any(n["role"] == "AXButton" and d.text(n) == label
                                    for n in read.note_scope(s)[0]["web"]), 6)
            else:
                d.wait(lambda s: read.note_scope(s)[2].get("value") == "", 8)
            current = reopen(url)
            if owner_id(current) != expected:
                raise RuntimeError("desktop_interaction_owner_changed")
            if action in {"like", "favorite"}:
                after, _ = icon_state(current, action, directory, op + "_independent")
                verified = after["selected"] == wanted
            elif action == "follow":
                after = {"selected": read.read_detail(current, args["feed_id"])["note"]["following"]}
                verified = after["selected"] == wanted
            else:
                current = d.wait(lambda s: verified_reply(s, args, expected) if action == "reply" else any(
                    c["content"] == content and c["author_id"] == expected
                    for c in read.read_detail(s, args["feed_id"])["comments"]), 8)
                after = read.read_detail(current, args["feed_id"])["comments"]
                verified = (verified_reply(current, args, expected) if action == "reply" else
                            any(c["content"] == content and c["author_id"] == expected for c in after))
            states[op].update(verified=verified, after=after)
        except Exception as exc:
            states[op]["reason"] = str(exc)
        states[op]["state"] = "done" if states[op]["verified"] else "uncertain"
        atomic_json(path, states)
        task.append_trace("interaction_result", states[op])
        return {"ok": states[op]["verified"], "status": "ok" if states[op]["verified"] else "unverified",
                "verified": states[op]["verified"], "attempt_id": op, "result": states[op]}
