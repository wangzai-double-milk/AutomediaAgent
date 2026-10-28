"""Browser-local drafts and creator collection/RSS forms, bound to visible data."""

import hashlib
import json
import re
from pathlib import Path

from tools import xhs_desktop as d
from tools.xhs_publish import atomic_json, PublicationJournalError
from tools import xhs_test_control as test_control

DRAFT_KINDS = {"video": "视频笔记", "image": "图文笔记",
               "longform": "长文笔记", "podcast": "播客笔记"}


def require_transport(args):
    if args.get("transport", "desktop") not in {"auto", "desktop"}:
        raise ValueError("draft/collection/RSS tools support macOS desktop only; CDP fallback is unavailable")


def require_account(account, current=None):
    if not account or d.identity(current) != account:
        raise RuntimeError("desktop_library_account_mismatch")


def attempts(directory):
    path = directory / "library_attempts.json"
    try:
        states = json.loads(path.read_text()) if path.exists() else {}
        if not isinstance(states, dict) or any(not isinstance(v, dict) or
                                              type(v.get("verified")) is not bool for v in states.values()):
            raise ValueError("invalid_library_journal")
    except (OSError, ValueError) as exc:
        raise PublicationJournalError("草稿/合集操作记录不可读；禁止重复提交。") from exc
    return path, states


def drawer(current=None):
    current = current or d.snapshot()
    header = d.choose(current, lambda n: n["role"] == "AXHeading" and d.text(n) == "草稿箱")
    return {**current, "web": d.descendants(current, header["parent"])}


def draft_rows(current, kind):
    """Segment adjacent title/date/edit/delete sibling groups in the real list.

    This site's list has one AXGroup for all entries. Ancestor-based card logic
    would accidentally bind several drafts to the same delete control.
    """
    scoped = drawer(current)
    nodes = {n["ref"]: n for n in scoped["web"]}
    dates = [n for n in scoped["web"] if n["role"] == "AXStaticText"
             and re.fullmatch(r"保存于\d{4}-\d\d-\d\d \d\d:\d\d:\d\d", d.text(n))]
    records = []
    for date in dates:
        date_group = nodes.get(date.get("parent"), {})
        siblings = [n for n in scoped["web"] if n.get("parent") == date_group.get("parent")]
        if date_group not in siblings:
            continue
        index = siblings.index(date_group)
        if index < 1 or index + 2 >= len(siblings):
            continue
        title_group, edit_group, delete_group = siblings[index - 1], siblings[index + 1], siblings[index + 2]
        title_nodes = [n for n in d.descendants(scoped, title_group["ref"])
                       if n["role"] == "AXStaticText" and d.text(n)]
        edits = [n for n in d.descendants(scoped, edit_group["ref"])
                 if n["role"] == "AXStaticText" and d.text(n) == "编辑"]
        deletes = [n for n in d.descendants(scoped, delete_group["ref"])
                   if n["role"] == "AXStaticText" and d.text(n) == "删除"]
        if len(title_nodes) != 1 or len(edits) != 1 or len(deletes) != 1:
            continue
        records.append({"draft_id": None, "title": d.text(title_nodes[0]),
                        "saved_at": d.text(date)[3:], "kind": kind,
                        "_edit": edits[0], "_delete": deletes[0]})
    return records


def public_row(record):
    return {key: value for key, value in record.items() if not key.startswith("_")}


def confirm_draft_delete(current, target):
    warning = d.choose(current, lambda n: n["role"] == "AXStaticText"
                       and d.text(n) == "草稿删除后不可找回")
    nodes = {n["ref"]: n for n in current["web"]}
    ancestor = warning.get("parent")
    while ancestor in nodes:
        children = d.descendants(current, ancestor)
        buttons = [n for n in children if n["role"] == "AXButton" and d.visible(n)]
        if {d.text(n) for n in buttons} == {"取消", "删除"} and len(buttons) == 2:
            break
        ancestor = nodes[ancestor].get("parent")
    if ancestor not in nodes:
        raise RuntimeError("desktop_draft_delete_popover_ambiguous")
    # The popover has no title. Bind it to the previously observed exact row and
    # its delete control; a generic global delete button is never accepted.
    row = next((r for r in draft_rows(current, target["kind"])
                if r["title"] == target["title"] and r["saved_at"] == target["saved_at"]), None)
    if not row:
        raise RuntimeError("desktop_draft_delete_target_changed")
    box, anchor = warning["bounds"], row["_delete"]["bounds"]
    if abs(box["y"] - anchor["y"]) > 180 or abs(box["x"] - anchor["x"]) > 350:
        raise RuntimeError("desktop_draft_delete_popover_not_bound")
    require_account(target["account"], current)
    d.act("press", current, next(n for n in buttons if d.text(n) == "删除"))


def open_drafts(kind):
    if kind not in DRAFT_KINDS:
        raise ValueError("draft kind must be image/video/longform/podcast")
    current = d.select_site(True)
    if d.editor(current)["has_editor"] or any(
            n["role"] == "AXTextArea" and d.visible(n) for n in current["web"]):
        raise RuntimeError("desktop_unsaved_editor_present")
    if not any(n["role"] == "AXHeading" and d.text(n) == "草稿箱" and d.visible(n) for n in current["web"]):
        d.open_area("drafts")
    current = d.wait(lambda s: any(n["role"] == "AXHeading" and d.text(n) == "草稿箱"
                                  and d.visible(n) for n in s["web"]), 6)
    scoped = drawer(current)
    tab = d.choose(scoped, lambda n: n["role"] == "AXStaticText"
                   and re.fullmatch(re.escape(DRAFT_KINDS[kind]) + r"[（(]\d+[）)]", d.text(n)))
    d.act("press", current, tab)
    # Wait for the kind-specific storage explanation, not just the tab label.
    current = d.wait(lambda s: any(n["role"] == "AXStaticText" and
                    "草稿存储于" in d.text(n) and DRAFT_KINDS[kind] in d.text(n)
                    for n in drawer(s)["web"]), 5)
    return current


