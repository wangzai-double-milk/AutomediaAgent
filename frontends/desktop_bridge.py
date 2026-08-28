#!/usr/bin/env python3
"""
GenericAgent Web2 Bridge.

Clear split:
1) AgentManager: owns GenericAgent instances, sessions and histories.
2) Transport: HTTP is the command/data channel; WebSocket only pushes small
   session-state notifications.

HTTP API:
  GET    /status
  GET    /config
  POST   /config
  GET    /model-profiles
  GET    /sessions
  POST   /session/new
  GET    /session/{sid}
  DELETE /session/{sid}
  POST   /session/{sid}/prompt
  GET    /session/{sid}/messages?after=0&limit=200
  POST   /session/{sid}/cancel

WS API:
  GET /ws -> events only, e.g.
  {"type":"session-state","sessionId":"sess-...","state":"running","seq":3,"updatedAt":...}
"""
from __future__ import annotations

import asyncio, contextlib, importlib, json, os, re, sys
import mimetypes
import threading, time, traceback, uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set
from urllib.parse import urlsplit, urlunsplit
from aiohttp import web, WSMsgType

APP_DIR = Path(__file__).resolve().parent


def find_default_ga_root() -> Path:
    candidates = [
        APP_DIR / "..",
        APP_DIR / ".." / "..",
        APP_DIR / ".." / "GenericAgent",
        APP_DIR / ".." / ".." / "GenericAgent",
    ]
    for p in candidates:
        root = p.resolve()
        if (root / "agentmain.py").exists():
            return root
    return APP_DIR.parent.parent.resolve()


DEFAULT_GA_ROOT = find_default_ga_root()

for _s in (sys.stdout, sys.stderr):
    with contextlib.suppress(Exception):
        _s.reconfigure(encoding="utf-8", errors="replace")


# ---------------------------------------------------------------------------
# Agent management layer
# ---------------------------------------------------------------------------

@dataclass
class Session:
    id: str
    title: str = "New chat"
    cwd: str = ""
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    messages: List[dict] = field(default_factory=list)
    msg_seq: int = 0
    partial: Optional[dict] = None
    status: str = "idle"  # idle|running|error|cancelled
    agent: Any = None
    thread: Optional[threading.Thread] = None
    cancel_requested: bool = False
    last_error: str = ""


