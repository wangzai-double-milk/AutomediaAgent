"""Image/video publishing with readback, schedules and persistent submission guards.

Uses existing CDP bridge primitives; no private signed API or cookie extraction.
"""

import hashlib
import json
import os
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from tools import xhs_browser as browser

TIMEZONE = ZoneInfo("Asia/Shanghai")
VISIBILITY = {"self": "仅自己可见", "public": "公开可见", "friends": "仅互关好友可见"}
TITLE_SELECTOR = 'input[placeholder*="标题"]'
EDITOR_SELECTOR = 'div.tiptap.ProseMirror,div.ProseMirror,div.ql-editor,[role="textbox"][contenteditable="true"]'


class PublicationJournalError(RuntimeError):
    """Submission history cannot be trusted; the caller must stop automatic work."""


def normalize(args, now=None):
    title, desc = str(args.get("title") or "").strip(), str(args.get("desc") or "").strip()
    if not title or not desc:
        raise ValueError("title / desc 必填")
    if len(title.encode("utf-16-le")) // 2 > 20:
        raise ValueError("标题超过 20 个 UTF-16 字符，请压缩标题")
    if len(desc.encode("utf-16-le")) // 2 > 1000:
        raise ValueError("正文超过 1000 个 UTF-16 字符")
    video, audio = args.get("video"), args.get("audio")
    longform = args.get("longform_body")
    images = args.get("images") or []
    if not isinstance(images, list) or sum(bool(v) for v in (video, audio, longform, images)) != 1:
        raise ValueError("images/video/audio/longform_body 四选一")
    if longform:
        if not isinstance(longform, str) or not longform.strip() or len(longform) > 16000:
            raise ValueError("longform_body 必须为1–16000字符文本；页面限制仍须回读")
        files = []
    else:
        files = [audio, args.get("cover")] if audio else [video] if video else images
    if len(files) > 18 or not all(isinstance(p, str) and os.path.isabs(p) and os.path.isfile(p) for p in files):
        raise ValueError("媒体须为存在的绝对路径；播客须同时提供audio和cover")
    if any(os.path.getsize(p) == 0 for p in files):
        raise ValueError("媒体文件为空")
    if video and Path(video).suffix.lower() not in {".mp4", ".mov", ".m4v", ".webm"}:
        raise ValueError("不支持的视频后缀")
    image_files = files[1:] if audio else [] if video else files
    if any(Path(p).suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"} for p in image_files):
        raise ValueError("图片仅支持 png/jpg/jpeg/webp")
    if (video and os.path.getsize(video) > 20 * 1024**3) or any(
            os.path.getsize(p) > 32 * 1024**2 for p in image_files):
        raise ValueError("视频最大 20GB，单张图片最大 32MB")
    if audio:
        if Path(audio).suffix.lower() not in {".m4a", ".mp3", ".wav", ".flac", ".aac"}:
            raise ValueError("播客支持m4a/mp3/wav/flac/aac")
        if os.path.getsize(audio) > 1024**3:
            raise ValueError("播客音频不超过1GB")
        from tools.xhs_formats import audio_duration
        duration = audio_duration(audio)
        if not 600 <= duration <= 7200:
            raise ValueError("播客音频必须为10分钟至2小时")
    visibility = args.get("visibility", "self")
    if visibility not in VISIBILITY:
        raise ValueError("visibility 必须是 self/public/friends")
    tags = args.get("tags") or []
    if not isinstance(tags, list) or not all(isinstance(t, str) and t.strip().lstrip("#") for t in tags):
        raise ValueError("tags 必须是非空话题字符串列表")
    tags = list(dict.fromkeys(t.strip().lstrip("#") for t in tags))
    if len(tags) > 10:
        raise ValueError("最多 10 个话题")
    schedule = args.get("schedule_at")
    if schedule:
        try:
            when = datetime.fromisoformat(schedule.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            raise ValueError("schedule_at 必须是 ISO 8601 时间")
        if when.tzinfo is None:
            when = when.replace(tzinfo=TIMEZONE)
        when = when.astimezone(TIMEZONE).replace(second=0, microsecond=0)
        current = now or datetime.now(TIMEZONE)
        if current.tzinfo is None:
            current = current.replace(tzinfo=TIMEZONE)
        if not current + timedelta(hours=1) <= when <= current + timedelta(days=14):
            raise ValueError("定时发布必须在当前时间 1 小时至 14 天内（北京时间）")
        schedule = when.isoformat()
    result = {"title": title, "desc": desc, "tags": tags, "files": files,
              "kind": "podcast" if audio else "longform" if longform else "video" if video else "image",
              "visibility": visibility, "schedule_at": schedule}
    if longform:
        result["longform_body"] = longform.strip()
    if audio:
        result["audio_duration"] = duration
    return result


def fingerprint(spec):
    metadata = {k: v for k, v in spec.items() if k != "files"}
    digest = hashlib.sha256(json.dumps(metadata, sort_keys=True, ensure_ascii=False).encode())
    for path in spec["files"]:
        item = hashlib.sha256()
        with open(path, "rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                item.update(chunk)
        digest.update(item.digest())
    return digest.hexdigest()


def legacy_fingerprint(spec):
    """Retain the original image/self journal identity during adapter migration."""
    if spec["kind"] != "image" or spec["visibility"] != "self" or spec["schedule_at"]:
        return None
    digest = hashlib.sha256(json.dumps(
        [spec["title"], spec["desc"], ["#" + tag for tag in spec["tags"]]], ensure_ascii=False).encode())
    for path in spec["files"]:
        with open(path, "rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    return digest.hexdigest()


def select_transport(requested="auto"):
    if requested not in {"auto", "cdp", "desktop"}:
        raise ValueError("transport 必须是 auto/cdp/desktop")
    if requested != "auto":
        return requested
    import ga
    sessions = ga.driver.get_all_sessions() if ga.driver is not None else []
    from urllib.parse import urlsplit
    if any(urlsplit(str(item.get("url") or "")).hostname in
           {"www.xiaohongshu.com", "creator.xiaohongshu.com"} for item in sessions):
        return "cdp"
    from tools import xhs_desktop
    current = xhs_desktop.snapshot()
    if urlsplit(current["url"]).hostname not in {"www.xiaohongshu.com", "creator.xiaohongshu.com"}:
        if not xhs_desktop.readiness()["available"]:
            raise RuntimeError("desktop_xhs_tab_not_observed")
    return "desktop"


def atomic_json(path, data):
    """Persist before side effects, so crash recovery never automatically resubmits."""
    path = Path(path)
    fd, temp = tempfile.mkstemp(prefix=".state-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def editor_snapshot(tab):
    return browser.execute("""(function(){
      const visible=e=>e.getClientRects().length;
      const title=Array.from(document.querySelectorAll(%s)).find(visible);
      const editor=Array.from(document.querySelectorAll(%s)).find(visible);
      const topicSelector=
        '[data-type="topic"],[data-type="mention"],[data-topic-id],a[href*="/search_result"],[class*="topic"],[class*="mention"]';
      const chips=editor?Array.from(editor.querySelectorAll(
        '[data-type="topic"],[data-type="mention"],[data-topic-id],a[href*="/search_result"],[class*="topic"],[class*="mention"]'))
        .map(e=>(e.innerText||e.textContent||'').trim().replace(/^#/,'')):[];
      const clone=editor?.cloneNode(true);
      if(clone)clone.querySelectorAll(topicSelector).forEach(e=>e.remove());
      const busy=Array.from(document.querySelectorAll('[class*="upload"],[class*="progress"],[class*="transcod"]'))
        .filter(visible).some(e=>/上传中|处理中|转码中/.test(e.innerText||''));
      const errors=Array.from(document.querySelectorAll('[role="alert"],[class*="error"],[class*="toast"]'))
        .filter(visible).map(e=>(e.innerText||'').trim()).filter(t=>/失败|错误|超出|不支持/.test(t));
      return JSON.stringify({title:title?.value||'',desc:editor?.innerText||'',
        plain_desc:clone?.textContent||'',chips,busy,errors,
        has_editor:!!title&&!!editor,url:location.href});
    })()""" % (json.dumps(TITLE_SELECTOR), json.dumps(EDITOR_SELECTOR)), tab)


def fill_editor(spec, tab):
    result = browser.execute("""(function(title,desc){
      const visible=e=>e.getClientRects().length;
      const input=Array.from(document.querySelectorAll(%s)).find(visible);
      const ed=Array.from(document.querySelectorAll(%s)).find(visible);
      if(!input||!ed)return JSON.stringify({ok:false,reason:'editor_missing'});
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(input,title);
      input.dispatchEvent(new Event('input',{bubbles:true}));input.dispatchEvent(new Event('change',{bubbles:true}));
      ed.focus();const range=document.createRange();range.selectNodeContents(ed);
      const selection=window.getSelection();selection.removeAllRanges();selection.addRange(range);
      const inserted=document.execCommand('insertText',false,desc);
      ed.dispatchEvent(new Event('input',{bubbles:true}));
      return JSON.stringify({ok:inserted});
    })(%s,%s)""" % (json.dumps(TITLE_SELECTOR), json.dumps(EDITOR_SELECTOR),
                    json.dumps(spec["title"]), json.dumps(spec["desc"])), tab)
    if not isinstance(result, dict) or not result.get("ok"):
        raise RuntimeError("editor_fill_failed")
    for word in spec["tags"]:
        _add_topic(word, tab)
    snapshot = browser.wait(lambda: editor_snapshot(tab), lambda v: content_matches(spec, v), 5)
    if not snapshot:
        raise RuntimeError("editor_readback_mismatch")
    return snapshot


def content_matches(spec, snapshot):
    if not isinstance(snapshot, dict) or snapshot.get("errors") or snapshot.get("busy"):
        return False
    # Rich editors convert line breaks and spaces. Preserve all non-whitespace text.
    compact = lambda text: "".join(text.split())
    return (snapshot.get("has_editor") and snapshot.get("title") == spec["title"]
            and compact(spec["desc"]) == compact(snapshot.get("plain_desc", snapshot.get("desc", "")))
            and set(spec["tags"]) <= set(snapshot.get("chips") or []))


def _add_topic(word, tab):
    from ga import cdp_type_text
    focused = browser.execute("""(function(){
      const ed=Array.from(document.querySelectorAll(%s)).find(e=>e.getClientRects().length);
      if(!ed)return false;ed.focus();const range=document.createRange();range.selectNodeContents(ed);range.collapse(false);
      const sel=window.getSelection();sel.removeAllRanges();sel.addRange(range);return true;
    })()""" % json.dumps(EDITOR_SELECTOR), tab)
    if focused is not True:
        raise RuntimeError("topic_editor_missing")
    cdp_type_text(" #" + word, switch_tab_id=tab)
    def pick():
        return browser.click_text(["#" + word, word], tab,
                                  '[role="listbox"],[class*="suggest"],[class*="topic-container"],[class*="mention-list"]')
    picked = browser.wait(pick, lambda v: isinstance(v, dict) and v.get("ok"), 5)
    if not picked:
        raise RuntimeError("topic_candidate_not_found: " + word)
    if not browser.wait(lambda: editor_snapshot(tab),
                        lambda v: isinstance(v, dict) and word in v.get("chips", []), 4):
        raise RuntimeError("topic_chip_not_verified: " + word)


def schedule_snapshot(tab):
    return browser.execute("""(function(){
      const roots=Array.from(document.querySelectorAll('.post-time-wrapper'))
        .filter(e=>e.getClientRects().length);
      if(roots.length!==1)return JSON.stringify({ok:false,reason:'schedule_row_ambiguous'});
      const sw=roots[0].querySelector('.d-switch,[role="switch"],input[type="checkbox"]');
      if(!sw)return JSON.stringify({ok:false,reason:'schedule_switch_missing'});
      const checkbox=sw.matches('input')?sw:sw.querySelector('input[type="checkbox"]');
      const checked=checkbox?checkbox.checked:sw.getAttribute('aria-checked');
      const enabled=typeof checked==='boolean'?checked:checked==='true'?true:checked==='false'?false:null;
      const input=document.querySelector('.date-picker-container input');
      const r=sw.getBoundingClientRect();
      return JSON.stringify({ok:enabled!==null,enabled,value:input?.value||'',
        x:r.x+r.width/2,y:r.y+r.height/2,visible:r.width>0&&r.height>0});
    })()""", tab)


def set_schedule(schedule, tab):
    from ga import cdp_mouse_click
    wanted = bool(schedule)
    state = schedule_snapshot(tab)
    if not isinstance(state, dict) or not state.get("ok"):
        raise RuntimeError("schedule_state_unavailable")
    if state["enabled"] != wanted:
        browser.execute("""document.querySelector('.post-time-wrapper .d-switch')?.scrollIntoView({block:'center'});true""", tab)
        state = schedule_snapshot(tab)
        if not state.get("visible"):
            raise RuntimeError("schedule_switch_not_visible")
        clicked = cdp_mouse_click(state["x"], state["y"], switch_tab_id=tab)
        if isinstance(clicked, dict) and clicked.get("error"):
            raise RuntimeError("schedule_toggle_failed")
        if not browser.wait(lambda: schedule_snapshot(tab),
                            lambda s: isinstance(s, dict) and s.get("ok") and s["enabled"] == wanted, 4):
            raise RuntimeError("schedule_enabled_readback_mismatch")
    if not wanted:
        return {"ok": True, "schedule_at": None, "enabled": False, "verified": True}
    when = datetime.fromisoformat(schedule).astimezone(TIMEZONE)
    requested = when.strftime("%Y-%m-%d %H:%M")
    result = browser.execute("""(function(value){
      const inputs=Array.from(document.querySelectorAll(
        '.date-picker-container input,input[placeholder*="日期"],input[placeholder*="时间"],input[type="datetime-local"]'))
        .filter(e=>e.getClientRects().length);
      if(inputs.length!==1)return JSON.stringify({ok:false,reason:'schedule_input_ambiguous'});
      const el=inputs[0];const next=el.type==='datetime-local'?value.replace(' ','T'):value;
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(el,next);
      el.dispatchEvent(new Event('input',{bubbles:true}));el.dispatchEvent(new Event('change',{bubbles:true}));
      el.dispatchEvent(new Event('blur',{bubbles:true}));
      return JSON.stringify({ok:el.value===next,value:el.value});
    })(%s)""" % json.dumps(requested), tab)
    if not isinstance(result, dict) or not result.get("ok"):
        raise RuntimeError("schedule_input_not_verified")
    # Persisted control value must survive re-read after the framework render.
    if not browser.wait(lambda: schedule_snapshot(tab),
      lambda s: isinstance(s, dict) and s.get("enabled") is True and str(s.get("value")).replace("T", " ") == requested, 4):
        raise RuntimeError("schedule_readback_mismatch")
    return {"ok": True, "schedule_at": schedule, "control_value": requested, "enabled": True, "verified": True}


def prepare(spec, tab):
    from ga import cdp_upload_files_to_input, cdp_set_xhs_visibility
    url = "https://creator.xiaohongshu.com/publish/publish?source=official&from=menu&target=" + spec["kind"]
    browser.navigate(url, tab)
    uploaded = cdp_upload_files_to_input(spec["files"], switch_tab_id=tab, media_kind=spec["kind"])
    if not uploaded.get("ok"):
        raise RuntimeError("upload_failed: " + str(uploaded.get("reason")))
    timeout = 180 if spec["kind"] == "video" else 30
    ready = browser.wait(lambda: editor_snapshot(tab),
                         lambda v: isinstance(v, dict) and v.get("has_editor")
                         and not v.get("busy") and not v.get("errors"), timeout)
    if not ready:
        raise RuntimeError("upload_processing_timeout")
    snapshot = fill_editor(spec, tab)
    visibility = cdp_set_xhs_visibility(spec["visibility"], switch_tab_id=tab)
    if not visibility.get("ok") or visibility.get("now") != VISIBILITY[spec["visibility"]]:
        raise RuntimeError("visibility_not_verified")
    schedule = set_schedule(spec["schedule_at"], tab)
    return {"snapshot": snapshot, "visibility": visibility, "schedule": schedule}


def publish(args, task, preview=False):
    from ga import cdp_find_xhs_publish_bar_btn, cdp_mouse_click, wait_xhs_publish_result
    spec = normalize(args)
    directory = task.get_current()
    if not directory:
        raise ValueError("请先调用 xhs_task_init")
    attempt_id = fingerprint(spec)
    path = Path(directory) / "publication_attempts.json"
    try:
        states = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        if not isinstance(states, dict) or any(not isinstance(value, dict) or
            type(value.get("verified")) is not bool for value in states.values()):
            raise ValueError("invalid_publication_journal")
    except (OSError, ValueError) as exc:
        raise PublicationJournalError("发布记录不可读，请核对创作中心；禁止自动重发。") from exc
    previous = states.get(attempt_id) or states.get(legacy_fingerprint(spec))
    draft = bool(args.get("save_draft"))
    if previous and not preview and not draft:
        return {"ok": bool(previous.get("verified")), "status": "ok" if previous.get("verified") else "unverified",
                "skipped": True, "attempt_id": attempt_id, "previous": previous,
                "message": "已有提交记录；未重复发布。"}
    from tools.xhs_account import assert_writable
    assert_writable()
    from tools import xhs_test_control as control
    control.preflight("draft" if draft or preview else "publish", args.get("owner_id"),
                      args.get("expected_account"), args.get("transport", "auto"))
    bound_account = ((control.status().get("binding") or {}).get("nickname")
                     if control.status().get("enabled") else args.get("expected_account"))
    control.verify_browser(args.get("owner_id"), bound_account)
    task.append_trace("publish_start", {**spec, "desc_len": len(spec["desc"]), "attempt_id": attempt_id})
    transport = ("desktop" if control.status().get("enabled") else
                 select_transport(args.get("transport", "auto")))
    from tools import xhs_desktop
    if transport == "desktop":
        evidence = xhs_desktop.prepare(spec, bound_account)
        tab = None
    else:
        if spec["kind"] in {"longform", "podcast"}:
            raise RuntimeError("CDP_longform_podcast_not_validated; use_desktop")
        if draft:
            raise RuntimeError("CDP_save_draft_not_validated; use_desktop")
        if args.get("expected_account"):
            # Until the CDP header has been observed and validated, do not silently
            # drop an account binding when auto selects a connected CDP session.
            raise RuntimeError("CDP_expected_account_not_validated; use_desktop")
        tab = browser.connect(creator=True)
        evidence = prepare(spec, tab)
    task.append_trace("publish_prepared", {"attempt_id": attempt_id, "kind": spec["kind"], "preview": preview,
                                          "visibility": spec["visibility"], "schedule_at": spec["schedule_at"]})
    if draft:
        result = xhs_desktop.save_draft(spec)
        atomic_json(Path(directory) / "publication_draft.json", {
            "attempt_id": attempt_id, "spec": spec, "evidence": evidence, "result": result})
        task.append_trace("draft_saved", result)
        return {**result, "attempt_id": attempt_id}
    if preview:
        atomic_json(Path(directory) / "publication_preview.json", {
            "attempt_id": attempt_id, "spec": spec, "tab_id": tab, "evidence": evidence})
        return {"ok": True, "status": "prepared", "submitted": False, "saved_as_draft": False,
                "attempt_id": attempt_id, "tab_id": tab, "transport": transport, **evidence,
                "message": "页面已预填并回读验证；尚未点击发布，也未保存草稿。"}
    if not content_matches(spec, xhs_desktop.editor() if transport == "desktop" else editor_snapshot(tab)):
        raise RuntimeError("editor_changed_before_submit")
    button = cdp_find_xhs_publish_bar_btn(switch_tab_id=tab) if transport == "cdp" else {"ok": True}
    if not button.get("ok"):
        raise RuntimeError("publish_button_unavailable: " + str(button.get("reason")))
    receipt = control.reserve("publish", "publish:" + attempt_id, account=evidence.get("account"))
    states[attempt_id] = {"verified": False, "state": "submitting", "tab_id": tab,
                          "title": spec["title"], "visibility": spec["visibility"],
                          "kind": spec["kind"], "schedule_at": spec["schedule_at"],
                          "spec": spec,
                          "transport": transport, "account": evidence.get("account"),
                          "test_receipt": receipt,
                          "submitted_at": datetime.now(TIMEZONE).isoformat()}
    atomic_json(path, states)
    try:
        if transport == "desktop":
            acknowledgement = xhs_desktop.submit(spec, evidence["account"])
        else:
            clicked = cdp_mouse_click(button["x"], button["y"], switch_tab_id=tab)
            if isinstance(clicked, dict) and clicked.get("error"):
                raise RuntimeError(str(clicked["error"]))
            acknowledgement = wait_xhs_publish_result(switch_tab_id=tab, timeout=30)
    except Exception as exc:
        acknowledgement = {"verified": False, "reason": str(exc)}
    verified = bool(acknowledgement.get("verified"))
    independent = None
    if transport == "desktop" and verified:
        try:
            independent = xhs_desktop.verify_publication(
                spec, evidence["account"], states[attempt_id]["submitted_at"])
            atomic_json(Path(directory) / "publication_independent_verification.json", independent)
        except Exception as exc:
            independent = {"verified": False, "reason": str(exc)}
        verified = bool(independent.get("verified"))
    states[attempt_id].update(verified=verified, state="submitted" if verified else "uncertain",
                             acknowledgement=acknowledgement, independent_verification=independent,
                             note_id=independent.get("note_id") if independent else None)
    atomic_json(path, states)
    task.append_trace("publish_result", states[attempt_id])
    return {"ok": verified, "status": "ok" if verified else "unverified", "attempt_id": attempt_id,
            "visibility": spec["visibility"], "schedule_at": spec["schedule_at"],
            "kind": spec["kind"], "acknowledgement": acknowledgement,
            "independent_verification": independent,
            "note_id": independent.get("note_id") if independent else None,
            "message": ("已从作品管理和编辑页独立核对账号、笔记ID、标题正文、时间及可见性。"
                        if independent and verified else "已观察到发布成功信号。") if verified
            else "提交结果未确认；请读取作品管理页核对，禁止重发。"}