def drafts(args, task):
    require_transport(args)
    action, kind = args.get("action", "list"), args.get("kind", "image")
    if action not in {"list", "open", "delete", "publish"}:
        raise ValueError("draft action must be list/open/delete/publish")
    if action in {"delete", "publish"}:
        from tools.xhs_account import assert_writable
        assert_writable()
        test_control.preflight("publish" if action == "publish" else "write",
                          args.get("owner_id"), args.get("expected_account"))
        if test_control.status().get("enabled"):
            raise test_control.TestDeferred({"ok": False, "status": "deferred", "submitted": False,
                "reason": "浏览器本地草稿没有稳定账号归属，换号测试期间保留全部既有草稿。",
                "retry_after": None})
    account = args.get("expected_account")
    directory = Path(task.ensure_task(hint="desktop_drafts"))
    with d._lock:
        current = open_drafts(kind)
        require_account(account, current)
        rows = draft_rows(current, kind)
        if action == "list":
            scoped = drawer(current)
            label = d.choose(scoped, lambda n: n["role"] == "AXStaticText" and re.fullmatch(
                re.escape(DRAFT_KINDS[kind]) + r"[（(]\d+[）)]", d.text(n)))
            count = int(re.search(r"\d+", d.text(label))[0])
            result = {"ok": True, "kind": kind, "drafts": [public_row(row) for row in rows],
                      "count": count, "loaded_count": len(rows), "all_loaded": len(rows) == count,
                      "storage": "current_browser_local", "account": account,
                      "evidence_url": current["page_url"], "transport": "macOS_accessibility"}
            atomic_json(directory / ("drafts_" + kind + ".json"), result)
            return result
        title, saved_at = args.get("title"), args.get("saved_at")
        if not title or not saved_at:
            raise ValueError("open/delete require exact observed title, saved_at and kind")
        matches = [r for r in rows if r["title"] == title and r["saved_at"] == saved_at]
        if len(matches) != 1:
            raise RuntimeError("desktop_target_draft_ambiguous_or_not_loaded")
        target = matches[0]
        control = target["_edit" if action in {"open", "publish"} else "_delete"]
        if not d.visible(control) or control["bounds"]["y"] > 900:
            d.act("reveal", current, control)
            current = d.snapshot()
            matches = [r for r in draft_rows(current, kind)
                       if r["title"] == title and r["saved_at"] == saved_at]
            if len(matches) != 1:
                raise RuntimeError("desktop_target_draft_changed")
            target = matches[0]
            control = target["_edit" if action in {"open", "publish"} else "_delete"]
        require_account(account, current)
        if action in {"open", "publish"}:
            d.act("press", current, control)
            current = d.wait(lambda s: not any(n["role"] == "AXHeading" and
                       d.text(n) == "草稿箱" and d.visible(n) for n in s["web"])
                       and (d.editor(s)["has_editor"] or any(n["role"] == "AXTextArea"
                           and d.visible(n) for n in s["web"])), 12)
            require_account(account, current)
            editor = d.editor(current)
            if editor["has_editor"] and editor["title"] != title:
                raise RuntimeError("desktop_opened_draft_title_mismatch")
            result = {"ok": True, "status": "prepared", "submitted": False,
                      "draft": public_row(target), "editor": editor,
                      "page": d.page_summary(current), "account": account}
            atomic_json(directory / "opened_draft.json", result)
            if action == "publish":
                return publish_opened_draft(args, public_row(target), current, task)
            return result
        path, states = attempts(directory)
        op = hashlib.sha256(json.dumps({"action": action, "title": title, "saved_at": saved_at,
                                       "kind": kind, "account": account}, sort_keys=True).encode()).hexdigest()
        if op in states:
            return {"ok": states[op]["verified"], "status": "ok" if states[op]["verified"] else "unverified",
                    "skipped": True, "attempt_id": op, "previous": states[op]}
        # Record before the delete control; a platform may delete immediately.
        test_control.reserve("write", "delete_draft:" + op, account=account)
        states[op] = {"verified": False, "action": "delete_draft", "draft": public_row(target),
                      "account": account, "state": "submitting"}
        atomic_json(path, states)
        d.act("press", current, control)
        try:
            current = d.wait(lambda s: not any(r["title"] == title and r["saved_at"] == saved_at
                                  for r in draft_rows(s, kind))
                             or any(d.text(n) == "草稿删除后不可找回" for n in s["web"]), 6)
            if any(d.text(n) == "草稿删除后不可找回" for n in current["web"]):
                states[op]["phase"] = "confirming"
                atomic_json(path, states)
                confirm_draft_delete(current, {**public_row(target), "account": account})
                d.wait(lambda s: not any(r["title"] == title and r["saved_at"] == saved_at
                                         for r in draft_rows(s, kind)), 6)
            # Independently reopen through the manager; don't trust local row removal.
            d.navigate("https://creator.xiaohongshu.com/new/note-manager?source=official")
            d.wait_notes()
            current = open_drafts(kind)
            require_account(account, current)
            after = draft_rows(current, kind)
            remaining = {(r["title"], r["saved_at"]) for r in after}
            before = {(r["title"], r["saved_at"]) for r in rows}
            states[op]["verified"] = (len(after) == len(rows) - 1 and
                                     remaining == before - {(title, saved_at)})
            states[op]["after"] = [public_row(row) for row in after]
        except Exception as exc:
            states[op]["reason"] = str(exc)
        states[op]["state"] = "done" if states[op]["verified"] else "uncertain"
        atomic_json(path, states)
        task.append_trace("desktop_draft_delete", states[op])
        return {"ok": states[op]["verified"], "status": "ok" if states[op]["verified"] else "unverified",
                "attempt_id": op, "result": states[op]}