class AgentManager:
    def __init__(self):
        self.lock = threading.RLock()
        self.ga_root = str(DEFAULT_GA_ROOT)
        self.config: Dict[str, Any] = {}
        self.sessions: Dict[str, Session] = {}
        self.active_session_id: Optional[str] = None
        self._platform_cache: Dict[str, Any] = {}
        self._platform_cache_at = 0.0
        self._browser_preview_cache: Dict[str, dict] = {}
        self._browser_preview_lock = threading.Lock()
        self._browser_preview_inflight: Set[str] = set()

    @property
    def mykey_path(self) -> str:
        return str(Path(self.ga_root) / "mykey.py")

    def ensure_ga_import_path(self) -> Path:
        root = Path(self.ga_root).resolve()
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
        return root

    def make_agent(self, sess: Session):
        root = self.ensure_ga_import_path()
        old_cwd = os.getcwd()
        try:
            os.chdir(sess.cwd or str(root))
            agentmain = importlib.import_module("agentmain")
            GA = getattr(agentmain, "GenericAgent")
            agent = GA()
            agent.inc_out = True
            agent.verbose = True
            threading.Thread(target=agent.run, daemon=True, name=f"GA-{sess.id}").start()
            return agent
        finally:
            with contextlib.suppress(Exception):
                os.chdir(old_cwd)

    def list_model_profiles(self):
        self.ensure_ga_import_path()
        try:
            agentmain = importlib.import_module("agentmain")
            agent = agentmain.GenericAgent()
            if hasattr(agent, "list_llms"):
                return [{"id": i, "name": name, "active": active} for i, name, active in agent.list_llms()]
        except Exception as e:
            print(f"get model profiles failed: {e}", file=sys.stderr)
        return []

    def snapshot(self, sess: Session, include_messages: bool = True) -> dict:
        out = {
            "sessionId": sess.id,
            "id": sess.id,
            "title": sess.title,
            "cwd": sess.cwd,
            "status": sess.status,
            "createdAt": sess.created_at,
            "updatedAt": sess.updated_at,
            "lastError": sess.last_error,
            "msgSeq": sess.msg_seq,
        }
        if include_messages:
            out["messages"] = list(sess.messages)
            out["partial"] = dict(sess.partial) if sess.partial else None
        return out

    def add_message(self, sess: Session, role: str, content: str, **extra) -> dict:
        sess.msg_seq += 1
        msg = {"id": sess.msg_seq, "role": role, "content": content, "ts": time.time()}
        msg.update(extra)
        sess.messages.append(msg)
        sess.updated_at = time.time()
        if role == "user" and content.strip() and sess.title == "New chat":
            sess.title = content.strip().replace("\n", " ")[:40]
        return msg

    def create_session(self, cwd: Optional[str] = None) -> Session:
        sid = "sess-" + uuid.uuid4().hex[:12]
        sess = Session(id=sid, cwd=str(cwd or self.ga_root))
        with self.lock:
            self.sessions[sid] = sess
            self.active_session_id = sid
        emit_session_state(sess, "created")
        return sess

    def get_session(self, sid: str) -> Session:
        with self.lock:
            sess = self.sessions.get(sid)
            if not sess:
                raise web.HTTPNotFound(text=json.dumps({"error": f"session not found: {sid}"}, ensure_ascii=False), content_type="application/json")
            return sess

    def delete_session(self, sid: str) -> dict:
        with self.lock:
            sess = self.sessions.pop(sid, None)
            if not sess:
                raise web.HTTPNotFound(text=json.dumps({"error": f"session not found: {sid}"}, ensure_ascii=False), content_type="application/json")
            if self.active_session_id == sid:
                self.active_session_id = next(iter(self.sessions), None)
            if sess.agent and hasattr(sess.agent, "abort"):
                with contextlib.suppress(Exception):
                    sess.agent.abort()
        emit_session_state(sess, "closed")
        return {"ok": True, "sessionId": sid}

    def submit_prompt(self, sid: str, prompt: Any, attachments: Optional[list] = None) -> dict:
        prompt, attachment_ids = normalize_prompt(prompt, attachments)
        with self.lock:
            sess = self.sessions.get(sid)
            if not sess:
                raise web.HTTPNotFound(text=json.dumps({"error": f"session not found: {sid}"}, ensure_ascii=False), content_type="application/json")
            if sess.status == "running":
                raise web.HTTPConflict(text=json.dumps({"error": "session is already running"}, ensure_ascii=False), content_type="application/json")
            extra = {}
            if attachment_ids:
                extra["image_ids"] = attachment_ids
            user_msg = self.add_message(sess, "user", prompt, **extra)
            sess.status = "running"
            sess.cancel_requested = False
            sess.last_error = ""
            sess.partial = {"id": sess.msg_seq + 1, "role": "assistant", "content": "", "ts": time.time(), "partial": True}
            t = threading.Thread(target=self.run_agent_turn, args=(sess, prompt, None), daemon=True, name=f"Turn-{sid}")
            sess.thread = t
            t.start()
            seq = sess.msg_seq
        emit_session_state(sess, "running")
        return {"ok": True, "sessionId": sid, "accepted": True, "userMessageId": user_msg["id"], "seq": seq}

    @staticmethod
    def _browser_platform(url: str) -> str:
        lowered = (url or "").lower()
        if "xiaohongshu.com" in lowered:
            return "xiaohongshu"
        if "douyin.com" in lowered:
            return "douyin"
        return "web"

    def _refresh_browser_preview(self, driver: Any, tab_id: str) -> None:
        try:
            payload = json.dumps({
                "cmd": "cdp",
                "method": "Page.captureScreenshot",
                "params": {"format": "jpeg", "quality": 48, "captureBeyondViewport": False},
            })
            result = driver.execute_js(payload, timeout=4, session_id=tab_id)
            value = result.get("data") if isinstance(result, dict) else result
            if isinstance(value, dict):
                data = value.get("data") or ""
            else:
                data = ""
            if data:
                with self._browser_preview_lock:
                    self._browser_preview_cache[str(tab_id)] = {
                        "data": data,
                        "updatedAt": time.time(),
                    }
        except Exception:
            pass
        finally:
            with self._browser_preview_lock:
                self._browser_preview_inflight.discard(str(tab_id))

    def browser_windows(self) -> list:
        try:
            driver = self._browser_driver()
            tabs = list(driver.get_all_sessions() or [])
        except Exception:
            return []
        default_id = str(getattr(driver, "default_session_id", ""))
        tabs.sort(key=lambda tab: (
            0 if str(tab.get("id") or "") == default_id else 1,
            {"xiaohongshu": 0, "douyin": 1, "web": 2}.get(
                self._browser_platform(str(tab.get("url") or "")), 3
            ),
        ))
        windows = []
        now = time.time()
        for tab in tabs[:6]:
            tab_id = str(tab.get("id") or "")
            url = str(tab.get("url") or "")
            if not tab_id or not url.startswith(("http://", "https://")):
                continue
            with self._browser_preview_lock:
                cached = self._browser_preview_cache.get(tab_id)
            preview_ready = bool(cached and cached.get("data"))
            if not cached or now - float(cached.get("updatedAt") or 0) > 3.5:
                with self._browser_preview_lock:
                    should_start = tab_id not in self._browser_preview_inflight
                    if should_start:
                        self._browser_preview_inflight.add(tab_id)
                if should_start:
                    threading.Thread(
                        target=self._refresh_browser_preview,
                        args=(driver, tab_id),
                        daemon=True,
                        name=f"BrowserPreview-{tab_id[:8]}",
                    ).start()
            windows.append({
                "id": tab_id,
                "platform": self._browser_platform(url),
                "title": str(tab.get("title") or urlsplit(url).netloc or "网页")[:120],
                "url": self._safe_browser_url(url),
                "active": tab_id == default_id,
                "previewReady": preview_ready,
                "previewVersion": int(float(cached.get("updatedAt") or 0)) if cached else 0,
            })
        return windows

    def browser_preview(self, tab_id: str) -> Optional[bytes]:
        with self._browser_preview_lock:
            cached = self._browser_preview_cache.get(str(tab_id))
        if not cached or not cached.get("data"):
            return None
        try:
            return base64.b64decode(cached["data"])
        except Exception:
            return None

    def run_agent_turn(self, sess: Session, prompt: str, images: Optional[list] = None):
        try:
            if sess.agent is None:
                sess.agent = self.make_agent(sess)
            agent = sess.agent
            full = ""
            if hasattr(agent, "put_task"):
                display_q = agent.put_task(prompt, images=images or [])
                pieces = []
                import queue as _queue
                while True:
                    if sess.cancel_requested:
                        break
                    try:
                        item = display_q.get(timeout=1.0)
                    except _queue.Empty:
                        continue
                    if isinstance(item, dict):
                        if item.get("next"):
                            text = str(item["next"])
                            pieces.append(text)
                            with self.lock:
                                if sess.partial is not None:
                                    sess.partial["content"] = "".join(pieces) if getattr(agent, "inc_out", False) else text
                                    sess.partial["ts"] = time.time()
                                    sess.updated_at = time.time()
                        if "done" in item:
                            full = str(item.get("done") or "")
                            break
                    else:
                        pieces.append(str(item))
                if not full and pieces:
                    full = pieces[-1] if not getattr(agent, "inc_out", False) else "".join(pieces)
            elif hasattr(agent, "run"):
                ret = agent.run(prompt)
                if isinstance(ret, str):
                    full = ret
            else:
                full = "GenericAgent object has no put_task/run method"
            if not full:
                full = "(completed)"
            if sess.cancel_requested:
                with self.lock:
                    sess.partial = None
                    # Ensure status stays cancelled (don't overwrite)
                    if sess.status != "cancelled":
                        sess.status = "cancelled"
                    sess.updated_at = time.time()
                emit_session_state(sess, "cancelled")
                return
            with self.lock:
                sess.partial = None
                # Strip trailing [Info] Final response to user. marker
                import re as _re
                full = _re.sub(r'\n*`{5}\n*\[Info\] Final response to user\.\n*`{5}\s*$', '', full)
                self.add_message(sess, "assistant", full)
                sess.status = "idle"
                sess.last_error = ""
            emit_session_state(sess, "idle")
        except Exception as e:
            tb = traceback.format_exc()
            with self.lock:
                sess.partial = None
                sess.status = "error"
                sess.last_error = str(e)
                self.add_message(sess, "error", str(e))
            print(tb, file=sys.stderr)
            emit_session_state(sess, "error")

    def messages(self, sid: str, after: int = 0, limit: int = 200) -> dict:
        with self.lock:
            sess = self.sessions.get(sid)
            if not sess:
                raise web.HTTPNotFound(text=json.dumps({"error": f"session not found: {sid}"}, ensure_ascii=False), content_type="application/json")
            msgs = [m for m in sess.messages if int(m.get("id", 0)) > after]
            if limit > 0:
                msgs = msgs[-limit:]
            return {
                "sessionId": sid,
                "status": sess.status,
                "messages": msgs,
                "partial": dict(sess.partial) if sess.partial else None,
                "msgSeq": sess.msg_seq,
                "updatedAt": sess.updated_at,
                "lastError": sess.last_error,
            }

    def cancel(self, sid: str) -> dict:
        with self.lock:
            sess = self.sessions.get(sid)
            if not sess:
                raise web.HTTPNotFound(text=json.dumps({"error": f"session not found: {sid}"}, ensure_ascii=False), content_type="application/json")
            sess.cancel_requested = True
            if sess.agent and hasattr(sess.agent, "abort"):
                with contextlib.suppress(Exception):
                    sess.agent.abort()
            sess.status = "cancelled"
            sess.partial = None
            sess.updated_at = time.time()
        emit_session_state(sess, "cancelled")
        return {"ok": True, "sessionId": sid}

    @staticmethod
    def _safe_browser_url(value: str) -> str:
        try:
            parts = urlsplit(value or "")
            return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
        except Exception:
            return ""

    @staticmethod
    def _decode_js_result(result: Any) -> dict:
        value = result.get("data") if isinstance(result, dict) and "data" in result else result
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except (TypeError, ValueError):
                return {}
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _login_probe(platform: str) -> str:
        if platform == "xiaohongshu":
            return r"""
            (function(){
              try {
                var state = window.__INITIAL_STATE__ || {};
                var user = state.user || {};
                function unwrap(x) {
                  if (x == null) return null;
                  return x.value !== undefined ? x.value : (x._value !== undefined ? x._value : x);
                }
                var info = unwrap(user.userInfo) || {};
                var login = unwrap(user.loginStatus);
                var loggedIn = login === 1 || login === true || !!(info.nickname || info.userId);
                var loginVisible = !!document.querySelector(
                  '.login-container, [class*="login-modal"], [class*="login-btn"]'
                );
                return JSON.stringify({
                  loggedIn: loggedIn,
                  nickname: info.nickname || '',
                  loginVisible: loginVisible
                });
              } catch (e) {
                return JSON.stringify({error: String(e)});
              }
            })()
            """
        return r"""
        (function(){
          try {
            var cookie = document.cookie || '';
            var hasSessionCookie = /(?:^|;\s*)(sessionid|sid_guard|uid_tt)=/i.test(cookie);
            var avatar = document.querySelector(
              '[data-e2e*="user-avatar"], [class*="user-avatar"], [class*="semi-avatar"], a[href*="/user/"] img'
            );
            var loginButton = Array.from(document.querySelectorAll('button, a')).some(function(el) {
              return /^\s*(登录|立即登录)\s*$/.test(el.textContent || '');
            });
            return JSON.stringify({
              loggedIn: !!hasSessionCookie || (!!avatar && !loginButton),
              nickname: '',
              loginVisible: loginButton
            });
          } catch (e) {
            return JSON.stringify({error: String(e)});
          }
        })()
        """

    def _browser_driver(self):
        self.ensure_ga_import_path()
        ga_module = importlib.import_module("ga")
        driver = getattr(ga_module, "driver", None)
        if driver is None:
            with self.lock:
                driver = getattr(ga_module, "driver", None)
                if driver is None:
                    from TMWebDriver import TMWebDriver
                    driver = TMWebDriver()
                    ga_module.driver = driver
            # Give the browser extension a short window to reconnect.
            deadline = time.time() + 2.0
            while time.time() < deadline:
                try:
                    if driver.get_all_sessions():
                        break
                except Exception:
                    break
                time.sleep(0.2)
        return driver

    def platform_status(self, force: bool = False) -> dict:
        now = time.time()
        if not force and self._platform_cache and now - self._platform_cache_at < 8:
            return self._platform_cache

        definitions = {
            "xiaohongshu": {
                "name": "小红书",
                "domain": "xiaohongshu.com",
                "openUrl": "https://www.xiaohongshu.com/explore",
            },
            "douyin": {
                "name": "抖音",
                "domain": "douyin.com",
                "openUrl": "https://www.douyin.com/",
            },
        }
        result = {
            "browserConnected": False,
            "overallReady": False,
            "checkedAt": now,
            "platforms": {},
        }
        try:
            driver = self._browser_driver()
        except Exception:
            driver = None
        tabs = []
        if driver is not None:
            try:
                tabs = list(driver.get_all_sessions() or [])
                result["browserConnected"] = bool(tabs)
            except Exception:
                tabs = []

        for key, definition in definitions.items():
            matches = [
                tab for tab in tabs
                if definition["domain"] in str(tab.get("url") or "").lower()
            ]
            item = {
                "id": key,
                "name": definition["name"],
                "state": "open_required",
                "label": "还没打开",
                "detail": f"请先打开{definition['name']}并完成登录",
                "tabCount": len(matches),
                "nickname": "",
                "openUrl": definition["openUrl"],
            }
            if matches:
                item.update({
                    "state": "check_required",
                    "label": "请确认登录",
                    "detail": f"已发现{definition['name']}页面，请确认账号处于登录状态",
                })
                tab = matches[0]
                try:
                    probe = driver.execute_js(
                        self._login_probe(key),
                        timeout=2.5,
                        session_id=tab.get("id"),
                    )
                    login = self._decode_js_result(probe)
                    if login.get("loggedIn"):
                        nickname = str(login.get("nickname") or "")[:40]
                        item.update({
                            "state": "ready",
                            "label": "已就绪",
                            "detail": f"{nickname} 已登录" if nickname else "账号已登录，可以开始工作",
                            "nickname": nickname,
                        })
                    elif not login.get("error"):
                        item.update({
                            "state": "login_required",
                            "label": "等待登录",
                            "detail": f"请在{definition['name']}页面完成扫码或账号登录",
                        })
                except Exception:
                    pass
            result["platforms"][key] = item

        result["overallReady"] = all(
            item["state"] == "ready" for item in result["platforms"].values()
        )
        self._platform_cache = result
        self._platform_cache_at = now
        return result

    @staticmethod
    def _event_view(record: dict) -> dict:
        step = str(record.get("step") or "")
        payload = record.get("payload") or {}
        labels = {
            "task_init": ("boot", "建立任务工作区"),
            "explore_task_init": ("boot", "建立探索工作区"),
            "research_search_round": ("search", "搜索新样本"),
            "search_viral_start": ("search", "准备搜索热门内容"),
            "search_viral_nav": ("search", "前往新的搜索结果"),
            "search_viral_tab": ("observe", "打开一条热门内容"),
            "search_viral_done": ("search", "完成一轮爆款搜索"),
            "search_feeds_start": ("search", "搜索相关内容"),
            "search_feeds_done": ("search", "完成一轮内容搜索"),
            "research_item_read": ("observe", "深读一条内容"),
            "get_feed_detail_start": ("observe", "打开内容并查看细节"),
            "get_feed_detail_done": ("observe", "读取内容详情"),
            "vlm_analyze_start": ("vision", "开始理解图片或视频"),
            "vlm_analyze_done": ("vision", "完成画面理解"),
            "vlm_analyze_succeeded": ("vision", "完成媒体理解"),
            "vlm_analyze_failed": ("warning", "媒体理解失败"),
            "visual_click_start": ("observe", "尝试打开感兴趣的内容"),
            "visual_click_done": ("observe", "已经进入内容页面"),
            "video_url_resolved": ("vision", "找到视频素材"),
            "video_url_resolve_failed": ("warning", "暂时无法读取视频"),
            "video_understanding_skipped": ("observe", "跳过重复的视频素材"),
            "research_knowledge_delta": ("learn", "更新知识增量"),
            "report_write": ("write", "更新研究报告"),
            "convergence_gate_BLOCKED": ("guard", "收敛守卫要求继续调查"),
            "xhs_sop_gate_BLOCKED": ("guard", "正在补齐开始搜索前的准备"),
            "media_understanding_gate_BLOCKED": ("guard", "还需要认真看完素材"),
            "check_login_start": ("boot", "检查平台登录状态"),
            "check_login_done": ("boot", "平台状态检查完成"),
            "image_review_start": ("vision", "检查图片是否适合发布"),
            "image_review_done": ("learn", "完成图片质量检查"),
            "image_generate_start": ("create", "开始生成新图片"),
            "image_generate_done": ("create", "新图片已经生成"),
            "publish_draft_start": ("publish", "正在准备发布内容"),
            "publish_draft_tab": ("publish", "打开小红书创作中心"),
            "publish_visibility": ("publish", "确认内容可见范围"),
            "publish_draft_done": ("done", "内容已经发布"),
            "publish_draft_error": ("warning", "发布遇到问题"),
            "task_terminal": ("done", "任务进入终态"),
        }
        if step.startswith("image_edit_round_") and step.endswith("_start"):
            kind, label = "create", "正在调整图片"
        elif step.startswith("image_edit_round_") and step.endswith("_done"):
            kind, label = "create", "完成一版新图片"
        elif step in labels:
            kind, label = labels[step]
        elif "search" in step:
            kind, label = "search", "继续寻找相关内容"
        elif any(word in step for word in ("feed", "detail", "read", "click")):
            kind, label = "observe", "正在浏览一条内容"
        elif any(word in step for word in ("image", "video", "media", "vlm", "vision")):
            kind, label = "vision", "正在理解图片或视频"
        elif any(word in step for word in ("gate", "guard", "blocked")):
            kind, label = "guard", "证据还不够，继续调查"
        elif any(word in step for word in ("report", "write", "save")):
            kind, label = "write", "正在整理任务成果"
        else:
            kind, label = "action", "正在推进任务"
        detail = ""
        for key in ("keyword", "title", "topic"):
            value = payload.get(key)
            if value not in (None, "", []):
                detail = str(value)
                break
        if not detail and payload.get("error"):
            detail = "遇到页面读取问题，正在尝试其他办法"
        return {
            "ts": record.get("ts") or "",
            "step": step,
            "kind": kind,
            "label": label,
            "detail": detail[:180],
        }

    @staticmethod
    def _inside_task(task_root: Path, value: Any) -> Optional[Path]:
        if not value or not isinstance(value, (str, os.PathLike)):
            return None
        try:
            path = Path(value)
            if not path.is_absolute():
                path = task_root / path
            path = path.resolve()
            path.relative_to(task_root.resolve())
            return path if path.is_file() else None
        except (OSError, RuntimeError, ValueError):
            return None

    @staticmethod
    def _asset_role(path: Path) -> str:
        lowered = str(path).lower()
        name = path.name.lower()
        if "viral_refs" in lowered or "/covers/" in lowered:
            return "reference"
        if name.startswith("original"):
            return "source"
        if any(word in name for word in ("edited", "generated", "annotated", "result", "final", "cover")):
            return "output"
        return "material"

    @staticmethod
    def _asset_label(role: str) -> str:
        return {
            "reference": "参考帖子",
            "source": "原始素材",
            "output": "制作版本",
            "material": "任务素材",
            "publish": "待发布素材",
        }.get(role, "任务素材")

    def _media_item(
        self,
        task_root: Path,
        path: Path,
        role: Optional[str] = None,
        title: str = "",
        **extra,
    ) -> dict:
        relative = path.resolve().relative_to(task_root.resolve()).as_posix()
        kind = "video" if path.suffix.lower() in (".mp4", ".mov", ".webm", ".m4v") else "image"
        actual_role = role or self._asset_role(path)
        item = {
            "id": relative,
            "name": path.name,
            "relativePath": relative,
            "kind": kind,
            "role": actual_role,
            "roleLabel": self._asset_label(actual_role),
            "title": title or path.stem,
            "size": path.stat().st_size,
            "updatedAt": path.stat().st_mtime,
        }
        item.update(extra)
        return item

    def _workspace_view(self, sid: str, task_dir: str, records: list) -> dict:
        empty = {
            "mode": "idle",
            "modeLabel": "等待任务",
            "stages": [],
            "focus": None,
            "references": [],
            "assets": [],
            "documents": [],
            "draft": None,
        }
        if not task_dir:
            return empty
        task_root = Path(task_dir).resolve()
        if not task_root.is_dir():
            return empty

        steps = [str(record.get("step") or "") for record in records]
        has_publish = any(step.startswith("publish_") for step in steps)
        has_create = has_publish or any(
            step.startswith(("image_edit_", "image_generate_", "image_review_"))
            for step in steps
        )
        mode = "publish" if has_publish else "create" if has_create else "research"
        mode_label = {
            "publish": "内容发布",
            "create": "素材制作",
            "research": "内容调研",
        }[mode]

        media_exts = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".mp4", ".mov", ".webm", ".m4v"}
        media_by_path: Dict[str, dict] = {}

        def add_media(value: Any, role: Optional[str] = None, title: str = "", **extra):
            path = self._inside_task(task_root, value)
            if not path or path.suffix.lower() not in media_exts:
                return None
            key = str(path)
            item = self._media_item(task_root, path, role=role, title=title, **extra)
            previous = media_by_path.get(key)
            if previous and role is None:
                return previous
            if previous and previous.get("role") == "output" and role != "publish":
                return previous
            media_by_path[key] = item
            return item

        draft = None
        for record in records:
            step = str(record.get("step") or "")
            payload = record.get("payload") or {}
            if step in ("task_init", "explore_task_init"):
                add_media(payload.get("original_image"), "source", "用户提供的原始素材")
            if step == "vlm_analyze_start":
                for value in payload.get("images") or []:
                    add_media(value, "reference", "Agent 正在分析的图片")
            if step.startswith("image_edit_round_") and step.endswith("_start"):
                add_media(payload.get("image"), "source")
                add_media(payload.get("save_path"), "output")
            if step.startswith("image_edit_round_") and step.endswith("_done"):
                add_media(payload.get("output"), "output", "修图版本")
            if step == "image_generate_done":
                add_media(payload.get("output"), "output", "生成图片")
            if step == "publish_draft_start":
                publish_items = [
                    add_media(value, "publish", "待发布图片")
                    for value in payload.get("images") or []
                ]
                draft = {
                    "title": str(payload.get("title") or ""),
                    "desc": str(payload.get("desc") or ""),
                    "descLength": int(payload.get("desc_len") or len(str(payload.get("desc") or ""))),
                    "tags": list(payload.get("tags") or []),
                    "visibility": "",
                    "status": "preparing",
                    "images": [item["id"] for item in publish_items if item],
                }
            elif step == "publish_visibility" and draft:
                draft["visibility"] = str(payload.get("now") or payload.get("visibility") or "")
            elif step == "publish_draft_done" and draft:
                draft["status"] = "published"
                draft["visibility"] = str(payload.get("visibility") or draft.get("visibility") or "")
            elif step == "publish_draft_error" and draft:
                draft["status"] = "error"

        try:
            paths = sorted(
                (
                    path for path in task_root.rglob("*")
                    if path.is_file() and path.suffix.lower() in media_exts
                ),
                key=lambda path: path.stat().st_mtime,
                reverse=True,
            )[:160]
            for path in paths:
                add_media(path)
        except OSError:
            pass

        meta_by_path = {}
        meta_path = task_root / "viral_meta.json"
        if meta_path.is_file():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8", errors="replace"))
                for cover in meta.get("covers") or []:
                    local = self._inside_task(task_root, cover.get("local_path"))
                    if local:
                        meta_by_path[str(local)] = {
                            "title": str(cover.get("title") or ""),
                            "likes": str(cover.get("likes") or ""),
                            "sourceUrl": self._safe_browser_url(str(cover.get("note_url") or "")),
                            "keyword": str(meta.get("keyword") or ""),
                        }
            except (OSError, TypeError, ValueError):
                pass
        for path_key, extra in meta_by_path.items():
            item = media_by_path.get(path_key)
            if item:
                item.update(extra)

        all_media = sorted(media_by_path.values(), key=lambda item: item["updatedAt"], reverse=True)
        reference_media = [item for item in all_media if item["role"] == "reference"]
        featured_references = [item for item in reference_media if item.get("sourceUrl")]
        detail_references = [item for item in reference_media if not item.get("sourceUrl")]
        references = (featured_references + detail_references)[:18]
        assets = [item for item in all_media if item["role"] != "reference"][:18]

        documents = []
        for path in sorted(task_root.glob("*"), key=lambda item: item.stat().st_mtime, reverse=True):
            if not path.is_file() or path.name in ("trace.log", "llm_calls.log"):
                continue
            if path.suffix.lower() not in (".md", ".txt"):
                continue
            preview = ""
            if path.suffix.lower() in (".md", ".txt"):
                try:
                    preview = path.read_text(encoding="utf-8", errors="replace")[:900]
                except OSError:
                    pass
            documents.append({
                "name": path.name,
                "relativePath": path.relative_to(task_root).as_posix(),
                "kind": "report" if "report" in path.name.lower() else "document",
                "preview": preview,
                "updatedAt": path.stat().st_mtime,
            })
            if len(documents) >= 8:
                break

        stage_defs = [
            ("brief", "理解目标", bool(records), bool(records)),
            ("reference", "寻找参考", any("search" in step or "feed" in step for step in steps), bool(references)),
            ("create", "制作素材", has_create, any(
                step == "image_generate_done"
                or (step.startswith("image_edit_round_") and step.endswith("_done"))
                for step in steps
            )),
            ("copy", "准备文案", bool(draft) or any("report_write" == step for step in steps), bool(draft)),
            ("publish", "发布内容", has_publish, any(step == "publish_draft_done" for step in steps)),
        ]
        if mode == "research":
            stage_defs = [
                ("brief", "理解目标", bool(records), bool(records)),
                ("reference", "寻找样本", any("search" in step for step in steps), bool(references)),
                ("observe", "深入阅读", any("feed" in step or "vlm" in step for step in steps), any("research_item_read" == step for step in steps)),
                ("learn", "提炼发现", any("knowledge" in step for step in steps), any("research_knowledge_delta" == step for step in steps)),
                ("report", "整理结果", any("report_write" == step for step in steps), bool(documents)),
            ]
        stages = []
        last_started = max((index for index, (_, _, started, _) in enumerate(stage_defs) if started), default=0)
        for index, (stage_id, label, started, done) in enumerate(stage_defs):
            status = "done" if done else "active" if started and index == last_started else "pending"
            stages.append({"id": stage_id, "label": label, "status": status})

        focus = next((item for item in assets if item["role"] in ("publish", "output")), None)
        if not focus:
            focus = assets[0] if assets else references[0] if references else None
        return {
            "mode": mode,
            "modeLabel": mode_label,
            "stages": stages,
            "focus": focus,
            "references": references,
            "assets": assets,
            "documents": documents,
            "draft": draft,
        }

    def activity(self, sid: str) -> dict:
        sess = self.get_session(sid)
        agent = sess.agent
        task_dir = str(getattr(agent, "task_dir", "") or "") if agent else ""
        records = []
        if task_dir:
            trace_path = Path(task_dir) / "trace.log"
            if trace_path.is_file():
                try:
                    lines = trace_path.read_text(encoding="utf-8", errors="replace").splitlines()[-600:]
                    for line in lines:
                        try:
                            record = json.loads(line)
                            if isinstance(record, dict):
                                records.append(record)
                        except Exception:
                            continue
                except OSError:
                    pass

        searches, reads, failed_media = set(), set(), set()
        consecutive_no_new = 0
        knowledge = []
        for index, record in enumerate(records):
            step = record.get("step") or ""
            payload = record.get("payload") or {}
            if step in ("research_search_round", "search_viral_done", "search_feeds_done"):
                searches.add(str(payload.get("round_id") or payload.get("keyword") or index))
            if step == "research_item_read":
                reads.add(str(payload.get("item_id") or payload.get("feed_id") or index))
            elif step == "get_feed_detail_done" and payload.get("ok"):
                reads.add(str(payload.get("feed_id") or index))
            elif step == "vlm_analyze_succeeded" and payload.get("video_url"):
                reads.add(str(payload.get("video_url")))
                failed_media.discard(str(payload.get("media_key") or payload.get("video_url")))
            if step == "vlm_analyze_failed":
                failed_media.add(str(payload.get("media_key") or payload.get("call_id") or index))
            if step == "research_knowledge_delta":
                has_new = bool(payload.get("has_new") or payload.get("new_points") or payload.get("revised_points"))
                consecutive_no_new = 0 if has_new else consecutive_no_new + 1
                points = list(payload.get("new_points") or []) + list(payload.get("revised_points") or [])
                for point in points:
                    text = str(point).strip()
                    if text:
                        knowledge.append({
                            "text": text[:300],
                            "itemId": str(payload.get("item_id") or ""),
                            "platform": str(payload.get("platform") or ""),
                            "ts": record.get("ts") or "",
                        })

        browser = self.browser_windows()

        artifacts = []
        if task_dir and Path(task_dir).is_dir():
            try:
                for path in sorted(Path(task_dir).iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
                    if path.is_file() and path.name != "trace.log":
                        artifacts.append({
                            "name": path.name,
                            "path": str(path),
                            "size": path.stat().st_size,
                        })
                    if len(artifacts) >= 8:
                        break
            except OSError:
                pass

        workspace = self._workspace_view(sid, task_dir, records)
        timeline = [self._event_view(record) for record in records[-18:]]
        current = timeline[-1] if timeline else {
            "kind": "idle",
            "label": "等你安排任务",
            "detail": "给它一个目标，然后看它自己搜索、浏览和学习",
            "ts": "",
            "step": "",
        }
        phase = current["kind"]
        if sess.status == "running" and phase in ("done", "idle"):
            phase = "thinking"
        elif sess.status != "running" and not records:
            phase = "idle"

        return {
            "sessionId": sid,
            "status": sess.status,
            "phase": phase,
            "current": current,
            "taskDir": task_dir,
            "timeline": timeline,
            "knowledge": knowledge[-10:],
            "browser": browser,
            "artifacts": artifacts,
            "workspace": workspace,
            "metrics": {
                "searchRounds": len(searches),
                "itemsRead": len(reads),
                "consecutiveNoNew": consecutive_no_new,
                "failedMedia": len(failed_media),
                "searchTarget": 2,
                "readTarget": 5,
                "noNewTarget": 5,
            },
        }


import base64
import tempfile

# Shared temp dir for image uploads (persists for process lifetime)
_UPLOAD_DIR = Path(tempfile.gettempdir()) / "ga_web2_uploads"
_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
_ALLOWED_UPLOAD_TYPES = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "video/mp4": ".mp4",
    "video/quicktime": ".mov",
    "video/webm": ".webm",
}
_MAX_UPLOAD_BYTES = 500 * 1024 * 1024


