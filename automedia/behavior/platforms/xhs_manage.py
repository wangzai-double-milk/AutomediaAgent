"""Read creator pages and perform target-scoped operations with explicit evidence.

Routes beyond known public entry points are discovered from the logged-in page.
Unsupported/permission-gated areas return evidence, never fabricated metrics.
"""

# Platform implementation; legacy tools imports share this module.
import json
import re
from pathlib import Path
from urllib.parse import urlsplit

from tools import xhs_browser as browser
from tools.xhs_publish import atomic_json, VISIBILITY

AREAS = {
    "notes": ["笔记管理", "作品管理", "内容管理"],
    "overview": ["账号概览", "数据中心", "数据概览", "账号数据"],
    "audience": ["粉丝数据", "粉丝分析"],
    "note_metrics": ["内容分析", "笔记数据", "作品数据"],
    "drafts": ["草稿箱", "草稿"],
    "collections": ["合集管理", "我的合集", "合集"],
    "longform": ["发布长文", "写长文", "长文"],
    "podcast": ["发播客", "播客"],
    "products": ["商品管理", "带货中心", "商品"],
    "live": ["直播中心", "直播管理", "直播"],
    "ads": ["推广中心", "广告投放", "专业号中心", "薯条"],
    "messages": ["私信", "消息"],
    "account": ["账号设置", "个人资料", "设置"],
}
NOTIFICATION_TABS = {"comments": ["评论和@", "评论和提及", "评论与提及"],
                     "likes": ["赞和收藏", "赞与收藏"], "follows": ["新增关注", "关注"]}


def _id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{4,80}", value):
        raise ValueError("目标 ID 格式无效")
    return value


def page_snapshot(tab):
    """Only rendered business data, bounded; no whole user state, cookies or keys."""
    return browser.execute("""(function(){
      const visible=e=>e.getClientRects().length;
      const links=Array.from(document.querySelectorAll('a[href]')).filter(visible)
        .map(e=>({text:(e.innerText||'').trim().slice(0,100),url:e.href})).filter(x=>x.text);
      const sections=Array.from(document.querySelectorAll(
        '[class*="note-item"],[class*="note-card"],[class*="notification-item"],[class*="message-item"],tr'))
        .filter(visible).slice(0,100).map(e=>({
          id:e.getAttribute('data-note-id')||e.getAttribute('data-id')||e.id||null,
          text:(e.innerText||'').slice(0,3000),
          links:Array.from(e.querySelectorAll('a[href]')).map(a=>({text:(a.innerText||'').slice(0,100),url:a.href}))
        }));
      const metrics=Array.from(document.querySelectorAll(
        '[class*="data-card"],[class*="metric"],[class*="overview-item"],[class*="stat-item"]'))
        .filter(visible).map(e=>(e.innerText||'').trim()).filter(Boolean).slice(0,80);
      const selfLink=Array.from(document.querySelectorAll('a[href*="/user/profile/"]')).find(e=>
        /我|个人主页/.test(e.innerText||'')||e.closest('[class*="user"],[class*="avatar"]'));
      const body=(document.body?.innerText||'').slice(0,20000);
      return JSON.stringify({url:location.href,title:document.title,links,sections,metrics,
        self_url:selfLink?.href||null,text:body,login_required:/扫码登录|登录后查看|手机号登录/.test(body),
        restricted:/暂无权限|开通专业号|暂未开通|申请开通/.test(body)});
    })()""", tab)


def _ready(tab):
    result = browser.wait(lambda: page_snapshot(tab),
                          lambda v: isinstance(v, dict) and v.get("text"), 12)
    if not result:
        raise RuntimeError("page_snapshot_unavailable")
    if result.get("login_required"):
        raise RuntimeError("login_required")
    return result


