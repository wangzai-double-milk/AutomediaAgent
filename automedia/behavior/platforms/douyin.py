"""Bounded Douyin browsing through the project's existing browser bridge.

Browser-search route and result shape adapted from MIT douyin-mcp:
pazwusimple-netizen/douyin-mcp@ab9eb3b (src/browser_search.py).
See docs/DOUYIN_UPSTREAM_LICENSE.txt. No upstream cookies, signing, stealth,
private requests, download rewriting or verification retry code is included.
"""
from __future__ import annotations

import json
import base64
from pathlib import Path
import re
import time
from urllib.parse import parse_qs, quote, unquote, urlsplit
import uuid

from tools import research_trace

from automedia.paths import RESOURCE_ROOT
DOM_READER = (RESOURCE_ROOT / "tools/douyin_dom.js").read_text(encoding="utf-8")
ACTIONS = {"status", "feed", "search", "read", "open", "next", "resume"}
BLOCKED = {"verification_required", "restricted", "login_required", "wrong_page"}
RESEARCH_STOPS = {"live_preview", "unidentified_player"}


class BrowseError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def allowed_url(value):
    if not isinstance(value, str) or len(value) > 3000:
        return False
    try:
        u = urlsplit(value)
        return (u.scheme == "https" and u.hostname == "www.douyin.com"
                and not u.username and not u.password and u.port in (None, 443)
                and bool(re.fullmatch(r"/(?:|jingxuan/?|search/[^/]+/?|video/\d{5,25}/?|user/[A-Za-z0-9_-]+/?)", u.path)))
    except ValueError:
        return False


def video_id(value):
    if not allowed_url(value):
        return None
    u = urlsplit(value)
    match = re.fullmatch(r"/video/(\d{5,25})/?", u.path)
    candidate = match[1] if match else parse_qs(u.query).get("modal_id", [""])[0]
    return candidate if re.fullmatch(r"\d{5,25}", candidate) else None


def search_url(keyword):
    if not isinstance(keyword, str) or not keyword.strip() or len(keyword) > 100:
        raise BrowseError("invalid_arguments", "搜索词必须为1–100个字符")
    # Adapted public browser route from upstream BrowserSearchProvider.search.
    return "https://www.douyin.com/search/" + quote(keyword.strip(), safe="") + "?type=video"