def publish_opened_draft(args, target, current, task):
    """Submit existing media without re-uploading or re-rendering the draft."""
    from tools.xhs_account import assert_writable
    assert_writable()
    from datetime import datetime, timedelta
    from tools.xhs_publish import VISIBILITY, TIMEZONE, content_matches
    contents = d.editor(current)
    if not contents["has_editor"] or not contents["title"] or not contents["plain_desc"]:
        raise RuntimeError("desktop_draft_final_editor_not_ready")
    visibility = args.get("visibility", "self")
    if visibility not in VISIBILITY:
        raise ValueError("draft publish visibility must be self/public/friends")
    schedule = args.get("schedule_at")
    if schedule:
        when = datetime.fromisoformat(schedule.replace("Z", "+00:00"))
        if when.tzinfo is None:
            when = when.replace(tzinfo=TIMEZONE)
        when = when.astimezone(TIMEZONE).replace(second=0, microsecond=0)
        now = datetime.now(TIMEZONE)
        if not now + timedelta(hours=1) <= when <= now + timedelta(days=14):
            raise ValueError("draft schedule requires 1 hour to 14 days in Asia/Shanghai")
        schedule = when.isoformat()
    if contents["busy"] or contents["errors"]:
        raise RuntimeError("desktop_draft_media_not_ready")
    spec = {"title": contents["title"], "desc": contents["plain_desc"], "tags": contents["chips"],
            "kind": target["kind"], "visibility": visibility, "schedule_at": schedule, "files": []}
    # Actual longform source view isn't the final note editor. Don't treat its
    # source as already rendered media; require the final stage above.
    if not content_matches(spec, contents):
        raise RuntimeError("desktop_draft_contents_not_matched")
    directory = Path(task.ensure_task(hint="desktop_draft_publish"))
    path, states = attempts(directory)
    op = hashlib.sha256(json.dumps({"action": "publish_draft", "target": target, "spec": spec,
                                   "account": args["expected_account"]}, sort_keys=True).encode()).hexdigest()
    if op in states:
        return {"ok": states[op]["verified"], "status": "ok" if states[op]["verified"] else "unverified",
                "skipped": True, "attempt_id": op, "previous": states[op]}
    d.set_visibility(visibility)
    d.set_schedule(schedule)
    require_account(args["expected_account"])
    if not content_matches(spec, d.editor()):
        raise RuntimeError("desktop_draft_changed_before_submit")
    test_control.reserve("publish", "publish_draft:" + op, account=args["expected_account"])
    states[op] = {"verified": False, "action": "publish_draft", "draft": target, "spec": spec,
                  "account": args["expected_account"], "state": "submitting",
                  "submitted_at": datetime.now(TIMEZONE).isoformat()}
    atomic_json(path, states)
    try:
        acknowledgement = d.submit(spec, args["expected_account"])
        states[op]["acknowledgement"] = acknowledgement
        if acknowledgement.get("verified"):
            result = d.verify_publication(spec, args["expected_account"], states[op]["submitted_at"])
            states[op].update(verified=bool(result["verified"]), note_id=result["note_id"],
                              independent_verification=result)
        else:
            states[op]["reason"] = acknowledgement.get("reason")
    except Exception as exc:
        states[op]["reason"] = str(exc)
    states[op]["state"] = "done" if states[op]["verified"] else "uncertain"
    atomic_json(path, states)
    task.append_trace("draft_publication_result", states[op])
    return {"ok": states[op]["verified"], "status": "ok" if states[op]["verified"] else "unverified",
            "attempt_id": op, "result": states[op], "note_id": states[op].get("note_id"),
            "media_pixels_verified": False}


def form_scope(current, marker, button_labels):
    node = d.choose(current, lambda n: d.text(n) == marker)
    nodes = {n["ref"]: n for n in current["web"]}
    ancestor = node.get("parent")
    while ancestor in nodes:
        children = d.descendants(current, ancestor)
        labels = {d.text(n) for n in children if n["role"] == "AXButton"}
        if button_labels <= labels:
            if nodes[ancestor]["role"] == "AXWebArea":
                break
            return {**current, "web": children}
        ancestor = nodes[ancestor].get("parent")
    raise RuntimeError("desktop_library_form_not_identified")


def collection_inputs(current):
    fields = [n for n in current["web"] if n["role"] == "AXTextField"
              and n.get("placeholder") in {"请输入合集名称", "好的合集名称能吸引更多用户"} and d.visible(n)]
    descriptions = [n for n in current["web"] if n["role"] == "AXTextArea"
                    and n.get("placeholder") in {"请输入合集简介", "简单介绍你的合集"} and d.visible(n)]
    if len(fields) != len(descriptions) or len(fields) != 1:
        raise RuntimeError("desktop_collection_inputs_not_identified")
    return fields[0], descriptions[0]


def collection_cover_ready(current):
    previews = [n for n in current["web"] if n["role"] == "AXGroup"
                and any(d.text(c) == "播放最新" for c in d.descendants(current, n["ref"]))]
    previews.sort(key=lambda n: len(d.descendants(current, n["ref"])))
    if not previews:
        return False
    return any(n["role"] == "AXImage" and (n.get("url") or "").startswith("data:image/")
               and (n.get("bounds") or {}).get("width", 0) >= 40
               for n in d.descendants(current, previews[0]["ref"]))


def open_collection(args):
    kind = args.get("kind", "podcast")
    if kind not in {"note", "podcast"}:
        raise ValueError("collection kind must be note/podcast")
    current = d.select_site(True)
    if any(n.get("placeholder") in {"请输入合集名称", "好的合集名称能吸引更多用户"} for n in current["web"]):
        name, _ = collection_inputs(current)
        if (name.get("placeholder") == "请输入合集名称") != (kind == "podcast"):
            raise RuntimeError("desktop_collection_kind_mismatch")
        if kind == "note" and not any(n["role"] == "AXTextField" and "标题" in str(n.get("placeholder"))
                                     and n.get("value") == args.get("target_title") for n in current["web"]):
            raise RuntimeError("desktop_collection_note_target_mismatch")
        return current
    if kind == "note":
        contents = d.editor(current)
        if not contents["has_editor"] or contents["title"] != args.get("target_title"):
            raise RuntimeError("note_collection_requires_bound_current_editor")
        _, selected = note_collection_row(current, args["target_title"])
        if selected:
            current, _, _ = note_collection_selector(current, args["target_title"])
        d.press_label(["创建合集"], current)
    else:
        if d.editor(current)["has_editor"] or any(n["role"] == "AXTextArea"
                                                 and d.visible(n) for n in current["web"]):
            raise RuntimeError("desktop_unsaved_editor_present")
        d.open_area("podcast")
        current = d.snapshot()
        label = d.choose(current, lambda n: d.text(n) == "新建播客合集")
        # The actual clickable tile owns this label; text AXPress doesn't open it.
        group = next(n for n in current["web"] if n["ref"] == label["parent"] and n["role"] == "AXGroup")
        d.act("press", current, group)
    return d.wait(lambda s: any(n.get("placeholder") in {"请输入合集名称", "好的合集名称能吸引更多用户"} and d.visible(n)
                               for n in s["web"]), 8)