def _save_image_data(data_url: str, img_id: str) -> str:
    """Save a data URL to disk, return absolute path."""
    # data:image/png;base64,xxxxx
    if "," in data_url:
        header, b64 = data_url.split(",", 1)
    else:
        b64 = data_url
        header = ""
    ext = "png"
    if "jpeg" in header or "jpg" in header:
        ext = "jpg"
    elif "webp" in header:
        ext = "webp"
    elif "gif" in header:
        ext = "gif"
    fpath = _UPLOAD_DIR / f"{img_id}.{ext}"
    fpath.write_bytes(base64.b64decode(b64))
    return str(fpath)


def normalize_prompt(prompt: Any, attachments: Optional[list] = None):
    """Normalize prompt and uploaded image/video references."""
    attachments = list(attachments or [])
    if isinstance(prompt, list):
        text_parts = []
        for part in prompt:
            if isinstance(part, str):
                text_parts.append(part)
            elif isinstance(part, dict):
                if part.get("type") in ("text", "input_text"):
                    text_parts.append(str(part.get("text") or part.get("content") or ""))
                elif part.get("type") in ("image", "input_image"):
                    url = part.get("image_url") or part.get("url") or part.get("data")
                    if isinstance(url, dict):
                        url = url.get("url")
                    if url:
                        attachments.append(url)
        prompt = "\n".join([p for p in text_parts if p])

    attachment_ids = []
    attachment_tags = []
    for item in attachments:
        if isinstance(item, dict):
            item_id = item.get("id") or f"media-{uuid.uuid4().hex[:8]}"
            data_url = item.get("dataUrl") or item.get("data_url") or ""
            upload_path = item.get("path") or ""
            kind = item.get("kind") or ("video" if str(upload_path).lower().endswith((".mp4", ".mov", ".webm")) else "image")
        else:
            item_id = f"media-{uuid.uuid4().hex[:8]}"
            data_url = str(item)
            upload_path = ""
            kind = "image"
        path = ""
        if upload_path:
            try:
                candidate = Path(upload_path).resolve()
                candidate.relative_to(_UPLOAD_DIR.resolve())
                if candidate.is_file():
                    path = str(candidate)
            except (OSError, RuntimeError, ValueError):
                path = ""
        if data_url:
            path = _save_image_data(data_url, item_id)
        if path:
            tag = "video" if kind == "video" else "image"
            attachment_tags.append(f"[{tag}:{path}]")
            attachment_ids.append(item_id)

    final_prompt = str(prompt or "")
    if attachment_tags:
        final_prompt = final_prompt + "\n" + "\n".join(attachment_tags)

    return final_prompt, attachment_ids