class Bridge:
    """All calls are pinned; a disconnected Douyin tab never falls back to XHS."""
    def __init__(self, tab_id=None):
        import ga
        if ga.driver is None:
            ga.first_init_driver()
        self.ga = ga
        sessions = ga.driver.get_all_sessions()
        choices = [s for s in sessions if allowed_url(s.get("url", ""))]
        if tab_id:
            selected = next((s for s in choices if str(s["id"]) == str(tab_id)), None)
            if not selected:
                raise BrowseError("wrong_tab", "指定标签不是已连接的抖音浏览页")
        else:
            pinned = ga._browser_session.get()
            selected = next((s for s in choices if s["id"] == pinned), None)
            if not selected:
                if len(choices) != 1:
                    raise BrowseError("select_tab" if choices else "no_tab",
                                      "请先在Chrome打开抖音；多个抖音标签时用web_scan查看并指定tab_id")
                selected = choices[0]
        self.tab = str(selected["id"])
        ga._browser_session.set(self.tab)

    def execute(self, script):
        result = self.ga.web_execute_js(script, switch_tab_id=self.tab, no_monitor=True)
        if not isinstance(result, dict) or result.get("status") == "error" or "js_return" not in result:
            raise BrowseError("browser_unavailable", "浏览器未返回结果；保留页面，检查桥接后再试")
        value = result["js_return"]
        try:
            return json.loads(value) if isinstance(value, str) else value
        except ValueError:
            raise BrowseError("invalid_browser_response", "浏览器返回结构不正确")

    def read(self, limit):
        result = self.execute(f"JSON.stringify(({DOM_READER})({limit}))")
        if not isinstance(result, dict):
            raise BrowseError("invalid_browser_response", "抖音页面尚未返回可读信息")
        return result

    def capture(self):
        result = self.ga.cdp_send("Page.captureScreenshot", {"format": "png"}, switch_tab_id=self.tab)
        try:
            raw = base64.b64decode(result if isinstance(result, str) else result["data"], validate=True)
            if not raw.startswith(b"\x89PNG\r\n\x1a\n"):
                raise ValueError()
            return raw
        except (KeyError, TypeError, ValueError):
            raise BrowseError("browser_unavailable", "未取得有效页面截图")

    def navigate(self, before, target):
        if not allowed_url(target):
            raise BrowseError("invalid_url", "只支持抖音官方网页浏览地址")
        # Recheck exact page before changing it; don't overwrite a user's other tab.
        result = self.execute("""JSON.stringify((()=>{
          if(location.href!==%s)return {ok:false};
          const observed=(%s)(1);
          if(!observed.ok)return {ok:false};
          if(%s && ((observed.media?.video_id||observed.media?.player_key)!==%s
             || observed.media?.content_kind==='live_preview'))return {ok:false};
          if(document.querySelector('[role="dialog"] input,[contenteditable="true"]:focus,textarea:focus'))
            return {ok:false};
          location.href=%s;return {ok:true};
        })())""" % (json.dumps(before["url"]), DOM_READER,
                    json.dumps(bool(_identity(before))), json.dumps(_identity(before)), json.dumps(target)))
        if not result or not result.get("ok"):
            raise BrowseError("page_changed", "页面已变化或正在编辑，请保留当前内容并重新读取")

    def next(self, before):
        check = self.execute("""JSON.stringify((()=>{
          const e=document.activeElement;
          const editing=e&&(e.isContentEditable||/^(INPUT|TEXTAREA|SELECT)$/.test(e.tagName));
          const observed=(%s)(1);
          return {ok:location.href===%s&&observed.ok
            &&!observed.idle_pause
            &&observed.media?.content_kind!=='live_preview'
            &&(observed.media?.video_id||observed.media?.player_key)===%s
            &&!editing&&!document.querySelector('[role="dialog"]')};
        })())""" % (DOM_READER, json.dumps(before["url"]), json.dumps(_identity(before))))
        if not check or not check.get("ok"):
            raise BrowseError("page_changed", "页面变化、弹层或输入焦点存在，未翻页")
        for kind in ("keyDown", "keyUp"):
            result = self.ga.cdp_send("Input.dispatchKeyEvent", {
                "type": kind, "key": "ArrowDown", "code": "ArrowDown",
                "windowsVirtualKeyCode": 40, "nativeVirtualKeyCode": 40,
            }, switch_tab_id=self.tab)
            if not isinstance(result, dict) or result.get("error"):
                raise BrowseError("browser_unavailable", "翻页输入未被确认，不自动重复")

    def resume(self, before):
        result = self.execute("""JSON.stringify((()=>{
          const observed=(%s)(1);
          if(location.href!==%s||!observed.ok||!observed.idle_pause||
            (observed.media?.video_id||observed.media?.player_key)!==%s)
            return {ok:false};
          const visible=e=>{const r=e.getBoundingClientRect(),s=getComputedStyle(e);
            return r.width>2&&r.height>2&&s.visibility!=='hidden'&&s.display!=='none';};
          const all=[...document.querySelectorAll('button,[role="button"],div,span')]
            .filter(e=>visible(e)&&(e.innerText||'').trim()==='继续播放');
          const leaves=all.filter(e=>!all.some(other=>other!==e&&e.contains(other)));
          if(leaves.length!==1)return {ok:false};
          leaves[0].click();return {ok:true};
        })())""" % (DOM_READER, json.dumps(before["url"]), json.dumps(_identity(before))))
        if not result or not result.get("ok"):
            raise BrowseError("page_changed", "当前暂停提示或继续播放控件不唯一，未点击")


def _identity(snapshot):
    media = snapshot.get("media") or {}
    return media.get("video_id") or media.get("player_key")


