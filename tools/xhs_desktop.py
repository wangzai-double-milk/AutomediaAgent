"""Business workflows on the user's existing Chrome via ordinary macOS AX.

Every control comes from a fresh accessibility observation. No fixed coordinates,
cookies, private API, or developer-policy changes. Failed readback stops a write.
"""

import re
import threading
import time
from urllib.parse import urlsplit

from tools import mac_browser

_lock = threading.RLock()
_pause = threading.Event()


def text(node):
    value = node.get("value")
    return value if isinstance(value, str) and value else node.get("title", "")


def visible(node):
    bounds = node.get("bounds") or {}
    return bounds.get("width", 0) >= 2 and bounds.get("height", 0) >= 2 and node.get("enabled") is not False


def snapshot():
    result = mac_browser.operate({"action": "snapshot"})
    nodes = result.get("nodes", [])
    addresses = [n.get("value", "") for n in nodes if n.get("title") == "地址和搜索栏"]
    url = addresses[0] if len(addresses) == 1 else ""
    if url and not url.startswith("http"):
        url = "https://" + url
    web_refs = {n["ref"] for n in nodes if n["role"] == "AXWebArea"}
    parents = {n["ref"]: n.get("parent") for n in nodes}
    def in_web(node):
        ref = node["ref"]
        while ref:
            if ref in web_refs:
                return True
            ref = parents.get(ref)
        return False
    result.update(url=url, web=[n for n in nodes if in_web(n)])
    result["page_url"] = next((n.get("url") for n in result["web"] if n["role"] == "AXWebArea"), None)
    from tools.xhs_account import observe
    result["account_access"] = observe(result)
    return result


def wait(predicate, timeout=8):
    deadline = time.monotonic() + timeout
    while True:
        current = snapshot()
        if predicate(current):
            return current
        if time.monotonic() >= deadline:
            raise RuntimeError("desktop_page_readback_timeout")
        _pause.wait(min(.2, max(0, deadline - time.monotonic())))


def choose(current, predicate, scope="web"):
    hits = [n for n in current[scope] if visible(n) and predicate(n)]
    # AXButton labels can sit below one or more otherwise empty AXGroups.
    parents = {n["ref"]: n.get("parent") for n in current[scope]}
    refs = {n["ref"] for n in hits}
    def nested(node):
        parent = node.get("parent")
        while parent:
            if parent in refs:
                return True
            parent = parents.get(parent)
        return False
    hits = [n for n in hits if not nested(n)]
    if len(hits) != 1:
        raise RuntimeError("desktop_control_ambiguous: " + str(len(hits)))
    return hits[0]


def act(action, current, node, **args):
    observed = snapshot()
    if ((observed.get("page_url") or "").split("#")[0]
            != (current.get("page_url") or "").split("#")[0]):
        raise RuntimeError("desktop_site_changed_before_action")
    # Restore refs through the exact observed attributes in the fresh tree.
    candidates = [n for n in observed["nodes"] if n["role"] == node["role"]
                  and n.get("bounds") == node.get("bounds") and n.get("title") == node.get("title")
                  and n.get("value") == node.get("value")]
    if len(candidates) != 1:
        raise RuntimeError("desktop_control_changed_before_action")
    node, current = candidates[0], observed
    result = mac_browser.operate({"action": action, "ref": node["ref"],
                                 "snapshot_id": current["snapshot_id"], **args})
    if not result.get("ok"):
        raise RuntimeError("desktop_action_failed: " + str(result))
    return result


def press_label(labels, current=None):
    current = current or snapshot()
    return act("press", current, choose(current, lambda n: text(n) in labels))


def publish_tab(label):
    """Select the tab strip that contains all four publishing formats."""
    current = snapshot()
    labels = {"上传视频", "上传图文", "写长文", "发播客"}
    groups = [n for n in current["web"] if n["role"] == "AXGroup"
              and labels <= {text(c) for c in descendants(current, n["ref"])}]
    groups.sort(key=lambda n: len(descendants(current, n["ref"])))
    if not groups:
        raise RuntimeError("desktop_publish_tab_strip_missing")
    scoped = {**current, "web": descendants(current, groups[0]["ref"])}
    act("press", scoped, choose(scoped, lambda n: text(n) == label))
    markers = {"上传视频": {"上传视频"}, "上传图文": {"上传图片", "文字配图"},
               "写长文": {"新的创作", "导入链接"}, "发播客": {"上传音频", "添加RSS订阅"}}
    return wait(lambda s: any(n["role"] == "AXButton" and text(n) in markers[label]
                             for n in s["web"]), 12)


def address_focused(current):
    if any(n.get("title") == "地址和搜索栏" and n.get("focused") for n in current["nodes"]):
        return True
    focused = current.get("focused_element") or {}
    # Chrome's autocomplete owns AX focus while the address text is selected.
    address = next((n for n in current["nodes"] if n.get("title") == "地址和搜索栏"), None)
    value = str(focused.get("value") or "")
    box = focused.get("bounds") or {}
    return bool(address and focused.get("role") == "AXStaticText" and value.startswith(current["url"])
                and box.get("y", 99999) < address["bounds"]["y"] + 150
                and box.get("y", -1) >= address["bounds"]["y"])


def navigate(url, discard_verified_editor=None):
    if urlsplit(url).hostname not in {"www.xiaohongshu.com", "creator.xiaohongshu.com"}:
        raise ValueError("desktop_navigation_requires_xhs_url")
    def destination(actual):
        from urllib.parse import parse_qsl
        wanted, got = urlsplit(url), urlsplit(actual or "")
        return (got.scheme == wanted.scheme and got.hostname == wanted.hostname
                and got.path.rstrip("/") == wanted.path.rstrip("/")
                and all(pair in parse_qsl(got.query, keep_blank_values=True)
                        for pair in parse_qsl(wanted.query, keep_blank_values=True)))
    current = snapshot()
    if destination(current.get("page_url")):
        return current
    if discard_verified_editor is not None and editor(current) != discard_verified_editor:
        raise RuntimeError("desktop_editor_changed_before_leave")
    mac_browser.operate({"action": "key", "key": "address"})
    current = wait(address_focused, 4)
    address = choose(current, lambda n: n.get("title") == "地址和搜索栏", "nodes")
    act("fill", current, address, text=url)
    wait(lambda s: s["url"].rstrip("/") == url.rstrip("/") and address_focused(s), 3)
    mac_browser.operate({"action": "key", "key": "enter"})
    current = wait(lambda s: destination(s.get("page_url"))
                   or any(text(n) == "离开此网站？" for n in s["nodes"]), 12)
    if any(text(n) == "离开此网站？" for n in current["nodes"]):
        if discard_verified_editor is None or editor(current) != discard_verified_editor:
            raise RuntimeError("desktop_leave_confirmation_requires_verified_unchanged_editor")
        act("press", current, choose(current, lambda n: n["role"] == "AXButton" and text(n) == "离开", "nodes"))
    return wait(lambda s: bool(s["web"]) and destination(s.get("page_url")), 12)