manager = AgentManager()


# ---------------------------------------------------------------------------
# Transport layer: WS notification only
# ---------------------------------------------------------------------------

class WsHub:
    def __init__(self):
        self.websockets: Set[web.WebSocketResponse] = set()
        self.loop: Optional[asyncio.AbstractEventLoop] = None

    def emit(self, obj: dict):
        if self.loop and self.loop.is_running():
            asyncio.run_coroutine_threadsafe(self._broadcast(obj), self.loop)

    async def _broadcast(self, obj: dict):
        data = json.dumps(obj, ensure_ascii=False, default=str)
        dead = set()
        for ws in list(self.websockets):
            try:
                await ws.send_str(data)
            except Exception:
                dead.add(ws)
        self.websockets.difference_update(dead)


hub = WsHub()


def emit_session_state(sess: Session, state_name: str):
    hub.emit({
        "type": "session-state",
        "sessionId": sess.id,
        "state": state_name,
        "status": sess.status,
        "seq": sess.msg_seq,
        "updatedAt": sess.updated_at,
        "title": sess.title,
    })


async def ws_handler(request):
    ws = web.WebSocketResponse(heartbeat=30)
    await ws.prepare(request)
    hub.websockets.add(ws)
    await ws.send_str(json.dumps({
        "type": "bridge-ready",
        "gaRoot": manager.ga_root,
        "mykeyPath": manager.mykey_path,
        "http": True,
        "wsEventsOnly": True,
    }, ensure_ascii=False))
    async for msg in ws:
        if msg.type == WSMsgType.TEXT:
            # WS is intentionally not a data/command channel anymore.
            with contextlib.suppress(Exception):
                data = json.loads(msg.data)
                if data.get("action") == "ping":
                    await ws.send_str(json.dumps({"type": "pong", "ts": time.time()}, ensure_ascii=False))
    hub.websockets.discard(ws)
    return ws


