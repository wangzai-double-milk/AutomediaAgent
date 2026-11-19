"""tools.xhs_actions - 小红书数据读取与互动（稳定 selector + __INITIAL_STATE__，零 VLM）。

移植自参考项目 xiaohongshu-mcp（Go + go-rod）的核心思路：
  - 数据读取：小红书前端把页面数据挂在全局 window.__INITIAL_STATE__（纯 JSON），
    直接读它最准最稳，不依赖 DOM 结构 / 不需要 VLM。
  - 互动操作：用经实测的稳定 CSS selector + 状态校验（点赞/收藏前先读 INITIAL_STATE
    判断当前状态，避免重复操作）。

对外函数（均返回 dict，{ok: bool, ...}）：
  - check_login()                              登录状态
  - list_feeds()                               首页推荐流
  - search_feeds(keyword, filters=None)        搜索（带筛选）
  - get_feed_detail(feed_id, xsec_token, ...)  笔记详情 + 评论
  - user_profile(user_id, xsec_token)          用户主页
  - like_feed(feed_id, xsec_token, unlike=False)
  - favorite_feed(feed_id, xsec_token, unfavorite=False)
  - post_comment(feed_id, xsec_token, content)
  - reply_comment(feed_id, xsec_token, content, comment_id='', user_id='')

底层全部走 ga.web_execute_js（取 js_return），切 tab 用 ga.ensure_xhs_tab。
selector 命中失败时由上层（ga.py 工具）决定是否回退 web_vision 的 VLM 兜底。
"""

import os
import json
import time
import logging
from urllib.parse import urlencode

# 规则1：调用外部接口（浏览器 JS 注入 / 互动操作）落盘到 logs/xhs_actions.log，方便查问题。
from automedia.paths import WORKSPACE_ROOT
_LOG_DIR = str(WORKSPACE_ROOT / 'logs')
os.makedirs(_LOG_DIR, exist_ok=True)
_logger = logging.getLogger('xhs_actions')
if not _logger.handlers:
    _logger.setLevel(logging.INFO)
    _fmt = logging.Formatter('%(asctime)s %(levelname)s [xhs_actions] %(message)s',
                             datefmt='%Y-%m-%d %H:%M:%S')
    _fh = logging.FileHandler(os.path.join(_LOG_DIR, 'xhs_actions.log'), encoding='utf-8')
    _fh.setFormatter(_fmt)
    _logger.addHandler(_fh)
    _sh = logging.StreamHandler()
    _sh.setFormatter(_fmt)
    _logger.addHandler(_sh)


# ───────────────────────── 稳定 selector（实测，2026-06 真实页面校正） ─────────────────────────
# 笔记主体的底部操作栏是 .engage-bar-style .left（注意：评论区每条评论也有 .like-lottie/.like-wrapper，
# 必须用 .engage-bar-style 限定到笔记主体，否则会误点评论的赞）。
# 点赞/收藏要点可 click 的外层 <span> wrapper，而非内部不可 click 的 <svg>。
SELECTORS = {
    # 笔记详情页 主体互动按钮（点 wrapper span）
    'like_button': '.engage-bar-style .left .like-wrapper',
    'collect_button': '.engage-bar-style .left .collect-wrapper',
    # 评论输入（必须锚定 .engage-bar，否则 .input-box 会匹配到页面顶部搜索框）
    'comment_input_box': '.engage-bar .content-edit .not-active',
    'comment_input_field': '.engage-bar .content-edit p.content-input',
    'comment_submit': '.engage-bar button.submit',
    # 评论项 / 回复
    'reply_button': '.right .interactions .reply',
}


# ───────────────────────── 底层封装 ─────────────────────────
def _exec(script, switch_tab_id=None):
    """执行 JS，返回原始 web_execute_js 结果 dict。"""
    from ga import web_execute_js
    _snippet = ' '.join(script.split())[:80]
    _logger.info('web_execute_js tab=%s js=%s...', switch_tab_id, _snippet)
    _t0 = time.time()
    try:
        res = web_execute_js(script, switch_tab_id=switch_tab_id, no_monitor=True)
    except Exception as e:
        _logger.error('web_execute_js 异常 (%.1fs): %s', time.time() - _t0, e)
        raise
    _logger.info('web_execute_js 完成 (%.1fs)', time.time() - _t0)
    return res