def identity(current=None):
    """Only a visible creator-header identity; do not infer ID from a nickname."""
    current = current or snapshot()
    if urlsplit(current["url"]).hostname != "creator.xiaohongshu.com":
        return None
    areas = [n for n in current["web"] if n["role"] == "AXWebArea"]
    if len(areas) != 1:
        return None
    box = areas[0].get("bounds") or {}
    candidates = [text(n) for n in current["web"] if n["role"] == "AXStaticText"
                  and visible(n) and text(n) and (n["bounds"]["x"] > box.get("x", 0) + box.get("width", 0) * .8)
                  and n["bounds"]["y"] < box.get("y", 0) + 60]
    return candidates[0] if len(candidates) == 1 else None


def editor(current=None):
    current = current or snapshot()
    titles = [n for n in current["web"] if n["role"] == "AXTextField"
              and ("标题" in (n.get("placeholder") or n.get("title", "")))]
    if not titles:
        # Actual Chrome AX omits the placeholder; the only visible single-line
        # input above the unique body is the title. Schedule/location are below.
        bodies = [n for n in current["web"] if n["role"] == "AXTextArea" and visible(n)
                  and not n.get("placeholder")]
        if len(bodies) == 1 and any(text(n) == "暂存离开" for n in current["web"]):
            titles = [n for n in current["web"] if n["role"] == "AXTextField" and visible(n)
                      and not n.get("placeholder")
                      and n["bounds"]["y"] < bodies[0]["bounds"]["y"]]
    bodies = [n for n in current["web"] if n["role"] == "AXTextArea"]
    errors = [text(n) for n in current["web"] if visible(n) and re.search(r"上传失败|转码失败|格式不支持", text(n))]
    busy = any(re.search(r"上传中|转码中|处理中", text(n)) for n in current["web"] if visible(n))
    chips, plain = [], bodies[0].get("value", "") if len(bodies) == 1 else ""
    if len(bodies) == 1:
        parts = [n for n in descendants(current, bodies[0]["ref"]) if n["role"] == "AXStaticText"]
        for index, part in enumerate(parts):
            if text(part) == "[话题]#" and index and text(parts[index - 1]).startswith("#"):
                label = text(parts[index - 1])
                if parts[index - 1].get("parent") == part.get("parent"):
                    chips.append(label[1:])
                    plain = plain.replace(label + "[话题]#", "", 1)
    return {"has_editor": len(titles) == len(bodies) == 1,
            "title": titles[0].get("value", "") if len(titles) == 1 else "",
            "desc": bodies[0].get("value", "") if len(bodies) == 1 else "",
            "plain_desc": plain, "chips": chips, "busy": busy, "errors": errors,
            "url": current.get("page_url") or current["url"]}


def fill_editor(spec):
    current = snapshot()
    titles = [n for n in current["web"] if n["role"] == "AXTextField"
              and "标题" in (n.get("placeholder") or "")]
    if len(titles) == 1 and not visible(titles[0]):
        act("reveal", current, titles[0])
        current = wait(lambda s: any(n["role"] == "AXTextField" and "标题" in (n.get("placeholder") or "")
                                    and visible(n) for n in s["web"]), 4)
    title = choose(current, lambda n: n["role"] == "AXTextField" and "标题" in (n.get("placeholder") or ""))
    act("fill", current, title, text=spec["title"])
    wait(lambda s: editor(s)["title"] == spec["title"], 4)
    current = snapshot()
    body = choose(current, lambda n: n["role"] == "AXTextArea")
    act("fill", current, body, text=spec["desc"])
    from tools.xhs_publish import content_matches
    wait(lambda s: content_matches({**spec, "tags": []}, editor(s)), 5)
    for word in spec["tags"]:
        add_topic(word)
    return editor(wait(lambda s: content_matches(spec, editor(s)), 5))


def add_topic(word):
    before = editor()
    current = snapshot()
    # Recommended topics have a single exact label outside the editor.
    body = next(n for n in current["web"] if n["role"] == "AXTextArea")
    body_refs = {n["ref"] for n in descendants(current, body["ref"])}
    hits = [n for n in current["web"] if n["ref"] not in body_refs
            and n["role"] == "AXStaticText" and text(n) == "#" + word and visible(n)]
    if len(hits) == 1:
        act("press", current, hits[0])
    else:
        act("press", current, body)
        wait(lambda s: any(n["role"] == "AXTextArea" and n.get("focused") for n in s["web"]), 4)
        mac_browser.operate({"action": "key", "key": "end"})
        current = snapshot()
        act("type", current, choose(current, lambda n: n["role"] == "AXTextArea" and n.get("focused")),
            text=" #" + word)
        # A real search candidate has its own row containing a browse count.
        def candidate(s):
            for node in s["web"]:
                if text(node) == "#" + word and node["role"] == "AXStaticText" and visible(node):
                    row = descendants(s, node["parent"])
                    if any(re.search(r"[\d.]+[万亿]?浏览", text(n)) for n in row):
                        return node
            return None
        current = wait(lambda s: candidate(s) is not None, 12)
        act("press", current, candidate(current))
    current = wait(lambda s: word in editor(s)["chips"], 5)
    after = editor(current)
    compact = lambda value: "".join(value.split())
    if compact(after["plain_desc"]) != compact(before["plain_desc"]):
        raise RuntimeError("desktop_topic_changed_plain_body")
    return {"verified": True, "topic": word, "evidence": "AX_editor_topic_marker"}