# ---------------------------------------------------------------------------
# Transport layer: HTTP command/data API
# ---------------------------------------------------------------------------

def cors_headers():
    return {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET,POST,DELETE,OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type",
    }


@web.middleware
async def cors_middleware(request, handler):
    if request.method == "OPTIONS":
        return web.Response(status=204, headers=cors_headers())
    resp = await handler(request)
    for k, v in cors_headers().items():
        resp.headers[k] = v
    return resp


def json_ok(data: dict, status: int = 200):
    return web.json_response(data, status=status, headers=cors_headers(), dumps=lambda x: json.dumps(x, ensure_ascii=False, default=str))


async def read_json(request) -> dict:
    if request.can_read_body:
        try:
            data = await request.json()
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}
    return {}


async def status_handler(request):
    return json_ok({
        "ok": True,
        "running": True,
        "ready": True,
        "gaRoot": manager.ga_root,
        "mykeyPath": manager.mykey_path,
        "sessionCount": len(manager.sessions),
        "activeSessionId": manager.active_session_id,
        "ws": "/ws",
        "transport": {"http": True, "wsEventsOnly": True},
    })


async def get_config_handler(request):
    return json_ok({"gaRoot": manager.ga_root, "mykeyPath": manager.mykey_path, "config": manager.config})