def note_collection_row(current, title):
    if not d.editor(current)["has_editor"] or d.editor(current)["title"] != title:
        raise RuntimeError("note_collection_requires_bound_current_editor")
    label = d.choose(current, lambda n: n["role"] == "AXStaticText" and d.text(n) == "加入合集")
    nodes = {n["ref"]: n for n in current["web"]}
    parent = nodes.get(label.get("parent"), {})
    row = nodes.get(parent.get("parent"), {})
    children = d.descendants(current, row.get("ref"))
    if row.get("role") != "AXGroup" or not any(
            d.text(n) == "汇集系列视频，有利于连续性观看" for n in children):
        raise RuntimeError("desktop_note_collection_row_not_identified")
    values = [d.text(n) for n in children if n["role"] == "AXStaticText"
              and n.get("parent") == row["ref"]]
    if len(values) != 1:
        raise RuntimeError("desktop_note_collection_selection_ambiguous")
    return row, None if values[0] == "创建合集" else values[0]


def note_collection_menu(current, title):
    row, _ = note_collection_row(current, title)
    nodes = {n["ref"]: n for n in current["web"]}
    labels = [n for n in current["web"] if n["role"] == "AXStaticText"
              and d.text(n) == "创建合集" and d.visible(n) and n.get("parent") != row["ref"]]
    if not labels:
        return None
    if len(labels) != 1:
        raise RuntimeError("desktop_note_collection_menu_ambiguous")
    item = nodes.get(labels[0].get("parent"), {})
    menu = nodes.get(item.get("parent"), {})
    if menu.get("role") != "AXGroup" or any(
            n["role"] == "AXTextArea" for n in d.descendants(current, menu.get("ref"))):
        raise RuntimeError("desktop_note_collection_menu_not_identified")
    records = []
    for item in current["web"]:
        if item["role"] != "AXGroup" or item.get("parent") != menu["ref"]:
            continue
        texts = [n for n in d.descendants(current, item["ref"])
                 if n["role"] == "AXStaticText" and d.visible(n)]
        if len(texts) == 1 and d.text(texts[0]) != "创建合集":
            records.append({"name": d.text(texts[0]), "_control": item})
    return records


def note_collection_selector(current, title):
    row, _ = note_collection_row(current, title)
    if note_collection_menu(current, title) is None:
        d.act("press", current, row)
        current = d.wait(lambda s: note_collection_menu(s, title) is not None, 4)
    return current, note_collection_menu(current, title), note_collection_row(current, title)[1]


def verify_note_collection(account, title, name, expected_editor=None, expected_selected=True):
    """Save the bound draft, reopen it, then read its persistent collection."""
    from tools.xhs_publish import content_matches
    current = d.snapshot()
    require_account(account, current)
    contents = expected_editor or d.editor(current)
    spec = {"title": title, "desc": contents["plain_desc"], "tags": contents["chips"]}
    if not content_matches(spec, d.editor(current)):
        raise RuntimeError("desktop_collection_editor_changed_before_save")
    d.save_draft(spec)
    d.navigate("https://creator.xiaohongshu.com/new/note-manager?source=official")
    d.wait_notes()
    current = open_drafts("image")
    rows = [r for r in draft_rows(current, "image") if r["title"] == title]
    if len(rows) != 1:
        raise RuntimeError("desktop_collection_draft_title_ambiguous")
    target = rows[0]
    d.act("press", current, target["_edit"])
    current = d.wait(lambda s: content_matches(spec, d.editor(s)), 12)
    require_account(account, current)
    # The draft body can arrive before the asynchronously populated selection.
    current = d.wait(lambda s: note_collection_row(s, title)[1] ==
                     (name if expected_selected else None), 8)
    current, records, selected = note_collection_selector(current, title)
    if len([r for r in records if r["name"] == name]) != 1:
        raise RuntimeError("desktop_collection_name_not_unique_in_independent_selector")
    result = {"verified": True, "kind": "note", "collection_id": None,
              "name": name, "selected_name": selected, "account": account,
              "draft": public_row(target), "draft_saved": True,
              "description_verified": False, "media_pixels_verified": False,
              "independent_evidence": d.page_summary(current)}
    # Dismiss the selector by focusing the unchanged title field.
    field = d.choose(current, lambda n: n["role"] == "AXTextField" and n.get("value") == title)
    d.act("press", current, field)
    return result


def manage_note_collection(args, task):
    account, title, action = args.get("expected_account"), args.get("target_title"), args["action"]
    current = d.select_site(True)
    require_account(account, current)
    current, records, selected = note_collection_selector(current, title)
    if action in {"list", "open"}:
        return {"ok": True, "kind": "note", "collections": [
            {"collection_id": None, "name": r["name"], "description": None} for r in records],
            "selected_name": selected, "account": account, "target_title": title,
            "visible_page_only": True, "submitted": False}
    name = args.get("target_name") or args.get("name")
    hits = [r for r in records if r["name"] == name]
    if len(hits) != 1:
        raise RuntimeError("desktop_note_collection_target_not_unique")
    if action in {"delete", "edit", "unselect"}:
        return {"ok": False, "status": "unavailable", "submitted": False,
                "reason": "ordinary_collection_selector_has_no_edit_delete_or_unselect_control",
                "page": d.page_summary(current)}
    directory = Path(task.ensure_task(hint="desktop_note_collection"))
    path, states = attempts(directory)
    if action == "verify":
        evidence = verify_note_collection(account, title, name)
        for state in states.values():
            if (state.get("action") == "create_collection" and state.get("kind") == "note"
                    and state.get("name") == name and state.get("target_title") == title
                    and state.get("account") == account):
                state.update(initial_reason=state.get("initial_reason") or state.get("reason"),
                             verified=True, state="done", reconciled=True,
                             independent_verification=evidence)
        atomic_json(path, states)
        atomic_json(directory / "ordinary_collection_verification.json", evidence)
        return {"ok": True, "submitted": False, "result": evidence}
    if action != "select":
        raise ValueError("unknown ordinary collection action")
    wanted = name
    if selected == wanted:
        return {"ok": True, "skipped": True, "selected_name": selected, "submitted": False}
    contents = d.editor(current)
    op = hashlib.sha256(json.dumps(args, sort_keys=True).encode()).hexdigest()
    if op in states:
        return {"ok": states[op]["verified"], "skipped": True, "previous": states[op]}
    test_control.reserve("write", "select_collection:" + op, account=account)
    states[op] = {"verified": False, "action": action + "_collection", "kind": "note",
                  "account": account, "name": name, "target_title": title, "state": "submitting"}
    atomic_json(path, states)
    d.act("press", current, hits[0]["_control"])
    try:
        d.wait(lambda s: note_collection_row(s, title)[1] == wanted, 5)
        evidence = verify_note_collection(account, title, name, contents)
        states[op].update(verified=True, independent_verification=evidence)
    except Exception as exc:
        states[op]["reason"] = str(exc)
    states[op]["state"] = "done" if states[op]["verified"] else "uncertain"
    atomic_json(path, states)
    return {"ok": states[op]["verified"], "status": "ok" if states[op]["verified"] else "unverified",
            "attempt_id": op, "result": states[op]}