def _exec_json(script, switch_tab_id=None):
    """执行 JS 并把 js_return 当 JSON 解析。JS 应 return JSON.stringify(...)。"""
    res = _exec(script, switch_tab_id=switch_tab_id)
    val = None
    if isinstance(res, dict):
        val = res.get('js_return')
        if val is None:
            val = res.get('data') or res.get('result')
    if isinstance(val, str):
        s = val.strip()
        if not s:
            return None
        try:
            return json.loads(s)
        except Exception:
            return val
    return val


def _ensure_tab(want_creator=False):
    from ga import ensure_xhs_tab
    return ensure_xhs_tab(want_creator=want_creator)


def _navigate(url, wait=1.5):
    """在当前 default tab 导航到 url（先 ensure 小红书主站 tab）。"""
    _ensure_tab(want_creator=False)
    _exec(f"window.location.href = {json.dumps(url)}; 'navigating';")
    time.sleep(wait)


def _wait_initial_state(timeout=8.0):
    """轮询等待 window.__INITIAL_STATE__ 出现。返回 bool。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = _exec_json("(function(){return JSON.stringify(typeof window.__INITIAL_STATE__!=='undefined');})()")
        if r is True or r == 'true':
            return True
        time.sleep(0.4)
    return False


def _feed_detail_url(feed_id, xsec_token, xsec_source='pc_feed'):
    # xsec_source 必须与 xsec_token 的来源页一致：搜索结果页 token 配 pc_search，
    # 首页推荐流 token 配 pc_feed。写死会导致 token/source 错配被风控（error_code=300031）。
    src = xsec_source or 'pc_feed'
    return f"https://www.xiaohongshu.com/explore/{feed_id}?xsec_token={xsec_token}&xsec_source={src}"


def _user_profile_url(user_id, xsec_token, xsec_source='pc_note'):
    src = xsec_source or 'pc_note'
    return f"https://www.xiaohongshu.com/user/profile/{user_id}?xsec_token={xsec_token}&xsec_source={src}"


def _search_url(keyword):
    return "https://www.xiaohongshu.com/search_result?" + urlencode(
        {'keyword': keyword, 'source': 'web_explore_feed'})


# ───────────────────────── 登录状态 ─────────────────────────
def check_login():
    """读 __INITIAL_STATE__.user.loginStatus / userInfo 判断是否登录。"""
    _navigate("https://www.xiaohongshu.com/explore", wait=2.0)
    if not _wait_initial_state():
        return {'ok': False, 'logged_in': False, 'reason': 'no_initial_state'}
    js = r"""
    (function(){
      try {
        var s = window.__INITIAL_STATE__ || {};
        var u = s.user || {};
        function unwrap(x){ if(x==null) return null; return (x.value!==undefined)?x.value:((x._value!==undefined)?x._value:x); }
        var info = unwrap(u.userInfo);
        var loginStatus = unwrap(u.loginStatus);
        var loggedIn = false, nickname = '', userId = '';
        if (info && (info.nickname || info.userId)) { loggedIn = true; nickname = info.nickname||''; userId = info.userId||''; }
        if (loginStatus === 1 || loginStatus === true) loggedIn = true;
        return JSON.stringify({loggedIn: loggedIn, nickname: nickname, userId: userId});
      } catch(e){ return JSON.stringify({error: String(e)}); }
    })()
    """
    data = _exec_json(js)
    if isinstance(data, dict) and 'error' not in data:
        return {'ok': True, 'logged_in': bool(data.get('loggedIn')),
                'nickname': data.get('nickname', ''), 'user_id': data.get('userId', '')}
    return {'ok': False, 'logged_in': False, 'reason': str(data)[:120]}


# ───────────────────────── 数据读取 ─────────────────────────
def list_feeds():
    """首页推荐流：读 __INITIAL_STATE__.feed.feeds。"""
    _navigate("https://www.xiaohongshu.com/explore", wait=2.0)
    if not _wait_initial_state():
        return {'ok': False, 'reason': 'no_initial_state', 'feeds': []}
    js = r"""
    (function(){
      try {
        var s = window.__INITIAL_STATE__;
        if (s && s.feed && s.feed.feeds) {
          var f = s.feed.feeds;
          var d = (f.value!==undefined)?f.value:f._value;
          if (d) return JSON.stringify(d);
        }
        return "";
      } catch(e){ return ""; }
    })()
    """
    data = _exec_json(js)
    feeds = _simplify_feeds(data, xsec_source='pc_feed')
    return {'ok': bool(feeds), 'count': len(feeds), 'feeds': feeds}


def search_feeds(keyword, filters=None):
    """搜索：导航到搜索页，可选应用筛选（综合/最新/最多点赞...），读 search.feeds。"""
    if not keyword:
        return {'ok': False, 'reason': 'keyword 必填', 'feeds': []}
    _navigate(_search_url(keyword), wait=2.0)
    if not _wait_initial_state():
        return {'ok': False, 'reason': 'no_initial_state', 'feeds': []}

    applied = []
    if filters:
        applied = _apply_search_filters(filters)
        time.sleep(1.5)

    js = r"""
    (function(){
      try {
        var s = window.__INITIAL_STATE__;
        if (s && s.search && s.search.feeds) {
          var f = s.search.feeds;
          var d = (f.value!==undefined)?f.value:f._value;
          if (d) return JSON.stringify(d);
        }
        return "";
      } catch(e){ return ""; }
    })()
    """
    data = _exec_json(js)
    feeds = _simplify_feeds(data, xsec_source='pc_search')
    return {'ok': bool(feeds), 'count': len(feeds), 'keyword': keyword,
            'filters_applied': applied, 'feeds': feeds}


# 筛选组 → 选项文本（参考项目 filterOptionsMap），DOM 用 nth-child 索引点击
_FILTER_GROUPS = {
    'sort_by': (1, ['综合', '最新', '最多点赞', '最多评论', '最多收藏']),
    'note_type': (2, ['不限', '视频', '图文']),
    'publish_time': (3, ['不限', '一天内', '一周内', '半年内']),
    'search_scope': (4, ['不限', '已看过', '未看过', '已关注']),
    'location': (5, ['不限', '同城', '附近']),
}


def _apply_search_filters(filters):
    """悬停筛选按钮 → 展开面板 → 按 nth-child 点选项。返回成功应用的 [(组,值)]。"""
    applied = []
    # 触发筛选面板：hover div.filter
    _exec("(function(){var f=document.querySelector('div.filter'); if(f){f.dispatchEvent(new MouseEvent('mouseover',{bubbles:true}));} return true;})()")
    time.sleep(0.6)
    for key, val in (filters or {}).items():
        grp = _FILTER_GROUPS.get(key)
        if not grp or not val:
            continue
        fi, opts = grp
        if val not in opts:
            continue
        ti = opts.index(val) + 1  # nth-child 从 1 开始
        sel = f"div.filter-panel div.filters:nth-child({fi}) div.tags:nth-child({ti})"
        clicked = _exec_json(
            "(function(){var e=document.querySelector(%s); if(e){e.click(); return true;} return false;})()"
            % json.dumps(sel))
        if clicked is True or clicked == 'true':
            applied.append([key, val])
        time.sleep(0.4)
    return applied


def get_feed_detail(feed_id, xsec_token, load_comments=True, max_scrolls=8, xsec_source='pc_feed'):
    """笔记详情 + 评论：读 __INITIAL_STATE__.note.noteDetailMap[feed_id]。
    load_comments=True 时滚动评论区加载更多（懒加载）。
    xsec_source 须与 token 来源一致（搜索结果→pc_search，首页流→pc_feed），错配会被风控。"""
    if not feed_id or not xsec_token:
        return {'ok': False, 'reason': 'feed_id / xsec_token 必填'}
    _navigate(_feed_detail_url(feed_id, xsec_token, xsec_source), wait=2.0)
    if not _wait_initial_state():
        return {'ok': False, 'reason': 'no_initial_state'}

    block = _check_accessible()
    if block:
        return {'ok': False, 'reason': block, 'blocked': True}

    if load_comments:
        _load_more_comments(max_scrolls)

    js = r"""
    (function(){
      try {
        var s = window.__INITIAL_STATE__;
        if (s && s.note && s.note.noteDetailMap) return JSON.stringify(s.note.noteDetailMap);
        return "";
      } catch(e){ return ""; }
    })()
    """
    data = _exec_json(js)
    if not isinstance(data, dict):
        return {'ok': False, 'reason': 'no_note_detail'}
    entry = data.get(feed_id) or {}
    note = entry.get('note') or {}
    comments = (entry.get('comments') or {}).get('list') or []
    return {'ok': bool(note), 'detail': _simplify_note(note),
            'comments': [_simplify_comment(c) for c in comments],
            'comment_count': len(comments)}


def user_profile(user_id, xsec_token, xsec_source='pc_note'):
    """用户主页：读 __INITIAL_STATE__.user.userPageData + user.notes。
    xsec_source 须与 token 来源一致（来自笔记/搜索结果），错配会被风控。"""
    if not user_id or not xsec_token:
        return {'ok': False, 'reason': 'user_id / xsec_token 必填'}
    _navigate(_user_profile_url(user_id, xsec_token, xsec_source), wait=2.0)
    if not _wait_initial_state():
        return {'ok': False, 'reason': 'no_initial_state'}
    js = r"""
    (function(){
      try {
        var s = window.__INITIAL_STATE__, u = (s&&s.user)||{};
        function unwrap(x){ if(x==null) return null; return (x.value!==undefined)?x.value:((x._value!==undefined)?x._value:x); }
        var page = unwrap(u.userPageData);
        var notes = unwrap(u.notes);
        return JSON.stringify({page: page||null, notes: notes||null});
      } catch(e){ return JSON.stringify({error:String(e)}); }
    })()
    """
    data = _exec_json(js)
    if not isinstance(data, dict) or data.get('error'):
        return {'ok': False, 'reason': str(data)[:120]}
    page = data.get('page') or {}
    basic = page.get('basicInfo') or {}
    interactions = page.get('interactions') or []
    notes_raw = data.get('notes') or []
    feeds = []
    for group in notes_raw:  # notes 是双重数组
        if isinstance(group, list):
            for f in group:
                feeds.append(_simplify_one_feed(f))
    return {'ok': True,
            'basic_info': {'nickname': basic.get('nickname', ''), 'desc': basic.get('desc', ''),
                           'red_id': basic.get('redId', ''), 'ip_location': basic.get('ipLocation', '')},
            'interactions': [{'name': i.get('name', ''), 'count': i.get('count', '')} for i in interactions],
            'notes_count': len(feeds), 'notes': feeds}


# ───────────────────────── 互动操作 ─────────────────────────
def _read_interact_state(feed_id):
    """读 noteDetailMap[feed_id].note.interactInfo 的 liked / collected。"""
    js = r"""
    (function(){
      try {
        var s = window.__INITIAL_STATE__;
        if (s && s.note && s.note.noteDetailMap) return JSON.stringify(s.note.noteDetailMap);
        return "";
      } catch(e){ return ""; }
    })()
    """
    data = _exec_json(js)
    if isinstance(data, dict):
        entry = data.get(feed_id) or {}
        info = ((entry.get('note') or {}).get('interactInfo')) or {}
        return (bool(info['liked']) if 'liked' in info else None,
                bool(info['collected']) if 'collected' in info else None)
    return None, None


def _click_selector(sel):
    clicked = _exec_json(
        "(function(){var e=document.querySelector(%s); if(e){e.click(); return true;} return false;})()"
        % json.dumps(sel))
    return clicked is True or clicked == 'true'


def _wait_selector(sel, timeout=6.0):
    """轮询等待目标元素出现（应对 SPA 导航后 DOM 渐进渲染）。返回 bool。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = _exec_json(
            "(function(){return JSON.stringify(!!document.querySelector(%s));})()"
            % json.dumps(sel))
        if r is True or r == 'true':
            return True
        time.sleep(0.4)
    return False