async def save_config_handler(request):
    data = await read_json(request)
    cfg = data.get("config", data)
    if isinstance(cfg, dict):
        manager.config.update(cfg)
    return json_ok({"ok": True, "gaRoot": manager.ga_root, "mykeyPath": manager.mykey_path, "config": manager.config})


async def model_profiles_handler(request):
    return json_ok({"profiles": manager.list_model_profiles()})


async def list_sessions_handler(request):
    with manager.lock:
        sessions = [manager.snapshot(s, include_messages=False) for s in manager.sessions.values()]
    return json_ok({"sessions": sessions, "activeSessionId": manager.active_session_id})


async def new_session_handler(request):
    data = await read_json(request)
    sess = manager.create_session(cwd=data.get("cwd") or data.get("path"))
    return json_ok({"ok": True, "sessionId": sess.id, "session": manager.snapshot(sess)}, status=201)


async def get_session_handler(request):
    sid = request.match_info["sid"]
    sess = manager.get_session(sid)
    return json_ok({"sessionId": sid, "session": manager.snapshot(sess), "messages": list(sess.messages), "partial": sess.partial})


async def delete_session_handler(request):
    sid = request.match_info["sid"]
    return json_ok(manager.delete_session(sid))


async def prompt_handler(request):
    sid = request.match_info["sid"]
    data = await read_json(request)
    prompt = data.get("prompt", data.get("content", data.get("message", "")))
    attachments = data.get("attachments") or data.get("images") or []
    return json_ok(manager.submit_prompt(sid, prompt, attachments))