def podcast_list(account, allowed_form=None):
    current = d.select_site(True)
    require_account(account, current)
    if d.editor(current)["has_editor"]:
        raise RuntimeError("desktop_unsaved_editor_present")
    forms = [n for n in current["web"] if n.get("placeholder") == "请输入合集名称"]
    if forms:
        title, body = collection_inputs(current)
        if allowed_form != (title.get("value"), body.get("value")):
            raise RuntimeError("desktop_collection_form_present; preserve_or_explicitly_cancel_first")
    d.navigate("https://creator.xiaohongshu.com/new/note-manager?source=official")
    d.wait_notes()
    d.navigate("https://creator.xiaohongshu.com/publish/publish?source=official")
    d.wait(lambda s: any(d.text(n) == "发播客" and d.visible(n) for n in s["web"]), 8)
    current = d.publish_tab("发播客")
    current = d.wait(lambda s: any(d.text(n) == "新建播客合集" and d.visible(n) for n in s["web"]), 8)
    current = d.wait(lambda s: any(d.text(n) == "播客合集上线啦" for n in s["web"])
                     or any(n["role"] == "AXHeading" and d.visible(n) for n in s["web"]), 8)
    require_account(account, current)
    return current


def podcast_segments(current):
    """Bind flattened sibling cover/title/body/count/control sequences."""
    records = []
    headers = [n for n in current["web"] if n["role"] == "AXHeading" and d.visible(n)]
    for header in headers:
        siblings = [n for n in current["web"] if n.get("parent") == header.get("parent")]
        index = siblings.index(header)
        if index < 1 or siblings[index - 1]["role"] != "AXGroup":
            raise RuntimeError("desktop_collection_cover_sequence_not_identified")
        cover = siblings[index - 1]
        if not any(n["role"] == "AXImage" and d.text(n) == d.text(header)
                   for n in d.descendants(current, cover["ref"])):
            raise RuntimeError("desktop_collection_cover_title_mismatch")
        next_index = next((i for i in range(index + 1, len(siblings))
                           if siblings[i]["role"] == "AXHeading"), len(siblings))
        end = next_index - 1 if next_index < len(siblings) else next_index
        refs = {n["ref"] for root in siblings[index - 1:end]
                for n in d.descendants(current, root["ref"])}
        scoped = {**current, "web": [n for n in current["web"] if n["ref"] in refs]}
        excluded = {n["ref"] for n in d.descendants(current, header["ref"])}
        values = [d.text(n) for n in scoped["web"]
                  if n["role"] == "AXStaticText" and n["ref"] not in excluded]
        counts = [re.fullmatch(r"共 (\d+) 篇", value) for value in values]
        counts = [int(match[1]) for match in counts if match]
        descriptions = [value for value in values if value and
                        not re.fullmatch(r"共 \d+ 篇", value) and value not in {"查看", "管理"}]
        if len(counts) != 1 or len(descriptions) > 1:
            raise RuntimeError("desktop_collection_fields_ambiguous")
        records.append({"collection_id": None, "name": d.text(header),
                        "description": descriptions[0] if descriptions else "",
                        "note_count": counts[0], "_scope": scoped, "_header": header})
    return records


def podcast_card(current, name, description):
    hits = [r for r in podcast_segments(current) if r["name"] == name and r["description"] == description]
    if len(hits) != 1:
        raise RuntimeError("desktop_collection_name_description_not_unique")
    record = hits[0]
    return record["_scope"], record["_header"], record["note_count"]


def open_podcast_edit(current, name, description):
    scoped, header, count = podcast_card(current, name, description)
    d.act("hover", current, header)
    current = d.wait(lambda s: any(n["role"] == "AXButton" and not d.text(n) and d.visible(n)
                                  for n in podcast_card(s, name, description)[0]["web"]), 4)
    scoped, _, _ = podcast_card(current, name, description)
    button = d.choose(scoped, lambda n: n["role"] == "AXButton" and not d.text(n))
    d.act("press", current, button)
    current = d.wait(lambda s: any(d.text(n) == "编辑播客合集" and d.visible(n) for n in s["web"]), 8)
    actual_name, actual_description = collection_inputs(current)
    if actual_name["value"] != name or actual_description["value"] != description:
        raise RuntimeError("desktop_collection_edit_target_changed")
    return current, count


def verify_collection(account, name, description):
    current = podcast_list(account, allowed_form=(name, description))
    scoped, _, count = podcast_card(current, name, description)
    listing = d.page_summary(scoped)
    current, _ = open_podcast_edit(current, name, description)
    require_account(account, current)
    result = {"verified": True, "collection_id": None, "identity": "unique_name_and_description",
              "account": account, "name": name, "description": description, "note_count": count,
              "list_evidence": listing, "edit_evidence": d.page_summary(current),
              "cover_pixels_verified": False}
    podcast_list(account, allowed_form=(name, description))
    return result