def _wait_interact_state(feed_id, kind, target, timeout=6.0):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        liked, collected = _read_interact_state(feed_id)
        last = liked if kind == 'like' else collected
        if last is not None and last == target:
            return True, last
        time.sleep(0.4)
    return False, last


def _comment_occurrences(content):
    """Count rendered comments/replies containing the submitted text."""
    js = r"""
    (function(){
      var target = %s;
      var selectors = [
        '.comment-item', '.parent-comment', '.comment-inner-container',
        '[class*="comment-item"]', '[class*="reply-item"]'
      ];
      var seen = new Set(), count = 0;
      document.querySelectorAll(selectors.join(',')).forEach(function(el){
        if(seen.has(el)) return;
        seen.add(el);
        var text = (el.innerText || el.textContent || '').trim();
        if(text.indexOf(target) >= 0) count++;
      });
      return JSON.stringify(count);
    })()
    """ % json.dumps(str(content))
    value = _exec_json(js)
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _wait_comment_visible(content, before_count, timeout=8.0):
    deadline = time.time() + timeout
    last = before_count
    while time.time() < deadline:
        last = _comment_occurrences(content)
        if last is not None and before_count is not None and last > before_count:
            return True, last
        time.sleep(0.5)
    return False, last


def like_feed(feed_id, xsec_token, unlike=False, xsec_source='pc_feed'):
    """点赞/取消点赞。先读状态，已是目标态则跳过；点击后校验。"""
    return _toggle_interact(feed_id, xsec_token, target=not unlike,
                            selector=SELECTORS['like_button'], kind='like',
                            xsec_source=xsec_source)