async def messages_handler(request):
    sid = request.match_info["sid"]
    after = int(request.query.get("after") or request.query.get("afterId") or 0)
    limit = int(request.query.get("limit") or 200)
    return json_ok(manager.messages(sid, after=after, limit=limit))


async def activity_handler(request):
    sid = request.match_info["sid"]
    return json_ok(manager.activity(sid))


async def upload_handler(request):
    reader = await request.multipart()
    part = await reader.next()
    if part is None or part.name != "file" or not part.filename:
        raise web.HTTPBadRequest(text="missing upload file")
    content_type = (part.headers.get("Content-Type") or "").split(";", 1)[0].lower()
    extension = _ALLOWED_UPLOAD_TYPES.get(content_type)
    if not extension:
        raise web.HTTPUnsupportedMediaType(text="only image and video files are supported")
    upload_id = "media-" + uuid.uuid4().hex[:16]
    destination = _UPLOAD_DIR / f"{upload_id}{extension}"
    size = 0
    try:
        with destination.open("wb") as output:
            while True:
                chunk = await part.read_chunk(size=1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > _MAX_UPLOAD_BYTES:
                    raise web.HTTPRequestEntityTooLarge(
                        max_size=_MAX_UPLOAD_BYTES,
                        actual_size=size,
                    )
                output.write(chunk)
    except Exception:
        with contextlib.suppress(OSError):
            destination.unlink()
        raise
    kind = "video" if content_type.startswith("video/") else "image"
    return json_ok({
        "ok": True,
        "id": upload_id,
        "name": Path(part.filename).name,
        "path": str(destination),
        "kind": kind,
        "contentType": content_type,
        "size": size,
        "previewUrl": f"/upload/{upload_id}",
    }, status=201)


async def upload_preview_handler(request):
    upload_id = request.match_info["upload_id"]
    if not re.fullmatch(r"media-[a-f0-9]{16}", upload_id):
        raise web.HTTPNotFound()
    matches = list(_UPLOAD_DIR.glob(f"{upload_id}.*"))
    if len(matches) != 1 or not matches[0].is_file():
        raise web.HTTPNotFound(text="upload not found")
    response = web.FileResponse(matches[0])
    content_type, _ = mimetypes.guess_type(str(matches[0]))
    if content_type:
        response.content_type = content_type
    response.headers["Cache-Control"] = "private, max-age=3600"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


async def browser_preview_handler(request):
    tab_id = request.match_info["tab_id"]
    preview = manager.browser_preview(tab_id)
    if preview is None:
        raise web.HTTPNotFound(text="preview not ready")
    return web.Response(
        body=preview,
        content_type="image/jpeg",
        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
    )


async def artifact_handler(request):
    sid = request.match_info["sid"]
    sess = manager.get_session(sid)
    task_dir = str(getattr(sess.agent, "task_dir", "") or "") if sess.agent else ""
    if not task_dir:
        raise web.HTTPNotFound(text="task workspace not available")
    task_root = Path(task_dir).resolve()
    relative = request.query.get("path") or ""
    try:
        target = (task_root / relative).resolve()
        target.relative_to(task_root)
    except (OSError, RuntimeError, ValueError):
        raise web.HTTPForbidden(text="artifact path is outside task workspace")
    if not target.is_file():
        raise web.HTTPNotFound(text="artifact not found")
    content_type, _ = mimetypes.guess_type(str(target))
    response = web.FileResponse(target)
    if content_type:
        response.content_type = content_type
    response.headers["Cache-Control"] = "private, max-age=2"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


async def platform_status_handler(request):
    force = request.query.get("force", "").lower() in ("1", "true", "yes")
    status = await asyncio.to_thread(manager.platform_status, force)
    return json_ok(status)


async def platform_open_handler(request):
    data = await read_json(request)
    platform = str(data.get("platform") or "")
    urls = {
        "xiaohongshu": "https://www.xiaohongshu.com/explore",
        "douyin": "https://www.douyin.com/",
    }
    url = urls.get(platform)
    if not url:
        raise web.HTTPBadRequest(
            text=json.dumps({"error": "unsupported platform"}, ensure_ascii=False),
            content_type="application/json",
        )
    import webbrowser
    opened = await asyncio.to_thread(webbrowser.open, url)
    manager._platform_cache_at = 0.0
    return json_ok({"ok": bool(opened), "platform": platform, "url": url})


async def cancel_handler(request):
    sid = request.match_info["sid"]
    return json_ok(manager.cancel(sid))


async def path_open_handler(request):
    data = await read_json(request)
    kind = data.get("kind", "")
    if kind == "mykey":
        target = Path(manager.ga_root) / "mykey.py"
    else:
        target = Path(data.get("path") or data.get("target") or manager.ga_root)
    target = target.resolve()
    if not target.exists():
        return json_ok({"ok": False, "error": f"File not found: {target}"})
    # Actually open the file with the system default editor
    import subprocess, platform
    if platform.system() == "Windows":
        os.startfile(str(target))
    elif platform.system() == "Darwin":
        subprocess.Popen(["open", str(target)])
    else:
        subprocess.Popen(["xdg-open", str(target)])
    return json_ok({"ok": True, "path": str(target)})


def create_app():
    app = web.Application(
        middlewares=[cors_middleware],
        client_max_size=_MAX_UPLOAD_BYTES + 1024 * 1024,
    )
    app.router.add_get("/ws", ws_handler)
    app.router.add_get("/status", status_handler)
    app.router.add_get("/config", get_config_handler)
    app.router.add_post("/config", save_config_handler)
    app.router.add_get("/model-profiles", model_profiles_handler)
    app.router.add_get("/sessions", list_sessions_handler)
    app.router.add_post("/session/new", new_session_handler)
    app.router.add_get("/session/{sid}", get_session_handler)
    app.router.add_delete("/session/{sid}", delete_session_handler)
    app.router.add_post("/session/{sid}/prompt", prompt_handler)
    app.router.add_get("/session/{sid}/messages", messages_handler)
    app.router.add_get("/session/{sid}/activity", activity_handler)
    app.router.add_get("/session/{sid}/artifact", artifact_handler)
    app.router.add_post("/session/{sid}/cancel", cancel_handler)
    app.router.add_post("/upload", upload_handler)
    app.router.add_get("/upload/{upload_id}", upload_preview_handler)
    app.router.add_get("/browser-preview/{tab_id}", browser_preview_handler)
    app.router.add_get("/platform/status", platform_status_handler)
    app.router.add_post("/platform/open", platform_open_handler)
    app.router.add_post("/path/open", path_open_handler)

    # Serve static frontend (desktop/static/)
    static_dir = APP_DIR / "desktop" / "static"

    async def index_handler(request):
        return web.FileResponse(static_dir / "index.html")

    app.router.add_get("/", index_handler)
    app.router.add_static("/", static_dir, show_index=False)

    async def on_startup(app):
        hub.loop = asyncio.get_running_loop()

    app.on_startup.append(on_startup)
    return app


if __name__ == "__main__":
    host = os.environ.get("BRIDGE_HOST", "127.0.0.1")
    port = int(os.environ.get("BRIDGE_PORT", "14168"))
    print(f"GenericAgent Web2 bridge: http://{host}:{port}  ws://{host}:{port}/ws", file=sys.stderr)
    web.run_app(create_app(), host=host, port=port, print=None)