def upload_file(path):
    current = snapshot()
    target = choose(current, lambda n: n["role"] == "AXButton" and n.get("title", "").startswith("选择文件"))
    act("press", current, target)
    select_open_file(path)


def select_open_file(path):
    """Complete an already observed system file dialog for one exact file."""
    wait(lambda s: any(n["role"] == "AXButton" and text(n) == "打开" for n in s["nodes"]), 4)
    mac_browser.operate({"action": "key", "key": "go_to_folder"})
    current = wait(lambda s: any(n["role"] in {"AXComboBox", "AXTextField"}
                                and n.get("focused") for n in s["nodes"]), 4)
    path_input = choose(current, lambda n: n["role"] in {"AXComboBox", "AXTextField"} and n.get("focused"), "nodes")
    act("fill", current, path_input, text=path)
    wait(lambda s: any(n.get("value") == path for n in s["nodes"]), 4)
    mac_browser.operate({"action": "key", "key": "enter"})
    current = wait(lambda s: any(n["role"] == "AXButton" and text(n) == "打开" and visible(n)
                                for n in s["nodes"]), 4)
    # The selection must identify the requested file before opening.
    from pathlib import Path
    filename = Path(path).name
    if not any(filename in text(n) for n in current["nodes"]):
        raise RuntimeError("desktop_selected_file_not_verified")
    act("press", current, choose(current, lambda n: n["role"] == "AXButton" and text(n) == "打开", "nodes"))


def image_count(current=None):
    current = current or snapshot()
    counters = [int(text(n).split("/")[0]) for n in current["web"]
                if n["role"] == "AXStaticText" and re.fullmatch(r"\d+/18", text(n))]
    if len(counters) != 1:
        raise RuntimeError("desktop_image_count_unknown")
    return counters[0]


def add_image(path):
    current = snapshot()
    before = image_count(current)
    if before >= 18:
        raise RuntimeError("desktop_image_limit_reached")
    label = choose(current, lambda n: text(n) == "图片编辑")
    if not visible(label):
        act("reveal", current, label)
        current = wait(lambda s: any(text(n) == "图片编辑" and visible(n) for n in s["web"]), 4)
        label = choose(current, lambda n: text(n) == "图片编辑")
    nodes = {n["ref"]: n for n in current["web"]}
    group = nodes[nodes[label["parent"]]["parent"]]
    scoped = {**current, "web": descendants(current, group["ref"])}
    # Real media group exposes exactly one 24px add icon; thumbnails are groups.
    icon = choose(scoped, lambda n: n["role"] == "AXImage"
                  and n["bounds"]["width"] == n["bounds"]["height"] == 24)
    act("press", current, icon)
    select_open_file(path)
    current = wait(lambda s: image_count(s) == before + 1 and not editor(s)["busy"]
                   and not editor(s)["errors"], 30)
    return {"verified": True, "count": image_count(current)}


def set_visibility(level):
    from tools.xhs_publish import VISIBILITY
    current = snapshot()
    web_box = next(n["bounds"] for n in current["web"] if n["role"] == "AXWebArea")
    def controls(s):
        return [n for n in s["web"] if n["role"] == "AXStaticText" and text(n) in VISIBILITY.values()
                and n["bounds"]["x"] < web_box["x"] + web_box["width"] * .7]
    control = controls(current)
    if len(control) not in {1, 4}:
        raise RuntimeError("desktop_visibility_control_ambiguous")
    if len(control) == 1 and not visible(control[0]):
        act("reveal", current, control[0])
        current = wait(lambda s: any(text(n) in VISIBILITY.values() and visible(n) for n in s["web"]), 3)
    control = controls(current)
    if len(control) == 1 and visible(control[0]) and text(control[0]) == VISIBILITY[level]:
        return {"ok": True, "now": VISIBILITY[level], "verified": True, "skipped": True}
    if len(control) == 1:
        main_y = control[0]["bounds"]["y"]
        act("press", current, control[0])
        current = wait(lambda s: len([n for n in controls(s) if visible(n)]) == 4, 4)
    else:
        groups = [n for n in current["web"] if n["role"] == "AXGroup" and text(n) in VISIBILITY.values()]
        main = [n for n in control if any(n.get("parent") == g["ref"] for g in groups)]
        if len(main) != 1:
            raise RuntimeError("desktop_open_visibility_menu_parent_ambiguous")
        main_y = main[0]["bounds"]["y"]
    target = VISIBILITY[level]
    # The popover can appear above or below the row.
    hits = [n for n in controls(current) if visible(n) and text(n) == target
            and abs(n["bounds"]["y"] - main_y) > 25]
    if len(hits) != 1:
        raise RuntimeError("desktop_visibility_menu_candidate_ambiguous")
    node = hits[0]
    act("press", current, node)
    wait(lambda s: len([n for n in controls(s) if visible(n)]) == 1
         and any(text(n) == target and visible(n) for n in controls(s)), 4)
    return {"ok": True, "now": target, "verified": True}


def prepare(spec, expected_account=None):
    with _lock:
        from tools.xhs_account import assert_writable
        assert_writable()
        if spec["kind"] in {"longform", "podcast"}:
            from tools.xhs_formats import prepare as format_prepare
            return format_prepare(spec, expected_account)
        current = snapshot()
        if editor(current)["has_editor"]:
            raise RuntimeError("desktop_unsaved_editor_present; save_or_resume_draft_first")
        if urlsplit(current["url"]).hostname != "creator.xiaohongshu.com":
            current = select_site(True)
        if (urlsplit(current["url"]).hostname != "creator.xiaohongshu.com"
                or urlsplit(current["url"]).path != "/publish/publish"
                or any(n["role"] == "AXHeading" and text(n) == "草稿箱" for n in current["web"])):
            navigate("https://creator.xiaohongshu.com/publish/publish?source=official")
        label = "上传视频" if spec["kind"] == "video" else "上传图文"
        current = wait(lambda s: identity(s) is not None
                       and any(text(n) == label and visible(n) for n in s["web"]), 8)
        account = identity(current)
        if expected_account and account != expected_account:
            raise RuntimeError("desktop_account_mismatch")
        upload_label = "上传视频" if spec["kind"] == "video" else "上传图片"
        def ready(s):
            return (any(n["role"] == "AXButton" and text(n) == upload_label for n in s["web"])
                    and any(n["role"] == "AXButton" and n.get("title", "").startswith("选择文件") for n in s["web"]))
        if not ready(current):
            publish_tab(label)
            wait(ready, 6)
        upload_file(spec["files"][0])
        wait(lambda s: editor(s)["has_editor"] and not editor(s)["busy"] and not editor(s)["errors"],
             180 if spec["kind"] == "video" else 30)
        for path in spec["files"][1:]:
            add_image(path)
        contents = fill_editor(spec)
        visibility = set_visibility(spec["visibility"])
        schedule = set_schedule(spec["schedule_at"])
        return {"snapshot": contents, "visibility": visibility, "schedule": schedule,
                "account": account, "transport": "macOS_accessibility"}