def favorite_feed(feed_id, xsec_token, unfavorite=False, xsec_source='pc_feed'):
    """收藏/取消收藏。"""
    return _toggle_interact(feed_id, xsec_token, target=not unfavorite,
                            selector=SELECTORS['collect_button'], kind='collect',
                            xsec_source=xsec_source)


def _toggle_interact(feed_id, xsec_token, target, selector, kind, xsec_source='pc_feed'):
    from tools.xhs_account import assert_writable
    assert_writable()
    from tools.xhs_test_control import preflight
    preflight(transport="cdp")
    if not feed_id or not xsec_token:
        return {'ok': False, 'reason': 'feed_id / xsec_token 必填'}
    _navigate(_feed_detail_url(feed_id, xsec_token, xsec_source), wait=2.0)
    if not _wait_initial_state():
        return {'ok': False, 'reason': 'no_initial_state'}
    block = _check_accessible()
    if block:
        return {'ok': False, 'reason': block, 'blocked': True}

    liked, collected = _read_interact_state(feed_id)
    cur = liked if kind == 'like' else collected
    if cur is not None and cur == target:
        return {'ok': True, 'skipped': True, 'state': cur,
                'reason': f'已是目标状态({target})，跳过', 'selector_hit': True}

    hit = False
    if _wait_selector(selector):
        hit = _click_selector(selector)
    if not hit:
        return {'ok': False, 'selector_hit': False, 'reason': f'selector 未命中: {selector}'}
    verified, new = _wait_interact_state(feed_id, kind, target)
    if verified:
        return {'ok': True, 'state': new, 'selector_hit': True, 'verified': True}
    return {
        'ok': False,
        'state': new,
        'selector_hit': True,
        'verified': False,
        'reason': '已点击一次，但状态在等待窗口内未变为目标值；为避免二次 toggle，未重复点击',
    }