def open_area(area, tab=None, transport="cdp"):
    if area not in AREAS:
        raise ValueError("未知创作者区域")
    if tab is None and transport != "cdp":
        from tools.xhs_publish import select_transport
        if select_transport(transport) == "desktop":
            from tools.xhs_desktop import open_area as desktop_area
            return desktop_area(area)
    tab = tab or browser.connect(creator=True)
    url = ("https://creator.xiaohongshu.com/publish/publish?source=official"
           if area in {"drafts", "longform", "podcast"} else "https://creator.xiaohongshu.com")
    browser.navigate(url, tab)
    page = _ready(tab)
    labels = AREAS[area]
    if area in {"overview", "audience", "note_metrics"} and not any(link["text"] in labels for link in page["links"]):
        # The observed sidebar contains a collapsible 数据看板 parent.
        browser.click_text(["数据看板"], tab, 'nav,aside,[class*="menu"],[class*="sidebar"]')
        page = _ready(tab)
    targets = [link for link in page["links"] if link["text"] in labels
               and urlsplit(link["url"]).hostname == "creator.xiaohongshu.com"]
    urls = list(dict.fromkeys(link["url"] for link in targets))
    if len(urls) == 1:
        browser.navigate(urls[0], tab)
    else:
        if area == "drafts":
            # Actual page uses 草稿箱(N), so select the exact observed counter label.
            label = browser.execute("""JSON.stringify(Array.from(document.querySelectorAll('span,button,div'))
              .filter(e=>e.getClientRects().length&&/^草稿箱[（(]\\d+[）)]$/.test((e.innerText||'').trim()))
              .map(e=>(e.innerText||'').trim()).filter((x,i,a)=>a.indexOf(x)===i))""", tab)
            if isinstance(label, list) and len(label) == 1:
                labels = label
        scope = "body" if area in {"drafts", "longform", "podcast"} else 'nav,aside,[class*="menu"],[class*="sidebar"]'
        control = browser.click_text(labels, tab, scope)
        if not control.get("ok"):
            return {"ok": False, "status": "unavailable", "area": area, "tab_id": tab,
                    "reason": "当前网页未发现唯一入口；可能需要账号权限或客户端。",
                    "available_links": page["links"], "evidence_url": page["url"]}
    result = _ready(tab)
    return {"ok": not result.get("restricted"), "status": "restricted" if result.get("restricted") else "ok",
            "area": area, "tab_id": tab, "page": result, "evidence_url": result["url"]}


def notifications(kind="comments", pages=1, transport="cdp"):
    if kind not in NOTIFICATION_TABS:
        raise ValueError("kind 必须是 comments/likes/follows")
    if not isinstance(pages, int) or not 1 <= pages <= 10:
        raise ValueError("pages 必须是 1–10")
    if transport != "cdp":
        from tools.xhs_publish import select_transport
        if select_transport(transport) == "desktop":
            from tools.xhs_desktop import notifications as desktop_notifications
            return desktop_notifications(kind, pages)
    tab = browser.connect()
    browser.navigate("https://www.xiaohongshu.com/notification", tab)
    clicked = browser.click_text(NOTIFICATION_TABS[kind], tab)
    if not clicked.get("ok"):
        return {"ok": False, "reason": "notification_tab_unavailable", "control": clicked}
    seen, records = set(), []
    for _ in range(pages):
        snapshot = _ready(tab)
        for row in snapshot.get("sections", []):
            key = json.dumps(row, sort_keys=True, ensure_ascii=False)
            if key not in seen:
                seen.add(key)
                records.append(row)
        browser.execute("""(function(){
          const el=document.querySelector('[class*="notification-container"],[class*="list-container"]');
          if(el)el.scrollTop=el.scrollHeight;else window.scrollTo(0,document.body.scrollHeight);return true;
        })()""", tab)
        browser.wait(lambda: page_snapshot(tab),
                     lambda v: isinstance(v, dict) and any(json.dumps(row, sort_keys=True, ensure_ascii=False) not in seen
                                                           for row in v.get("sections", [])), 2)
    return {"ok": True, "kind": kind, "count": len(records), "notifications": records,
            "evidence_url": snapshot["url"], "text": snapshot["text"],
            "structured": bool(records), "reason": None if records else "未提取到结构化通知；请根据页面文字判读。"}


def follow_user(user_id, xsec_token="", unfollow=False, xsec_source="pc_note"):
    from tools.xhs_account import assert_writable
    assert_writable()
    from tools.xhs_test_control import preflight
    preflight(transport="cdp")
    user_id = _id(user_id)
    from tools.xhs_actions import _user_profile_url
    tab = browser.connect()
    browser.navigate(_user_profile_url(user_id, xsec_token, xsec_source), tab)
    labels = ["关注", "已关注", "互相关注", "取消关注"]
    def state():
        return browser.execute("""(function(labels){
          const header=document.querySelector('.user-info,[class*="user-info"],[class*="profile-header"]');
          if(!header)return null;
          const controls=Array.from(header.querySelectorAll('button,[role="button"],[class*="follow"]'))
            .filter(e=>e.getClientRects().length&&labels.includes((e.innerText||'').trim()));
          const values=Array.from(new Set(controls.map(e=>(e.innerText||'').trim())));
          return JSON.stringify(values.length===1?values[0]:null);
        })(%s)""" % json.dumps(labels, ensure_ascii=False), tab)
    before = browser.wait(state, lambda v: v in labels, 6)
    if before is None:
        return {"ok": False, "reason": "follow_state_unknown"}
    following = before in {"已关注", "互相关注", "取消关注"}
    desired = not unfollow
    if following == desired:
        return {"ok": True, "verified": True, "skipped": True, "following": following, "user_id": user_id}
    clicked = browser.click_text([before], tab, '.user-info,[class*="user-info"],[class*="profile-header"]')
    if not clicked.get("ok"):
        return clicked
    if unfollow:
        # Confirmation is optional; never use an unscoped generic confirm.
        browser.click_text(["取消关注", "确认取消"], tab, '[role="dialog"],[class*="modal"]')
    after = browser.wait(state, lambda v: v in labels and (v != "关注") == desired, 6)
    return {"ok": after is not None, "verified": after is not None, "user_id": user_id,
            "before": before, "after": after, "following": desired if after else None,
            "reason": None if after else "follow_result_unverified"}