def _changed(before, after):
    """CDN rotation/quality switching must not masquerade as a new video."""
    old, new = before.get("media") or {}, after.get("media") or {}
    if old.get("video_id") and new.get("video_id"):
        return old["video_id"] != new["video_id"]
    try:
        old_key, new_key = json.loads(old["player_key"]), json.loads(new["player_key"])
        return bool(old_key[0] != new_key[0] and old_key[1] != new_key[1])
    except (KeyError, TypeError, ValueError, IndexError):
        return False


def _ready(snapshot):
    return bool(snapshot.get("items") or snapshot.get("media") or snapshot.get("empty_explicit"))


class Browser:
    def __init__(self, bridge, task=None):
        self.bridge, self.task = bridge, task
        self.research_stops = False

    def guarded(self, result):
        if not self.research_stops or not result.get("ok"):
            return result
        media = result.get("media") or {}
        if media.get("content_kind") == "live_preview":
            return {**result, "ok": False, "status": "live_preview",
                    "reason": "当前为直播预览，本次研究停止，不换路径继续浏览"}
        if (media or urlsplit(result.get("url", "")).path in ("/", "/jingxuan")) and not _identity(result):
            return {**result, "ok": False, "status": "unidentified_player",
                    "reason": "当前播放器缺少可核对标识，本次研究停止"}
        return result

    def wait(self, predicate, limit, timeout=12):
        deadline = time.monotonic() + timeout
        while True:
            result = self.guarded(self.bridge.read(limit))
            if result.get("status") in BLOCKED | RESEARCH_STOPS or predicate(result):
                return result
            if time.monotonic() >= deadline:
                return {**result, "ok": False, "status": "not_ready",
                        "reason": "页面未在时限内出现目标内容，未重复导航或翻页"}
            time.sleep(.4)

    def run(self, args):
        self.research_stops = bool(args.get("_research_stops"))
        self.capture_requested = args.get("capture", False)
        if type(self.capture_requested) is not bool:
            raise BrowseError("invalid_arguments", "capture必须为布尔值")
        action, limit = args.get("action", "status"), args.get("limit", 6)
        if action not in ACTIONS or type(limit) is not int or not 1 <= limit <= 20:
            raise BrowseError("invalid_arguments", "action不支持，或limit不在1–20之间")
        target = None
        if action == "search":
            target = search_url(args.get("keyword"))
        elif action == "feed":
            target = "https://www.douyin.com/"
        elif action == "open":
            target = args.get("url")
            if not video_id(target):
                raise BrowseError("invalid_url", "open需要实际来源中的抖音视频链接，不接受短链或其他域名")
        before = self.guarded(self.bridge.read(limit))
        if before.get("status") in BLOCKED | RESEARCH_STOPS:
            return self.save(before, action)
        if not allowed_url(before.get("url")):
            raise BrowseError("wrong_page", "当前页已离开抖音浏览区域")
        if args.get("expected_url") and before["url"] != args["expected_url"]:
            raise BrowseError("page_changed", "当前页与上次观察不一致，未继续动作")
        if args.get("_expected_video_id") and _identity(before) != args["_expected_video_id"]:
            raise BrowseError("page_changed", "播放器已切换到另一内容，未沿用旧观察继续动作")
        if args.get("_expected_player_key") and (before.get("media") or {}).get("player_key") != args["_expected_player_key"]:
            raise BrowseError("page_changed", "当前播放源或标题已变化，未沿用旧观察继续动作")
        result = before
        if action == "feed":
            observed = (before.get("navigation") or {}).get("recommend")
            if allowed_url(observed) and urlsplit(observed).path in ("/", "/jingxuan"):
                target = observed
        if target:
            if before["url"] != target:
                self.bridge.navigate(before, target)
            def arrived(s):
                u = s.get("url", "")
                if action == "open":
                    media = s.get("media") or {}
                    return (media.get("video_id") == video_id(target)
                            and media.get("ready_state", 0) >= 2)
                actual, expected = urlsplit(u), urlsplit(target)
                if action == "feed":
                    return (actual.hostname == "www.douyin.com" and actual.path in ("/", "/jingxuan")
                            and _ready(s))
                return (actual.hostname == expected.hostname and unquote(actual.path) == unquote(expected.path)
                        and (action != "search" or parse_qs(actual.query).get("type") == ["video"])
                        and _ready(s))
            result = self.wait(arrived, limit)
        elif action == "next":
            if before.get("idle_pause"):
                raise BrowseError("playback_paused", "页面因长时间无操作暂停；先明确resume并核对恢复，不重复翻页")
            if (urlsplit(before["url"]).path not in ("/", "/jingxuan") or not _identity(before)
                    or not before.get("media") or before["media"].get("content_kind") == "live_preview"):
                raise BrowseError("unsupported_next", "仅支持有明确当前播放器标识的首页推荐流翻页；搜索结果请用open")
            self.bridge.next(before)
            result = self.wait(lambda s: bool(_identity(s)) and _changed(before, s), limit, 8)
            result["previous_video_id"] = (before.get("media") or {}).get("video_id")
            result["previous_player_key"] = (before.get("media") or {}).get("player_key")
            result["changed"] = bool(result.get("ok") and _changed(before, result))
        elif action == "resume":
            if not before.get("idle_pause") or not _identity(before):
                raise BrowseError("unsupported_resume", "仅恢复当前可核对视频的长时间无操作暂停提示")
            self.bridge.resume(before)
            result = self.wait(lambda s: not s.get("idle_pause") and _identity(s) == _identity(before)
                               and (s.get("media") or {}).get("paused") is False, limit, 8)
            result["resumed"] = bool(result.get("ok") and not result.get("idle_pause")
                                     and (result.get("media") or {}).get("paused") is False)
        if result.get("ok") and not _ready(result) and action != "status":
            result.update(ok=False, status="no_extractable_content",
                          reason="未识别到视频或结果，不能据此判断没有内容")
        if action == "search" and result.get("ok") and self.task:
            research_trace.append_search_round(self.task, "douyin", "douyin_browse",
                keyword=args["keyword"], count=len(result.get("items", [])), evidence_url=result.get("url"))
        # Reading player metadata is NOT a deep-read; analyze_media records actual understanding.
        return self.save(result, action)

    def save(self, result, action):
        result = {**result, "tab_id": self.bridge.tab, "action": action,
                  "submitted": False, "account_write": False}
        if self.task:
            directory = Path(self.task.ensure_task(hint="douyin_browse"))
            name = f"douyin_{action}_{uuid.uuid4().hex[:10]}.json"
            if getattr(self, "capture_requested", False) and result.get("ok") and action in ("status", "read"):
                # Screenshot is separate evidence, not proof that video content was understood.
                fresh = self.bridge.read(1)
                if fresh.get("ok") and fresh.get("url") == result.get("url") and _identity(fresh) == _identity(result):
                    try:
                        image_path = directory / name.replace(".json", ".png")
                        image_path.write_bytes(self.bridge.capture())
                        result["screenshot_path"] = str(image_path)
                    except BrowseError as exc:
                        result["screenshot_error"] = str(exc)
            (directory / name).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            result["evidence_path"] = str(directory / name)
            self.task.append_trace("douyin_browse_done", {
                "action": action, "status": result.get("status"), "url": result.get("url"),
                "count": len(result.get("items", [])), "evidence_path": result["evidence_path"]})
        return result


def operate(args, task=None):
    import ga
    try:
        if args.get("action", "status") not in ACTIONS:
            raise BrowseError("invalid_arguments", "本工具仅支持浏览读取，不支持互动或发布")
        with ga._browser_action_lock:
            return Browser(Bridge(args.get("tab_id")), task).run(args)
    except BrowseError as exc:
        return {"ok": False, "status": exc.status, "reason": str(exc),
                "submitted": False, "account_write": False}