def post_comment(feed_id, xsec_token, content, xsec_source='pc_feed'):
    """发表评论：点输入框 → 输入 → 点提交。"""
    from tools.xhs_account import assert_writable
    assert_writable()
    from tools.xhs_test_control import preflight
    preflight(transport="cdp")
    if not (feed_id and xsec_token and content):
        return {'ok': False, 'reason': 'feed_id / xsec_token / content 必填'}
    _navigate(_feed_detail_url(feed_id, xsec_token, xsec_source), wait=2.0)
    block = _check_accessible()
    if block:
        return {'ok': False, 'reason': block, 'blocked': True}
    before_count = _comment_occurrences(content)

    # 1. 点开输入框（先等渲染）
    if not _wait_selector(SELECTORS['comment_input_box']):
        return {'ok': False, 'selector_hit': False, 'reason': '未找到评论输入框（可能不支持评论）'}
    if not _click_selector(SELECTORS['comment_input_box']):
        return {'ok': False, 'selector_hit': False, 'reason': '未找到评论输入框（可能不支持评论）'}
    time.sleep(0.8)
    # 2. 输入内容（contenteditable：focus + insertText）
    ok_input = _input_into(SELECTORS['comment_input_field'], content)
    if not ok_input:
        return {'ok': False, 'selector_hit': False, 'reason': '未找到评论输入区域'}
    time.sleep(0.8)
    # 3. 提交
    if not _click_selector(SELECTORS['comment_submit']):
        return {'ok': False, 'selector_hit': False, 'reason': '未找到提交按钮'}
    verified, after_count = _wait_comment_visible(content, before_count)
    return {
        'ok': verified,
        'selector_hit': True,
        'verified': verified,
        'feed_id': feed_id,
        'reason': '' if verified else '提交按钮已点击，但未在评论 DOM 中观察到新增内容',
        'comment_matches_before': before_count,
        'comment_matches_after': after_count,
    }


