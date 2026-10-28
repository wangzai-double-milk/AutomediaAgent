"""Small, pinned browser operations shared by the creator/operations modules."""

import json
import time
from urllib.parse import urlsplit


def execute(script, tab):
    from ga import web_execute_js
    result = web_execute_js(script, switch_tab_id=tab, no_monitor=True)
    if not isinstance(result, dict):
        return None
    value = result.get("js_return")
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


def connect(creator=False):
    from ga import ensure_xhs_tab
    info = ensure_xhs_tab(want_creator=creator)
    if not info.get("ok"):
        raise RuntimeError("browser_not_connected: " + str(info.get("reason")))
    return info["tab_id"]


def navigate(url, tab, timeout=15):
    host = urlsplit(url).hostname
    if host not in {"www.xiaohongshu.com", "creator.xiaohongshu.com"}:
        raise ValueError("unsupported navigation host")
    expected = urlsplit(url)
    execute(f"location.href={json.dumps(url)}; 'navigating'", tab)
    value = wait(lambda: execute(
        "JSON.stringify({url:location.href,ready:document.readyState,"
        "text:(document.body?.innerText||'').slice(0,200)})", tab),
        lambda v: isinstance(v, dict) and urlsplit(v.get("url", "")).hostname == host
        and (urlsplit(v.get("url", "")).path.rstrip("/") == expected.path.rstrip("/")
             or (expected.path in ("", "/") and urlsplit(v.get("url", "")).path in ("/new/home", "/home")))
        and (not expected.query or urlsplit(v.get("url", "")).query == expected.query)
        and v.get("ready") == "complete" and v.get("text"), timeout)
    if not value:
        raise RuntimeError("page_navigation_timeout")
    if urlsplit(value["url"]).path.startswith("/login"):
        raise RuntimeError("login_required")
    return value


def wait(read, accepted, timeout=10, interval=0.4):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = read()
        if accepted(value):
            return value
        time.sleep(interval)
    return None


def click_text(texts, tab, scope="body"):
    """Return one visible coordinate, reject ambiguous text, then CDP click."""
    from ga import cdp_mouse_click
    script = """(function(labels,scope){
      const roots=Array.from(document.querySelectorAll(scope));
      const hits=roots.flatMap(root=>Array.from(root.querySelectorAll(
        'button,a,[role="button"],[role="tab"],label,span,div')))
        .filter(e=>e.getClientRects().length&&labels.includes((e.innerText||'').trim())
          && !e.disabled && e.getAttribute('aria-disabled')!=='true');
      const leaves=hits.filter(e=>!hits.some(other=>other!==e&&e.contains(other)));
      if(leaves.length!==1)return JSON.stringify({ok:false,reason:'ambiguous_or_missing_control',count:leaves.length});
      const e=leaves[0];e.scrollIntoView({block:'center'});const r=e.getBoundingClientRect();
      return JSON.stringify({ok:true,x:r.x+r.width/2,y:r.y+r.height/2,text:e.innerText});
    })(%s,%s)""" % (json.dumps(texts, ensure_ascii=False), json.dumps(scope))
    result = execute(script, tab)
    if not isinstance(result, dict) or not result.get("ok"):
        return result or {"ok": False, "reason": "browser_no_result"}
    clicked = cdp_mouse_click(result["x"], result["y"], switch_tab_id=tab)
    if isinstance(clicked, dict) and clicked.get("error"):
        return {"ok": False, "reason": clicked["error"]}
    return result
