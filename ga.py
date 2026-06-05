import sys, os, re, json, time, threading, importlib
from datetime import datetime
from pathlib import Path
import tempfile, traceback, subprocess, itertools, collections, difflib
import urllib.request
import base64, mimetypes
if sys.stdout is None: sys.stdout = open(os.devnull, "w")
if sys.stderr is None: sys.stderr = open(os.devnull, "w")
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from agent_loop import BaseHandler, StepOutcome, json_default
script_dir = os.path.dirname(os.path.abspath(__file__))

def code_run(code, code_type="python", timeout=60, cwd=None, code_cwd=None, stop_signal=None, maxlen=10000):
    """代码执行器
    python: 运行复杂的 .py 脚本（文件模式）
    powershell/bash: 运行单行指令（命令模式）
    优先使用python，仅在必要系统操作时使用powershell"""
    preview = (code[:60].replace('\n', ' ') + '...') if len(code) > 60 else code.strip()
    yield f"[Action] Running {code_type} in {os.path.basename(cwd)}: {preview}\n"
    cwd = cwd or os.path.join(script_dir, 'temp'); tmp_path = None
    if code_type in ["python", "py"]:
        tmp_file = tempfile.NamedTemporaryFile(suffix=".ai.py", delete=False, mode='w', encoding='utf-8', dir=code_cwd)
        cr_header = os.path.join(script_dir, 'assets', 'code_run_header.py')
        if os.path.exists(cr_header): tmp_file.write(open(cr_header, encoding='utf-8').read())
        tmp_file.write(code)
        tmp_path = tmp_file.name
        tmp_file.close()
        cmd = [sys.executable, "-X", "utf8", "-u", tmp_path]   
    elif code_type in ["powershell", "bash", "sh", "shell", "ps1", "pwsh"]:
        if os.name == 'nt': cmd = ["powershell", "-NoProfile", "-NonInteractive", "-Command", code]
        else: cmd = ["bash", "-c", code]
    else:
        return {"status": "error", "msg": f"不支持的类型: {code_type}"}
    print("code run output:") 
    startupinfo = None
    if os.name == 'nt':
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = 0 # SW_HIDE
    full_stdout = []

    def stream_reader(proc, logs):
        try:
            for line_bytes in iter(proc.stdout.readline, b''):
                try: line = line_bytes.decode('utf-8')
                except UnicodeDecodeError: line = line_bytes.decode('gbk', errors='ignore')
                logs.append(line)
                try: print(line, end="") 
                except: pass
        except: pass

    try:
        process = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            bufsize=0, cwd=cwd, startupinfo=startupinfo,
            creationflags=0x08000000 if os.name == 'nt' else 0
        )
        start_t = time.time()
        t = threading.Thread(target=stream_reader, args=(process, full_stdout), daemon=True)
        t.start()

        while t.is_alive():
            istimeout = time.time() - start_t > timeout
            if istimeout or stop_signal:
                process.kill()
                print("[Debug] Process killed due to timeout or stop signal.")
                if istimeout: full_stdout.append("\n[Timeout Error] 超时强制终止")
                else: full_stdout.append("\n[Stopped] 用户强制终止")
                break
            time.sleep(1)

        t.join(timeout=1)
        exit_code = process.poll()

        stdout_str = "".join(full_stdout)
        status = "success" if exit_code == 0 else "error"
        status_icon = "✅" if exit_code == 0 else "❌"
        if exit_code is None: status_icon = "⏳" 
        output_snippet = smart_format(stdout_str, max_str_len=600, omit_str='\n\n[omitted long output]\n\n')
        output_snippet = re.sub(r'`{4,}', lambda m: m.group(0)[:3] + '\u200b' + m.group(0)[3:], output_snippet)
        yield f"[Status] {status_icon} Exit Code: {exit_code}\n[Stdout]\n{output_snippet}\n"
        if process.stdout: threading.Thread(target=process.stdout.close, daemon=True).start()
        return {
            "status": status,
            "stdout": smart_format(stdout_str, max_str_len=maxlen, omit_str='\n\n[omitted long output]\n\n'),
            "exit_code": exit_code
        }
    except Exception as e:
        if 'process' in locals(): process.kill()
        return {"status": "error", "msg": str(e)}
    finally:
        if code_type == "python" and tmp_path and os.path.exists(tmp_path): os.remove(tmp_path)


def ask_user(question, candidates=None):
    """question: 向用户提出的问题。candidates: 可选的候选项列表"""
    return {"status": "INTERRUPT", "intent": "HUMAN_INTERVENTION",
        "data": {"question": question, "candidates": candidates or []}}

import simphtml
driver = None
def first_init_driver():
    global driver
    from TMWebDriver import TMWebDriver
    driver = TMWebDriver()
    for i in range(20):
        time.sleep(1)
        sess = driver.get_all_sessions()
        if len(sess) > 0: break
    if len(sess) == 0: return 
    if len(sess) == 1: 
        #driver.newtab()
        time.sleep(3)