def reply_comment(feed_id, xsec_token, content, comment_id='', user_id='', xsec_source='pc_feed'):
    """回复指定评论：滚动找到目标评论 → 点回复 → 输入 → 提交。"""
    from tools.xhs_account import assert_writable
    assert_writable()
    from tools.xhs_test_control import preflight
    preflight(transport="cdp")
    if not (feed_id and xsec_token and content):
        return {'ok': False, 'reason': 'feed_id / xsec_token / content 必填'}
    if not (comment_id or user_id):
        return {'ok': False, 'reason': 'comment_id 或 user_id 至少一个'}
    _navigate(_feed_detail_url(feed_id, xsec_token, xsec_source), wait=2.0)
    block = _check_accessible()
    if block:
        return {'ok': False, 'reason': block, 'blocked': True}
    # 滚动加载并定位目标评论的回复按钮
    found = _locate_and_click_reply(comment_id, user_id)
    if not found:
        return {'ok': False, 'selector_hit': False, 'reason': '滚动后仍未找到目标评论'}
    time.sleep(0.8)
    if not _input_into(SELECTORS['comment_input_field'], content):
        return {'ok': False, 'selector_hit': False, 'reason': '未找到回复输入框'}
    time.sleep(0.6)
    before_count = _comment_occurrences(content)
    if not _click_selector(SELECTORS['comment_submit']):
        return {'ok': False, 'selector_hit': False, 'reason': '未找到提交按钮'}
    verified, after_count = _wait_comment_visible(content, before_count)
    return {
        'ok': verified,
        'selector_hit': True,
        'verified': verified,
        'feed_id': feed_id,
        'comment_id': comment_id,
        'user_id': user_id,
        'reason': '' if verified else '提交按钮已点击，但未在回复 DOM 中观察到新增内容',
        'comment_matches_before': before_count,
        'comment_matches_after': after_count,
    }