def own_profile(transport="cdp"):
    if transport != "cdp":
        from tools.xhs_publish import select_transport
        if select_transport(transport) == "desktop":
            from tools.xhs_read_desktop import Actions
            actions = Actions()
            login = actions.check_login()
            if not login.get("logged_in") or not login.get("user_id"):
                return {"ok": False, "reason": "own_identity_unavailable"}
            return actions.user_profile(login["user_id"])
    from tools.xhs_actions import check_login
    login = check_login()
    if not login.get("logged_in") or not login.get("user_id"):
        return {"ok": False, "reason": "own_identity_unavailable"}
    tab = browser.connect()
    browser.navigate("https://www.xiaohongshu.com/user/profile/" + _id(login["user_id"]), tab)
    return {"ok": True, "user_id": login["user_id"], "page": _ready(tab)}


def _note_card(note_id, tab):
    """Match ID from attributes/URLs, never by title/substrings in rendered text."""
    return browser.execute("""(function(id){
      const candidates=Array.from(document.querySelectorAll(
        '[data-note-id],[class*="note-item"],[class*="note-card"],tr'))
        .filter(e=>e.getClientRects().length);
      const hits=candidates.filter(e=>e.getAttribute('data-note-id')===id||e.getAttribute('data-id')===id||
        Array.from(e.querySelectorAll('a[href]')).some(a=>{
          const u=new URL(a.href);return u.pathname.split('/').includes(id)||
            ['note_id','noteId','id'].some(k=>u.searchParams.get(k)===id);
        }));
      const leaves=hits.filter(e=>!hits.some(other=>other!==e&&e.contains(other)));
      if(leaves.length!==1)return JSON.stringify({ok:false,reason:'note_identity_ambiguous',count:leaves.length});
      const e=leaves[0];e.setAttribute('data-automedia-target',id);
      return JSON.stringify({ok:true,text:e.innerText,links:Array.from(e.querySelectorAll('a[href]'))
        .map(a=>({text:(a.innerText||'').trim(),url:a.href}))});
    })(%s)""" % json.dumps(note_id), tab)