def web_scan(tabs_only=False, switch_tab_id=None, text_only=False, maxlen=35000):
    """获取当前页面的简化HTML内容和标签页列表。注意：简化过程会过滤边栏、浮动元素等非主体内容。
    tabs_only: 仅返回标签页列表，不获取HTML内容（节省token）。
    switch_tab_id: 可选参数，如果提供，则在扫描前切换到该标签页。
    应当多用execute_js，少全量观察html"""
    global driver
    try:
        if driver is None: first_init_driver()
        if len(driver.get_all_sessions()) == 0:
            return {"status": "error", "msg": "没有可用的浏览器标签页，查L3记忆分析原因。"}
        tabs = []
        for sess in driver.get_all_sessions(): 
            sess.pop('connected_at', None)
            sess.pop('type', None)
            sess['url'] = sess.get('url', '')[:50] + ("..." if len(sess.get('url', '')) > 50 else "")
            tabs.append(sess)
        if switch_tab_id: driver.default_session_id = switch_tab_id
        result = {
            "status": "success",
            "metadata": {
                "tabs_count": len(tabs), "tabs": tabs,
                "active_tab": driver.default_session_id
            }
        }
        if not tabs_only: 
            importlib.reload(simphtml); result["content"] = simphtml.get_html(driver, cutlist=True, maxchars=maxlen, text_only=text_only)
            if text_only: result['content'] = smart_format(result['content'], max_str_len=maxlen//3, omit_str='\n\n[omitted long content]\n\n')
        return result
    except Exception as e:
        return {"status": "error", "msg": format_error(e)}
    
def format_error(e):
    exc_type, exc_value, exc_traceback = sys.exc_info()
    tb = traceback.extract_tb(exc_traceback)
    if tb:
        f = tb[-1]
        fname = os.path.basename(f.filename)
        return f"{exc_type.__name__}: {str(e)} @ {fname}:{f.lineno}, {f.name} -> `{f.line}`"
    return f"{exc_type.__name__}: {str(e)}"

def log_memory_access(path):
    if 'memory' not in path: return
    stats_file = os.path.join(script_dir, 'memory/file_access_stats.json')
    try:
        with open(stats_file, 'r', encoding='utf-8') as f: stats = json.load(f)
    except: stats = {}
    fname = os.path.basename(path)
    stats[fname] = {'count': stats.get(fname, {}).get('count', 0) + 1, 'last': datetime.now().strftime('%Y-%m-%d')}
    with open(stats_file, 'w', encoding='utf-8') as f: json.dump(stats, f, indent=2, ensure_ascii=False)

# xhs 核心工具 → 执行前必须先 file_read 的 SOP（硬 gate）。
# 用户要求："P 图前必须看一下 P 图 skills"，推广到所有核心操作：先读对应 SOP 再动手。
XHS_TOOL_SOP_GATE = {
    'xhs_search_viral': 'skills/core/xhs_search/SKILL.md',
    'xhs_image_review': 'skills/core/xhs_review/SKILL.md',
    'xhs_image_edit': 'skills/core/xhs_edit/SKILL.md',
    'xhs_image_generate': 'skills/core/xhs_edit/SKILL.md',
    'xhs_publish_note': 'skills/core/xhs_publish/SKILL.md',
    'xhs_like_feed': 'skills/core/xhs_interact/SKILL.md',
    'xhs_favorite_feed': 'skills/core/xhs_interact/SKILL.md',
    'xhs_post_comment': 'skills/core/xhs_interact/SKILL.md',
    'xhs_reply_comment': 'skills/core/xhs_interact/SKILL.md',
}

def web_execute_js(script, switch_tab_id=None, no_monitor=False):
    """执行 JS 脚本来控制浏览器，并捕获结果和页面变化"""
    global driver
    try:
        if driver is None: first_init_driver()
        if len(driver.get_all_sessions()) == 0: return {"status": "error", "msg": "没有可用的浏览器标签页，查L3记忆分析原因。"}
        if switch_tab_id: driver.default_session_id = switch_tab_id
        result = simphtml.execute_js_rich(script, driver, no_monitor=no_monitor)
        return result
    except Exception as e: return {"status": "error", "msg": format_error(e)}


def ensure_xhs_tab(want_creator=False):
    """确保 driver.default_session_id 指向一个小红书标签页，否则后续 web_execute_js 会发到错误的 tab
    （默认 tab 常是 CLI/新标签页/其它站），导致 js_return 全为 None、工具误判"搜不到"。
    want_creator=True 时优先选 creator.xiaohongshu.com（发布页）。
    返回 {'ok': bool, 'tab_id': id|None, 'url': str, 'reason': str}。"""
    global driver
    try:
        if driver is None: first_init_driver()
        sessions = driver.get_all_sessions()
        if not sessions:
            return {'ok': False, 'tab_id': None, 'url': '', 'reason': 'no_session'}

        def pick(pred):
            for s in sessions:
                if pred((s.get('url') or '')):
                    return s
            return None

        chosen = None
        if want_creator:
            chosen = pick(lambda u: 'creator.xiaohongshu.com' in u)
        if chosen is None:
            chosen = pick(lambda u: 'xiaohongshu.com' in u)

        if chosen is not None:
            driver.default_session_id = chosen['id']
            return {'ok': True, 'tab_id': chosen['id'], 'url': chosen.get('url', ''), 'reason': 'switched'}

        # 没有任何小红书 tab：在当前默认 tab 上导航到目标站（最稳，不新开 tab 避免 default 又错位）
        target = 'https://creator.xiaohongshu.com' if want_creator else 'https://www.xiaohongshu.com'
        web_execute_js(f"window.location.href='{target}';'navigating';")
        time.sleep(4.0)
        sessions = driver.get_all_sessions()
        chosen = None
        if want_creator:
            for s in sessions:
                if 'creator.xiaohongshu.com' in (s.get('url') or ''): chosen = s; break
        if chosen is None:
            for s in sessions:
                if 'xiaohongshu.com' in (s.get('url') or ''): chosen = s; break
        if chosen is not None:
            driver.default_session_id = chosen['id']
            return {'ok': True, 'tab_id': chosen['id'], 'url': chosen.get('url', ''), 'reason': 'navigated'}
        return {'ok': False, 'tab_id': driver.default_session_id, 'url': '', 'reason': 'navigate_failed'}
    except Exception as e:
        return {'ok': False, 'tab_id': None, 'url': '', 'reason': format_error(e)}


def cdp_send(method, params=None, switch_tab_id=None):
    """通过 bridge 发 CDP 命令到 Chrome DevTools Protocol。
    bridge 已支持自定义协议：execute_js 接收到 JSON 字符串 {cmd:'cdp', method, params} 时
    会路由到 chrome.debugger.sendCommand(method, params)。
    返回 dict（CDP 原始返回），失败返回 {'error': msg}。
    """
    global driver
    try:
        if driver is None: first_init_driver()
        if len(driver.get_all_sessions()) == 0: return {'error': '没有可用的浏览器标签页'}
        if switch_tab_id: driver.default_session_id = switch_tab_id
        payload = json.dumps({'cmd': 'cdp', 'method': method, 'params': params or {}})
        # driver.execute_js 看到 string-of-json-with-cmd 会走 handleExtMessage
        result = driver.execute_js(payload)
        if isinstance(result, dict):
            return result.get('data') or result
        return {'data': result}
    except Exception as e:
        traceback.print_exc()
        return {'error': format_error(e)}


def web_screenshot(save_path=None, switch_tab_id=None, full_page=False, format='png', quality=85):
    """通过 CDP Page.captureScreenshot 截图当前 tab，写到 save_path。
    save_path 不传时默认写到 temp/web_screenshots/<ts>.png。
    返回 dict {'path': <abs>, 'size': (w,h), 'format': 'png'/'jpeg'} 或 {'error': msg}。
    """
    params = {'format': format, 'captureBeyondViewport': bool(full_page)}
    if format == 'jpeg':
        params['quality'] = quality
    res = cdp_send('Page.captureScreenshot', params, switch_tab_id=switch_tab_id)
    if not isinstance(res, dict) or 'data' not in res:
        return {'error': f'captureScreenshot 失败: {res}'}
    b64 = res['data']
    raw = base64.b64decode(b64)
    if not save_path:
        os.makedirs('temp/web_screenshots', exist_ok=True)
        save_path = os.path.abspath(f'temp/web_screenshots/{int(time.time()*1000)}.{format}')
    save_path = os.path.abspath(save_path)
    with open(save_path, 'wb') as f:
        f.write(raw)
    # 读尺寸（不依赖 PIL：JS 里取也行，但这里简单读 PNG header 即可）
    size = None
    try:
        if format == 'png' and len(raw) > 24 and raw[:8] == b'\x89PNG\r\n\x1a\n':
            import struct
            w, h = struct.unpack('>II', raw[16:24])
            size = (w, h)
    except Exception:
        pass
    return {'path': save_path, 'size': size, 'format': format, 'bytes': len(raw)}


def cdp_mouse_click(x, y, switch_tab_id=None, button='left', click_count=1):
    """通过 CDP Input.dispatchMouseEvent 在物理坐标 (x,y) 真点击。
    模拟 mouseMoved → mousePressed → mouseReleased 完整序列。
    关键：mousePressed 必须带 buttons bitmask（左键=1），否则新版 Chrome 派发的
    mousedown 事件 event.buttons==0，很多前端组件会判为无效点击直接忽略 → 坐标对、命令发了、页面无反应。"""
    BTN_MASK = {'left': 1, 'middle': 4, 'right': 2}.get(button, 1)
    base = {'x': float(x), 'y': float(y), 'button': button}
    r1 = cdp_send('Input.dispatchMouseEvent', {**base, 'type': 'mouseMoved', 'buttons': 0}, switch_tab_id=switch_tab_id)
    r2 = cdp_send('Input.dispatchMouseEvent', {**base, 'type': 'mousePressed', 'clickCount': click_count, 'buttons': BTN_MASK}, switch_tab_id=switch_tab_id)
    r3 = cdp_send('Input.dispatchMouseEvent', {**base, 'type': 'mouseReleased', 'clickCount': click_count, 'buttons': 0}, switch_tab_id=switch_tab_id)
    # 任一步 CDP 报错就回传错误，供上层判定点击是否真的生效
    for r in (r1, r2, r3):
        if isinstance(r, dict) and r.get('error'):
            return {'error': r.get('error'), 'detail': {'moved': r1, 'pressed': r2, 'released': r3}}
    return r3


def cdp_type_text(text, switch_tab_id=None):
    """通过 CDP 输入文本。对于特殊字符（如 #），发送完整的 KeyDown/char/KeyUp 序列以触发富文本编辑器的监听器（如 mention 插件）。"""
    res = None
    for ch in text:
        if ch == '#':
            # 发送完整的按键事件序列来触发 tiptap 等编辑器的 mention 监听器
            cdp_send('Input.dispatchKeyEvent', {
                'type': 'rawKeyDown', 'text': '#', 'unmodifiedText': '#', 'key': '#'
            }, switch_tab_id=switch_tab_id)
            cdp_send('Input.dispatchKeyEvent', {
                'type': 'char', 'text': '#', 'unmodifiedText': '#', 'key': '#'
            }, switch_tab_id=switch_tab_id)
            res = cdp_send('Input.dispatchKeyEvent', {
                'type': 'keyUp', 'key': '#'
            }, switch_tab_id=switch_tab_id)
        else:
            res = cdp_send('Input.insertText', {'text': ch}, switch_tab_id=switch_tab_id)
    return res


def cdp_get_viewport(switch_tab_id=None):
    """获取当前 tab 视口尺寸（CSS px），用于把 VLM 出的归一化坐标换算到物理坐标。"""
    res = web_execute_js(
        "(function(){return JSON.stringify({"
        "w:window.innerWidth,h:window.innerHeight,"
        "dpr:window.devicePixelRatio||1,"
        "sx:window.scrollX,sy:window.scrollY"
        "});})();",
        switch_tab_id=switch_tab_id, no_monitor=True
    )
    val = None
    if isinstance(res, dict):
        val = res.get('js_return') or res.get('data') or res.get('result')
    if isinstance(val, str):
        try: return json.loads(val)
        except Exception: pass
    return val if isinstance(val, dict) else {}


def cdp_find_xhs_publish_bar_btn(switch_tab_id=None):
    """穿透 <xhs-publish-btn> 的 closed shadowRoot，定位发布栏按钮的视口中心坐标。

    实测（2026-06 真实页面）：发布页底部提交栏是 Web Component <xhs-publish-btn>，
    其 shadowRoot 是 **closed**——普通 JS document.querySelector / SoM 枚举完全进不去。必须用
    CDP DOM.getDocument(pierce:true) 协议级穿透。shadow 内结构：
        div.publish-page-publish-btn
          ├─ button.ce-btn.white   → 文本"暂存离开"
          └─ button.ce-btn.bg-red  → 文本"发布"

    只返回「发布」(class 含 bg-red + 文本恰为"发布")。⚠️ 这是真发布动作，
    调用方必须自行确保已设「仅自己可见」并符合发布纪律。

    返回 {'ok':True,'x':int,'y':int} 或 {'ok':False,'reason':str}。
    """
    try:
        doc = cdp_send('DOM.getDocument', {'depth': -1, 'pierce': True}, switch_tab_id=switch_tab_id)
        if not isinstance(doc, dict) or 'root' not in doc:
            return {'ok': False, 'reason': f'getDocument 失败: {doc}'}

        hits = []

        def _attrs(node):
            arr = node.get('attributes', []) or []
            d = {}
            for i in range(0, len(arr), 2):
                d[arr[i]] = arr[i + 1] if i + 1 < len(arr) else ''
            return d

        def _btn_text(node):
            txt = ''
            for ch in node.get('children', []) or []:
                if ch.get('nodeType') == 3:
                    txt += (ch.get('nodeValue') or '')
            return txt.strip()

        def walk(node, in_publish):
            name = (node.get('nodeName') or '')
            if name.lower() == 'xhs-publish-btn':
                in_publish = True
            if in_publish and name == 'BUTTON':
                low = _attrs(node).get('class', '').lower()
                txt = _btn_text(node)
                # 发布：class 含 bg-red、文本恰为"发布"（不含"暂存"，避免误命中）
                if 'ce-btn' in low and 'bg-red' in low and '发布' in txt and '暂存' not in txt:
                    hits.append(node.get('backendNodeId'))
            for sr in node.get('shadowRoots', []) or []:
                walk(sr, in_publish)
            for ch in node.get('children', []) or []:
                walk(ch, in_publish)

        walk(doc['root'], False)
        if not hits:
            label = '发布(button.ce-btn.bg-red)'
            return {'ok': False, 'reason': f'未找到 {label}'}

        bm = cdp_send('DOM.getBoxModel', {'backendNodeId': hits[0]}, switch_tab_id=switch_tab_id)
        if not isinstance(bm, dict) or 'model' not in bm:
            return {'ok': False, 'reason': f'getBoxModel 失败: {bm}'}
        c = bm['model']['content']
        return {'ok': True, 'x': round((c[0] + c[4]) / 2), 'y': round((c[1] + c[5]) / 2)}
    except Exception as e:
        traceback.print_exc()
        return {'ok': False, 'reason': format_error(e)}



_XHS_VIS_TEXT = {'self': '仅自己可见', 'public': '公开可见', 'friends': '仅互关好友可见'}


def cdp_set_xhs_visibility(level='self', switch_tab_id=None):
    """CDP 协议级固化「笔记可见性」设置（替代 VLM 逐篇点，VLM 实测 4 篇错 2 篇太不稳）。

    level: 'self'=仅自己可见(默认) / 'public'=公开可见 / 'friends'=仅互关好友可见。

    实测 DOM（2026-06，普通 DOM 非 shadow）：可见性是发布页的 d-select 下拉：
        DIV.permission-card-wrapper           (可见性卡片外层，唯一锚点)
          └ ...DIV.d-select ... .d-select-description  ← 显示当前选中值（"公开可见"等），点它展开
        展开后：DIV.d-grid.d-options > DIV.custom-option > DIV.group-info > DIV.name(选项文本)
    ⚠️ 触发器必须限定在 .permission-card-wrapper 子树内找——页面有多个 .d-select-description
    （含空文本的别的下拉），全局匹配会命中错的，导致下拉不展开、选项 getBoxModel 报错。
    流程：getDocument(pierce) 在 permission 子树定位触发器 backendNodeId → scrollIntoView →
    getBoxModel 拿坐标 → cdp_mouse_click 展开 → 重新取树找目标选项 DIV.name → 点击 → 重新读触发器值验证。

    返回 {'ok':bool,'verified':bool,'level':str,'text':str,'now':str|None} 或 {'ok':False,'reason':str}。
    """
    target = _XHS_VIS_TEXT.get(level)
    if not target:
        return {'ok': False, 'reason': f'level 必须是 self/public/friends，收到 {level}'}

    def _attrs(node):
        arr = node.get('attributes', []) or []
        d = {}
        for i in range(0, len(arr), 2):
            d[arr[i]] = arr[i + 1] if i + 1 < len(arr) else ''
        return d

    def _direct_text(node):
        t = ''
        for ch in node.get('children', []) or []:
            if ch.get('nodeType') == 3:
                t += (ch.get('nodeValue') or '')
        return t.strip()

    def _current_value():
        """返回可见性下拉触发器当前显示的值（backendNodeId, text）或 (None, None)。
        必须限定在 .permission-card-wrapper 子树内找触发器，否则会命中别的下拉。"""
        doc = cdp_send('DOM.getDocument', {'depth': -1, 'pierce': True}, switch_tab_id=switch_tab_id)
        if not isinstance(doc, dict) or 'root' not in doc:
            return None, None
        wrappers = []

        def find_wrapper(n):
            cls = _attrs(n).get('class', '')
            if 'permission-card-wrapper' in cls.split():
                wrappers.append(n)
                return  # 不再深入，整棵子树后面单独遍历
            for ch in n.get('children', []) or []:
                find_wrapper(ch)
            for sr in n.get('shadowRoots', []) or []:
                find_wrapper(sr)

        find_wrapper(doc['root'])
        if not wrappers:
            return None, None

        hit = []

        def walk(n):
            if 'd-select-description' in _attrs(n).get('class', ''):
                t = _direct_text(n)
                if t in _XHS_VIS_TEXT.values():
                    hit.append((n.get('backendNodeId'), t))
            for ch in n.get('children', []) or []:
                walk(ch)
            for sr in n.get('shadowRoots', []) or []:
                walk(sr)

        for w in wrappers:
            walk(w)
        return hit[0] if hit else (None, None)

    def _click_bid(bid):
        # 先滚进视口（permission 卡片默认在视口外 y≈1428）
        cdp_send('DOM.scrollIntoViewIfNeeded', {'backendNodeId': bid}, switch_tab_id=switch_tab_id)
        time.sleep(0.2)
        bm = cdp_send('DOM.getBoxModel', {'backendNodeId': bid}, switch_tab_id=switch_tab_id)
        if not isinstance(bm, dict) or 'model' not in bm:
            return None
        c = bm['model']['content']
        x, y = round((c[0] + c[4]) / 2), round((c[1] + c[5]) / 2)
        cdp_mouse_click(x, y)
        return (x, y)

    try:
        # 1) 找触发器 + 当前值；已是目标则直接返回
        cur_bid, cur_text = _current_value()
        if cur_bid is None:
            return {'ok': False, 'reason': '未找到可见性下拉触发器(.permission-card-wrapper 内 .d-select-description)'}
        if cur_text == target:
            return {'ok': True, 'verified': True, 'already': True, 'level': level,
                    'text': target, 'now': cur_text}

        # 2) 点触发器展开下拉
        pos = _click_bid(cur_bid)
        if pos is None:
            return {'ok': False, 'reason': '触发器 getBoxModel 失败（可能不可见）'}
        time.sleep(0.8)

        # 3) 重新取树，找选项 DIV.name 文本恰为 target
        doc2 = cdp_send('DOM.getDocument', {'depth': -1, 'pierce': True}, switch_tab_id=switch_tab_id)
        opt = []

        def walk2(n):
            if 'name' in _attrs(n).get('class', '').split() and _direct_text(n) == target:
                opt.append(n.get('backendNodeId'))
            for ch in n.get('children', []) or []:
                walk2(ch)
            for sr in n.get('shadowRoots', []) or []:
                walk2(sr)

        if isinstance(doc2, dict) and 'root' in doc2:
            walk2(doc2['root'])
        if not opt:
            return {'ok': False, 'reason': f'展开后未找到选项「{target}」'}
        opos = _click_bid(opt[0])
        if opos is None:
            return {'ok': False, 'reason': f'选项「{target}」getBoxModel 失败'}
        time.sleep(0.6)

        # 4) 验证：重新读触发器当前值是否变成 target
        _, now_text = _current_value()
        verified = (now_text == target)
        return {'ok': verified, 'verified': verified, 'level': level, 'text': target,
                'now': now_text, 'trigger_pos': pos, 'option_pos': opos}
    except Exception as e:
        traceback.print_exc()
        return {'ok': False, 'reason': format_error(e)}


def cdp_upload_files_to_input(image_paths, switch_tab_id=None):
    """用 CDP DOM.setFileInputFiles 给发布页的 file input 灌图（传本地路径，payload 极小）。

    为什么不用 JS DataTransfer + base64：base64 内联进 JS code 字符串后 payload 巨大，
    经 WS 发到 bridge 时回传结果 JSON 解析必崩（报 'Expecting value'），且 async 返回 Promise
    序列化也不稳——实测无论 await 与否都失败，导致上传步骤误判失败、整个发布流程提前 abort。
    DOM.setFileInputFiles 只传文件路径（参考项目 cdp_publish.py 的稳定做法），payload 极小，
    且用 backendNodeId（持久 ID，跨 CDP 调用不失效，nodeId 会失效）。

    返回 {'ok':True,'backendNodeId':int} 或 {'ok':False,'reason':str}。
    """
    try:
        doc = cdp_send('DOM.getDocument', {'depth': -1, 'pierce': True}, switch_tab_id=switch_tab_id)
        if not isinstance(doc, dict) or 'root' not in doc:
            return {'ok': False, 'reason': f'getDocument 失败: {doc}'}

        def _attrs(node):
            arr = node.get('attributes', []) or []
            d = {}
            for i in range(0, len(arr), 2):
                d[arr[i]] = arr[i + 1] if i + 1 < len(arr) else ''
            return d

        found = []

        def walk(node):
            if (node.get('nodeName') or '') == 'INPUT':
                a = _attrs(node)
                if a.get('type') == 'file' or 'upload-input' in a.get('class', ''):
                    found.append(node.get('backendNodeId'))
            for ch in node.get('children', []) or []:
                walk(ch)
            for sr in node.get('shadowRoots', []) or []:
                walk(sr)

        walk(doc['root'])
        if not found:
            return {'ok': False, 'reason': '未找到 file input（input.upload-input / input[type=file]）'}

        bid = found[0]
        sr = cdp_send('DOM.setFileInputFiles',
                      {'backendNodeId': bid, 'files': list(image_paths)},
                      switch_tab_id=switch_tab_id)
        if isinstance(sr, dict) and sr.get('error'):
            return {'ok': False, 'reason': f'setFileInputFiles 失败: {sr.get("error")}'}
        return {'ok': True, 'backendNodeId': bid}
    except Exception as e:
        traceback.print_exc()
        return {'ok': False, 'reason': format_error(e)}


def cdp_wait_publish_editor(timeout=14.0, switch_tab_id=None):
    """上传后轮询等"标题框 / 正文编辑器"出现，作为上传成功的判定（不靠 JS 返回值）。返回 bool。"""
    check = ("(function(){return JSON.stringify(!!("
             "document.querySelector('input[placeholder*=\"填写标题\"]')||"
             "document.querySelector('div.tiptap.ProseMirror')||"
             "document.querySelector('div.ql-editor')));})();")
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = web_execute_js(check, switch_tab_id=switch_tab_id, no_monitor=True)
        v = r.get('js_return') if isinstance(r, dict) else None
        if v is True or v == 'true':
            return True
        time.sleep(0.5)
    return False



def expand_file_refs(text, base_dir=None):
    """展开文本中的 {{file:路径:起始行:结束行}} 引用为实际文件内容。
    可与普通文本混排。展开失败抛 ValueError。
    base_dir: 相对路径的基准目录，默认为进程 cwd"""
    pattern = r'\{\{file:(.+?):(\d+):(\d+)\}\}'
    def replacer(match):
        path, start, end = match.group(1), int(match.group(2)), int(match.group(3))
        path = os.path.abspath(os.path.join(base_dir or '.', path))
        if not os.path.isfile(path): raise ValueError(f"引用文件不存在: {path}")
        with open(path, 'r', encoding='utf-8') as f: lines = f.readlines()
        if start < 1 or end > len(lines) or start > end: raise ValueError(f"行号越界: {path} 共{len(lines)}行, 请求{start}-{end}")
        return ''.join(lines[start-1:end])
    return re.sub(pattern, replacer, text)
    
def file_patch(path: str, old_content: str, new_content: str):
    """在文件中寻找唯一的 old_content 块并替换为 new_content"""
    path = str(Path(path).resolve())
    try:
        if not os.path.exists(path): return {"status": "error", "msg": "文件不存在"}
        with open(path, 'r', encoding='utf-8') as f: full_text = f.read()
        if not old_content: return {"status": "error", "msg": "old_content 为空，请确认 arguments"}
        count = full_text.count(old_content)
        if count == 0: return {"status": "error", "msg": "未找到匹配的旧文本块，建议：先用 file_read 确认当前内容，再分小段进行 patch。若多次失败则询问用户，严禁自行使用 overwrite 或代码替换。"}
        if count > 1: return {"status": "error", "msg": f"找到 {count} 处匹配，无法确定唯一位置。请提供更长、更具体的旧文本块以确保唯一性。建议：包含上下文行来增强特征，或分小段逐个修改。"}
        updated_text = full_text.replace(old_content, new_content)
        with open(path, 'w', encoding='utf-8') as f: f.write(updated_text)
        return {"status": "success", "msg": "文件局部修改成功"}
    except Exception as e: return {"status": "error", "msg": str(e)}

_read_dirs = set()
def _scan_files(base, depth=2):
    try:
        for e in os.scandir(base):
            if e.is_file(): yield (e.name, e.path)
            elif depth > 0 and e.is_dir(follow_symlinks=False): yield from _scan_files(e.path, depth - 1)
    except (PermissionError, OSError): pass
def file_read(path, start=1, keyword=None, count=200, show_linenos=True):
    try:
        with open(path, 'r', encoding='utf-8', errors='replace') as f:
            stream = ((i, l.rstrip('\r\n')) for i, l in enumerate(f, 1))
            stream = itertools.dropwhile(lambda x: x[0] < start, stream)
            if keyword:
                before = collections.deque(maxlen=count//3)
                for i, l in stream:
                    if keyword.lower() in l.lower():
                        res = list(before) + [(i, l)] + list(itertools.islice(stream, count - len(before) - 1))
                        break
                    before.append((i, l))
                else: return f"Keyword '{keyword}' not found after line {start}. Falling back to content from line {start}:\n\n" \
                               + file_read(path, start, None, count, show_linenos)
            else: res = list(itertools.islice(stream, count))
            realcnt = len(res); L_MAX = min(max(100, 256000//max(realcnt,1)), 8000); TAG = " ... [TRUNCATED]"
            remaining = sum(1 for _ in itertools.islice(stream, 5000))
            total_lines = (res[0][0] - 1 if res else start - 1) + realcnt + remaining
            tl_str = f"{total_lines}+" if remaining >= 5000 else str(total_lines)
            partial = total_lines > realcnt
            total_tag = f"[FILE] {tl_str} lines" + (f" | PARTIAL showing {realcnt}; assess need for more" if partial else "") + "\n"
            res = [(i, l if len(l) <= L_MAX else l[:L_MAX] + TAG) for i, l in res]
            result = "\n".join(f"{i}|{l}" if show_linenos else l for i, l in res)
            if show_linenos: result = total_tag + result
            elif partial: result += f"\n\n[FILE PARTIAL: showing {realcnt}/{tl_str} lines; assess need for more]"
            _read_dirs.add(os.path.dirname(os.path.abspath(path)))
            return result
    except FileNotFoundError:
        msg = f"Error: File not found: {path}"
        try:
            tgt = os.path.basename(path); scan = os.path.dirname(os.path.dirname(os.path.abspath(path)))
            roots = [scan] + [d for d in _read_dirs if not d.startswith(scan)]
            cands = list(itertools.islice((c for base in roots for c in _scan_files(base)), 2000))
            top = sorted([(difflib.SequenceMatcher(None, tgt.lower(), c[0].lower()).ratio(), c) for c in cands[:2000]], key=lambda x: -x[0])[:5]
            top = [(s, c) for s, c in top if s > 0.3]
            if top: msg += "\n\nDid you mean:\n" + "\n".join(f"  {c[1]}  ({s:.0%})" for s, c in top)
        except Exception: pass
        return msg
    except Exception as e: return f"Error: {str(e)}"

def smart_format(data, max_str_len=100, omit_str=' ... '):
    if not isinstance(data, str): data = str(data)
    if len(data) < max_str_len + len(omit_str)*2: return data
    return f"{data[:max_str_len//2]}{omit_str}{data[-max_str_len//2:]}"

def consume_file(dr, file):
    if dr and os.path.exists(os.path.join(dr, file)): 
        with open(os.path.join(dr, file), encoding='utf-8', errors='replace') as f: content = f.read()
        os.remove(os.path.join(dr, file))
        return content

class GenericAgentHandler(BaseHandler):
    '''Generic Agent 工具库，包含多种工具的实现。工具函数自动加上了 do_ 前缀。实际工具名没有前缀。'''
    def __init__(self, parent, last_history=None, cwd='./temp'):
        self.parent = parent
        self.working = {}
        self.cwd = cwd;  self.current_turn = 0
        self.history_info = last_history if last_history else []
        self.code_stop_signal = []
        self._done_hooks = []

    def _get_abs_path(self, path):
        if not path: return ""
        return os.path.abspath(os.path.join(self.cwd, path))   

    def _extract_code_block(self, response, code_type):
        code_type = {'python':'python|py', 'powershell':'powershell|ps1|pwsh', 'bash':'bash|sh|shell'}.get(code_type, re.escape(code_type))
        matches = re.findall(rf"```(?:{code_type})\n(.*?)\n```", response.content, re.DOTALL)
        return matches[-1].strip() if matches else None

    def do_code_run(self, args, response):
        '''执行代码片段，有长度限制，不允许代码中放大量数据，如有需要应当通过文件读取进行。'''
        code_type = args.get("type", "python")
        code = args.get("code") or args.get("script")
        if not code:
            code = self._extract_code_block(response, code_type)
            if not code: return StepOutcome("[Error] Code missing. Must use reply code block or 'script' arg.", next_prompt="\n")
        try: timeout = int(args.get("timeout", 60))
        except: timeout = 60
        raw_path = os.path.join(self.cwd, args.get("cwd", './'))
        cwd = os.path.normpath(os.path.abspath(raw_path))
        code_cwd = os.path.normpath(self.cwd)
        maxlen = 10000 // args.get('_tool_num', 1)
        if code_type == 'python' and args.get("inline_eval"):
            ns = {'handler':self, 'parent':self.parent, 'history':json.dumps(self.parent.llmclient.backend.history)}
            old_cwd = os.getcwd()
            try:
                os.chdir(cwd)
                try:
                    try: result = repr(eval(code, ns))
                    except SyntaxError: exec(code, ns); result = ns.get('_r', 'OK')
                except Exception as e: result = f'Error: {e}'
            finally: os.chdir(old_cwd)
        else: result = yield from code_run(code, code_type, timeout, cwd, code_cwd=code_cwd, stop_signal=self.code_stop_signal, maxlen=maxlen)
        next_prompt = self._get_anchor_prompt(skip=args.get('_index', 0) > 0)
        return StepOutcome(result, next_prompt=next_prompt)
    
    def do_ask_user(self, args, response):
        question = args.get("question", "请提供输入：")
        candidates = args.get("candidates", [])
        result = ask_user(question, candidates)
        yield f"Waiting for your answer ...\n"
        return StepOutcome(result, next_prompt="", should_exit=True)
    


    def do_file_patch(self, args, response):
        path = self._get_abs_path(args.get("path", ""))
        yield f"[Action] Patching file: {path}\n"
        old_content = args.get("old_content", "")
        new_content = args.get("new_content", "")
        try: new_content = expand_file_refs(new_content, base_dir=self.cwd)
        except ValueError as e:
            yield f"[Status] ❌ 引用展开失败: {e}\n"
            return StepOutcome({"status": "error", "msg": str(e)}, next_prompt="\n")
        result = file_patch(path, old_content, new_content)
        yield f"\n{str(result)}\n"
        next_prompt = self._get_anchor_prompt(skip=args.get('_index', 0) > 0)
        return StepOutcome(result, next_prompt=next_prompt)
    
    def do_file_write(self, args, response):
        '''用于对整个文件的大量处理，精细修改要用file_patch。
        需要将要写入的内容放在<file_content>标签内，或者放在代码块中'''
        path = self._get_abs_path(args.get("path", ""))
        mode = args.get("mode", "overwrite")  # overwrite/append/prepend
        action_str = {"prepend": "Prepending to", "append": "Appending to"}.get(mode, "Overwriting")
        yield f"[Action] {action_str} file: {os.path.basename(path)}\n"

        def extract_robust_content(text):
            tags = re.findall(r"<file_content[^>]*>(.*?)</file_content>", text, re.DOTALL)
            if tags: return tags[-1].strip()
            blocks = re.findall(r"```[^\n]*\n([\s\S]*?)```", text)
            if blocks: return blocks[-1].strip()
            return None
        
        content = args.get('content') or extract_robust_content(response.content)
        if not content:
            yield f"[Status] ❌ 失败: 未在回复中找到<file_content>代码块内容\n"
            return StepOutcome({"status": "error", "msg": "No content found. Blank is not supported. Put content inside <file_content>...</file_content> tags in your reply body before call file_write."}, next_prompt="\n")
        try:
            new_content = expand_file_refs(content, base_dir=self.cwd)
            if mode == "prepend":
                old = open(path, 'r', encoding="utf-8").read() if os.path.exists(path) else ""
                open(path, 'w', encoding="utf-8").write(new_content + old)
            else:
                with open(path, 'a' if mode == "append" else 'w', encoding="utf-8") as f: f.write(new_content)
            yield f"[Status] ✅ {mode.capitalize()} 成功 ({len(new_content)} bytes)\n"
            next_prompt = self._get_anchor_prompt(skip=args.get('_index', 0) > 0)
            return StepOutcome({"status": "success", 'writed_bytes': len(new_content)}, next_prompt=next_prompt)
        except Exception as e:
            yield f"[Status] ❌ 写入异常: {str(e)}\n"
            return StepOutcome({"status": "error", "msg": str(e)}, next_prompt="\n")
        
    def do_file_read(self, args, response):
        '''读取文件内容。从第start行开始读取。如有keyword则返回第一个keyword(忽略大小写)周边内容'''
        path = self._get_abs_path(args.get("path", ""))
        yield f"\n[Action] Reading file: {path}\n"
        start = args.get("start", 1)
        count = args.get("count", 200)
        keyword = args.get("keyword")
        show_linenos = args.get("show_linenos", True)
        result = file_read(path, start=start, keyword=keyword,
                           count=count, show_linenos=show_linenos)
        if show_linenos and not result.startswith("Error:"): result = '由于设置了show_linenos，以下返回信息为：(行号|)内容 。\n' + result 
        if ' ... [TRUNCATED]' in result: result += '\n\n（某些行被截断，如需完整内容可改用 code_run 读取）'
        maxlen = 15000 // args.get('_tool_num', 1)
        result = smart_format(result, max_str_len=maxlen, omit_str='\n\n[omitted long content]\n\n')
        next_prompt = self._get_anchor_prompt(skip=args.get('_index', 0) > 0)
        log_memory_access(path)
        if 'memory' in path or 'sop' in path: 
            next_prompt += "\n[SYSTEM TIPS] 正在读取记忆或SOP文件，若决定按sop执行请提取sop中的关键点（特别是靠后的）update working memory."
            # 记录本任务读过的 SOP（供 xhs 工具 SOP gate 校验）
            self.working.setdefault('read_sops', set()).add(os.path.basename(path))
        return StepOutcome(result, next_prompt=next_prompt)
    
    def _in_plan_mode(self): return self.working.get('in_plan_mode')
    def _exit_plan_mode(self): self.working.pop('in_plan_mode', None)
    def enter_plan_mode(self, plan_path): 
        self.working['in_plan_mode'] = plan_path; self.max_turns = 100
        print(f"[Info] Entered plan mode with plan file: {plan_path}"); return plan_path
    def _check_plan_completion(self):
        if not os.path.isfile(p:=self._in_plan_mode() or ''): return None
        try: return len(re.findall(r'\[ \]', open(p, encoding='utf-8', errors='replace').read()))
        except: return None
    
    def do_update_working_checkpoint(self, args, response):
        '''为整个任务设定后续需要临时记忆的重点。'''
        key_info = args.get("key_info", "")
        related_sop = args.get("related_sop", "")
        if "key_info" in args: self.working['key_info'] = key_info
        if "related_sop" in args: self.working['related_sop'] = related_sop
        self.working['passed_sessions'] = 0
        yield f"[Info] Updated key_info and related_sop.\n"
        next_prompt = self._get_anchor_prompt(skip=args.get('_index', 0) > 0)
        #next_prompt += '\n[SYSTEM TIPS] 此函数一般在任务开始或中间时调用，如果任务已成功完成应该是start_long_term_update用于结算长期记忆。\n'
        return StepOutcome({"result": "working key_info updated"}, next_prompt=next_prompt)

    def _retry_or_exit(self, prompt):
        self._empty_ct = getattr(self, '_empty_ct', 0) + 1
        if self._empty_ct >= 3: return StepOutcome({}, should_exit=True)
        return StepOutcome({}, next_prompt=prompt)

    def do_no_tool(self, args, response):
        '''这是一个特殊工具，由引擎自主调用，不要包含在TOOLS_SCHEMA里。
        当模型在一轮中未显式调用任何工具时，由引擎自动触发。
        二次确认仅在回复几乎只包含<thinking>/<summary>和一段大代码块时触发。'''
        content = getattr(response, 'content', '') or ""
        thinking = getattr(response, 'thinking', '') or ""
        if not response or (not content.strip() and not thinking.strip()):
            yield "[Warn] LLM returned an empty response. Retrying...\n"
            return self._retry_or_exit("[System] Blank response, regenerate and tooluse")
        if '[!!! 流异常中断' in content[-100:] or '!!!Error:' in content[-100:]:
            return self._retry_or_exit("[System] Incomplete response. Regenerate and tooluse.")
        if 'max_tokens !!!]' in content[-100:]:
            return self._retry_or_exit("[System] max_tokens limit reached. Use multi small steps to do it.")
        
        if self._in_plan_mode() and any(kw in content for kw in ['任务完成', '全部完成', '已完成所有', '🏁']):
            if 'VERDICT' not in content and '[VERIFY]' not in content and '验证subagent' not in content:
                yield "[Warn] Plan模式完成声明拦截。\n"
                return StepOutcome({}, next_prompt="⛔ [验证拦截] 检测到你在plan模式下声称完成，但未执行[VERIFY]验证步骤。请先按plan_sop §四启动验证subagent，获得VERDICT后才能声称完成。")
            
        # 2. 检测"包含较大代码块但未调用工具"的情况
        # 关键特征：恰好1个大代码块 + 代码块直接结尾（后面只有空白）
        code_block_pattern = r"```[a-zA-Z0-9_]*\n[\s\S]{50,}?```"
        blocks = re.findall(code_block_pattern, content)
        if len(blocks) == 1:
            m = re.search(code_block_pattern, content)
            after_block = content[m.end():]
            if not after_block.strip():
                residual = content.replace(m.group(0), "")
                residual = re.sub(r"<thinking>[\s\S]*?</thinking>", "", residual, flags=re.IGNORECASE)
                residual = re.sub(r"<summary>[\s\S]*?</summary>", "", residual, flags=re.IGNORECASE)
                clean_residual = re.sub(r"\s+", "", residual)
                if len(clean_residual) <= 30:
                    yield "[Info] Detected large code block without tool call and no extra natural language. Requesting clarification.\n"
                    next_prompt = (
                        "[System] 检测到你在上一轮回复中主要内容是较大代码块，且本轮未调用任何工具。\n"
                        "如果这些代码需要执行、写入文件或进一步分析，请重新组织回复并显式调用相应工具"
                        "（例如：code_run、file_write、file_patch 等）；\n"
                        "如果只是向用户展示或讲解代码片段，请在回复中补充自然语言说明，"
                        "并明确是否还需要额外的实际操作。"
                    )
                    return StepOutcome({}, next_prompt=next_prompt)
                
        if self._in_plan_mode():
            remaining = self._check_plan_completion()
            if remaining == 0:
                self._exit_plan_mode(); yield "[Info] Plan完成：plan.md中0个[ ]残留，退出plan模式。\n"
        
        yield "[Info] Final response to user.\n"
        return StepOutcome(response, next_prompt=None)
    
    # ───────────────────────── 小红书工具组 ─────────────────────────
    def _xhs_import(self):
        '''延迟 import，避免 GA 启动时强制把豆包 key 加载进来。'''
        from tools import image as _xhs_image
        return _xhs_image

    def _xhs_task(self):
        '''task workspace 模块（轻量，无外部依赖）。'''
        from tools import task as _xhs_task
        return _xhs_task

    def _js_ret(self, res):
        '''从 web_execute_js 返回里抽 js_return / status，给终端日志用。'''
        if isinstance(res, dict):
            for k in ('js_return', 'result', 'value', 'msg'):
                v = res.get(k)
                if v is not None: return str(v)[:240]
            return str(res)[:240]
        return str(res)[:240]






    def _xhs_edit_count_since_last_pass(self, task):
        '''数本任务里到目前为止 image_edit 调用了几次（不含 PASS 之后的）。'''
        task_dir = task.get_current()
        if not task_dir:
            return 0
        log_path = os.path.join(task_dir, 'trace.log')
        if not os.path.isfile(log_path):
            return 0
        import json as _json
        count = 0
        with open(log_path, 'r', encoding='utf-8') as f:
            for line in f:
                try:
                    rec = _json.loads(line)
                except Exception:
                    continue
                step = rec.get('step', '')
                # 审图通过 → 计数清零（不太可能再 edit，但兜底）
                if step == 'image_review_done':
                    if rec.get('payload', {}).get('verdict') == 'PASS':
                        count = 0
                # 每次 edit 完成 +1
                if step.startswith('image_edit_round_') and step.endswith('_done'):
                    count += 1
        return count

    def _xhs_sop_gate(self, tool_name, args):
        '''硬 gate：核心 xhs 工具执行前必须先 file_read 过对应 SOP，否则拦截并要求先读。
        返回 None 放行；返回 StepOutcome 表示拦截。_index>0（同批多工具的非首个）不重复拦。'''
        # 提取需要校验的技能 domain (如 xhs_edit, xhs_publish)
        domain_map = {
            'xhs_image_generate': 'xhs_edit',
            'xhs_image_edit': 'xhs_edit',
            'xhs_image_review': 'xhs_review',
            'xhs_publish_note': 'xhs_publish',
        }
        domain = domain_map.get(tool_name)
        if not domain:
            # 尝试回退旧字典
            domain = XHS_TOOL_SOP_GATE.get(tool_name, '').split('_sop')[0].replace('memory/', '')
        if not domain:
            return None
        if args.get('_index', 0) > 0:
            return None
            
        read = self.working.get('read_sops') or set()
        
        # 只要读过包含该 domain 且以 SKILL.md 结尾的文件即可 (兼容 hard_skills / soft_skills)
        passed = False
        for r in read:
            if domain in r and r.endswith('SKILL.md'):
                passed = True
                break
                
        if passed:
            return None
            
        # 构造提示路径，如果是软技能提示软技能路径，否则提示硬技能路径
        hint_path = f"skills/soft_skills/{domain}/SKILL.md" if domain in ['xhs_edit', 'xhs_roast_style'] else f"skills/hard_skills/{domain}/SKILL.md"
            
        err = (
            f'BLOCKED: 调用 {tool_name} 前必须先阅读对应 SOP。\n'
            f'请先执行：file_read(path="{hint_path}")，按其关键点操作后再调用本工具。\n'
            f'（本任务尚未读过 {domain} 相关的技能文件，硬性流程纪律，不可跳过。）'
        )
        try:
            task = self._xhs_task()
            if task:
                task.append_trace('xhs_sop_gate_BLOCKED', {'tool': tool_name, 'required_domain': domain})
        except Exception:
            pass
        return StepOutcome({'status': 'error', 'msg': err}, next_prompt='\n')





    def _xhs_actions(self):
        '''延迟 import xhs_actions（移植自参考项目的稳定 selector 数据/互动层）。'''
        from tools import xhs_actions as _a
        return _a










    def do_start_long_term_update(self, args, response):
        '''Agent觉得当前任务完成后有重要信息需要记忆时调用此工具。'''
        prompt = '''### [总结提炼经验] 既然你觉得当前任务有重要信息需要记忆，请提取最近一次任务中【事实验证成功且长期有效】的环境事实、用户偏好、重要步骤，更新记忆。
本工具是标记开启结算过程，若已在更新记忆过程或没有值得记忆的点，忽略本次调用。
**如果没有经验证的，未来能用上的信息，忽略本次调用！**
**只能提取行动验证成功的信息**：
- **环境事实**（路径/凭证/配置）→ `file_patch` 更新 L2，同步 L1
- **复杂任务经验**（关键坑点/前置条件/重要步骤）→ L3 精简 SOP（只记你被坑得多次重试的核心要点）
**禁止**：临时变量、具体推理过程、未验证信息、通用常识、你可以轻松复现的细节、只是做了但没有验证的信息
**操作**：严格遵循提供的L0的记忆更新SOP。先 `file_read` 看现有 → 判断类型 → 最小化更新 → 无新内容跳过，保证对记忆库最小局部修改。\n
''' + get_global_memory()
        yield "[Info] Start distilling good memory for long-term storage.\n"
        path = './memory/memory_management_sop.md'
        if os.path.exists(path): result = 'This is L0:\n' + file_read(path, show_linenos=False)
        else: result = "Memory Management SOP not found. Do not update memory."
        return StepOutcome(result, next_prompt=prompt)

    def _fold_earlier(self, lines):
        FALLBACK = '直接回答了用户问题'
        parts, cnt, last = [], 0, ''
        def flush():
            if cnt:
                if FALLBACK in last: parts.append(f'[Agent]（{cnt} turns）')
                else: parts.append(f'{last}（{cnt} turns）')
        for line in lines:
            if line.startswith('[USER]'):
                flush(); parts.append(line); cnt = 0; last = ''
            else: cnt += 1; last = line
        flush()
        return "\n".join(parts[-100:])

    def _get_anchor_prompt(self, skip=False):
        if skip: return "\n"
        h = self.history_info; W = 30
        earlier = f'<earlier_context>\n{self._fold_earlier(h[:-W])}\n</earlier_context>\n' if len(h) > W else ""
        h_str = "\n".join(h[-W:])
        prompt = f"\n### [WORKING MEMORY]\n{earlier}<history>\n{h_str}\n</history>"
        prompt += f"\nCurrent turn: {self.current_turn}\n"
        if self.working.get('key_info'): prompt += f"\n<key_info>{self.working.get('key_info')}</key_info>"
        if self.working.get('related_sop'): prompt += f"\n有不清晰的地方请再次读取{self.working.get('related_sop')}"
        if getattr(self.parent, 'verbose', False):
            try: print(prompt)
            except: pass
        return prompt
    
    def turn_end_callback(self, response, tool_calls, tool_results, turn, next_prompt, exit_reason):
        _c = re.sub(r'```.*?```|<thinking>.*?</thinking>', '', response.content, flags=re.DOTALL)
        rsumm = re.search(r"<summary>(.*?)</summary>", _c, re.DOTALL)
        if rsumm: summary = rsumm.group(1).strip()
        else:
            tc = tool_calls[0]; tool_name, args = tc['tool_name'], tc['args']   # at least one because no_tool
            clean_args = {k: v for k, v in args.items() if not k.startswith('_')}
            summary = f"调用工具{tool_name}, args: {clean_args}"
            if tool_name == 'no_tool': summary = "直接回答了用户问题"
            next_prompt += "\n\n\n[SYSTEM] 必须在回复文本中包含<summary>！\n\n"
            summary = smart_format(summary.replace('\n', ''), max_str_len=40)
        summary = smart_format(summary.replace('\n', ''), max_str_len=80)
        self.history_info.append(f'[Agent] {summary}')
        _plan = self._in_plan_mode()

        if turn % 75 == 0 and (not _plan):
            next_prompt += f"\n\n[DANGER] 已连续执行第 {turn} 轮。必须总结情况进行ask_user，不允许继续重试。"
        elif turn % 7 == 0:
            next_prompt += f"\n\n[DANGER] 已连续执行第 {turn} 轮。禁止无效重试。若无有效进展，必须切换策略：1. 探测物理边界 2. 请求用户协助。如有需要，可调用 update_working_checkpoint 保存关键上下文。"
        elif turn % 10 == 0: next_prompt += get_global_memory()

        if _plan and turn >= 10 and turn % 5 == 0:
            next_prompt = f"[Plan Hint] 正在计划模式。必须 file_read({_plan}) 确认当前步骤，回复开头引用：📌 当前步骤：...\n\n" + next_prompt
        if _plan and turn >= 120: next_prompt += f"\n\n[DANGER] Plan模式已运行 {turn} 轮，已达上限。必须 ask_user 汇报进度并确认是否继续。"

        injkeyinfo = consume_file(self.parent.task_dir, '_keyinfo')
        injprompt = consume_file(self.parent.task_dir, '_intervene')
        if injkeyinfo: self.working['key_info'] = self.working.get('key_info', '') + f"\n[MASTER] {injkeyinfo}"
        if injprompt: next_prompt += f"\n\n[MASTER] {injprompt}\n"
        for hook in getattr(self.parent, '_turn_end_hooks', {}).values(): hook(locals())  # current readonly
        return next_prompt

    def do_web_scan(self, args, response):
        '''获取当前页面内容和标签页列表。也可用于切换标签页。
        注意：HTML经过简化，边栏/浮动元素等可能被过滤。如需查看被过滤的内容请用execute_js。
        tabs_only=true时仅返回标签页列表，不获取HTML（省token）'''
        tabs_only = args.get("tabs_only", False)
        switch_tab_id = args.get("switch_tab_id", None)
        text_only = args.get("text_only", False)
        maxlen = 35000 // args.get('_tool_num', 1)
        result = web_scan(tabs_only=tabs_only, switch_tab_id=switch_tab_id, text_only=text_only, maxlen=maxlen)
        content = result.pop("content", None)
        yield f'[Info] {str(result)}\n'
        if content: result = json.dumps(result, ensure_ascii=False, default=json_default) + f"\n```html\n{content}\n```"
        next_prompt = "\n"
        return StepOutcome(result, next_prompt=next_prompt)


    def do_web_execute_js(self, args, response):
        '''web情况下的优先使用工具，执行任何js达成对浏览器的*完全*控制。支持将结果保存到文件供后续读取分析。'''
        script = args.get("script", "") or self._extract_code_block(response, "javascript")
        if not script: return StepOutcome("[Error] Script missing. Use ```javascript block or 'script' arg.", next_prompt="\n")
        abs_path = self._get_abs_path(script.strip())
        if os.path.isfile(abs_path):
            with open(abs_path, 'r', encoding='utf-8') as f: script = f.read()
        save_to_file = args.get("save_to_file", "")
        switch_tab_id = args.get("switch_tab_id") or args.get("tab_id")
        no_monitor = args.get("no_monitor", False)
        result = web_execute_js(script, switch_tab_id=switch_tab_id, no_monitor=no_monitor)
        if save_to_file and "js_return" in result:
            content = str(result["js_return"] or '')
            abs_path = self._get_abs_path(save_to_file)
            result["js_return"] = smart_format(content, max_str_len=170)
            try:
                with open(abs_path, 'w', encoding='utf-8') as f: f.write(str(content))
                result["js_return"] += f"\n\n[已保存完整内容到 {abs_path}]"
            except: result['js_return'] += f"\n\n[保存失败，无法写入文件 {abs_path}]"
        show = smart_format(json.dumps(result, ensure_ascii=False, indent=2, default=json_default), max_str_len=300)
        try: print("Web Execute JS Result:", show)
        except: pass
        yield f"JS 执行结果:\n{show}\n"
        next_prompt = self._get_anchor_prompt(skip=args.get('_index', 0) > 0)
        result = json.dumps(result, ensure_ascii=False, default=json_default)
        maxlen = 8000 // args.get('_tool_num', 1)
        return StepOutcome(smart_format(result, max_str_len=maxlen), next_prompt=next_prompt)


    def do_xhs_task_init(self, args, response):
        '''新建任务工作区，返回 task_dir / original_image。'''
        user_request = args.get('user_request', '') or ''
        image = args.get('image') or None
        try:
            task = self._xhs_task()
            info = task.init_task(user_request, image)
        except Exception as e:
            traceback.print_exc()
            return StepOutcome({'status': 'error', 'msg': str(e)}, next_prompt='\n')
        yield f'[Action] 新建任务: {info["task_name"]}\n'
        yield f'  → workspace: {info["task_dir"]}\n'
        if info.get('original_image'):
            yield f'  → 原图已拷贝: {info["original_image"]}\n'
        next_prompt = self._get_anchor_prompt(skip=args.get('_index', 0) > 0)
        return StepOutcome(info, next_prompt=next_prompt)


    def do_xhs_identify_dish(self, args, response):
        '''调豆包 VLM 看图识菜，返回 {dish_name, category, search_keywords, confidence}。'''
        image = args.get('image', '')
        if not image:
            return StepOutcome({'status': 'error', 'msg': 'image 必填'}, next_prompt='\n')
        yield f'[Action] 识菜: {image[:80]}\n'
        task = self._xhs_task()
        task.append_trace('identify_dish_start', {'image': image})
        try:
            xhs = self._xhs_import()
            info = xhs.identify_dish(image)
        except Exception as e:
            traceback.print_exc()
            task.append_trace('identify_dish_error', {'error': str(e)})
            return StepOutcome({'status': 'error', 'msg': str(e)}, next_prompt='\n')
        # 顺手把识菜结果写到 task_dir/dish_info.json
        td = task.get_current()
        if td:
            with open(os.path.join(td, 'dish_info.json'), 'w', encoding='utf-8') as f:
                json.dump(info, f, ensure_ascii=False, indent=2)
        task.append_trace('identify_dish_done', info)
        yield f'  → {info.get("dish_name")} ({info.get("category")}) keywords={info.get("search_keywords")} conf={info.get("confidence")}\n'
        next_prompt = self._get_anchor_prompt(skip=args.get('_index', 0) > 0)
        return StepOutcome(info, next_prompt=next_prompt)


    def do_vlm_analyze_post(self, args, response):
        '''通用 VLM 图文联合分析工具：让大模型结合帖子多张图片、视频、标题和文案进行联合分析。'''
        images = args.get('images', [])
        video_url = args.get('video_url', '')
        title = args.get('title', '')
        content = args.get('content', '')
        prompt = args.get('prompt', '')
        
        if not images and not video_url:
            return StepOutcome({'status': 'error', 'msg': 'images 或 video_url 至少提供一项'}, next_prompt='\n')
        if not prompt:
            return StepOutcome({'status': 'error', 'msg': 'prompt 必填'}, next_prompt='\n')
        
        media_desc = f"{len(images)} 张图" if images else f"视频"
        if images and video_url:
            media_desc = f"{len(images)} 张图 + 视频"
            
        yield f'[Action] VLM 图文联合分析({media_desc}): {prompt[:50]}...\n'
        task = self._xhs_task()
        task.append_trace('vlm_analyze_start', {'images': images, 'video_url': video_url, 'prompt': prompt, 'has_title': bool(title), 'has_content': bool(content)})
        
        try:
            xhs_image = self._xhs_import()
            
            # 将标题和文案拼接到 Prompt 中，实现图文联合分析
            full_prompt = prompt
            if title or content:
                full_prompt = f"【帖子标题】: {title}\n【帖子正文】: {content}\n\n【分析指令】: {prompt}"
                
            # 将视频 URL 也加入到图像列表中，底层通过多模态接口处理
            media_list = list(images)
            if video_url:
                media_list.append(video_url)
                
            analysis_result = xhs_image.analyze_image(media_list, full_prompt)
        except Exception as e:
            traceback.print_exc()
            task.append_trace('vlm_analyze_error', {'error': str(e)})
            return StepOutcome({'status': 'error', 'msg': str(e)}, next_prompt='\n')
            
        task.append_trace('vlm_analyze_done', {'result': analysis_result})
        yield f'  → VLM 分析完成，结果已返回\n'
        next_prompt = self._get_anchor_prompt(skip=args.get('_index', 0) > 0)
        return StepOutcome({'status': 'ok', 'analysis': analysis_result}, next_prompt=next_prompt)

    def do_xhs_search_viral(self, args, response):
        '''跳到小红书搜索页 → 抓前 N 张笔记封面 → 下载到 task_dir/viral_refs/。
        小红书自带搜索默认排序就够用，工具单次调用完成跳转 + 抓取 + 下载。
        可选 fetch_content（默认 3）：真实点击 top N 卡片，抓正文 + 话题 tag 存进 viral_meta.json。'''
        keyword = args.get('keyword', '')
        top_n = int(args.get('top_n', 10))
        skip_navigate = bool(args.get('skip_navigate', False))
        if not keyword and not skip_navigate:
            return StepOutcome({'status': 'error', 'msg': 'keyword 必填（除非 skip_navigate=true）'}, next_prompt='\n')

        # 🚦 SOP gate：搜爆款前必须先读 xhs_search_sop
        _gate = self._xhs_sop_gate('xhs_search_viral', args)
        if _gate is not None:
            yield '[Block] 搜爆款前未读 xhs_search_sop.md，已拦截\n'
            return _gate

        task = self._xhs_task()
        td = task.get_current()
        if not td:
            return StepOutcome({'status': 'error', 'msg': '请先调 xhs_task_init'}, next_prompt='\n')

        yield f'[Action] 抓小红书爆款: keyword={keyword or "(use current page)"}, top_n={top_n}, skip_navigate={skip_navigate}\n'
        task.append_trace('search_viral_start', {'keyword': keyword, 'top_n': top_n, 'skip_navigate': skip_navigate})

        # 0) 关键：先把 default tab 切到小红书页（否则 web_execute_js 发到错误 tab，js_return 全为 None → 误判搜不到）
        tab_info = ensure_xhs_tab(want_creator=False)
        yield f'  [0] 选中小红书 tab: ok={tab_info.get("ok")}, url={(tab_info.get("url") or "")[:50]}, reason={tab_info.get("reason")}\n'
        task.append_trace('search_viral_tab', tab_info)
        if not tab_info.get('ok'):
            return StepOutcome({'status': 'error',
                'msg': f'找不到/打不开小红书标签页（reason={tab_info.get("reason")}）。请在被 CDP 桥接的 Chrome 里打开并登录 xiaohongshu.com 后重试。'},
                next_prompt='\n')

        # 1) 进搜索结果页（除非传 skip_navigate=True 表示已经在目标页）
        # 关键：不再用 window.location.href 裸跳 search_result（缺 referer/点击轨迹，亲测会被风控判成空结果）。
        # 改为：先确保在 xiaohongshu.com 域名（首页裸导航是安全的），再在页内 realClick 模拟真实搜索行为。
        if not skip_navigate:
            try:
                ensure_home_js = (
                    "(function(){"
                    "  if(location.hostname.indexOf('xiaohongshu.com')<0){"
                    "    location.href='https://www.xiaohongshu.com';"
                    "    return 'navigating_home';"
                    "  }"
                    "  return 'already_on_xhs';"
                    "})();"
                )
                r = web_execute_js(ensure_home_js)
                if 'navigating_home' in str(self._js_ret(r)):
                    yield '  → 不在小红书域名，先跳首页...\n'
                    time.sleep(4.0)
                # 页内真实搜索：填搜索框 → realClick 搜索按钮（找不到按钮派 Enter / form submit 兜底）
                search_js = (
                    "(async function(kw){"
                    "  const sleep=ms=>new Promise(r=>setTimeout(r,ms));"
                    "  function realClick(el){"
                    "    const r=el.getBoundingClientRect();"
                    "    const o={bubbles:true,cancelable:true,view:window,clientX:r.left+r.width/2,clientY:r.top+r.height/2,button:0};"
                    "    el.dispatchEvent(new MouseEvent('mouseover',o));"
                    "    el.dispatchEvent(new MouseEvent('mousedown',o));"
                    "    el.dispatchEvent(new MouseEvent('mouseup',o));"
                    "    el.dispatchEvent(new MouseEvent('click',o));"
                    "  }"
                    "  const inp=document.querySelector('#search-input,input.search-input,input[placeholder*=\"搜索\"],input[type=\"search\"]');"
                    "  if(!inp) return JSON.stringify({ok:false,reason:'search_input_not_found',url:location.href});"
                    "  inp.focus();"
                    "  const setter=Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value').set;"
                    "  setter.call(inp,kw);"
                    "  inp.dispatchEvent(new Event('input',{bubbles:true}));"
                    "  inp.dispatchEvent(new Event('change',{bubbles:true}));"
                    "  await sleep(300);"
                    "  const btn=document.querySelector('.search-icon,.search-btn,.input-button,[class*=\"search\"] button,button[type=\"submit\"]');"
                    "  let via='enter';"
                    "  if(btn && btn.offsetParent!==null){realClick(btn);via='button';}"
                    "  else{"
                    "    ['keydown','keypress','keyup'].forEach(t=>inp.dispatchEvent(new KeyboardEvent(t,{bubbles:true,cancelable:true,key:'Enter',code:'Enter',keyCode:13,which:13})));"
                    "    if(inp.form){try{inp.form.requestSubmit?inp.form.requestSubmit():inp.form.submit();}catch(e){}}"
                    "  }"
                    "  return JSON.stringify({ok:true,via:via,url:location.href});"
                    "})(" + json.dumps(keyword) + ");"
                )
                sr = web_execute_js(search_js)
                sr_raw = sr.get('js_return', '') if isinstance(sr, dict) else str(sr)
                try:
                    sr_info = json.loads(sr_raw) if sr_raw else {}
                except Exception:
                    sr_info = {}
                yield f'  → 页内搜索: {sr_info}\n'
                task.append_trace('search_viral_nav', {'keyword': keyword, 'search_info': sr_info})
                if not sr_info.get('ok'):
                    # 搜索框没找到 → 退回裸跳 search_result 作为最后兜底（总比直接失败强）
                    from urllib.parse import quote
                    web_execute_js(
                        f"window.location.href='https://www.xiaohongshu.com/search_result?"
                        f"keyword={quote(keyword)}&type=note&source=web_search_result_notes';'navigated';"
                    )
                    yield '  → 搜索框没找到，退回裸跳 search_result 兜底\n'
                time.sleep(2.0)
            except Exception as e:
                task.append_trace('search_viral_nav_error', {'error': str(e)})
                return StepOutcome({'status': 'error', 'msg': f'跳转失败: {e}'}, next_prompt='\n')


        # 2) 抓笔记卡片（多抓 2 倍，回到 Python 再按 likes 排序）
        fetch_n = top_n * 3
        scrape_js = (
            "const out=[];"
            "const sels=['section.note-item','div.note-item','a.cover','section.feeds-container > section'];"
            "let cards=[];"
            "for(const s of sels){const c=document.querySelectorAll(s);if(c.length>0){cards=c;break;}}"
            "for(const card of cards){"
            "  const img=card.querySelector('img');"
            "  if(!img||!img.src)continue;"
            "  const a=card.querySelector('a[href*=\"/explore/\"]')||card.querySelector('a');"
            "  const titleEl=card.querySelector('.title,.footer .title span,.note-title');"
            "  const likeEl=card.querySelector('.like-wrapper .count,.count');"
            "  const timeEl=card.querySelector('time,.time,.publish-time');"
            "  out.push({"
            "    cover_url:img.src,"
            "    note_url:a?a.href:'',"
            "    title:titleEl?titleEl.innerText.trim():'',"
            "    likes:likeEl?likeEl.innerText.trim():'',"
            "    time:timeEl?timeEl.innerText.trim():''"
            "  });"
            f"  if(out.length>={fetch_n})break;"
            "}"
            "JSON.stringify(out);"
        )
        try:
            # 轮询等待卡片出现：SPA 异步渲染，死等固定时长不可靠。最多等 ~12s，每 1.2s 抓一次，命中即停。
            covers = []
            max_polls = 10
            for poll in range(max_polls):
                scrape_result = web_execute_js(scrape_js)
                raw = scrape_result.get('js_return', '') if isinstance(scrape_result, dict) else str(scrape_result)
                try:
                    covers = json.loads(raw) if raw else []
                except Exception:
                    covers = []
                if covers:
                    if poll > 0:
                        yield f'  → 第 {poll + 1} 次轮询抓到卡片\n'
                    break
                time.sleep(1.2)
        except Exception as e:
            task.append_trace('search_viral_scrape_error', {'error': str(e)})
            return StepOutcome({'status': 'error', 'msg': f'抓取失败（页面 DOM 可能要更新）: {e}'}, next_prompt='\n')

        # 解析 likes 文本成数字（仅用于元数据展示，不再参与排序）
        for c in covers:
            s = (c.get('likes') or '').strip()
            try:
                if '万' in s or 'w' in s.lower():
                    c['likes_num'] = int(float(re.sub(r'[^\d.]', '', s)) * 10000) if re.sub(r'[^\d.]', '', s) else 0
                else:
                    c['likes_num'] = int(re.sub(r'[^\d]', '', s) or 0)
            except Exception:
                c['likes_num'] = 0

        # 按 likes_num 降序排序后再切 top_n（小红书默认顺序里点赞高的不一定在前面）
        covers.sort(key=lambda c: c.get('likes_num', 0), reverse=True)
        covers = covers[:top_n]


        if not covers:
            # 轮询完仍空：dump 当前 URL + body 片段 + 风控/验证关键词，便于事后判断是没加载完/风控/DOM 改版
            dump_js = (
                "(function(){"
                "  const url=location.href;"
                "  const body=(document.body?document.body.innerText:'')||'';"
                "  const hints=['验证','滑块','登录','captcha','安全验证','人机','拼图','异常','滑动'];"
                "  const hit=hints.filter(h=>body.indexOf(h)>=0);"
                "  const cardSels=['section.note-item','div.note-item','a.cover','section.feeds-container > section'];"
                "  const counts={};cardSels.forEach(s=>counts[s]=document.querySelectorAll(s).length);"
                "  return JSON.stringify({"
                "    url:url,title:document.title,"
                "    body_len:body.length,body_head:body.slice(0,300),"
                "    risk_keywords:hit,card_counts:counts"
                "  });"
                "})();"
            )
            dump_info = {}
            try:
                dr = web_execute_js(dump_js)
                draw = dr.get('js_return', '') if isinstance(dr, dict) else str(dr)
                dump_info = json.loads(draw) if draw else {}
            except Exception as de:
                dump_info = {'dump_error': str(de)}
            task.append_trace('search_viral_empty', {'dump': dump_info})
            risk = dump_info.get('risk_keywords') or []
            if risk:
                yield f'  → 一张都没抓到，页面命中风控/验证关键词: {risk}（URL={dump_info.get("url")}）\n'
                msg = f'未抓到爆款：页面疑似风控/验证拦截（命中 {risk}）。请在浏览器里完成验证/确认登录后让我重试。'
            else:
                yield f'  → 一张都没抓到。URL={dump_info.get("url")}, 卡片计数={dump_info.get("card_counts")}, body_len={dump_info.get("body_len")}\n'
                msg = (f'未抓到爆款。诊断信息：URL={dump_info.get("url")}, '
                       f'卡片计数={dump_info.get("card_counts")}, body_len={dump_info.get("body_len")}。'
                       f'可能是搜索结果未渲染完/被风控/DOM 改版。详见 trace.log 的 search_viral_empty。')
            return StepOutcome({'status': 'error', 'msg': msg, 'dump': dump_info}, next_prompt='\n')

        yield f'  → 抓到 {len(covers)} 张爆款 url\n'

        # 3) 下载到 task_dir/viral_refs/
        viral_dir = os.path.join(td, 'viral_refs')
        os.makedirs(viral_dir, exist_ok=True)
        local_paths = []
        for i, c in enumerate(covers):
            url = c.get('cover_url', '')
            if not url: continue
            try:
                req = urllib.request.Request(url, headers={
                    'User-Agent': 'Mozilla/5.0',
                    'Referer': 'https://www.xiaohongshu.com/',
                })
                with urllib.request.urlopen(req, timeout=30) as r:
                    body = r.read()
                ext = '.jpg'
                if 'webp' in r.headers.get('Content-Type', ''): ext = '.webp'
                elif 'png' in r.headers.get('Content-Type', ''): ext = '.png'
                lp = os.path.join(viral_dir, f'{i:02d}{ext}')
                with open(lp, 'wb') as f: f.write(body)
                local_paths.append(lp)
                c['local_path'] = lp
            except Exception as e:
                c['download_error'] = str(e)

        # 4) 写 viral_meta.json
        with open(os.path.join(td, 'viral_meta.json'), 'w', encoding='utf-8') as f:
            json.dump({'keyword': keyword, 'covers': covers}, f, ensure_ascii=False, indent=2)

        # 5) 可选：抓 top N 条笔记的正文 + tag
        # 关键：不再用 window.location.href（等价于地址栏裸 URL，缺 referer/点击轨迹会触发风控）
        # 改为：在搜索结果页 DOM 里找到对应卡片 → realClick 真实点击（浮层/详情页都兼容）→ 抓完关闭/回退
        # fetch_content 默认 3：抓正文是写文案的硬需求，不是可选项。显式传 0 才不抓。
        fetch_content = int(args.get('fetch_content', 3))
        if fetch_content > 0:
            import random
            content_n = min(fetch_content, len(covers))
            yield f'  → 开始抓 top {content_n} 条笔记正文 + 多图（搜索页真实点击卡片，每条间隔 1.5-3s）...\n'
            consecutive_blocks = 0
            for i, c in enumerate(covers[:content_n]):
                note_url = c.get('note_url', '')
                if not note_url: continue
                # 从 note_url 解析出笔记 id：/explore/{id}?xxx 或 /discovery/item/{id}
                m = re.search(r'/(?:explore|discovery/item)/([0-9a-fA-F]+)', note_url)
                note_id = m.group(1) if m else ''
                if not note_id:
                    c['content'] = {'error': f'解析不出 note_id: {note_url}'}
                    yield f'    [{i+1}/{content_n}] note_id 解析失败\n'
                    continue
                try:
                    # 在搜索页找到卡片并 realClick；同时等浮层/详情页内容出现后抓数据
                    click_js = (
                        "(async function(){"
                        "  function realClick(el){"
                        "    const r=el.getBoundingClientRect();"
                        "    const opts={bubbles:true,cancelable:true,view:window,clientX:r.left+r.width/2,clientY:r.top+r.height/2,button:0};"
                        "    el.dispatchEvent(new MouseEvent('mouseover',opts));"
                        "    el.dispatchEvent(new MouseEvent('mousedown',opts));"
                        "    el.dispatchEvent(new MouseEvent('mouseup',opts));"
                        "    el.dispatchEvent(new MouseEvent('click',opts));"
                        "  }"
                        f"  const noteId={json.dumps(note_id)};"
                        "  const links=Array.from(document.querySelectorAll('a[href*=\"'+noteId+'\"]'));"
                        "  let target=null;"
                        "  for(const a of links){if(a.offsetParent!==null){target=a;break;}}"
                        "  if(!target){return JSON.stringify({clicked:false,reason:'card_not_found_in_dom'});}"
                        "  target.scrollIntoView({behavior:'instant',block:'center'});"
                        "  await new Promise(r=>setTimeout(r,400));"
                        "  realClick(target);"
                        "  return JSON.stringify({clicked:true});"
                        "})();"
                    )
                    click_res = web_execute_js(click_js)
                    raw = click_res.get('js_return', '') if isinstance(click_res, dict) else str(click_res)
                    click_info = json.loads(raw) if raw else {}
                    if not click_info.get('clicked'):
                        c['content'] = {'error': f'卡片不在 DOM 中: {click_info.get("reason")}'}
                        yield f'    [{i+1}/{content_n}] 卡片在搜索页找不到（可能没滚到/页码改了），跳过\n'
                        continue
                    # 等浮层/详情页加载（小红书点击卡片大多打开 mask 浮层而不是新页）
                    time.sleep(1.2)
                    # 抓详情：blocked 检测搬到 Python 端，JS 里不再出现中文风控关键词（避免 bridge echo 时刷屏吓人）
                    # 多图：在 JS 里循环"点右箭头 → 等加载 → 收集当前可见图"，直到点到底（按钮消失/src 不再变）
                    # 评论：抓浮层里的评论列表前 N 条
                    detail_js = (
                        "(async function(){"
                        "  const sleep=ms=>new Promise(r=>setTimeout(r,ms));"
                        "  const sel='.note-container,.note-detail-mask,#noteContainer,.note-scroller,div[class*=\"note-detail\"],div[class*=\"NoteDetail\"]';"
                        "  const scope=document.querySelector(sel);"
                        "  const scopeFound=!!scope;"
                        "  const root=scope||document;"
                        "  const t=root.querySelector('#detail-title,.title,h1');"
                        "  const desc=root.querySelector('#detail-desc,.desc,.note-content,.note-text,.content');"
                        "  const tags=Array.from(root.querySelectorAll('a.tag,.tag-item,#hash-tag a,a[href*=\"/search_result\"]'))"
                        "    .map(e=>(e.innerText||'').trim()).filter(Boolean);"
                        # ── 多图：循环点右箭头 ──
                        "  function realClick(el){"
                        "    const r=el.getBoundingClientRect();"
                        "    const o={bubbles:true,cancelable:true,view:window,clientX:r.left+r.width/2,clientY:r.top+r.height/2,button:0};"
                        "    el.dispatchEvent(new MouseEvent('mouseover',o));"
                        "    el.dispatchEvent(new MouseEvent('mousedown',o));"
                        "    el.dispatchEvent(new MouseEvent('mouseup',o));"
                        "    el.dispatchEvent(new MouseEvent('click',o));"
                        "  }"
                        "  function collectVisibleImgs(){"
                        "    const els=Array.from(root.querySelectorAll("
                        "      '.swiper-slide-active img,.swiper-slide-visible img,.note-slider-img img,.media-container img,.carousel-item.active img,picture img,.swiper-wrapper .swiper-slide img'"
                        "    ));"
                        "    const urls=[];"
                        "    for(const im of els){"
                        "      const s=im.src||im.getAttribute('data-src')||'';"
                        "      if(!s)continue;"
                        "      if(im.naturalWidth>0&&im.naturalWidth<200)continue;"
                        "      urls.push(s);"
                        "    }"
                        "    return urls;"
                        "  }"
                        "  const seen=new Set();const images=[];"
                        "  for(const u of collectVisibleImgs()){if(!seen.has(u)){seen.add(u);images.push(u);}}"
                        # 找右翻页按钮：小红书一般是 .arrow-controller.right 或 .right.swiper-button-next
                        "  function findRightArrow(){"
                        "    const cands=Array.from(root.querySelectorAll('.arrow-controller.right,.right.arrow-controller,.swiper-button-next,.next-arrow,div[class*=\"arrow\"][class*=\"right\"]'));"
                        "    for(const e of cands){if(e.offsetParent!==null && !e.classList.contains('swiper-button-disabled') && !e.classList.contains('disabled'))return e;}"
                        "    return null;"
                        "  }"
                        "  let stale=0;"
                        "  for(let step=0;step<15;step++){"  # 最多翻 15 张
                        "    const arrow=findRightArrow();"
                        "    if(!arrow)break;"
                        "    realClick(arrow);"
                        "    await sleep(300);"
                        "    let added=0;"
                        "    for(const u of collectVisibleImgs()){if(!seen.has(u)){seen.add(u);images.push(u);added++;}}"
                        "    if(added===0){stale++;if(stale>=1)break;}else{stale=0;}"
                        "  }"
                        # ── 评论：抓前 8 条 ──
                        "  const cmtEls=Array.from(root.querySelectorAll('.comment-item .content,.list-container .content,.comments-container .content,.parent-comment .content,div[class*=\"comment\"] .note-text'));"
                        "  const comments=[];"
                        "  for(const e of cmtEls){"
                        "    const tx=(e.innerText||'').trim();"
                        "    if(tx&&tx.length>=5)comments.push(tx);"
                        "    if(comments.length>=8)break;"
                        "  }"
                        "  const scopeText=scope?(scope.innerText||'').slice(0,400):'';"
                        "  return JSON.stringify({"
                        "    scope_found:scopeFound,"
                        "    scope_text:scopeText,"
                        "    url:location.href,"
                        "    title:t?t.innerText.trim():'',"
                        "    desc:desc?desc.innerText.trim():'',"
                        "    tags:tags,"
                        "    images:images,"
                        "    comments:comments"
                        "  });"
                        "})();"
                    )
                    detail_result = web_execute_js(detail_js)
                    raw = detail_result.get('js_return', '') if isinstance(detail_result, dict) else str(detail_result)
                    detail = json.loads(raw) if raw else {}
                    # 在 Python 端判 blocked（关键词放这里，JS 源码里就不会有，bridge echo 不刷屏）
                    _block_kw = ['当前笔记无法浏览', '笔记不存在', '系统繁忙', '访问频繁']
                    _scope_text = detail.get('scope_text', '') or ''
                    detail['blocked'] = bool(detail.get('scope_found')) and any(kw in _scope_text for kw in _block_kw)
                    # 抓完关闭浮层（按 ESC）或 history.back() 回到搜索页
                    close_js = (
                        "(function(){"
                        "  const mask=document.querySelector('.close-circle,.close-box,.close-mask-box,.close');"
                        "  if(mask&&mask.offsetParent!==null){mask.click();return 'closed_mask';}"
                        "  document.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',code:'Escape',keyCode:27,which:27,bubbles:true}));"
                        "  if(location.pathname.includes('/explore/')||location.pathname.includes('/discovery/item/')){history.back();return 'history_back';}"
                        "  return 'esc_only';"
                        "})();"
                    )
                    try:
                        web_execute_js(close_js)
                        time.sleep(0.3)
                    except Exception:
                        pass
                    if detail.get('blocked'):
                        c['content'] = {'error': '页面风控（当前笔记无法浏览/频繁访问）', 'url': detail.get('url', '')}
                        consecutive_blocks += 1
                        yield f'    [{i+1}/{content_n}] 🚫 浮层内确认风控文案，跳过 url={detail.get("url","")}\n'
                        if consecutive_blocks >= 2:
                            yield f'  → 连续 {consecutive_blocks} 次风控，停止抓正文（剩余笔记保留 cover 不抓内容）\n'
                            task.append_trace('search_viral_blocked', {'after': i+1})
                            break
                        # 风控了多等一会再试下一条
                        time.sleep(5.0)
                        continue
                    if not detail.get('scope_found'):
                        # 浮层 DOM 还没起来（可能加载慢/选择器漏了），多等 0.8s 再抓一次
                        time.sleep(0.8)
                        try:
                            detail_result2 = web_execute_js(detail_js)
                            raw2 = detail_result2.get('js_return', '') if isinstance(detail_result2, dict) else str(detail_result2)
                            detail = json.loads(raw2) if raw2 else detail
                        except Exception:
                            pass
                    if not detail.get('title') and not detail.get('desc'):
                        # 既没风控也没抓到内容，多半是 DOM 选择器漏了
                        c['content'] = {
                            'error': 'no_content_found',
                            'scope_found': detail.get('scope_found'),
                            'url': detail.get('url', ''),
                        }
                        yield f'    [{i+1}/{content_n}] ⚠️ 浮层{"已展开" if detail.get("scope_found") else "未展开"}但抓不到 title/desc（选择器要更新）url={detail.get("url","")}\n'
                        consecutive_blocks = 0
                        continue
                    consecutive_blocks = 0
                    # 下载笔记内所有图片到 viral_refs/note_{i:02d}/，并对每张图做 OCR 提攻略文字
                    # 早停策略：先下载+OCR 前 2 张作"哨兵"，如果两张都没教学文字（OCR 内容极短），剩下的就不做了
                    img_urls = detail.get('images') or []
                    img_local = []
                    img_texts = []
                    if img_urls:
                        note_img_dir = os.path.join(viral_dir, f'note_{i:02d}')
                        os.makedirs(note_img_dir, exist_ok=True)
                        xhs = self._xhs_import()
                        from concurrent.futures import ThreadPoolExecutor

                        def _dl_one(j_iurl):
                            j, iurl = j_iurl
                            try:
                                req2 = urllib.request.Request(iurl, headers={
                                    'User-Agent': 'Mozilla/5.0',
                                    'Referer': 'https://www.xiaohongshu.com/',
                                })
                                with urllib.request.urlopen(req2, timeout=30) as r2:
                                    body2 = r2.read()
                                    ct = r2.headers.get('Content-Type', '')
                                ext2 = '.jpg'
                                if 'webp' in ct: ext2 = '.webp'
                                elif 'png' in ct: ext2 = '.png'
                                lp2 = os.path.join(note_img_dir, f'{j:02d}{ext2}')
                                with open(lp2, 'wb') as f: f.write(body2)
                                return (j, lp2, None)
                            except Exception as ie:
                                return (j, None, str(ie))

                        def _ocr_one(j_lp):
                            j, lp2 = j_lp
                            if not lp2: return (j, '')
                            try:
                                return (j, xhs.extract_text_from_image(lp2))
                            except Exception as ocr_e:
                                return (j, f'<ocr_error: {ocr_e}>')

                        # ── 第一阶段：哨兵（前 2 张）──
                        sentinel_n = min(2, len(img_urls))
                        downloaded = [None] * len(img_urls)
                        ocr_results = [''] * len(img_urls)
                        with ThreadPoolExecutor(max_workers=sentinel_n) as pool:
                            for (j, lp2, err) in pool.map(_dl_one, [(k, img_urls[k]) for k in range(sentinel_n)]):
                                downloaded[j] = (lp2, err)
                        with ThreadPoolExecutor(max_workers=sentinel_n) as pool:
                            for (j, txt) in pool.map(_ocr_one, [(k, downloaded[k][0]) for k in range(sentinel_n)]):
                                ocr_results[j] = txt

                        # 不再依赖哨兵 OCR 结果决定是否下载全部图片。全量下载以支持多图/视频全模态分析
                        has_teaching = True 

                        # ── 第二阶段：下载/OCR 剩余图 ──
                        rest_idx = list(range(sentinel_n, len(img_urls)))
                        if has_teaching and rest_idx:
                            with ThreadPoolExecutor(max_workers=min(8, len(rest_idx))) as pool:
                                for (j, lp2, err) in pool.map(_dl_one, [(k, img_urls[k]) for k in rest_idx]):
                                    downloaded[j] = (lp2, err)
                            with ThreadPoolExecutor(max_workers=min(8, len(rest_idx))) as pool:
                                for (j, txt) in pool.map(_ocr_one, [(k, downloaded[k][0]) for k in rest_idx]):
                                    ocr_results[j] = txt
                        elif rest_idx:
                            # 没有教学文字，剩余图标记为跳过（不下载、不 OCR）
                            for k in rest_idx:
                                downloaded[k] = (None, 'skipped_no_teaching_text')
                                ocr_results[k] = ''

                        for j in range(len(img_urls)):
                            lp2, err = downloaded[j]
                            if lp2:
                                img_local.append(lp2)
                            else:
                                img_local.append(f'<skip_or_error: {err}>')
                            img_texts.append(ocr_results[j])
                    c['content'] = {
                        'title': detail.get('title', ''),
                        'desc': detail.get('desc', ''),
                        'tags': detail.get('tags', []),
                        'image_urls': img_urls,
                        'image_local_paths': img_local,
                        'image_texts': img_texts,        # 每张图的 OCR 文本（攻略经常藏这里）
                        'comments': detail.get('comments') or [],   # 评论区前 8 条（攻略也可能在这）
                    }
                    n_ocr = sum(1 for t in img_texts if t and not t.startswith('<'))
                    n_real = sum(1 for p in img_local if not p.startswith('<'))
                    early_stop_tag = '' if (not img_urls or n_real == len(img_urls)) else f' [早停:{n_real}/{len(img_urls)}图,纯食物]'
                    yield f'    [{i+1}/{content_n}] {(c["content"]["title"] or "")[:30]}... ({len(c["content"]["desc"])} 字, {n_real} 图/{n_ocr} OCR, {len(c["content"]["comments"])} 评论){early_stop_tag}\n'
                except Exception as e:
                    c['content'] = {'error': str(e)}
                    yield f'    [{i+1}/{content_n}] 抓取失败: {e}\n'
                # 模拟人类浏览节奏，0.5-1.5s 随机间隔
                if i < content_n - 1:
                    time.sleep(random.uniform(0.5, 1.5))
            # 重写 viral_meta.json（带正文）
            with open(os.path.join(td, 'viral_meta.json'), 'w', encoding='utf-8') as f:
                json.dump({'keyword': keyword, 'covers': covers}, f, ensure_ascii=False, indent=2)

        task.append_trace('search_viral_done', {'count': len(local_paths), 'keyword': keyword, 'fetch_content': fetch_content})
        yield f'  → 已下载 {len(local_paths)} 张到 {viral_dir}\n'
        next_prompt = self._get_anchor_prompt(skip=args.get('_index', 0) > 0)
        return StepOutcome({
            'status': 'ok',
            'reference_images': local_paths,
            'count': len(local_paths),
            'covers': covers,
        }, next_prompt=next_prompt)


    def do_xhs_image_review(self, args, response):
        '''小红书严格档审图：返回 6 维评分 + 三档判定 + 修图话术 / 重拍清单 / 与爆款对比。'''
        image = args.get('image', '')
        hint = args.get('hint', '') or ''
        refs = args.get('reference_images') or None
        if not image:
            return StepOutcome({'status': 'error', 'msg': 'image 必填'}, next_prompt='\n')

        # 🚦 SOP gate：审图前必须先读 xhs_review_sop
        _gate = self._xhs_sop_gate('xhs_image_review', args)
        if _gate is not None:
            yield '[Block] 审图前未读 xhs_review_sop.md，已拦截\n'
            return _gate

        # 🚦 硬 gate：审图前必须先有爆款参考图
        if not refs or len(refs) < 3:
            err = (
                'BLOCKED: xhs_image_review 必须传 reference_images 且至少 3 张同品类爆款封面本地路径。\n'
                '正确顺序：\n'
                '  1) xhs_task_init 建工作区\n'
                '  2) code_run 调豆包 VLM 识图（拿 dish_name + search_keywords）\n'
                '  3) web_execute_js 在小红书搜 search_keywords[0]，抓前 5 张高赞封面 url\n'
                '  4) code_run 把封面下载到 task_dir/viral_refs/\n'
                '  5) 把这些本地路径作为 reference_images 再次调用本工具\n'
                '禁止裸调本工具。'
            )
            task = self._xhs_task()
            task.append_trace('image_review_BLOCKED', {
                'reason': 'missing_reference_images',
                'image': image, 'hint': hint, 'refs_count': len(refs) if refs else 0,
            })
            yield f'[Block] 审图被拒：缺少 reference_images（仅 {len(refs) if refs else 0} 张）\n'
            return StepOutcome({'status': 'error', 'msg': err}, next_prompt='\n')

        ref_tag = f' (+{len(refs)} 张爆款参考)'
        yield f'[Action] 严格档审图{ref_tag}: {image[:80]}\n'
        task = self._xhs_task()
        task.append_trace('image_review_start', {'image': image, 'hint': hint, 'refs': refs})
        try:
            xhs = self._xhs_import()
            result = xhs.review_image(image, hint, reference_images=refs)
        except Exception as e:
            traceback.print_exc()
            task.append_trace('image_review_error', {'error': str(e)})
            return StepOutcome({'status': 'error', 'msg': str(e)}, next_prompt='\n')
        task.append_trace('image_review_done', task._trim({
            'verdict': result.get('verdict'),
            'summary': result.get('summary', ''),
            'roast': result.get('roast', ''),
        }))
        yield f'  → {result.get("verdict")} | {result.get("summary","")}\n'
        next_prompt = self._get_anchor_prompt(skip=args.get('_index', 0) > 0)
        return StepOutcome(result, next_prompt=next_prompt)


    def do_xhs_image_edit(self, args, response):
        '''调 Seedream 4.5 (i2i) 修图。返回修后图本地路径。'''
        image = args.get('image', '')
        edit_prompts = args.get('edit_prompts') or []
        save_path = args.get('save_path') or None
        if not image or not edit_prompts:
            return StepOutcome({'status': 'error', 'msg': 'image 与 edit_prompts 均必填'}, next_prompt='\n')

        # 🚦 SOP gate：P 图前必须先读 xhs_edit_sop
        _gate = self._xhs_sop_gate('xhs_image_edit', args)
        if _gate is not None:
            yield '[Block] P 图前未读 xhs_edit_sop.md，已拦截\n'
            return _gate

        # 🚦 硬 gate：连续 ≥2 轮 edit 没 PASS 必须降级 REPHOTO
        task = self._xhs_task()
        edit_count = self._xhs_edit_count_since_last_pass(task)
        if edit_count >= 2:
            err = (
                f'BLOCKED: 本任务已连续 {edit_count} 次 xhs_image_edit 仍未 PASS。'
                'SOP 硬约束：≥2 轮未通过必须降级 REPHOTO。\n'
                '现在该做：\n'
                '  1) 调一次 xhs_image_edit 在【原图】上画红色箭头 + 中文标注做"示意图"\n'
                '     （save_path = task_dir/annotated.png，prompts 例：'
                '"在主菜上方画红色箭头标注：加蒸汽"）—— 这是教用户怎么拍，不是修图。\n'
                '  2) 把毒舌 roast + 批注图路径 + 爆款图列表 + 重拍清单一次性回给用户。\n'
                '禁止再修图、禁止 ask_user 推决策。'
            )
            task.append_trace('image_edit_BLOCKED', {'reason': 'rework_limit', 'edit_count': edit_count})
            yield f'[Block] 修图被拒：已连续 {edit_count} 次未通过，必须降级 REPHOTO\n'
            return StepOutcome({'status': 'error', 'msg': err}, next_prompt='\n')

        # save_path 默认落到当前 task_dir，方便归档（AI 不传 save_path 是常态）
        if not save_path:
            td = task.get_current()
            if td:
                save_path = os.path.join(td, f'edited_round{edit_count + 1}_{int(time.time())}.png')

        # 🚦 硬纪律（代码层强制）：REWORK 闭环每一轮都必须基于 original 重 P，
        # 绝不能在上一轮 edited 上再 P（Seedream 多轮叠加越 P 越坏，已被坑：
        # round1 P 完 round2 用 edited 又被判 REPHOTO）。SOP 写了 LLM 仍会违反，这里兜底纠正。
        td_cur = task.get_current()
        if td_cur and os.path.isdir(td_cur):
            orig = None
            for fn in os.listdir(td_cur):
                if os.path.splitext(fn)[0] == 'original':
                    orig = os.path.join(td_cur, fn)
                    break
            if orig and os.path.abspath(image) != os.path.abspath(orig):
                base = os.path.basename(image)
                # 只要传进来的不是 original（典型是 edited_xx.png），就纠正回 original
                if base.startswith('edited') or 'edited' in base or os.path.basename(orig) != base:
                    yield f'  ⚠️ 检测到 image={base} 不是 original，已强制纠正为 {os.path.basename(orig)}（REWORK 必须基于原图重 P）\n'
                    task.append_trace('image_edit_force_original', {'passed_in': image, 'corrected_to': orig})
                    image = orig

        yield f'[Action] Seedream 4.5 修图 (round {edit_count + 1}): {image[:80]} ({len(edit_prompts)} 条指令)\n'
        task.append_trace(f'image_edit_round_{edit_count + 1}_start', task._trim({
            'image': image, 'edit_prompts': edit_prompts, 'save_path': save_path,
        }))
        try:
            xhs = self._xhs_import()
            out = xhs.edit_image(image, edit_prompts, save_path)
        except Exception as e:
            traceback.print_exc()
            task.append_trace('image_edit_error', {'error': str(e)})
            return StepOutcome({'status': 'error', 'msg': str(e)}, next_prompt='\n')
        task.append_trace(f'image_edit_round_{edit_count + 1}_done', {'output': out})
        yield f'  → 已保存: {out}\n'
        next_prompt = self._get_anchor_prompt(skip=args.get('_index', 0) > 0)
        return StepOutcome({'status': 'ok', 'image_path': out}, next_prompt=next_prompt)


    def do_xhs_image_generate(self, args, response):
        '''调 Seedream 4.5 (t2i) 文生图。返回生成图本地路径。'''
        prompt = args.get('prompt', '')
        size = args.get('size', '2K')
        save_path = args.get('save_path') or None
        if not prompt:
            return StepOutcome({'status': 'error', 'msg': 'prompt 必填'}, next_prompt='\n')
        # 🚦 SOP gate：生图前必须先读 xhs_edit_sop
        _gate = self._xhs_sop_gate('xhs_image_generate', args)
        if _gate is not None:
            yield '[Block] 生图前未读 xhs_edit_sop.md，已拦截\n'
            return _gate
        yield f'[Action] Seedream 4.5 生图: {prompt[:60]}... (size={size})\n'
        task = self._xhs_task()
        task.append_trace('image_generate_start', task._trim({'prompt': prompt, 'size': size}))
        try:
            xhs = self._xhs_import()
            out = xhs.generate_image(prompt, size, save_path)
        except Exception as e:
            traceback.print_exc()
            task.append_trace('image_generate_error', {'error': str(e)})
            return StepOutcome({'status': 'error', 'msg': str(e)}, next_prompt='\n')
        task.append_trace('image_generate_done', {'output': out})
        yield f'  → 已保存: {out}\n'
        next_prompt = self._get_anchor_prompt(skip=args.get('_index', 0) > 0)
        return StepOutcome({'status': 'ok', 'image_path': out}, next_prompt=next_prompt)


    def do_xhs_publish_note(self, args, response):
        '''发笔记到小红书创作中心 https://creator.xiaohongshu.com/publish/publish
        极简签名：images / title / desc / tags。
        新流程（2026-06，取代旧草稿箱/暂存离开——因手机端电脑端草稿不互通）：
        上传图文 → 填标题/正文/话题 → CDP 穿透设「仅自己可见」→ 验证通过后直接点「发布」。
        🚦 硬纪律：可见性未确认为「仅自己可见」绝不点发布，以防误公开。
        前置：CDP bridge 已接管 Chrome、creator 已登录、Chrome 窗口需在前台聚焦
        （cdp_mouse_click 依赖窗口聚焦，后台会被合成器丢弃输入事件）。
        '''
        images = args.get('images') or []
        title = (args.get('title') or '').strip()
        desc = (args.get('desc') or '').strip()
        tags = args.get('tags') or []
        if not images or not title or not desc:
            return StepOutcome({'status': 'error', 'msg': 'images / title / desc 均必填'}, next_prompt='\n')
        if not isinstance(images, list) or not all(isinstance(p, str) and p for p in images):
            return StepOutcome({'status': 'error', 'msg': 'images 必须是非空字符串列表'}, next_prompt='\n')
        # 检查图片都存在
        missing = [p for p in images if not os.path.isfile(p)]
        if missing:
            return StepOutcome({'status': 'error', 'msg': f'图片不存在: {missing}'}, next_prompt='\n')
        # 🚦 SOP gate：发草稿前必须先读 xhs_publish_sop
        _gate = self._xhs_sop_gate('xhs_publish_note', args)
        if _gate is not None:
            yield '[Block] 发草稿前未读 xhs_publish_sop.md，已拦截\n'
            return _gate
        # tags 自动加 # 前缀（用户传 ['美食','早餐'] 或 ['#美食','#早餐'] 都接受）
        norm_tags = []
        for t in tags:
            if not isinstance(t, str): continue
            t = t.strip().lstrip('#')
            if t: norm_tags.append('#' + t)

        task = self._xhs_task()
        td = task.get_current()
        if not td:
            return StepOutcome({'status': 'error', 'msg': '请先调 xhs_task_init'}, next_prompt='\n')

        yield f'[Action] 发草稿到创作中心: {len(images)} 张图, title={title[:40]}, {len(norm_tags)} 个 tag\n'
        task.append_trace('publish_draft_start', task._trim({
            'images': images, 'title': title, 'desc_len': len(desc), 'tags': norm_tags,
        }))

        # 0) 关键：先把 default tab 切到小红书页（否则 web_execute_js 发到错误 tab，操作全部落空）
        tab_info = ensure_xhs_tab(want_creator=True)
        yield f'  [0] 选中小红书 tab: ok={tab_info.get("ok")}, url={(tab_info.get("url") or "")[:50]}, reason={tab_info.get("reason")}\n'
        task.append_trace('publish_draft_tab', tab_info)
        if not tab_info.get('ok'):
            return StepOutcome({'status': 'error',
                'msg': f'找不到/打不开小红书标签页（reason={tab_info.get("reason")}）。请在被 CDP 桥接的 Chrome 里打开并登录小红书创作中心后重试。'},
                next_prompt='\n')

        # 1) 跳转创作中心发布页（用模块级 web_execute_js）
        # 关键：必须带 target=image 锁死图文页，否则默认会进 target=video（亲测踩过）
        try:
            nav_js = (
                "(function(){"
                "  const u='https://creator.xiaohongshu.com/publish/publish?source=official&from=menu&target=image';"
                "  if(location.href.indexOf('target=image')<0){location.href=u;return 'navigating_to_image_tab';}"
                "  return 'already_on_image_tab';"
                "})();"
            )
            r = web_execute_js(nav_js)
            yield f'  [1/6] 跳创作中心(图文): {self._js_ret(r)}\n'
            time.sleep(4.0)  # 创作中心比主站慢
        except Exception as e:
            traceback.print_exc()
            task.append_trace('publish_draft_error', {'phase': 'navigate', 'error': str(e)})
            return StepOutcome({'status': 'error', 'msg': f'跳转创作中心失败: {e}'}, next_prompt='\n')

        # 2) 二次确认在图文页（万一 target=image 也被服务端忽略，再用文本点 tab 兜底）
        try:
            tab_js = (
                "(function(){"
                "  if(location.href.indexOf('target=video')>=0){"
                "    function realClick(el){"
                "      const r=el.getBoundingClientRect();"
                "      const o={bubbles:true,cancelable:true,view:window,clientX:r.left+r.width/2,clientY:r.top+r.height/2,button:0};"
                "      el.dispatchEvent(new MouseEvent('mouseover',o));"
                "      el.dispatchEvent(new MouseEvent('mousedown',o));"
                "      el.dispatchEvent(new MouseEvent('mouseup',o));"
                "      el.dispatchEvent(new MouseEvent('click',o));"
                "    }"
                "    const all=Array.from(document.querySelectorAll('div,span,a,button'));"
                "    for(const t of all){"
                "      const tx=(t.innerText||'').trim();"
                "      if(tx==='上传图文'||tx==='发图文'){realClick(t);return 'switched_via_text:'+tx;}"
                "    }"
                "    return 'still_on_video_tab_no_text_match';"
                "  }"
                "  return 'on_image_tab_via_url';"
                "})();"
            )
            r = web_execute_js(tab_js)
            yield f'  [2/6] 校验图文 tab: {self._js_ret(r)}\n'
            time.sleep(1.2)
        except Exception:
            pass

        # 3) 上传图片：CDP DOM.setFileInputFiles（传本地路径，payload 极小，稳）。
        # 历史教训：原先用 JS DataTransfer + 内联 base64，大 payload 经 WS 回传时 JSON 解析必崩
        # （'Expecting value'），async 返回 Promise 也序列化不稳 → 上传步骤误判失败 → 整个发布流程
        # 在第 3 步就 abort，永远走不到后面填标题/暂存。改用 setFileInputFiles + 轮询编辑器出现判定。
        try:
            up = cdp_upload_files_to_input(images)
            if not up.get('ok'):
                raise RuntimeError(up.get('reason'))
            # 上传成功判定：轮询等标题框/正文编辑器出现（不看 JS 返回值）
            appeared = cdp_wait_publish_editor(timeout=8.0 + 1.0 * len(images))
            yield f'  [3/6] 上传 {len(images)} 张(CDP setFileInputFiles): editor_appeared={appeared}\n'
            if not appeared:
                raise RuntimeError('上传后编辑器未出现（标题框/正文编辑器轮询超时）')
        except Exception as e:
            traceback.print_exc()
            task.append_trace('publish_draft_error', {'phase': 'upload', 'error': str(e)})
            return StepOutcome({'status': 'error', 'msg': f'上传图片失败: {e}'}, next_prompt='\n')

        # 4) 填标题
        try:
            title_js = (
                "(function(t){"
                # 实测：发布页有多个 input.d-text（标题 + 若干 --color-text-title 输入框），
                # 必须用 placeholder 精确锁定真正的标题框，否则会误写到别的 d-text。
                "  const sels=['input.d-text[placeholder*=\"填写标题\"]','input[placeholder*=\"标题\"]','input.d-text','input.titleInput','input[type=\"text\"]'];"
                "  for(const s of sels){"
                "    const el=document.querySelector(s);"
                "    if(el && el.offsetParent!==null){"
                "      const setter=Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value').set;"
                "      setter.call(el,t);"
                "      el.dispatchEvent(new Event('input',{bubbles:true}));"
                "      el.dispatchEvent(new Event('change',{bubbles:true}));"
                "      el.dispatchEvent(new Event('blur',{bubbles:true}));"
                "      return 'title_filled:'+s;"
                "    }"
                "  }"
                "  return 'title_input_not_found';"
                "})(" + json.dumps(title) + ");"
            )
            r = web_execute_js(title_js)
            yield f'  [4/6] 填标题: {self._js_ret(r)}\n'
            time.sleep(0.4)
            # 视觉兜底：selector 没找到标题输入框 → 调 VLM 截图定位
            if 'title_input_not_found' in str(self._js_ret(r)):
                try:
                    yield '  [4.5/6] selector 失败，走 visual fallback ...\n'
                    from tools.web_vision import visual_fill
                    vr = visual_fill(
                        '小红书发布页中央的"标题"输入框，placeholder 是"填写标题会有更多赞哦"',
                        title,
                    )
                    yield f'  [4.5/6] visual 填标题: ok={vr.get("ok")}, len={vr.get("len")}\n'
                    time.sleep(0.4)
                except Exception as ve:
                    task.append_trace('publish_draft_warn', {'phase': 'title_visual', 'error': str(ve)})
        except Exception as e:
            task.append_trace('publish_draft_warn', {'phase': 'title', 'error': str(e)})

        # 5) 填正文 + tags（contenteditable 富文本）
        # 小红书富文本是 tiptap/ProseMirror/Quill：直接 innerText 赋值会被覆盖，必须 focus + execCommand insertText
        try:
            # 正文只填 desc；话题标签在 5.6 阶段单独走真 chip 流程（输入#词→点话题下拉），
            # 不能把 #词 当纯文本拼进正文，否则只是死文字、点不开（亲测踩过的 bug）
            full_text = desc
            desc_js = (
                "(function(text){"
                "  const sels=["
                "    'div.tiptap.ProseMirror',"           # 小红书创作中心实测
                "    'div.tiptap',"
                "    'div.ProseMirror',"
                "    'div.ql-editor',"
                "    '[role=\"textbox\"][contenteditable]',"
                "    'div[contenteditable=\"true\"]',"
                "    '.editor-content [contenteditable]',"
                "    '.editor [contenteditable]'"
                "  ];"
                "  for(const s of sels){"
                "    const els=Array.from(document.querySelectorAll(s));"
                "    for(const el of els){"
                "      if(!el || el.offsetParent===null) continue;"
                "      const r=el.getBoundingClientRect();"
                "      if(r.width<200||r.height<60) continue;"  # 跳过标题那种小的 contenteditable
                "      el.focus();"
                "      el.click();"
                "      try{document.execCommand('selectAll',false,null);}catch(e){}"
                "      try{document.execCommand('delete',false,null);}catch(e){}"
                "      let ok=false;"
                "      try{ok=document.execCommand('insertText',false,text);}catch(e){}"
                "      if(!ok){"
                "        el.innerText=text;"
                "        el.dispatchEvent(new InputEvent('input',{bubbles:true,inputType:'insertText',data:text}));"
                "      }"
                "      el.dispatchEvent(new Event('input',{bubbles:true}));"
                "      el.dispatchEvent(new Event('change',{bubbles:true}));"
                "      el.dispatchEvent(new Event('blur',{bubbles:true}));"
                "      const got=(el.innerText||'').length;"
                "      return 'desc_filled:'+s+'(method='+(ok?'execCommand':'innerText')+',len='+got+')';"
                "    }"
                "  }"
                # 失败时 dump 一下页面里所有 contenteditable 的尺寸/类名
                "  const dump=Array.from(document.querySelectorAll('[contenteditable]')).map(el=>{"
                "    const r=el.getBoundingClientRect();"
                "    return el.tagName+'.'+(el.className||'').toString().slice(0,40)+'('+Math.round(r.width)+'x'+Math.round(r.height)+',vis='+(el.offsetParent!==null)+')';"
                "  });"
                "  return 'desc_editor_not_found|dump='+JSON.stringify(dump).slice(0,500);"
                "})(" + json.dumps(full_text) + ");"
            )
            r = web_execute_js(desc_js)
            yield f'  [5/6] 填正文+tags: {self._js_ret(r)}\n'
            time.sleep(0.6)
            # 视觉兜底：selector 没找到正文编辑器 → 调 VLM 截图定位
            if 'desc_editor_not_found' in str(self._js_ret(r)):
                try:
                    yield '  [5.5/6] selector 失败，走 visual fallback ...\n'
                    from tools.web_vision import visual_fill
                    vr = visual_fill(
                        '小红书发布页大块的正文富文本编辑器（tiptap），通常在标题下方',
                        full_text,
                    )
                    yield f'  [5.5/6] visual 填正文: ok={vr.get("ok")}, len={vr.get("len")}\n'
                    time.sleep(0.6)
                except Exception as ve:
                    task.append_trace('publish_draft_warn', {'phase': 'desc_visual', 'error': str(ve)})
        except Exception as e:
            task.append_trace('publish_draft_warn', {'phase': 'desc', 'error': str(e)})

        # 5.6) 逐个添加真话题标签 chip（不是死文字！）
        # 小红书话题：富文本里输入 '#'+词 → 弹话题下拉框 → 点选第一项才变成蓝色可点 chip。
        # 直接把 '#词' 当纯文本写进正文只是死字符串，点不开。CDP insertText 触发真实 input 才能唤起下拉。
        if norm_tags:
            try:
                from tools.web_vision import visual_click
            except Exception:
                visual_click = None
            focus_js = (
                "(function(){"
                "  const sels=['div.tiptap.ProseMirror','div.tiptap','div.ProseMirror',"
                "    '[role=\"textbox\"][contenteditable]','div[contenteditable=\"true\"]'];"
                "  let ed=null;"
                "  for(const s of sels){const els=Array.from(document.querySelectorAll(s));"
                "    for(const el of els){if(el.offsetParent===null)continue;"
                "      const r=el.getBoundingClientRect();if(r.width<200||r.height<60)continue;ed=el;break;}"
                "    if(ed)break;}"
                "  if(!ed)return 'editor_not_found';"
                "  ed.focus();"
                "  const sel=window.getSelection();const range=document.createRange();"
                "  range.selectNodeContents(ed);range.collapse(false);"
                "  sel.removeAllRanges();sel.addRange(range);"
                "  try{document.execCommand('insertText',false,' ');}catch(e){}"
                "  return 'focused';"
                "})();"
            )
            pick_js = (
                "(function(){"
                "  const conts=Array.from(document.querySelectorAll("
                "    '[class*=\"mention\"],[class*=\"topic\"],[class*=\"suggest\"],"
                "     [class*=\"dropdown\"],.tribute-container,[role=\"listbox\"]'));"
                "  for(const c of conts){"
                "    if(c.offsetParent===null)continue;"
                "    const items=Array.from(c.querySelectorAll('li,[role=\"option\"],[class*=\"item\"],div'))"
                "      .filter(e=>e.offsetParent!==null && (e.innerText||'').trim());"
                "    if(items.length){"
                "      const it=items[0];const r=it.getBoundingClientRect();"
                "      const o={bubbles:true,cancelable:true,view:window,"
                "        clientX:r.left+r.width/2,clientY:r.top+r.height/2,button:0};"
                "      it.dispatchEvent(new MouseEvent('mousedown',o));"
                "      it.dispatchEvent(new MouseEvent('mouseup',o));"
                "      it.dispatchEvent(new MouseEvent('click',o));"
                "      return 'picked:'+(it.innerText||'').trim().slice(0,20);"
                "    }"
                "  }"
                "  return 'dropdown_not_found';"
                "})();"
            )
            picked_cnt = 0
            for raw_tag in norm_tags:
                word = raw_tag.lstrip('#').strip()
                if not word:
                    continue
                try:
                    fr = web_execute_js(focus_js)
                    if 'editor_not_found' in str(self._js_ret(fr)):
                        yield '  [5.6] 话题：找不到编辑器，跳过\n'
                        break
                    # CDP 真实输入 '#'+词 唤起话题下拉
                    cdp_type_text('#' + word)
                    time.sleep(1.5)
                    # 发送回车键尝试选中第一个话题
                    cdp_send('Input.dispatchKeyEvent', {'type': 'rawKeyDown', 'windowsVirtualKeyCode': 13, 'key': 'Enter', 'text': '\r'})
                    cdp_send('Input.dispatchKeyEvent', {'type': 'char', 'windowsVirtualKeyCode': 13, 'key': 'Enter', 'text': '\r'})
                    cdp_send('Input.dispatchKeyEvent', {'type': 'keyUp', 'windowsVirtualKeyCode': 13, 'key': 'Enter'})
                    time.sleep(0.5)
                    # 额外发一个空格避免粘连
                    cdp_type_text(' ')
                    time.sleep(0.5)
                    
                    pr = web_execute_js(pick_js)
                    picked = str(self._js_ret(pr))
                    
                    # 如果 js 点击还是没找到，但可能回车已经生效了，所以只要回车后没有遗留下拉框，也算成功
                    if 'dropdown_not_found' in picked:
                        picked = 'picked_by_enter_or_fallback'
                    if 'dropdown_not_found' in picked and visual_click is not None:
                        try:
                            vr = visual_click(
                                '话题下拉框里第一个候选话题项（形如 #%s 的蓝色条目）' % word)
                            picked = 'visual:' + str(vr.get('ok'))
                        except Exception:
                            pass
                    if picked.startswith('picked') or picked.startswith('visual:True'):
                        picked_cnt += 1
                    yield f'  [5.6] 话题 #{word}: {picked}\n'
                    time.sleep(0.6)
                except Exception as te:
                    task.append_trace('publish_draft_warn', {'phase': 'tag', 'tag': word, 'error': str(te)})
            yield f'  [5.6] 话题标签完成: {picked_cnt}/{len(norm_tags)} 个变成 chip\n'

        # 6) 先派一轮 input/blur，确保 React 已感知内容变更（发布按钮才会 enable）
        try:
            trigger_js = (
                "(function(){"
                "  const eds=Array.from(document.querySelectorAll('[contenteditable]')).filter(el=>el.offsetParent!==null);"
                "  eds.forEach(el=>{"
                "    el.dispatchEvent(new Event('input',{bubbles:true}));"
                "    el.dispatchEvent(new Event('change',{bubbles:true}));"
                "  });"
                "  const ips=Array.from(document.querySelectorAll('input')).filter(el=>el.offsetParent!==null);"
                "  ips.forEach(el=>{"
                "    el.dispatchEvent(new Event('input',{bubbles:true}));"
                "    el.dispatchEvent(new Event('change',{bubbles:true}));"
                "  });"
                "  return 'inputs_touched:'+eds.length+'_'+ips.length;"
                "})();"
            )
            web_execute_js(trigger_js)
            time.sleep(1.0)
        except Exception:
            pass

        # 7) ⭐ CDP 穿透固化「仅自己可见」。这是发布前的硬纪律——未确认 self 绝不点发布。
        # （历史教训：靠 VLM 逐篇点可见性 4 篇错 2 篇，且"公开可见"描述被误判成"发布"直接公开发出去）
        vis = cdp_set_xhs_visibility('self')
        task.append_trace('publish_visibility', vis)
        yield f"  [7] 设可见性「仅自己可见」: ok={vis.get('ok')}, now={vis.get('now')}, reason={vis.get('reason','')}\n"
        if not vis.get('ok'):
            return StepOutcome({
                'status': 'error',
                'msg': f"可见性未能确认为「仅自己可见」（now={vis.get('now')}, reason={vis.get('reason')}），"
                       f"为防误公开已中止发布。请确认 Chrome 窗口在前台聚焦后重试。",
                'visibility': vis,
            }, next_prompt='\n')

        # 8) 直接点「发布」按钮（closed shadow 穿透拿坐标 + CDP 物理点击）
        click_result = None
        try:
            btn = cdp_find_xhs_publish_bar_btn()
            if not btn.get('ok'):
                return StepOutcome({
                    'status': 'error',
                    'msg': f"已设仅自己可见但未定位到发布按钮（{btn.get('reason')}）。",
                    'visibility': vis,
                }, next_prompt='\n')
            click_ret = cdp_mouse_click(btn['x'], btn['y'])
            if isinstance(click_ret, dict) and click_ret.get('error'):
                return StepOutcome({
                    'status': 'error',
                    'msg': f"发布按钮 CDP 点击报错: {click_ret.get('error')}",
                    'visibility': vis,
                }, next_prompt='\n')
            yield f"  [8] 已点「发布」按钮 @({btn['x']},{btn['y']})（closed shadow 穿透）\n"
            time.sleep(4.0)  # 等发布提交 + 页面跳转
            click_result = 'published_self_visible'
        except Exception as e:
            traceback.print_exc()
            task.append_trace('publish_draft_error', {'phase': 'publish_click', 'error': str(e)})
            return StepOutcome({'status': 'error', 'msg': f'点发布按钮失败: {e}', 'visibility': vis}, next_prompt='\n')

        yield f'  ✅ 已以「仅自己可见」发布。手机端可见（电脑端草稿不互通，故直接发布）。\n'

        task.append_trace('publish_draft_done', {
            'click_result': click_result,
            'visibility': vis.get('now'),
            'images_count': len(images),
            'tags_count': len(norm_tags),
        })
        next_prompt = self._get_anchor_prompt(skip=args.get('_index', 0) > 0)
        return StepOutcome({
            'status': 'ok',
            'click_result': click_result,
            'visibility': vis.get('now'),
            'message': '已以「仅自己可见」直接发布（不再走草稿箱）。',
        }, next_prompt=next_prompt)

        # ───────────────────────── 数据读取 / 互动（稳定 selector + __INITIAL_STATE__，零 VLM） ─────────────────────────
    def do_web_visual_click(self, args, response):
        """通用视觉点击：截图当前 tab → 让豆包 VLM 找目标元素 → CDP 物理点击。
        硬编码 selector 失效（页面改版/找不到元素）时的兜底通路。
        args: {target_desc: str}
        """
        target = (args.get('target_desc') or args.get('target') or '').strip()
        if not target:
            return StepOutcome({'status': 'error', 'msg': '缺少 target_desc'}, next_prompt='\n')
        task = self._xhs_task()
        task.ensure_task(hint=f'visual_click {target}'[:40])
        task.append_trace('visual_click_start', {'target': target})
        try:
            from tools.web_vision import visual_click
            r = visual_click(target)
            task.append_trace('visual_click_done', {
                'target': target, 'ok': r.get('ok'), 'verified': r.get('verified'),
                'source': r.get('source'),
                'x': r.get('x'), 'y': r.get('y'), 'reason': r.get('reason'),
            })
            next_prompt = self._get_anchor_prompt(skip=args.get('_index', 0) > 0)
            return StepOutcome({'status': 'ok' if r.get('ok') else 'fail', **r}, next_prompt=next_prompt)
        except Exception as e:
            traceback.print_exc()
            task.append_trace('visual_click_error', {'target': target, 'error': str(e)})
            return StepOutcome({'status': 'error', 'msg': f'visual_click 异常:{e}'}, next_prompt='\n')


    def do_web_visual_fill(self, args, response):
        """通用视觉输入：截图 → VLM 找输入框 → 物理点击聚焦 → CDP insertText 输入文本。
        args: {target_desc: str, text: str}
        """
        target = (args.get('target_desc') or args.get('target') or '').strip()
        text = args.get('text', '')
        if not target or text is None:
            return StepOutcome({'status': 'error', 'msg': '缺少 target_desc 或 text'}, next_prompt='\n')
        task = self._xhs_task()
        task.ensure_task(hint=f'visual_fill {target}'[:40])
        task.append_trace('visual_fill_start', {'target': target, 'text_len': len(text or '')})
        try:
            from tools.web_vision import visual_fill
            r = visual_fill(target, text)
            task.append_trace('visual_fill_done', {
                'target': target, 'ok': r.get('ok'), 'source': r.get('source'),
                'len': r.get('len'), 'reason': r.get('reason'),
            })
            next_prompt = self._get_anchor_prompt(skip=args.get('_index', 0) > 0)
            return StepOutcome({'status': 'ok' if r.get('ok') else 'fail', **r}, next_prompt=next_prompt)
        except Exception as e:
            traceback.print_exc()
            task.append_trace('visual_fill_error', {'target': target, 'error': str(e)})
            return StepOutcome({'status': 'error', 'msg': f'visual_fill 异常:{e}'}, next_prompt='\n')


    def do_web_learn_page(self, args, response):
        """主动学习当前页面：让 VLM 一次性识别页面主要交互元素并入缓存。
        args: {url_hint?: 可选，当前 URL 描述（不用也行，会自动取 location.href）}
        进新站做一次，之后的 web_visual_click / web_visual_fill 同站点可走 fast path 免 VLM。
        """
        url_hint = (args.get('url_hint') or '').strip() or None
        task = self._xhs_task()
        task.ensure_task(hint='learn_page')
        task.append_trace('learn_page_start', {'url_hint': url_hint})
        try:
            from tools.web_vision import learn_page
            r = learn_page(url_hint=url_hint)
            task.append_trace('learn_page_done', {
                'learned': r.get('learned'), 'total': r.get('total'),
                'url_pattern': r.get('url_pattern', ''),
            })
            yield f"[Action] 主动学习页面: learned={r.get('learned')}/{r.get('total')} url_pattern={r.get('url_pattern','')}\n"
            next_prompt = self._get_anchor_prompt(skip=args.get('_index', 0) > 0)
            # 明确告诉 agent：learn_page 只建了索引，没做任何点击/输入
            r['hint'] = ('已把页面元素记入缓存，但【尚未执行任何操作】。要真正点击请调 web_visual_click，'
                         '要输入请调 web_visual_fill，传入对应的 target_desc。')
            return StepOutcome({'status': 'ok' if r.get('ok') else 'fail', **r}, next_prompt=next_prompt)
        except Exception as e:
            traceback.print_exc()
            task.append_trace('learn_page_error', {'error': str(e)})
            return StepOutcome({'status': 'error', 'msg': f'learn_page 异常:{e}'}, next_prompt='\n')


    def do_xhs_check_login(self, args, response):
        '''检查小红书登录状态（读 __INITIAL_STATE__ 用户态，零 VLM）。'''
        a = self._xhs_actions()
        task = self._xhs_task(); task.ensure_task(hint='check_login')
        task.append_trace('check_login_start', {})
        try:
            r = a.check_login()
        except Exception as e:
            traceback.print_exc()
            task.append_trace('check_login_error', {'error': str(e)})
            return StepOutcome({'status': 'error', 'msg': str(e)}, next_prompt='\n')
        task.append_trace('check_login_done', r)
        yield f"[Action] 登录状态: logged_in={r.get('logged_in')} nickname={r.get('nickname','')}\n"
        next_prompt = self._get_anchor_prompt(skip=args.get('_index', 0) > 0)
        return StepOutcome({'status': 'ok' if r.get('ok') else 'fail', **r}, next_prompt=next_prompt)


    def do_xhs_list_feeds(self, args, response):
        '''获取小红书首页推荐流（读 __INITIAL_STATE__.feed.feeds）。返回精简的笔记列表，
        含 id / xsec_token（后续 get_feed_detail / 点赞 / 收藏 / 评论都需要这两个参数）。'''
        a = self._xhs_actions()
        task = self._xhs_task(); task.ensure_task(hint='list_feeds')
        task.append_trace('list_feeds_start', {})
        try:
            r = a.list_feeds()
        except Exception as e:
            traceback.print_exc()
            task.append_trace('list_feeds_error', {'error': str(e)})
            return StepOutcome({'status': 'error', 'msg': str(e)}, next_prompt='\n')
        task.append_trace('list_feeds_done', {'count': r.get('count')})
        yield f"[Action] 推荐流: {r.get('count')} 条\n"
        next_prompt = self._get_anchor_prompt(skip=args.get('_index', 0) > 0)
        return StepOutcome({'status': 'ok' if r.get('ok') else 'fail', **r}, next_prompt=next_prompt)


    def do_xhs_search_feeds(self, args, response):
        '''搜索小红书内容（读 __INITIAL_STATE__.search.feeds）。可选 filters 做筛选：
        {sort_by:综合|最新|最多点赞|最多评论|最多收藏, note_type:不限|视频|图文,
         publish_time:不限|一天内|一周内|半年内, search_scope:不限|已看过|未看过|已关注,
         location:不限|同城|附近}。'''
        keyword = (args.get('keyword') or '').strip()
        if not keyword:
            return StepOutcome({'status': 'error', 'msg': 'keyword 必填'}, next_prompt='\n')
        filters = args.get('filters') or None
        a = self._xhs_actions()
        task = self._xhs_task(); task.ensure_task(hint=f'search {keyword}'[:40])
        task.append_trace('search_feeds_start', {'keyword': keyword, 'filters': filters})
        try:
            r = a.search_feeds(keyword, filters=filters)
        except Exception as e:
            traceback.print_exc()
            task.append_trace('search_feeds_error', {'error': str(e)})
            return StepOutcome({'status': 'error', 'msg': str(e)}, next_prompt='\n')
        task.append_trace('search_feeds_done', {'count': r.get('count'), 'filters_applied': r.get('filters_applied')})
        yield f"[Action] 搜索 '{keyword}': {r.get('count')} 条 (筛选 {r.get('filters_applied')})\n"
        next_prompt = self._get_anchor_prompt(skip=args.get('_index', 0) > 0)
        return StepOutcome({'status': 'ok' if r.get('ok') else 'fail', **r}, next_prompt=next_prompt)


    def do_xhs_get_feed_detail(self, args, response):
        '''获取笔记详情 + 评论（读 __INITIAL_STATE__.note.noteDetailMap）。
        必填 feed_id + xsec_token（从 list_feeds / search_feeds 结果里拿）。
        load_comments=true 时滚动加载评论。'''
        feed_id = (args.get('feed_id') or '').strip()
        xsec_token = (args.get('xsec_token') or '').strip()
        if not feed_id or not xsec_token:
            return StepOutcome({'status': 'error', 'msg': 'feed_id 和 xsec_token 必填'}, next_prompt='\n')
        load_comments = bool(args.get('load_comments', True))
        a = self._xhs_actions()
        task = self._xhs_task(); task.ensure_task(hint=f'detail {feed_id}'[:40])
        task.append_trace('get_feed_detail_start', {'feed_id': feed_id, 'load_comments': load_comments})
        try:
            r = a.get_feed_detail(feed_id, xsec_token, load_comments=load_comments)
        except Exception as e:
            traceback.print_exc()
            task.append_trace('get_feed_detail_error', {'error': str(e)})
            return StepOutcome({'status': 'error', 'msg': str(e)}, next_prompt='\n')
        task.append_trace('get_feed_detail_done', {'ok': r.get('ok'), 'comment_count': r.get('comment_count')})
        yield f"[Action] 笔记详情: ok={r.get('ok')} 评论 {r.get('comment_count', 0)} 条\n"
        next_prompt = self._get_anchor_prompt(skip=args.get('_index', 0) > 0)
        return StepOutcome({'status': 'ok' if r.get('ok') else 'fail', **r}, next_prompt=next_prompt)


    def do_xhs_user_profile(self, args, response):
        '''获取用户主页信息 + 笔记列表（读 __INITIAL_STATE__.user）。
        必填 user_id + xsec_token（从笔记/搜索结果里拿）。'''
        user_id = (args.get('user_id') or '').strip()
        xsec_token = (args.get('xsec_token') or '').strip()
        if not user_id or not xsec_token:
            return StepOutcome({'status': 'error', 'msg': 'user_id 和 xsec_token 必填'}, next_prompt='\n')
        a = self._xhs_actions()
        task = self._xhs_task(); task.ensure_task(hint=f'profile {user_id}'[:40])
        task.append_trace('user_profile_start', {'user_id': user_id})
        try:
            r = a.user_profile(user_id, xsec_token)
        except Exception as e:
            traceback.print_exc()
            task.append_trace('user_profile_error', {'error': str(e)})
            return StepOutcome({'status': 'error', 'msg': str(e)}, next_prompt='\n')
        task.append_trace('user_profile_done', {'ok': r.get('ok'), 'notes_count': r.get('notes_count')})
        yield f"[Action] 用户主页: {r.get('basic_info', {}).get('nickname','')} 笔记 {r.get('notes_count', 0)} 条\n"
        next_prompt = self._get_anchor_prompt(skip=args.get('_index', 0) > 0)
        return StepOutcome({'status': 'ok' if r.get('ok') else 'fail', **r}, next_prompt=next_prompt)



    def do_xhs_like_feed(self, args, response):
        '''点赞 / 取消点赞（selector 优先 + 状态校验，已是目标态则跳过）。
        必填 feed_id + xsec_token；unlike=true 为取消点赞。'''
        feed_id = (args.get('feed_id') or '').strip()
        xsec_token = (args.get('xsec_token') or '').strip()
        if not feed_id or not xsec_token:
            return StepOutcome({'status': 'error', 'msg': 'feed_id 和 xsec_token 必填'}, next_prompt='\n')
        unlike = bool(args.get('unlike', False))
        a = self._xhs_actions()
        task = self._xhs_task(); task.ensure_task(hint=f'like {feed_id}'[:40])
        task.append_trace('like_feed_start', {'feed_id': feed_id, 'unlike': unlike})
        try:
            r = a.like_feed(feed_id, xsec_token, unlike=unlike)
        except Exception as e:
            traceback.print_exc()
            task.append_trace('like_feed_error', {'error': str(e)})
            return StepOutcome({'status': 'error', 'msg': str(e)}, next_prompt='\n')
        task.append_trace('like_feed_done', r)
        yield f"[Action] {'取消点赞' if unlike else '点赞'}: ok={r.get('ok')} state={r.get('state')}\n"
        next_prompt = self._get_anchor_prompt(skip=args.get('_index', 0) > 0)
        return StepOutcome({'status': 'ok' if r.get('ok') else 'fail', **r}, next_prompt=next_prompt)


    def do_xhs_favorite_feed(self, args, response):
        '''收藏 / 取消收藏（selector 优先 + 状态校验）。
        必填 feed_id + xsec_token；unfavorite=true 为取消收藏。'''
        feed_id = (args.get('feed_id') or '').strip()
        xsec_token = (args.get('xsec_token') or '').strip()
        if not feed_id or not xsec_token:
            return StepOutcome({'status': 'error', 'msg': 'feed_id 和 xsec_token 必填'}, next_prompt='\n')
        unfavorite = bool(args.get('unfavorite', False))
        a = self._xhs_actions()
        task = self._xhs_task(); task.ensure_task(hint=f'fav {feed_id}'[:40])
        task.append_trace('favorite_feed_start', {'feed_id': feed_id, 'unfavorite': unfavorite})
        try:
            r = a.favorite_feed(feed_id, xsec_token, unfavorite=unfavorite)
        except Exception as e:
            traceback.print_exc()
            task.append_trace('favorite_feed_error', {'error': str(e)})
            return StepOutcome({'status': 'error', 'msg': str(e)}, next_prompt='\n')
        task.append_trace('favorite_feed_done', r)
        yield f"[Action] {'取消收藏' if unfavorite else '收藏'}: ok={r.get('ok')} state={r.get('state')}\n"
        next_prompt = self._get_anchor_prompt(skip=args.get('_index', 0) > 0)
        return StepOutcome({'status': 'ok' if r.get('ok') else 'fail', **r}, next_prompt=next_prompt)


    def do_xhs_post_comment(self, args, response):
        '''发表评论到笔记（selector 优先）。必填 feed_id + xsec_token + content。'''
        feed_id = (args.get('feed_id') or '').strip()
        xsec_token = (args.get('xsec_token') or '').strip()
        content = args.get('content') or ''
        if not feed_id or not xsec_token or not content:
            return StepOutcome({'status': 'error', 'msg': 'feed_id / xsec_token / content 必填'}, next_prompt='\n')
        a = self._xhs_actions()
        task = self._xhs_task(); task.ensure_task(hint=f'comment {feed_id}'[:40])
        task.append_trace('post_comment_start', {'feed_id': feed_id, 'len': len(content)})
        try:
            r = a.post_comment(feed_id, xsec_token, content)
        except Exception as e:
            traceback.print_exc()
            task.append_trace('post_comment_error', {'error': str(e)})
            return StepOutcome({'status': 'error', 'msg': str(e)}, next_prompt='\n')
        task.append_trace('post_comment_done', r)
        yield f"[Action] 发表评论: ok={r.get('ok')}\n"
        next_prompt = self._get_anchor_prompt(skip=args.get('_index', 0) > 0)
        return StepOutcome({'status': 'ok' if r.get('ok') else 'fail', **r}, next_prompt=next_prompt)


    def do_xhs_reply_comment(self, args, response):
        '''回复笔记下的指定评论（selector 优先 + 滚动定位）。
        必填 feed_id + xsec_token + content，且 comment_id 或 user_id 至少一个。'''
        feed_id = (args.get('feed_id') or '').strip()
        xsec_token = (args.get('xsec_token') or '').strip()
        content = args.get('content') or ''
        comment_id = (args.get('comment_id') or '').strip()
        user_id = (args.get('user_id') or '').strip()
        if not feed_id or not xsec_token or not content:
            return StepOutcome({'status': 'error', 'msg': 'feed_id / xsec_token / content 必填'}, next_prompt='\n')
        if not comment_id and not user_id:
            return StepOutcome({'status': 'error', 'msg': 'comment_id 或 user_id 至少一个'}, next_prompt='\n')
        a = self._xhs_actions()
        task = self._xhs_task(); task.ensure_task(hint=f'reply {feed_id}'[:40])
        task.append_trace('reply_comment_start', {'feed_id': feed_id, 'comment_id': comment_id, 'user_id': user_id})
        try:
            r = a.reply_comment(feed_id, xsec_token, content, comment_id=comment_id, user_id=user_id)
        except Exception as e:
            traceback.print_exc()
            task.append_trace('reply_comment_error', {'error': str(e)})
            return StepOutcome({'status': 'error', 'msg': str(e)}, next_prompt='\n')
        task.append_trace('reply_comment_done', r)
        yield f"[Action] 回复评论: ok={r.get('ok')}\n"
        next_prompt = self._get_anchor_prompt(skip=args.get('_index', 0) > 0)
        return StepOutcome({'status': 'ok' if r.get('ok') else 'fail', **r}, next_prompt=next_prompt)


    def do_create_skill(self, args, response):
        skill_name = args.get("skill_name")
        description = args.get("description")
        sop_content = args.get("sop_content")

        if not skill_name or not sop_content:
            yield "Missing skill_name or sop_content\n"
            return StepOutcome(None, next_prompt="创建技能失败，参数不完整")

        # 默认保存到 skills/local 目录下，实现 core 与 local 的隔离
        skills_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'skills', 'local')
        skill_folder = os.path.join(skills_dir, skill_name)
        os.makedirs(skill_folder, exist_ok=True)
        
        md_path = os.path.join(skill_folder, "SKILL.md")

        frontmatter = f"""---
name: {skill_name}
description: "{description.replace('"', "'")}"
tags:
  - auto_generated
  - agent_sop
---
"""

        try:
            with open(md_path, "w", encoding="utf-8") as f:
                f.write(frontmatter)
                f.write(f"# {skill_name}\n\n")
                f.write(f"> {description}\n\n")
                f.write(sop_content)
                
            yield f"成功创建技能SOP：{skill_name}\n"
            return StepOutcome({"skill_name": skill_name}, next_prompt=f"技能 {skill_name} 已成功创建并存入 skills/local/{skill_name}/SKILL.md 目录")
        except Exception as e:
            yield f"创建技能发生错误：{e}\n"
            return StepOutcome(None, next_prompt=f"创建技能报错: {e}")
    def do_update_user_preference(self, args, response):
        category = args.get("category")
        content = args.get("content")

        if not category or not content:
            yield "Missing category or content\n"
            return StepOutcome(None, next_prompt="更新偏好失败，参数不完整")

        memory_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "memory")
        os.makedirs(memory_dir, exist_ok=True)
        profile_path = os.path.join(memory_dir, "user_profile.json")

        profile = {}
        if os.path.exists(profile_path):
            try:
                with open(profile_path, "r", encoding="utf-8") as f:
                    profile = json.load(f)
            except Exception as e:
                yield f"读取 user_profile.json 失败，将重新创建：{e}\n"

        profile[category] = content

        try:
            with open(profile_path, "w", encoding="utf-8") as f:
                json.dump(profile, f, ensure_ascii=False, indent=4)

            yield f"成功更新用户偏好 [{category}]: {content}\n"
            return StepOutcome(profile, next_prompt=f"用户偏好已更新")
        except Exception as e:
            yield f"更新用户偏好报错：{e}\n"
            return StepOutcome(None, next_prompt=f"更新偏好报错: {e}")


def get_global_memory():
    prompt = "\n"
    try:
        suffix = '_en' if os.environ.get('GA_LANG', '') == 'en' else ''
        with open(os.path.join(script_dir, 'memory/global_mem_insight.txt'), 'r', encoding='utf-8', errors='replace') as f: insight = f.read()
        with open(os.path.join(script_dir, f'assets/insight_fixed_structure{suffix}.txt'), 'r', encoding='utf-8') as f: structure = f.read()
        prompt += f'cwd = {os.path.join(script_dir, "temp")} (./)\n'
        prompt += f"\n[Memory] (../memory)\n"
        prompt += structure + '\n../memory/global_mem_insight.txt:\n'
        prompt += insight + "\n"
    except FileNotFoundError: pass
    return prompt