def _input_into(sel, content):
    """对 contenteditable / input 注入文本并派发 input 事件。"""
    js = r"""
    (function(){
      var el = document.querySelector(%s);
      if(!el) return false;
      el.focus();
      var txt = %s;
      try {
        if (el.isContentEditable || el.getAttribute('contenteditable')==='true'){
          document.execCommand('selectAll', false, null);
          document.execCommand('insertText', false, txt);
        } else if ('value' in el){
          var setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value') ||
                       Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype,'value');
          if(setter && setter.set) setter.set.call(el, txt); else el.value = txt;
        } else {
          el.textContent = txt;
        }
        el.dispatchEvent(new Event('input', {bubbles:true}));
        el.dispatchEvent(new Event('change', {bubbles:true}));
        return true;
      } catch(e){ return false; }
    })()
    """ % (json.dumps(sel), json.dumps(content))
    r = _exec_json(js)
    return r is True or r == 'true'


def _locate_and_click_reply(comment_id, user_id, max_scrolls=30):
    """滚动评论区找到目标评论并点其回复按钮。"""
    _exec("(function(){var c=document.querySelector('.comments-container'); if(c){c.scrollIntoView();} return true;})()")
    time.sleep(0.6)
    js_tpl = r"""
    (function(){
      var cid = %s, uid = %s;
      // 优先按 comment id
      if (cid){
        var el = document.querySelector('#comment-'+cid) || document.getElementById('comment-'+cid);
        if (el){ var rb = el.querySelector('.right .interactions .reply'); if(rb){ rb.scrollIntoView({block:'center'}); rb.click(); return 'clicked'; } }
      }
      if (uid){
        var items = document.querySelectorAll('.parent-comment, .comment-item, .comment');
        for (var i=0;i<items.length;i++){
          if (items[i].querySelector('[data-user-id="'+uid+'"]')){
            var rb2 = items[i].querySelector('.right .interactions .reply');
            if(rb2){ rb2.scrollIntoView({block:'center'}); rb2.click(); return 'clicked'; }
          }
        }
      }
      return 'not_found';
    })()
    """ % (json.dumps(comment_id or ''), json.dumps(user_id or ''))
    for _ in range(max_scrolls):
        r = _exec_json(js_tpl)
        if r == 'clicked':
            return True
        # 继续向下滚
        _exec("(function(){window.scrollBy(0, window.innerHeight*0.8); var c=document.querySelector('.comments-container'); return true;})()")
        time.sleep(0.6)
    return False


def _load_more_comments(max_scrolls):
    """滚动评论区触发懒加载，直到 THE END 或停滞。"""
    # 先等首批评论出现（导航后评论区懒加载，立刻滚动会读到 0 条）
    _wait_selector('.parent-comment', timeout=6.0)
    _exec("(function(){var c=document.querySelector('.comments-container'); if(c){c.scrollIntoView();} return true;})()")
    time.sleep(0.5)
    last = -1
    stagnant = 0
    for _ in range(max_scrolls):
        end = _exec_json(
            "(function(){var e=document.querySelector('.end-container'); if(!e) return false; "
            "return (e.innerText||'').toUpperCase().indexOf('THE END')>=0;})()")
        if end is True or end == 'true':
            break
        cnt = _exec_json("(function(){return document.querySelectorAll('.parent-comment').length;})()")
        try:
            cnt = int(cnt)
        except Exception:
            cnt = 0
        if cnt == last:
            stagnant += 1
            if stagnant >= 4:
                break
        else:
            stagnant = 0
            last = cnt
        _exec("(function(){window.scrollBy(0, window.innerHeight*0.85); return true;})()")
        time.sleep(0.6)


# ───────────────────────── 页面可访问性 ─────────────────────────
_BLOCK_KEYWORDS = [
    '当前笔记暂时无法浏览', '该内容因违规已被删除', '该笔记已被删除', '内容不存在',
    '笔记不存在', '已失效', '私密笔记', '仅作者可见', '因用户设置，你无法查看', '因违规无法查看',
]