def manage_collection(args, task):
    action, account = args["action"], args.get("expected_account")
    name, description = args.get("target_name"), args.get("target_description")
    if action == "list" and not name:
        current = podcast_list(account)
        records = [public_row(r) for r in podcast_segments(current)]
        result = {"ok": True, "kind": "podcast", "collections": records, "loaded_count": len(records),
                  "account": account, "visible_page_only": True, "page": d.page_summary(current)}
        atomic_json(Path(task.ensure_task(hint="desktop_collection_list")) / "collections_list.json", result)
        return result
    if not name or not isinstance(description, str):
        raise ValueError("collection manage requires observed target_name and target_description")
    directory = Path(task.ensure_task(hint="desktop_collection_manage"))
    current = podcast_list(account, allowed_form=(name, description))
    scoped, header, count = podcast_card(current, name, description)
    if action == "list":
        return {"ok": True, "collection_id": None, "name": name, "description": description,
                "note_count": count, "account": account, "evidence_url": current["page_url"]}
    current, count = open_podcast_edit(current, name, description)
    require_account(account, current)
    if action == "open":
        return {"ok": True, "status": "prepared", "submitted": False, "collection_id": None,
                "name": name, "description": description, "note_count": count,
                "page": d.page_summary(current)}
    path, states = attempts(directory)
    op = hashlib.sha256(json.dumps({k: v for k, v in args.items() if not k.startswith("_")},
                                  sort_keys=True).encode()).hexdigest()
    if op in states:
        return {"ok": states[op]["verified"], "status": "ok" if states[op]["verified"] else "unverified",
                "skipped": True, "attempt_id": op, "previous": states[op]}
    states[op] = {"verified": False, "action": action + "_collection", "account": account,
                  "name": name, "description": description, "state": "submitting"}
    if action == "delete":
        if count != 0:
            raise RuntimeError("desktop_collection_delete_only_validated_for_empty_collection")
        d.press_label(["删除合集"], current)
        current = d.wait(lambda s: any(re.search(r"删除.*合集|合集.*删除", d.text(n))
                                       for n in s["web"]) and any(n["role"] == "AXButton"
                                       and d.text(n) in {"删除", "确定", "确认"} for n in s["web"]), 5)
        test_control.reserve("write", "delete_collection:" + op, account=account)
        states[op]["phase"] = "confirming"
        atomic_json(path, states)
        try:
            confirm_collection_delete(current, account, name, description)
            current = podcast_list(account)
            states[op]["verified"] = not any(n["role"] == "AXHeading" and d.text(n) == name
                                             for n in current["web"])
            states[op]["after"] = d.page_summary(current)
        except Exception as exc:
            states[op]["reason"] = str(exc)
    elif action == "edit":
        revised_name, revised_desc = args.get("name", name), args.get("description", description)
        if (not isinstance(revised_name, str) or not revised_name.strip()
                or len(revised_name.encode("utf-16-le")) // 2 > 20
                or not isinstance(revised_desc, str) or len(revised_desc.encode("utf-16-le")) // 2 > 100):
            raise ValueError("podcast collection name/description exceeds limits")
        title, summary = collection_inputs(current)
        d.act("fill", current, title, text=revised_name)
        current = d.wait(lambda s: collection_inputs(s)[0].get("value") == revised_name, 4)
        d.act("fill", current, collection_inputs(current)[1], text=revised_desc)
        current = d.wait(lambda s: collection_inputs(s)[1].get("value") == revised_desc, 4)
        require_account(account, current)
        test_control.reserve("write", "edit_collection:" + op, account=account)
        atomic_json(path, states)
        d.act("press", current, d.choose(current, lambda n: n["role"] == "AXButton" and d.text(n) == "保存"))
        try:
            d.wait(lambda s: not any(n.get("placeholder") == "请输入合集名称" for n in s["web"])
                   or any(re.search(r"保存成功|修改成功|更新成功", d.text(n)) for n in s["web"]), 12)
            result = verify_collection(account, revised_name, revised_desc)
            states[op].update(verified=True, independent_verification=result)
        except Exception as exc:
            states[op]["reason"] = str(exc)
    else:
        raise ValueError("unknown collection manage action")
    states[op]["state"] = "done" if states[op]["verified"] else "uncertain"
    atomic_json(path, states)
    task.append_trace("collection_management_result", states[op])
    return {"ok": states[op]["verified"], "status": "ok" if states[op]["verified"] else "unverified",
            "attempt_id": op, "result": states[op]}


def confirm_collection_delete(current, account, name, description):
    require_account(account, current)
    title, summary = collection_inputs(current)
    if title.get("value") != name or summary.get("value") != description:
        raise RuntimeError("desktop_collection_delete_target_changed")
    scoped = form_scope(current, "确认删除播客合集吗？", {"取消", "确定"})
    if not any(d.text(n) == "删除后合集无法恢复，笔记会继续保留在个人页" for n in scoped["web"]):
        raise RuntimeError("desktop_collection_delete_warning_changed")
    d.act("press", current, d.choose(scoped, lambda n: n["role"] == "AXButton" and d.text(n) == "确定"))
    d.wait(lambda s: not any(d.text(n) == "确认删除播客合集吗？" for n in s["web"]), 8)


def collections(args, task):
    require_transport(args)
    action, kind = args.get("action", "inspect"), args.get("kind", "podcast")
    if action not in {"inspect", "prepare", "create", "cancel", "list", "open", "delete", "edit",
                      "verify", "select", "unselect"}:
        raise ValueError("unknown collection action")
    account = args.get("expected_account")
    directory = Path(task.ensure_task(hint="desktop_collections"))
    with d._lock:
        if action in {"create", "prepare", "edit", "delete", "select", "unselect"}:
            from tools.xhs_account import assert_writable
            assert_writable()
        if action == "create":
            path, states = attempts(directory)
            op = hashlib.sha256(json.dumps({"kind": kind, "name": args.get("name"),
                "description": args.get("description", ""), "account": account,
                "cover": args.get("cover")}, sort_keys=True).encode()).hexdigest()
            if op in states:
                return {"ok": states[op]["verified"],
                        "status": "ok" if states[op]["verified"] else "unverified",
                        "skipped": True, "attempt_id": op, "previous": states[op]}
        if action in {"create", "prepare", "edit", "delete", "select", "unselect"}:
            test_control.preflight(account=account)
            test_control.verify_browser(account=account)
        if action in {"list", "open", "delete", "edit", "verify", "select", "unselect"}:
            if kind == "note":
                return manage_note_collection(args, task)
            if kind != "podcast" or action in {"verify", "select", "unselect"}:
                raise ValueError("unsupported collection action/kind")
            return manage_collection(args, task)
        require_account(account, d.select_site(True))
        current = open_collection(args)
        require_account(account, current)
        name, description = collection_inputs(current)
        if action == "cancel":
            if (name.get("value") != args.get("name", "") or
                    description.get("value") != args.get("description", "")):
                raise RuntimeError("desktop_collection_form_changed_before_cancel")
            if kind == "note":
                scoped = form_scope(current, "合集名称", {"取消", "创建并加入"})
                d.act("press", current, d.choose(scoped, lambda n: n["role"] == "AXButton" and d.text(n) == "取消"))
            else:
                d.navigate("https://creator.xiaohongshu.com/new/note-manager?source=official")
                d.wait_notes()
            return {"ok": True, "status": "cancelled", "submitted": False}
        if action == "inspect":
            result = {"ok": True, "kind": kind, "status": "prepared", "submitted": False,
                      "name": name.get("value"), "description": description.get("value"),
                      "limits": {"name": 20, "description": 100 if kind == "podcast" else 50,
                                 "cover_max_mb": 5 if kind == "podcast" else None},
                      "page": d.page_summary(current), "account": account,
                      "completion_verified": False}
            atomic_json(directory / ("collection_" + kind + "_form.json"), result)
            return result
        value, summary = args.get("name"), args.get("description", "")
        utf16 = lambda s: len(s.encode("utf-16-le")) // 2
        if (not isinstance(value, str) or not value.strip() or utf16(value) > 20 or
                not isinstance(summary, str) or utf16(summary) > (100 if kind == "podcast" else 50)):
            raise ValueError("collection name/description exceeds observed limits")
        if name.get("value") not in {"", value} or description.get("value") not in {"", summary}:
            raise RuntimeError("desktop_existing_collection_form_text_present")
        d.act("fill", current, name, text=value)
        current = d.wait(lambda s: collection_inputs(s)[0].get("value") == value, 4)
        d.act("fill", current, collection_inputs(current)[1], text=summary)
        current = d.wait(lambda s: collection_inputs(s)[1].get("value") == summary, 4)
        cover = args.get("cover")
        if kind == "podcast" and cover and not collection_cover_ready(current):
            file = Path(cover)
            if (not file.is_absolute() or not file.is_file() or
                    file.suffix.lower() not in {".png", ".jpg", ".jpeg"} or
                    not 0 < file.stat().st_size <= 5 * 1024**2):
                raise ValueError("podcast collection cover requires PNG/JPG/JPEG <=5MB absolute path")
            control = d.choose(current, lambda n: n["role"] == "AXButton" and d.text(n).startswith("选择文件"))
            d.act("press", current, control)
            d.select_open_file(str(file))
            current = d.wait(lambda s: collection_cover_ready(s) or any(
                d.text(n) == "封面裁剪" and d.visible(n) for n in s["web"]), 15)
            if any(d.text(n) == "封面裁剪" and d.visible(n) for n in current["web"]):
                crop = form_scope(current, "封面裁剪", {"重新上传", "取消", "确定"})
                require_account(account, current)
                d.act("press", current, d.choose(crop, lambda n: n["role"] == "AXButton" and d.text(n) == "确定"))
                current = d.wait(collection_cover_ready, 12)
        result = {"ok": True, "status": "prepared", "submitted": False, "kind": kind,
                  "name": value, "description": summary, "account": account,
                  "page": d.page_summary(current), "completion_verified": False}
        atomic_json(directory / ("collection_" + kind + "_preview.json"), result)
        if action == "prepare":
            return result
        if kind == "podcast" and not cover:
            return {**result, "ok": False, "status": "needs_input", "reason": "podcast_cover_required"}
        scoped = current if kind == "podcast" else form_scope(current, "合集名称", {"取消", "创建并加入"})
        button = d.choose(scoped, lambda n: n["role"] == "AXButton"
                          and d.text(n) == ("创建" if kind == "podcast" else "创建并加入"))
        require_account(account, current)
        path, states = attempts(directory)
        op = hashlib.sha256(json.dumps({"kind": kind, "name": value, "description": summary,
                                       "account": account, "cover": cover}, sort_keys=True).encode()).hexdigest()
        if op in states:
            return {"ok": states[op]["verified"], "status": "ok" if states[op]["verified"] else "unverified",
                    "skipped": True, "attempt_id": op, "previous": states[op]}
        test_control.reserve("write", "create_collection:" + op, account=account)
        states[op] = {"verified": False, "action": "create_collection", "kind": kind,
                      "account": account, "name": value, "description": summary,
                      "target_title": args.get("target_title"), "state": "submitting"}
        atomic_json(path, states)
        d.act("press", current, button)
        try:
            d.wait(lambda s: not any(n.get("placeholder") in {
                "请输入合集名称", "好的合集名称能吸引更多用户"} for n in s["web"]), 12)
            if kind == "podcast":
                verified = verify_collection(account, value, summary)
                states[op].update(verified=True, independent_verification=verified)
            else:
                verified = verify_note_collection(account, args["target_title"], value)
                states[op].update(verified=True, independent_verification=verified)
        except Exception as exc:
            states[op]["reason"] = str(exc)
        states[op]["state"] = "done" if states[op]["verified"] else "uncertain"
        atomic_json(path, states)
        task.append_trace("collection_submitted", states[op])
        return {"ok": states[op]["verified"], "status": "ok" if states[op]["verified"] else "unverified",
                "attempt_id": op, "result": states[op]}


def rss_scope(current):
    return form_scope(current, "RSS地址", {"添加", "取消", "确认"})


def rss_collection_control(current):
    """Read the selected value inside the observed '加入合集' field only."""
    scoped = rss_scope(current)
    label = d.choose(scoped, lambda n: n["role"] == "AXStaticText" and d.text(n) == "加入合集")
    nodes = {n["ref"]: n for n in scoped["web"]}
    ancestor = label.get("parent")
    while ancestor in nodes:
        children = d.descendants(scoped, ancestor)
        if any(d.text(n) in {"邮箱验证", "RSS地址"} for n in children):
            break
        candidates = [n for n in children if n["role"] in {
            "AXStaticText", "AXTextField", "AXComboBox", "AXPopUpButton"}
            and d.visible(n) and d.text(n) and d.text(n) != "加入合集"]
        if candidates:
            return d.choose({**scoped, "web": children}, lambda n: n in candidates)
        ancestor = nodes[ancestor].get("parent")
    raise RuntimeError("desktop_RSS_collection_field_not_identified")


def rss_state(current):
    scoped = rss_scope(current)
    fields = {n.get("placeholder"): n for n in scoped["web"] if n["role"] == "AXTextField"}
    try:
        selected = d.text(rss_collection_control(current))
        collection = None if selected == "请选择合集" else selected
    except RuntimeError:
        collection = None
    return {"url": fields.get("请粘贴链接", {}).get("value", ""),
            "email": fields.get("获取RSS地址后将自动填入", {}).get("value", ""),
            "code_present": bool(fields.get("请输入", {}).get("value")),
            "start_time": fields.get("请选择起始时间", {}).get("value"),
            "agreement": next((bool(n.get("value")) for n in scoped["web"]
                               if n["role"] == "AXCheckBox"), None),
            "confirm_enabled": any(n["role"] == "AXButton" and d.text(n) == "确认"
                                   and d.visible(n) for n in scoped["web"]),
            "collection": collection}


def rss(args, task):
    require_transport(args)
    action = args.get("action", "inspect")
    if action not in {"inspect", "prepare", "fetch", "request_code", "confirm", "cancel"}:
        raise ValueError("RSS action must be inspect/prepare/fetch/request_code/confirm/cancel")
    if action in {"fetch", "request_code", "confirm"}:
        from tools.xhs_account import assert_writable
        assert_writable()
    if action in {"prepare", "fetch", "request_code", "confirm"}:
        test_control.preflight(account=args.get("expected_account"))
        test_control.verify_browser(account=args.get("expected_account"))
    from urllib.parse import urlsplit
    feed = args.get("rss_url")
    if feed and (not isinstance(feed, str) or urlsplit(feed).scheme not in {"http", "https"}
                 or not urlsplit(feed).hostname):
        raise ValueError("RSS requires a valid HTTP(S) feed URL")
    account = args.get("expected_account")
    directory = Path(task.ensure_task(hint="desktop_rss"))
    with d._lock:
        current = d.select_site(True)
        require_account(account, current)
        if not any(d.text(n) == "RSS地址" and d.visible(n) for n in current["web"]):
            if d.editor(current)["has_editor"] or any(n["role"] == "AXTextArea"
                                                     and d.visible(n) for n in current["web"]):
                raise RuntimeError("desktop_unsaved_editor_present")
            d.open_area("podcast")
            d.press_label(["添加RSS订阅"])
            current = d.wait(lambda s: any(d.text(n) == "RSS地址" and d.visible(n)
                                          for n in s["web"]), 5)
        scoped = rss_scope(current)
        state = rss_state(current)
        if action == "cancel":
            if state["url"] != (feed or "") or state["code_present"]:
                raise RuntimeError("desktop_RSS_changed_before_cancel")
            d.act("press", current, d.choose(scoped, lambda n: n["role"] == "AXButton" and d.text(n) == "取消"))
            return {"ok": True, "status": "cancelled", "submitted": False}
        if feed:
            if state["url"] not in {"", feed}:
                raise RuntimeError("desktop_existing_RSS_form_present")
            field = d.choose(scoped, lambda n: n["role"] == "AXTextField" and n.get("placeholder") == "请粘贴链接")
            d.act("fill", current, field, text=feed)
            current = d.wait(lambda s: rss_state(s)["url"] == feed, 4)
            scoped = rss_scope(current)
        if action == "fetch":
            if not feed:
                raise ValueError("fetch requires the user's real rss_url")
            test_control.reserve("write", "RSS_fetch:" + hashlib.sha256(
                json.dumps([account, feed]).encode()).hexdigest(), account=account)
            d.act("press", current, d.choose(scoped, lambda n: n["role"] == "AXButton" and d.text(n) == "添加"))
            try:
                current = d.wait(lambda s: bool(rss_state(s)["email"]), 15)
            except RuntimeError:
                return {"ok": False, "status": "needs_review", "submitted": False,
                        "reason": "RSS_metadata_not_verified", "state": rss_state(d.snapshot())}
        if args.get("start_time"):
            d.act("press", current, d.choose(scoped, lambda n: d.text(n) == "选择导入起始时间"))
            current = d.wait(lambda s: any(n.get("placeholder") == "请选择起始时间"
                                          for n in rss_scope(s)["web"]), 4)
            scoped = rss_scope(current)
            field = d.choose(scoped, lambda n: n["role"] == "AXTextField" and n.get("placeholder") == "请选择起始时间")
            d.act("fill", current, field, text=args["start_time"])
            current = d.wait(lambda s: rss_state(s)["start_time"] == args["start_time"], 4)
        if args.get("collection_name"):
            scoped = rss_scope(current)
            if rss_state(current)["collection"] != args["collection_name"]:
                selector = rss_collection_control(current)
                d.act("press", current, selector)
                current = d.wait(lambda s: any(d.text(n) == args["collection_name"] and d.visible(n)
                                              for n in s["web"]), 4)
                d.act("press", current, d.choose(current, lambda n: d.text(n) == args["collection_name"]))
                current = d.wait(lambda s: rss_state(s)["collection"] == args["collection_name"], 4)
        code = args.get("verification_code")
        if code:
            scoped = rss_scope(current)
            field = d.choose(scoped, lambda n: n["role"] == "AXTextField" and n.get("placeholder") == "请输入")
            d.act("fill", current, field, text=str(code))
            current = d.wait(lambda s: rss_state(s)["code_present"], 4)
        if args.get("accept_agreement") is True:
            scoped = rss_scope(current)
            check = d.choose(scoped, lambda n: n["role"] == "AXCheckBox")
            if not check.get("value"):
                d.act("press", current, check)
                current = d.wait(lambda s: rss_state(s)["agreement"] is True, 4)
        state = rss_state(current)
        result = {"ok": True, "status": "prepared", "submitted": False, "account": account,
                  "state": state, "evidence_url": current["page_url"], "completion_verified": False}
        atomic_json(directory / "rss_preview.json", result)
        if action in {"inspect", "prepare", "fetch"}:
            return result
        missing = [key for key, present in {"rss_url": state["url"], "feed_email": state["email"],
                   "collection_name": state["collection"] == args.get("collection_name") and state["collection"],
                   "verification_code": state["code_present"] if action == "confirm" else True,
                   "accept_agreement": state["agreement"] if action == "confirm" else True}.items() if not present]
        if missing:
            return {**result, "ok": False, "status": "needs_input", "missing": missing}
        if args.get("expected_email") != state["email"]:
            raise RuntimeError("RSS_email_binding_requires_expected_email")
        path, states = attempts(directory)
        op = hashlib.sha256(json.dumps({"action": action, "account": account,
                                       "rss_url": state["url"], "email": state["email"],
                                       "collection": state["collection"], "start_time": state["start_time"]},
                                      sort_keys=True).encode()).hexdigest()
        if op in states:
            return {"ok": states[op]["verified"], "status": "ok" if states[op]["verified"] else "unverified",
                    "skipped": True, "attempt_id": op, "previous": states[op]}
        scoped = rss_scope(current)
        button = d.choose(scoped, lambda n: d.text(n) == ("获取邮箱验证码" if action == "request_code" else "确认"))
        require_account(account, current)
        test_control.reserve("write", "RSS:" + op, account=account)
        states[op] = {"verified": False, "action": "RSS_" + action, "state": "submitting",
                      "account": account, "rss_url": state["url"]}
        atomic_json(path, states)
        d.act("press", current, button)
        states[op].update(state="uncertain", reason="RSS_server_result_not_independently_verified")
        atomic_json(path, states)
        return {"ok": False, "status": "unverified", "attempt_id": op, "result": states[op]}