def schedule_state(current=None):
    current = current or snapshot()
    labels = [n for n in current["web"] if text(n) == "定时发布" and n["role"] != "AXButton"]
    if len(labels) != 1:
        raise RuntimeError("desktop_schedule_label_ambiguous")
    index = current["web"].index(labels[0])
    adjacent = current["web"][index + 1:index + 6]
    switches = [n for n in adjacent if n["role"] in {"AXSwitch", "AXCheckBox"}]
    if len(switches) != 1 or switches[0].get("value") not in {0, 1, False, True}:
        raise RuntimeError("desktop_schedule_state_unknown")
    inputs = [n for n in current["web"][index + 2:] if n["role"] == "AXTextField"
              and re.fullmatch(r"\d{4}-\d\d-\d\d \d\d:\d\d", str(n.get("value") or ""))]
    return {"enabled": bool(switches[0]["value"]), "label": labels[0], "switch": switches[0],
            "input": inputs[0] if len(inputs) == 1 else None, "current": current}


def set_schedule(schedule):
    from datetime import datetime
    from tools.xhs_publish import TIMEZONE
    state = schedule_state()
    wanted = bool(schedule)
    if state["enabled"] != wanted:
        if not visible(state["label"]):
            act("reveal", state["current"], state["label"])
            state = schedule_state(wait(lambda s: visible(schedule_state(s)["label"]), 4))
        toggled = mac_browser.toggle_checkbox(state["switch"]["ref"], state["current"]["snapshot_id"],
                                               state["label"]["ref"])
        if not toggled.get("ok"):
            raise RuntimeError("desktop_schedule_toggle_failed")
        state = schedule_state(wait(lambda s: schedule_state(s)["enabled"] == wanted, 4))
    if not wanted:
        return {"ok": True, "schedule_at": None, "enabled": False, "verified": True}
    requested = datetime.fromisoformat(schedule).astimezone(TIMEZONE).strftime("%Y-%m-%d %H:%M")
    state = schedule_state(wait(lambda s: schedule_state(s)["input"] is not None, 4))
    act("fill", state["current"], state["input"], text=requested)
    wait(lambda s: schedule_state(s)["input"] and schedule_state(s)["input"].get("value") == requested, 4)
    mac_browser.operate({"action": "key", "key": "tab"})
    state = schedule_state(wait(lambda s: schedule_state(s)["enabled"] and schedule_state(s)["input"]
                               and schedule_state(s)["input"].get("value") == requested, 4))
    return {"ok": True, "schedule_at": schedule, "enabled": True,
            "control_value": state["input"]["value"], "verified": True}