def _check_accessible():
    """返回拦截原因字符串；可访问则返回 ''。"""
    js = r"""
    (function(){
      var w = document.querySelector('.access-wrapper, .error-wrapper, .not-found-wrapper, .blocked-wrapper');
      var txt = w ? (w.innerText||'').trim() : '';
      if(!txt && document.body) {
          txt = document.body.innerText || '';
      }
      return txt;
    })()
    """
    txt = _exec_json(js)
    if isinstance(txt, str) and txt.strip():
        for kw in _BLOCK_KEYWORDS:
            if kw in txt:
                return kw
        # 为了不误伤正常页面，仅在存在 .access-wrapper 等风控专属容器时才返回截断文本
        # 如果是从 body 抓取的，且不包含明确关键词，就不当做拦截
        js_strict = r"return !!document.querySelector('.access-wrapper, .error-wrapper, .not-found-wrapper, .blocked-wrapper');"
        if _exec_json(js_strict):
            return txt.strip()[:60]
    return ''


# ───────────────────────── 数据精简（省 token） ─────────────────────────
def _simplify_feeds(data, xsec_source=''):
    """feeds 原始结构可能是 list 或 {_value:[...]}。统一成精简 list。
    xsec_source 标记这批 token 的来源页（list_feeds→pc_feed，search_feeds→pc_search），
    随每条一起返回，供后续 get_feed_detail 等绑定使用，避免 token/source 错配被风控。"""
    items = data
    if isinstance(data, dict):
        items = data.get('_value') or data.get('value') or []
    if not isinstance(items, list):
        return []
    return [_simplify_one_feed(f, xsec_source) for f in items if isinstance(f, dict)]


def _simplify_one_feed(f, xsec_source=''):
    card = f.get('noteCard') or {}
    user = card.get('user') or {}
    inter = card.get('interactInfo') or {}
    cover = card.get('cover') or {}
    return {
        'id': f.get('id', ''),
        'xsec_token': f.get('xsecToken', ''),
        'xsec_source': xsec_source,
        'type': card.get('type', ''),
        'title': card.get('displayTitle', ''),
        'author': user.get('nickName') or user.get('nickname', ''),
        'author_id': user.get('userId', ''),
        'liked_count': inter.get('likedCount', ''),
        'comment_count': inter.get('commentCount', ''),
        'collected_count': inter.get('collectedCount', ''),
        'cover': cover.get('urlDefault') or cover.get('url', ''),
    }


def _simplify_note(note):
    user = note.get('user') or {}
    inter = note.get('interactInfo') or {}
    imgs = note.get('imageList') or []
    
    # 提取视频链接
    video_url = ''
    if note.get('type') == 'video':
        try:
            video_url = note.get('video', {}).get('media', {}).get('stream', {}).get('h264', [{}])[0].get('masterUrl', '')
        except Exception:
            pass
            
    return {
        'note_id': note.get('noteId', ''),
        'title': note.get('title', ''),
        'desc': note.get('desc', ''),
        'type': note.get('type', ''),
        'author': user.get('nickname') or user.get('nickName', ''),
        'author_id': user.get('userId', ''),
        'ip_location': note.get('ipLocation', ''),
        'liked_count': inter.get('likedCount', ''),
        'comment_count': inter.get('commentCount', ''),
        'collected_count': inter.get('collectedCount', ''),
        'liked': bool(inter.get('liked')),
        'collected': bool(inter.get('collected')),
        'images': [i.get('urlDefault') or i.get('urlPre', '') for i in imgs],
        'video_url': video_url,
    }


def _simplify_comment(c):
    u = c.get('userInfo') or {}
    subs = c.get('subComments') or []
    return {
        'id': c.get('id', ''),
        'content': c.get('content', ''),
        'like_count': c.get('likeCount', ''),
        'author': u.get('nickname') or u.get('nickName', ''),
        'author_id': u.get('userId', ''),
        'ip_location': c.get('ipLocation', ''),
        'sub_count': c.get('subCommentCount', ''),
        'sub_comments': [_simplify_comment(s) for s in subs] if subs else [],
    }