def manage_note(args, task):
    """Delete or open edit for a verified owner; uncertain writes remain journaled."""
    if args.get("action", "prepare_edit") != "prepare_edit":
        from tools.xhs_account import assert_writable
        assert_writable()
    if args.get("transport", "auto") != "cdp":
        from tools.xhs_publish import select_transport
        from tools.xhs_test_control import status as test_status
        if test_status().get("enabled") or select_transport(args.get("transport", "auto")) == "desktop":
            from tools.xhs_desktop import manage_note as desktop_manage
            return desktop_manage(args, task)
    note_id, owner_id = _id(args.get("note_id")), _id(args.get("owner_id"))
    action = args.get("action", "prepare_edit")
    if action != "prepare_edit":
        from tools.xhs_test_control import preflight
        preflight(transport="cdp")
    if action not in {"prepare_edit", "delete", "visibility", "edit"}:
        raise ValueError("action 必须是 prepare_edit/delete/visibility/edit")
    from tools.xhs_actions import check_login
    identity = check_login()
    if not identity.get("logged_in") or identity.get("user_id") != owner_id:
        return {"ok": False, "reason": "owner_identity_mismatch"}
    opened = open_area("notes")
    if not opened.get("ok"):
        return opened
    tab = opened["tab_id"]
    card = _note_card(note_id, tab)
    if not isinstance(card, dict) or not card.get("ok"):
        return card or {"ok": False, "reason": "target_note_not_found"}
    scope = '[data-automedia-target="' + note_id + '"]'
    directory = task.ensure_task(hint="manage_note")
    path = Path(directory) / "management_attempts.json"
    states = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    import hashlib
    op = hashlib.sha256(json.dumps({k: v for k, v in args.items() if not k.startswith("_")},
                                  sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    if action != "prepare_edit" and op in states:
        return {"ok": bool(states[op].get("verified")), "status": "ok" if states[op].get("verified") else "unverified",
                "skipped": True, "attempt_id": op, "previous": states[op]}
    if action == "delete":
        states[op] = {"verified": False, "note_id": note_id, "owner_id": owner_id, "action": action}
        atomic_json(path, states)
        clicked = browser.click_text(["删除"], tab, scope)
        if not clicked.get("ok"):
            # No control was clicked; safe to retry this pre-submit failure.
            states.pop(op)
            atomic_json(path, states)
            return clicked
        dialog = browser.execute("""JSON.stringify(Array.from(document.querySelectorAll(
          '[role="dialog"],[class*="modal"]')).filter(e=>e.getClientRects().length)
          .map(e=>e.innerText).filter(t=>/删除/.test(t)))""", tab)
        if not isinstance(dialog, list) or len(dialog) != 1:
            return {"ok": False, "status": "unverified", "reason": "delete_confirmation_not_identified", "attempt_id": op}
        confirmed = browser.click_text(["删除", "确认删除", "确定"], tab, '[role="dialog"],[class*="modal"]')
        if not confirmed.get("ok"):
            return {"ok": False, "status": "unverified", "reason": "delete_confirmation_failed", "attempt_id": op}
        result = browser.wait(lambda: browser.execute("""JSON.stringify(Array.from(document.querySelectorAll(
          '[role="alert"],[class*="toast"],[class*="message"]')).filter(e=>e.getClientRects().length)
          .some(e=>/^删除成功[！!。\\s]*$/.test((e.innerText||'').trim())))""", tab), lambda v: v is True, 8)
        states[op]["verified"] = result is True
        atomic_json(path, states)
        task.append_trace("manage_note_delete", states[op])
        return {"ok": result is True, "status": "ok" if result else "unverified", "attempt_id": op,
                "note_id": note_id, "verified": result is True}
    edit_links = [link for link in card["links"] if "编辑" in link["text"]
                  and urlsplit(link["url"]).hostname == "creator.xiaohongshu.com"]
    if len(edit_links) == 1:
        browser.navigate(edit_links[0]["url"], tab)
    else:
        clicked = browser.click_text(["编辑", "修改"], tab, scope)
        if not clicked.get("ok"):
            return clicked
    if action == "prepare_edit":
        return {"ok": True, "status": "prepared", "submitted": False, "note_id": note_id,
                "tab_id": tab, "page": _ready(tab)}
    from tools import xhs_publish
    if action == "edit":
        snapshot = xhs_publish.editor_snapshot(tab)
        spec = {"title": args.get("title") or snapshot.get("title"),
                "desc": args.get("desc") or snapshot.get("desc"), "tags": []}
        xhs_publish.fill_editor(spec, tab)
    else:
        from ga import cdp_set_xhs_visibility
        level = args.get("visibility")
        if level not in VISIBILITY:
            raise ValueError("visibility 必须是 self/public/friends")
        vis = cdp_set_xhs_visibility(level, switch_tab_id=tab)
        if not vis.get("ok") or vis.get("now") != VISIBILITY[level]:
            return {"ok": False, "reason": "visibility_not_verified", "visibility": vis}
    states[op] = {"verified": False, "note_id": note_id, "owner_id": owner_id, "action": action}
    atomic_json(path, states)
    saved = browser.click_text(["保存修改", "确认修改", "更新笔记"], tab)
    if not saved.get("ok"):
        states.pop(op)
        atomic_json(path, states)
        return {"ok": False, "status": "prepared", "submitted": False, "reason": "edit_save_control_unavailable",
                "note_id": note_id, "tab_id": tab}
    verified = browser.wait(lambda: browser.execute("""JSON.stringify(Array.from(document.querySelectorAll(
      '[role="alert"],[class*="toast"],[class*="success"]')).filter(e=>e.getClientRects().length)
      .some(e=>/^修改成功|^保存成功|^更新成功/.test((e.innerText||'').trim())))""", tab), lambda v: v is True, 8)
    states[op]["verified"] = verified is True
    atomic_json(path, states)
    task.append_trace("manage_note_update", states[op])
    return {"ok": verified is True, "status": "ok" if verified else "unverified",
            "attempt_id": op, "note_id": note_id, "verified": verified is True}