def save_draft(spec):
    from tools.xhs_publish import content_matches
    from tools import xhs_test_control as control
    import hashlib
    import json
    current = snapshot()
    if not content_matches(spec, editor(current)):
        raise RuntimeError("desktop_editor_changed_before_draft")
    from tools.xhs_account import assert_writable
    assert_writable()
    operation = hashlib.sha256(json.dumps(spec, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    control.reserve("draft", "save_draft:" + operation, account=identity(current))
    press_label(["暂存离开"])
    current = wait(lambda s: not editor(s)["has_editor"]
                   and any(re.fullmatch(r"草稿箱[（(]\d+[）)]", text(n)) for n in s["web"]), 8)
    counters = [text(n) for n in current["web"] if re.fullmatch(r"草稿箱[（(]\d+[）)]", text(n))]
    press_label(counters)
    current = wait(lambda s: any(text(n) == spec["title"] for n in s["web"]), 6)
    current = wait(lambda s: any(text(n) == spec["title"] for n in s["web"])
                   and any(re.fullmatch(r"草稿箱[（(][1-9]\d*[）)]", text(n)) for n in s["web"]), 4)
    hits = [n for n in current["web"] if text(n) == spec["title"]]
    if len(hits) != 1:
        raise RuntimeError("desktop_saved_draft_title_ambiguous")
    counters = [text(n) for n in current["web"] if re.fullmatch(r"草稿箱[（(]\d+[）)]", text(n))]
    return {"ok": True, "status": "draft", "submitted": False, "saved_as_draft": True,
            "verified": True, "title": spec["title"], "counter": counters[0],
            "evidence_url": current["url"], "transport": "macOS_accessibility"}


def submit(spec, account):
    from tools.xhs_account import assert_writable
    assert_writable()
    from tools.xhs_publish import content_matches
    current = snapshot()
    if identity(current) != account or not content_matches(spec, editor(current)):
        raise RuntimeError("desktop_submit_identity_or_content_changed")
    # Read all prepared settings again immediately before the sole submit action.
    from tools.xhs_publish import VISIBILITY
    if not any(text(n) == VISIBILITY[spec["visibility"]] and visible(n) for n in current["web"]):
        raise RuntimeError("desktop_submit_visibility_not_visible")
    state = schedule_state(current)
    if state["enabled"] != bool(spec["schedule_at"]):
        raise RuntimeError("desktop_submit_schedule_changed")
    if spec["schedule_at"]:
        from datetime import datetime
        from tools.xhs_publish import TIMEZONE
        value = datetime.fromisoformat(spec["schedule_at"]).astimezone(TIMEZONE).strftime("%Y-%m-%d %H:%M")
        if not state["input"] or state["input"]["value"] != value:
            raise RuntimeError("desktop_submit_schedule_readback_mismatch")
    label = "发布" if not spec["schedule_at"] else "定时发布"
    button = choose(current, lambda n: n["role"] == "AXButton" and text(n) == label)
    act("press", current, button)
    try:
        current = wait(lambda s: any(re.fullmatch(r"发布成功[！!。\s]*", text(n)) for n in s["web"]), 30)
        return {"verified": True, "evidence_url": current["url"], "transport": "macOS_accessibility"}
    except RuntimeError as exc:
        return {"verified": False, "reason": str(exc), "transport": "macOS_accessibility"}


def page_summary(current=None):
    current = current or snapshot()
    # Rendered text only. Filter signed image URLs and browser toolbar data.
    lines = list(dict.fromkeys(text(n) for n in current["web"]
                               if n["role"] in {"AXStaticText", "AXHeading"} and visible(n) and text(n)))
    return {"url": current.get("page_url") or current["url"], "lines": lines[:180],
            "text": "\n".join(lines[:180]), "account": identity(current),
            "links": [{"text": text(n), "url": n["url"]} for n in current["web"]
                      if n["role"] == "AXLink" and n.get("url") and visible(n)],
            "structured": False, "transport": "macOS_accessibility",
            "restricted": any(re.search(r"暂未开通数据权限|暂无权限|申请开通", line) for line in lines),
            "login_required": any(re.search(r"扫码登录|手机号登录", line) for line in lines)}


def readiness():
    """Non-activating observation for Studio; never switch tabs or infer login."""
    current = snapshot()
    tabs = [n for n in current["nodes"] if n["role"] == "AXRadioButton" and (
        text(n).startswith("小红书创作服务平台") or re.search(r" - 小红书(?:搜索)?(?: -|$)", text(n)))]
    host = urlsplit(current.get("page_url") or "").hostname
    account = identity(current)
    own = [n for n in current["web"] if n["role"] == "AXLink" and text(n) == "我"
           and re.search(r"/user/profile/[a-f0-9]{24}", n.get("url") or "")]
    logged_in = True if account or (host == "www.xiaohongshu.com" and len(own) == 1) else None
    return {"available": bool(tabs), "transport": "macOS_accessibility",
            "loggedIn": logged_in, "account": account, "tabCount": len(tabs),
            "currentSiteObserved": host in {"www.xiaohongshu.com", "creator.xiaohongshu.com"}}


def open_area(area):
    from tools.xhs_manage import AREAS
    if area not in AREAS:
        raise ValueError("未知创作者区域")
    with _lock:
        if area == "messages":
            return read_messages()
        current = snapshot()
        if editor(current)["has_editor"]:
            raise RuntimeError("desktop_unsaved_editor_present")
        if urlsplit(current["url"]).hostname != "creator.xiaohongshu.com":
            current = select_site(True)
        if any(n["role"] == "AXHeading" and text(n) == "草稿箱" for n in current["web"]) and area != "drafts":
            navigate("https://creator.xiaohongshu.com/publish/publish?source=official")
            current = wait(lambda s: any(text(n) == "写长文" for n in s["web"]), 8)
        if area in {"drafts", "longform", "podcast"} and urlsplit(current["url"]).path != "/publish/publish":
            press_label(["发布笔记"])
            current = wait(lambda s: urlsplit(s.get("page_url") or "").path == "/publish/publish"
                           and any(text(n) == "写长文" for n in s["web"]), 8)
        labels = AREAS[area]
        if area == "drafts":
            labels = [text(n) for n in current["web"] if re.fullmatch(r"草稿箱[（(]\d+[）)]", text(n))]
        if area in {"overview", "audience", "note_metrics"}:
            labels = {"overview": ["账号概览"], "audience": ["粉丝数据"], "note_metrics": ["内容分析"]}[area]
            if not any(text(n) in labels for n in current["web"]):
                press_label(["数据看板"], current)
                current = wait(lambda s: any(text(n) in labels for n in s["web"]), 4)
        if area in {"longform", "podcast"}:
            current = publish_tab("写长文" if area == "longform" else "发播客")
            page = page_summary(current)
            return {"ok": True, "status": "ok", "area": area, "page": page,
                    "evidence_url": page["url"], "transport": "macOS_accessibility"}
        hits = [n for n in current["web"] if text(n) in labels and visible(n)]
        if not hits:
            return {"ok": False, "status": "unavailable", "area": area,
                    "reason": "当前账号网页未发现可操作入口", "page": page_summary(current)}
        before_url = current.get("page_url")
        before_lines = page_summary(current)["lines"]
        # The smallest matching element avoids parent/child duplicate labels.
        press_label(labels, current)
        current = wait(lambda s: s.get("page_url") != before_url or page_summary(s)["lines"] != before_lines
                       or (area == "notes" and bool(note_cards(s))), 8)
        if area in {"overview", "audience", "note_metrics"}:
            current = wait(lambda s: page_summary(s)["restricted"]
                           or (not any(re.search(r"数据加载中|加载中", text(n)) for n in s["web"])
                               and any(re.search(r"近7天|数据概览|累计粉丝|核心指标", text(n))
                                       for n in s["web"])), 15)
        elif area == "notes":
            current = wait_notes()
        page = page_summary(current)
        if area == "notes":
            page["notes"] = note_cards(current)
            page["structured"] = bool(page["notes"])
        return {"ok": not page["restricted"] and not page["login_required"],
                "status": "restricted" if page["restricted"] else "login_required" if page["login_required"] else "ok",
                "area": area, "page": page, "evidence_url": page["url"], "transport": "macOS_accessibility"}


def select_site(creator=False):
    current = snapshot()
    host = "creator.xiaohongshu.com" if creator else "www.xiaohongshu.com"
    if urlsplit(current.get("page_url") or "").hostname == host:
        return current
    match = (lambda n: n["role"] == "AXRadioButton" and (
        text(n).startswith("小红书创作服务平台") if creator else
        ("小红书" in text(n) and "创作服务平台" not in text(n))))
    tabs = [n for n in current["nodes"] if match(n) and visible(n)]
    if not creator and len(tabs) > 1:
        # Header profile links may open extra XHS tabs. Prefer an existing
        # message/notification/home tab; the destination is still verified below.
        preferred = [n for n in tabs if re.match(r"消息 -|通知 -|小红书 -", text(n))]
        tabs = preferred or [n for n in tabs if re.search(r" - 小红书(?:搜索)?(?: -|$)", text(n))]
        tabs.sort(key=lambda n: n["bounds"]["x"])
        tabs = tabs[:1]
    if len(tabs) != 1:
        raise RuntimeError("desktop_site_tab_ambiguous")
    tab = tabs[0]
    act("press", current, tab)
    return wait(lambda s: urlsplit(s.get("page_url") or "").hostname == host, 5)


def read_messages():
    select_site()
    current = snapshot()
    if not urlsplit(current.get("page_url") or "").path.startswith("/chat"):
        press_label(["消息"], current)
        current = wait(lambda s: urlsplit(s.get("page_url") or "").path == "/chat", 8)
    page = page_summary(current)
    return {"ok": not page["login_required"], "area": "messages", "page": page,
            "status": "login_required" if page["login_required"] else "ok",
            "evidence_url": page["url"], "structured": False,
            "message": "仅返回当前网页已渲染的会话文字；没有发送消息。"}


def notifications(kind="comments", pages=1):
    from tools.xhs_manage import NOTIFICATION_TABS
    if kind not in NOTIFICATION_TABS or not isinstance(pages, int) or not 1 <= pages <= 10:
        raise ValueError("kind/comments/likes/follows，pages 1–10")
    select_site()
    current = snapshot()
    if urlsplit(current.get("page_url") or "").path != "/notification":
        press_label(["通知"], current)
        current = wait(lambda s: urlsplit(s.get("page_url") or "").path == "/notification", 8)
    current = wait(lambda s: any(text(n) in NOTIFICATION_TABS[kind] for n in s["web"]), 8)
    press_label(NOTIFICATION_TABS[kind], current)
    # AX has no stable notification IDs. Return the observed page without claiming
    # zero notifications or reliable pagination.
    page = page_summary(snapshot())
    return {"ok": not page["login_required"], "kind": kind, "structured": False,
            "count": None, "notifications": [], "requested_pages": pages, "page": page,
            "evidence_url": page["url"],
            "reason": "desktop未提取稳定通知ID；当前页文字不等同于结构化分页结果"}


def descendants(current, ref):
    parents = {n["ref"]: n.get("parent") for n in current["web"]}
    def belongs(node):
        ancestor = node["ref"]
        while ancestor:
            if ancestor == ref:
                return True
            ancestor = parents.get(ancestor)
        return False
    return [n for n in current["web"] if belongs(n)]


def note_cards(current=None):
    """Read fields from an actual AX card ancestor, never join adjacent cards."""
    from tools.xhs_publish import VISIBILITY
    current = current or snapshot()
    nodes = {n["ref"]: n for n in current["web"]}
    timestamps = [n for n in current["web"] if n["role"] == "AXStaticText"
                  and re.fullmatch(r"\d{4}-\d\d-\d\d \d\d:\d\d", text(n)) and visible(n)]
    records = []
    for timestamp in timestamps:
        ancestor = timestamp.get("parent")
        while ancestor in nodes:
            children = descendants(current, ancestor)
            dates = [n for n in children if n["ref"] in {t["ref"] for t in timestamps}]
            settings = [n for n in children if text(n) in VISIBILITY.values() and visible(n)]
            if len(dates) == 1 and len(settings) == 1:
                statuses = {"审核中", "未通过", "已发布", "定时发布"}
                titles = [n for n in children if n["role"] == "AXStaticText" and visible(n)
                          and text(n) and text(n) not in statuses | set(VISIBILITY.values())
                          and not re.fullmatch(r"[\d.]+|\d{4}-\d\d-\d\d \d\d:\d\d", text(n))]
                if len(titles) == 1:
                    records.append({"note_id": None, "title": text(titles[0]),
                                    "published_at": text(timestamp), "visibility": text(settings[0]),
                                    "review_status": next((text(n) for n in children
                                                           if text(n) in statuses and visible(n)), None),
                                    "scheduled": any(text(n) == "定时发布" and visible(n) for n in children),
                                    "card_ref": ancestor, "snapshot_id": current["snapshot_id"]})
                    break
            ancestor = nodes[ancestor].get("parent")
    return records


def wait_notes(timeout=15):
    # “全部 0” is a placeholder while the request is pending. A rendered card or
    # an explicit empty-state message is required to interpret an empty list.
    return wait(lambda s: bool(note_cards(s)) or any(
        re.search(r"暂无笔记|暂无内容|还没有发布|没有找到相关笔记", text(n)) for n in s["web"]), timeout)


def target_card(current, title, published_at=None):
    records = [r for r in note_cards(current) if r["title"] == title
               and (published_at is None or r["published_at"] == published_at)]
    if len(records) != 1:
        raise RuntimeError("desktop_target_note_card_ambiguous")
    return records[0]


def card_control(card, label):
    """Hover observed icon refs until the page itself identifies their meaning."""
    current = snapshot()
    card = target_card(current, card["title"], card["published_at"])
    children = descendants(current, card["card_ref"])
    labelled = [n for n in children if text(n).strip() == label and visible(n)]
    if len(labelled) == 1:
        return act("press", current, labelled[0])
    group = next(n for n in children if n["ref"] == card["card_ref"])
    box = group["bounds"]
    candidates = [n for n in children if n["role"] == "AXImage" and visible(n)
                  and 14 <= n["bounds"]["width"] <= 28 and 14 <= n["bounds"]["height"] <= 28
                  and n["bounds"]["x"] > box["x"] + box["width"] * .7
                  and n["bounds"]["y"] < box["y"] + box["height"] * .3]
    # Keep coordinates only to correlate a tooltip with its observed icon.
    for candidate in candidates:
        current = snapshot()
        icons = [n for n in descendants(current, target_card(current, card["title"], card["published_at"])["card_ref"])
                 if n["role"] == "AXImage" and n.get("bounds") == candidate["bounds"]]
        if len(icons) != 1:
            raise RuntimeError("desktop_card_control_changed")
        act("hover", current, icons[0])
        center = lambda n: n["bounds"]["x"] + n["bounds"]["width"] / 2
        def bound_tooltip(s):
            return any(text(n).strip() == label and visible(n)
                       and abs(center(n) - center(candidate)) < 30
                       and abs(n["bounds"]["y"] - candidate["bounds"]["y"]) < 80 for n in s["web"])
        try:
            current = wait(bound_tooltip, 1.5)
        except RuntimeError:
            continue
        tooltip = choose(current, lambda n: text(n).strip() == label)
        icon = choose(current, lambda n: n["role"] == "AXImage" and n.get("bounds") == candidate["bounds"])
        if abs(center(icon) - center(tooltip)) > 30 or abs(icon["bounds"]["y"] - tooltip["bounds"]["y"]) > 80:
            raise RuntimeError("desktop_tooltip_not_bound_to_icon")
        return act("press", current, icon)
    raise RuntimeError("desktop_card_control_not_identified: " + label)


def inspect_note(card, expected_account, expected_id=None):
    from urllib.parse import parse_qs
    current = snapshot()
    if identity(current) != expected_account:
        raise RuntimeError("desktop_account_mismatch")
    card_control(card, "编辑")
    current = wait(lambda s: editor(s)["has_editor"]
                   and urlsplit(s.get("page_url") or "").path == "/publish/update", 12)
    query = parse_qs(urlsplit(current["page_url"]).query)
    ids = query.get("id", [])
    if len(ids) != 1 or not re.fullmatch(r"[a-fA-F0-9]{24}", ids[0]):
        raise RuntimeError("desktop_edit_note_id_not_observed")
    if expected_id and ids[0] != expected_id:
        raise RuntimeError("desktop_target_note_id_mismatch")
    if identity(current) != expected_account or editor(current)["title"] != card["title"]:
        raise RuntimeError("desktop_edit_identity_or_title_changed")
    return {"note_id": ids[0], "account": expected_account, "editor": editor(current),
            "evidence_url": current["page_url"], "card": {k: v for k, v in card.items()
                                                       if k not in {"card_ref", "snapshot_id"}}}


def verify_publication(spec, account, submitted_at, expected_id=None):
    from datetime import datetime
    from tools.xhs_publish import VISIBILITY, TIMEZONE, content_matches
    with _lock:
        navigate("https://creator.xiaohongshu.com/new/note-manager?source=official")
        current = wait_notes()
        if identity(current) != account:
            raise RuntimeError("desktop_account_mismatch")
        card = target_card(current, spec["title"])
        instant = datetime.strptime(card["published_at"], "%Y-%m-%d %H:%M").replace(tzinfo=TIMEZONE)
        submitted = datetime.fromisoformat(submitted_at).astimezone(TIMEZONE)
        scheduled = datetime.fromisoformat(spec["schedule_at"]).astimezone(TIMEZONE) if spec["schedule_at"] else None
        # During loading the manager can show creation time, then the scheduled
        # time. The persisted edit-page checkbox/date independently proves timing.
        if (abs((instant - submitted).total_seconds()) > 300
                and not (scheduled and abs((instant - scheduled).total_seconds()) < 60)):
            raise RuntimeError("desktop_publication_time_mismatch")
        if card["visibility"] != VISIBILITY[spec["visibility"]]:
            raise RuntimeError("desktop_publication_visibility_mismatch")
        result = inspect_note(card, account, expected_id)
        if not content_matches(spec, result["editor"]):
            raise RuntimeError("desktop_published_content_mismatch")
        schedule_available = any(text(n) == "定时发布" for n in snapshot()["web"])
        result["schedule_editor_available"] = schedule_available
        state = schedule_state() if schedule_available or scheduled else None
        if state and state["enabled"] != bool(scheduled):
            raise RuntimeError("desktop_saved_schedule_enabled_mismatch")
        if not scheduled and card["scheduled"]:
            raise RuntimeError("desktop_unexpected_scheduled_card")
        if scheduled:
            wanted = scheduled.strftime("%Y-%m-%d %H:%M")
            if not state["enabled"] or not state["input"] or state["input"]["value"] != wanted:
                raise RuntimeError("desktop_saved_schedule_mismatch")
            result["schedule"] = {"enabled": True, "control_value": wanted,
                                  "schedule_at": spec["schedule_at"], "verified": True}
        navigate("https://creator.xiaohongshu.com/new/note-manager?source=official",
                 discard_verified_editor=result["editor"])
        wait_notes()
        return {"verified": True, **result, "transport": "macOS_accessibility"}


def manage_note(args, task):
    """Desktop writes bind header, unique card fields and the actual edit-route ID."""
    import hashlib
    import json
    from pathlib import Path
    from tools.xhs_manage import _id
    from tools.xhs_publish import atomic_json, PublicationJournalError, content_matches
    account, title = args.get("expected_account"), args.get("target_title")
    note_id, action = _id(args.get("note_id")), args.get("action", "prepare_edit")
    if not account or not title:
        raise ValueError("desktop管理需要expected_account和target_title")
    if action not in {"prepare_edit", "edit", "delete", "visibility"}:
        raise ValueError("未知作品管理动作")
    if action != "prepare_edit":
        from tools.xhs_account import assert_writable
        assert_writable()
    if action == "visibility":
        from tools.xhs_publish import VISIBILITY
        if args.get("visibility") not in VISIBILITY:
            raise ValueError("visibility 必须是 self/public/friends")
    directory = task.ensure_task(hint="desktop_manage_note")
    path = Path(directory) / "management_attempts.json"
    try:
        states = json.loads(path.read_text()) if path.exists() else {}
        if not isinstance(states, dict) or any(not isinstance(v, dict) or
                                              type(v.get("verified")) is not bool for v in states.values()):
            raise ValueError("invalid_management_journal")
    except (OSError, ValueError) as exc:
        raise PublicationJournalError("管理操作记录不可读；禁止自动重复保存或删除。") from exc
    op = hashlib.sha256(json.dumps({k: v for k, v in args.items() if not k.startswith("_")},
                                  sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    if action != "prepare_edit" and op in states:
        return {"ok": states[op]["verified"], "status": "ok" if states[op]["verified"] else "unverified",
                "skipped": True, "previous": states[op], "attempt_id": op}
    from tools import xhs_test_control as control
    if action != "prepare_edit":
        control.preflight(owner=args.get("owner_id"), account=account)
        control.verify_browser(owner=args.get("owner_id"), account=account)
    with _lock:
        current = snapshot()
        if editor(current)["has_editor"]:
            raise RuntimeError("desktop_unsaved_editor_present")
        open_area("notes")
        current = wait_notes()
        card = target_card(current, title, args.get("published_at"))
        inspected = inspect_note(card, account, note_id)
        if action == "prepare_edit":
            return {"ok": True, "status": "prepared", "submitted": False, **inspected}
        if action == "delete":
            navigate("https://creator.xiaohongshu.com/new/note-manager?source=official",
                     discard_verified_editor=inspected["editor"])
            current = wait_notes()
            card = target_card(current, title, card["published_at"])
            if identity(current) != account:
                raise RuntimeError("desktop_account_mismatch")
            card_control(card, "删除")
            warning = "删除后将无法恢复，确定要删除《" + title + "》这篇笔记吗"
            current = wait(lambda s: any(text(n) == warning and visible(n) for n in s["web"]), 5)
            message = choose(current, lambda n: text(n) == warning)
            parents = {n["ref"]: n for n in current["web"]}
            ancestor = message.get("parent")
            controls = []
            while ancestor in parents:
                controls = descendants(current, ancestor)
                if any(text(n) == "删除笔记" for n in controls) and any(
                        n["role"] == "AXButton" and text(n) == "确定" for n in controls):
                    break
                ancestor = parents[ancestor].get("parent")
            if ancestor not in parents or ancestor == next(
                    (n["ref"] for n in current["web"] if n["role"] == "AXWebArea"), None):
                raise RuntimeError("desktop_delete_dialog_not_identified")
            confirms = [n for n in controls if text(n).strip() in {"删除", "确认删除", "确定"}
                        and n["role"] == "AXButton" and visible(n)]
            if len(confirms) != 1:
                raise RuntimeError("desktop_delete_confirmation_ambiguous")
            control.reserve("write", "manage:" + op, account=account)
            states[op] = {"verified": False, "note_id": note_id, "account": account, "action": action}
            atomic_json(path, states)
            act("press", current, confirms[0])
            try:
                wait(lambda s: not any(text(n) == warning and visible(n) for n in s["web"])
                     and not any(r["title"] == title and r["published_at"] == card["published_at"]
                                 for r in note_cards(s)), 10)
                # Reopen the manager independently, not just the optimistic card removal.
                navigate("https://creator.xiaohongshu.com/publish/publish?source=official")
                navigate("https://creator.xiaohongshu.com/new/note-manager?source=official")
                current = wait_notes()
                verified = identity(current) == account and not any(
                    r["title"] == title and r["published_at"] == card["published_at"] for r in note_cards(current))
            except Exception as exc:
                verified = False
                states[op]["reason"] = str(exc)
        else:
            title_value = inspected["editor"]["title"] if action == "visibility" else args.get(
                "title", inspected["editor"]["title"])
            desc = inspected["editor"]["plain_desc"] if action == "visibility" else args.get(
                "desc", inspected["editor"]["plain_desc"])
            if not isinstance(title_value, str) or not isinstance(desc, str) or not title_value.strip() or not desc.strip():
                raise ValueError("修改标题正文不能为空")
            if len(title_value.encode("utf-16-le")) // 2 > 20 or len(desc.encode("utf-16-le")) // 2 > 1000:
                raise ValueError("修改标题或正文超长")
            spec = {"title": title_value, "desc": desc, "tags": inspected["editor"]["chips"]}
            if action == "visibility":
                visibility_evidence = set_visibility(args["visibility"])
            else:
                fill_editor(spec)
            current = snapshot()
            # Actual update page has a “发布” save button; only this ID-bound route
            # may use it. The fresh-note route must never be accepted here.
            from urllib.parse import parse_qs
            if (urlsplit(current["page_url"]).path != "/publish/update"
                    or parse_qs(urlsplit(current["page_url"]).query).get("id") != [note_id]
                    or identity(current) != account or not content_matches(spec, editor(current))):
                raise RuntimeError("desktop_edit_target_or_contents_changed")
            button = choose(current, lambda n: n["role"] == "AXButton"
                            and text(n) in {"发布", "保存修改", "确认修改", "更新笔记"})
            control.reserve("write", "manage:" + op, account=account)
            states[op] = {"verified": False, "note_id": note_id, "account": account, "action": action}
            if action == "visibility":
                states[op]["visibility"] = args["visibility"]
                states[op]["prepared_visibility"] = visibility_evidence
            atomic_json(path, states)
            act("press", current, button)
            try:
                wait(lambda s: not editor(s)["has_editor"] and (
                    "/note-manager" in (s.get("page_url") or "") or
                    any(re.search(r"发布成功|修改成功|更新成功", text(n)) for n in s["web"])), 30)
                navigate("https://creator.xiaohongshu.com/new/note-manager?source=official")
                current = wait_notes()
                revised = target_card(current, title_value, card["published_at"])
                expected_visibility = (VISIBILITY[args["visibility"]] if action == "visibility"
                                       else card["visibility"])
                if revised["visibility"] != expected_visibility:
                    raise RuntimeError("desktop_saved_visibility_mismatch")
                after = inspect_note(revised, account, note_id)
                verified = content_matches(spec, after["editor"])
                navigate("https://creator.xiaohongshu.com/new/note-manager?source=official",
                         discard_verified_editor=after["editor"])
                wait_notes()
                states[op]["independent_verification"] = after
            except Exception as exc:
                verified = False
                states[op]["reason"] = str(exc)
        states[op]["verified"] = bool(verified)
        atomic_json(path, states)
        task.append_trace("desktop_manage_result", states[op])
        return {"ok": bool(verified), "status": "ok" if verified else "unverified",
                "note_id": note_id, "attempt_id": op, "result": states[op]}
