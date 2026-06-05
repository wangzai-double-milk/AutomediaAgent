"""VLM 视觉点击通用层 + selector 自学习缓存（DOM/selector 失效时的鲁棒兜底通路）。

提供函数：
  - visual_locate(target_desc, screenshot_path) -> dict
      让豆包 VLM 在截图里定位目标元素，返回归一化坐标 {found,x_pct,y_pct,reason,raw}
  - visual_click(target_desc) -> dict
      入口先查缓存 → 命中且验证通过 → fast path（直接 CDP 点击，不调 VLM）
      失败/缓存 miss → VLM 定位 → CDP 物理点击 → 反查 selector 入缓存
  - visual_fill(target_desc, text) -> dict
      复用 visual_click 聚焦 → CDP insertText 输入文本。
  - learn_page(url_hint=None) -> dict
      主动学习当前页面：VLM 一次性识别页面主要交互元素，批量入缓存。

缓存：<project_root>/.cache/web_locator_cache.json
  key 公式：<host + path 前两段>::<target_desc 归一化>

使用场景：硬编码 selector 失效（小红书/任意网站改版）时的视觉兜底，
首次 VLM 调用后自动学习 selector 入缓存，同 URL 同 target_desc 后续直接 fast path 秒杀。

冒烟测试（不点击，仅定位）：
  python3 tools/web_vision.py "页面右上角的发布笔记按钮" /path/to/shot.png
"""

import os
import sys
import json
import time
import re
import logging
from datetime import datetime
from urllib.parse import urlparse

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.abspath(os.path.join(_THIS_DIR, '..'))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from tools.image import image_to_url, _http_post_json, _strip_md_fence, CHAT_URL, VLM_MODEL  # noqa: E402

_logger = logging.getLogger(__name__)


_LOCATE_PROMPT_TPL = """你是 UI 视觉定位专家。截图来自一个 Web 页面。
请在截图里精确定位用户描述的目标元素。
目标：{target_desc}

输出严格 JSON（无 markdown，无任何其他文本）：
{{"found": true, "x_pct": 0.523, "y_pct": 0.187, "reason": "<30字内说明为什么是这里>"}}

约束：
- 坐标是元素中心点
- x_pct/y_pct 是相对于截图宽/高的归一化值，必须 ∈ [0, 1]
- 找不到时 found=false，坐标随便填 0.5
"""


_LEARN_PAGE_PROMPT = """你是 UI 视觉分析专家。截图是一个 Web 页面的当前 viewport。
请一次性识别这个页面上"主要可交互元素"，包括：
- 标题/正文输入框、富文本编辑器
- 主要按钮（发布、保存、提交、登录、关注、点赞、收藏、关闭等）
- 主导航入口、关键链接
- 关键 tab / 切换控件

不要识别装饰性元素、背景图、纯文本段落、广告 banner。
每个元素给出**自然语言 target_desc**（要带位置和外观特征，便于以后复用）、**归一化中心坐标**、**kind**。

输出严格 JSON（无 markdown 围栏）：
{
  "elements": [
    {"target_desc": "页面顶部中央的标题输入框", "x_pct": 0.5, "y_pct": 0.18, "kind": "input"},
    {"target_desc": "页面右上角红色的发布按钮",  "x_pct": 0.92, "y_pct": 0.85, "kind": "button"}
  ]
}

kind 取值：input / editor / button / link / tab / other
最多输出 12 个元素。坐标是元素中心点，必须 ∈ [0, 1]。
"""


_SOM_PICK_PROMPT_TPL = """你是 Web UI 操作专家。截图里每个可交互元素都被框出并标了一个红色数字编号。
下面是这些编号对应元素的清单（id / 标签 / 文本）：
{catalog}

用户想操作的目标：{target_desc}

请判断该点哪个编号。只做选择题，不要给坐标。
输出严格 JSON（无 markdown，无任何其他文本），三选一：
1) 目标就在当前截图里 → {{"action":"click","id":3,"reason":"<30字内为什么是它>"}}
2) 当前屏没看到目标，但页面可能更下面还有 → {{"action":"scroll","dir":"down","reason":"..."}}
3) 当前屏没看到，且可能在更上面 → {{"action":"scroll","dir":"up","reason":"..."}}

约束：
- id 必须是清单里真实存在的编号
- 宁可滚动再看，也不要乱选一个不相关的编号
- 找不到且无处可滚时返回 {{"action":"none","reason":"..."}}
"""


def visual_locate(target_desc: str, screenshot_path: str) -> dict:
    """让豆包 VLM 在截图里找到 target_desc 描述的元素，返回归一化坐标。

    Args:
      target_desc: 自然语言目标描述，例：'页面右上角的"发布笔记"按钮'
      screenshot_path: 本地截图路径（PNG/JPG）

    Returns:
      {
        'found': bool,
        'x_pct': 0.0-1.0,    # 归一化坐标（相对截图宽度）
        'y_pct': 0.0-1.0,    # 归一化坐标（相对截图高度）
        'reason': str,
        'raw': str,          # VLM 原始返回
      }
    """
    raw = ''
    try:
        data_url = image_to_url(screenshot_path)
        prompt = _LOCATE_PROMPT_TPL.format(target_desc=target_desc)
        payload = {
            'model': VLM_MODEL,
            'messages': [{
                'role': 'user',
                'content': [
                    {'type': 'text', 'text': prompt},
                    {'type': 'image_url', 'image_url': {'url': data_url, 'detail': 'high'}},
                ],
            }],
            'temperature': 0.1,
            'max_tokens': 256,
        }
        data = _http_post_json(CHAT_URL, payload)
        raw = (data['choices'][0]['message']['content'] or '').strip()
        body = _strip_md_fence(raw)
        obj = json.loads(body)
        x = float(obj.get('x_pct', 0.5))
        y = float(obj.get('y_pct', 0.5))
        # 夹紧到 [0, 1]
        x = max(0.0, min(1.0, x))
        y = max(0.0, min(1.0, y))
        return {
            'found': bool(obj.get('found', False)),
            'x_pct': x,
            'y_pct': y,
            'reason': str(obj.get('reason', ''))[:120],
            'raw': raw,
        }
    except Exception as e:
        return {
            'found': False,
            'x_pct': 0.5,
            'y_pct': 0.5,
            'reason': f'visual_locate 异常: {e}',
            'raw': raw,
        }


# ─────────────────────────────────────────────────────────────────
# Selector 自学习缓存
# ─────────────────────────────────────────────────────────────────

# Selector 反向生成 JS：从 (x,y) 找最近可点击祖先 → 生成 selector + 文本锚 + tag + rect
SELECTOR_GEN_JS = r"""
(function(x, y){
  var el = document.elementFromPoint(x, y);
  if(!el) return JSON.stringify({success:false, reason:'no_element_at_point'});
  // 爬到最近的可点击祖先
  var target = el;
  for(var i=0; i<6 && target; i++){
    var cs = window.getComputedStyle(target);
    var tag = target.tagName;
    if(tag==='BUTTON'||tag==='A'||tag==='INPUT'||tag==='TEXTAREA') break;
    if(target.getAttribute('role')==='button'||target.onclick||cs.cursor==='pointer') break;
    if(target.getAttribute('contenteditable')==='true') break;
    target = target.parentElement;
  }
  if(!target) target = el;

  function genSel(el){
    if(el.id && !/^[0-9a-f]{8}-/.test(el.id) && !/^_/.test(el.id)){
      try {
        var sid = '#' + CSS.escape(el.id);
        if(document.querySelectorAll(sid).length===1) return sid;
      } catch(e){}
    }
    var dt = el.getAttribute && el.getAttribute('data-testid');
    if(dt){
      var s='[data-testid="'+dt+'"]';
      try { if(document.querySelectorAll(s).length===1) return s; } catch(e){}
    }
    var al = el.getAttribute && el.getAttribute('aria-label');
    if(al && al.length<30){
      var s2 = el.tagName.toLowerCase()+'[aria-label="'+al.replace(/"/g,'\\"')+'"]';
      try { if(document.querySelectorAll(s2).length===1) return s2; } catch(e){}
    }
    var cls = ((el.className && el.className.toString) ? el.className.toString() : '').trim().split(/\s+/).filter(function(c){
      return c && !/^_/.test(c) && c.length<30;
    });
    if(cls.length){
      for(var k=0;k<cls.length;k++){
        try {
          var s3 = el.tagName.toLowerCase()+'.'+CSS.escape(cls[k]);
          if(document.querySelectorAll(s3).length===1) return s3;
        } catch(e){}
      }
      try {
        var s4 = el.tagName.toLowerCase()+'.'+cls.map(function(c){return CSS.escape(c);}).join('.');
        if(document.querySelectorAll(s4).length<=3) return s4;
      } catch(e){}
    }
    // 兜底：tag + nth-of-type 路径（最多 3 层）
    var path = []; var cur = el;
    for(var i=0;i<3 && cur && cur.tagName!=='BODY';i++){
      var sib = cur.parentElement ? Array.prototype.filter.call(cur.parentElement.children, function(s){ return s.tagName===cur.tagName; }) : [];
      var idx = sib.indexOf(cur)+1;
      path.unshift(cur.tagName.toLowerCase()+(sib.length>1?':nth-of-type('+idx+')':''));
      cur = cur.parentElement;
    }
    return path.join('>');
  }

  var r = target.getBoundingClientRect();
  return JSON.stringify({
    success: true,
    selector: genSel(target),
    text_marker: ((target.innerText||target.value||'')+'').trim().slice(0,30),
    tag: target.tagName,
    w: Math.round(r.width),
    h: Math.round(r.height),
    cx: Math.round(r.left + r.width/2),
    cy: Math.round(r.top + r.height/2)
  });
})(__X__, __Y__);
"""


# 验证缓存项 selector 在当前 DOM 是否仍然有效
SELECTOR_VERIFY_JS = r"""
(function(sel, marker){
  try {
    var el = document.querySelector(sel);
    if(!el) return JSON.stringify({status:'invalid', reason:'no_match'});
    if(el.offsetParent === null && el.tagName !== 'BODY') {
      var cs = window.getComputedStyle(el);
      if(cs.display === 'none' || cs.visibility === 'hidden') {
        return JSON.stringify({status:'invalid', reason:'not_visible'});
      }
    }
    var r = el.getBoundingClientRect();
    if(r.width<2 || r.height<2) return JSON.stringify({status:'invalid', reason:'zero_size'});
    var txt = ((el.innerText||el.value||'')+'').trim();
    if(marker && marker.length>=2){
      var m10 = marker.slice(0,10);
      if(txt.indexOf(m10)<0) return JSON.stringify({status:'text_drift', reason:'marker_missing', cx: Math.round(r.left+r.width/2), cy: Math.round(r.top+r.height/2)});
    }
    return JSON.stringify({status:'valid', cx: Math.round(r.left+r.width/2), cy: Math.round(r.top+r.height/2), w: Math.round(r.width), h: Math.round(r.height)});
  } catch(e){
    return JSON.stringify({status:'invalid', reason:'exception:'+e.message});
  }
})(__SEL__, __MARKER__);
"""


# ─────────────────────────────────────────────────────────────────
# Set-of-Marks：枚举视口内可交互元素 → 编号标记 → VLM 做选择题 → 按编号精确点击
# 规避两个坑：① VLM 给坐标有 ±10px 误差易点偏 ② 纯文本/selector 匹配脆弱
# ─────────────────────────────────────────────────────────────────

# 枚举视口内可交互元素，去重，分配编号，打 data-som-id 锚 + 画红色编号徽标，返回元素清单。
SOM_MARK_JS = r"""
(function(){
  // 先清掉上一轮的标记层（幂等）
  var old = document.getElementById('__som_overlay__');
  if(old) old.parentNode.removeChild(old);
  var marked = document.querySelectorAll('[data-som-id]');
  for(var i=0;i<marked.length;i++) marked[i].removeAttribute('data-som-id');

  var W = window.innerWidth, H = window.innerHeight;
  function visible(el){
    var cs = window.getComputedStyle(el);
    if(cs.display==='none'||cs.visibility==='hidden'||parseFloat(cs.opacity||'1')<0.1) return false;
    var r = el.getBoundingClientRect();
    if(r.width<6||r.height<6) return false;
    // 至少部分落在视口内
    if(r.bottom<0||r.top>H||r.right<0||r.left>W) return false;
    return true;
  }
  function interactive(el){
    var tag = el.tagName;
    if(tag==='BUTTON'||tag==='A'||tag==='INPUT'||tag==='TEXTAREA'||tag==='SELECT') return true;
    var role = el.getAttribute('role');
    if(role==='button'||role==='tab'||role==='link'||role==='menuitem'||role==='checkbox'||role==='radio') return true;
    if(el.getAttribute('contenteditable')==='true') return true;
    if(el.hasAttribute('onclick')) return true;
    var cs = window.getComputedStyle(el);
    if(cs.cursor==='pointer') return true;
    return false;
  }
  var all = document.querySelectorAll('*');
  var candSet = new Set();
  var cands = [];
  for(var i=0;i<all.length;i++){
    var el = all[i];
    if(!interactive(el)||!visible(el)) continue;
    candSet.add(el);
    cands.push(el);
  }
  var VPAREA = W*H;
  // 把每个候选归并到“最外层仍可交互、面积不超过视口 18% 且文本不过长的祖先”作为代表，
  // 这样图标 svg/path + 文字 span 合并成同一个按钮，又不会把整条侧边栏/卡片列表并成一块。
  function repOf(el){
    var rep = el, cur = el.parentElement, hop = 0;
    while(cur && hop<5){
      if(candSet.has(cur)){
        var rr = cur.getBoundingClientRect();
        var txt = (cur.innerText||'').trim();
        if(rr.width*rr.height <= VPAREA*0.18 && txt.length<=24) rep = cur; else break;
      }
      cur = cur.parentElement; hop++;
    }
    return rep;
  }
  var repSet = new Set();
  var reps = [];
  for(var j=0;j<cands.length;j++){
    var rep = repOf(cands[j]);
    if(repSet.has(rep)) continue;
    repSet.add(rep); reps.push(rep);
  }
  // 顶层可见性校验：中心点命中自身/后代才保留
  var picked = [];
  for(var m=0;m<reps.length;m++){
    var el = reps[m];
    var r = el.getBoundingClientRect();
    var cx = Math.min(W-1, Math.max(0, r.left + r.width/2));
    var cy = Math.min(H-1, Math.max(0, r.top + r.height/2));
    var top = document.elementFromPoint(cx, cy);
    if(!top) continue;
    if(top!==el && !el.contains(top) && !top.contains(el)) continue;
    picked.push({el: el, r: r, cx: cx, cy: cy});
  }

  // 画标记层
  var overlay = document.createElement('div');
  overlay.id = '__som_overlay__';
  overlay.style.cssText = 'position:fixed;left:0;top:0;width:0;height:0;z-index:2147483647;pointer-events:none;';
  document.documentElement.appendChild(overlay);

  var out = [];
  for(var n=0;n<picked.length && n<60;n++){
    var p = picked[n];
    p.el.setAttribute('data-som-id', String(n));
    var box = document.createElement('div');
    box.style.cssText = 'position:fixed;border:2px solid #FF2D55;box-sizing:border-box;'
      + 'left:'+p.r.left+'px;top:'+p.r.top+'px;width:'+p.r.width+'px;height:'+p.r.height+'px;';
    var tag = document.createElement('div');
    tag.textContent = String(n);
    tag.style.cssText = 'position:fixed;background:#FF2D55;color:#fff;font:bold 12px/14px sans-serif;'
      + 'padding:1px 4px;border-radius:3px;left:'+p.r.left+'px;top:'+Math.max(0,p.r.top-15)+'px;';
    overlay.appendChild(box);
    overlay.appendChild(tag);
    var label = ((p.el.innerText||p.el.value||p.el.getAttribute('aria-label')||p.el.getAttribute('placeholder')||'')+'').trim().replace(/\s+/g,' ').slice(0,40);
    out.push({
      id: n,
      tag: p.el.tagName,
      role: p.el.getAttribute('role')||'',
      text: label,
      editable: (p.el.getAttribute('contenteditable')==='true')||p.el.tagName==='INPUT'||p.el.tagName==='TEXTAREA',
      cx: Math.round(p.cx), cy: Math.round(p.cy)
    });
  }
  return JSON.stringify({
    ok:true, count: out.length, elements: out,
    scroll:{x: window.scrollX, y: window.scrollY},
    page:{sh: document.documentElement.scrollHeight, ih: H, atBottom: (window.scrollY + H >= document.documentElement.scrollHeight - 4)}
  });
})();
"""


# 清掉标记层（截图给 VLM 后、真正点击前调用，避免徽标挡住目标）
SOM_CLEAR_JS = r"""
(function(){
  var old = document.getElementById('__som_overlay__');
  if(old) old.parentNode.removeChild(old);
  return JSON.stringify({ok:true});
})();
"""


# 按 data-som-id 把元素滚进视口中央，返回它当前的视口中心坐标（供 CDP 物理点击）。
SOM_LOCATE_BY_ID_JS = r"""
(function(somId){
  var el = document.querySelector('[data-som-id="'+somId+'"]');
  if(!el) return JSON.stringify({ok:false, reason:'no_som_id'});
  try{ el.scrollIntoView({block:'center', inline:'center'}); }catch(e){}
  var r = el.getBoundingClientRect();
  return JSON.stringify({
    ok:true,
    cx: Math.round(r.left + r.width/2),
    cy: Math.round(r.top + r.height/2),
    w: Math.round(r.width), h: Math.round(r.height),
    inview: (r.top>=0 && r.bottom<=window.innerHeight && r.left>=0 && r.right<=window.innerWidth)
  });
})("__SOMID__");
"""


# 滚动一屏（dir=1 向下 / -1 向上），返回是否还能继续滚。
# 关键：很多 SPA（如小红书创作中心）是 fixed 布局，内容在内部 overflow:auto 容器里，
# window 本身不滚动。所以 window 滚不动时，自动 fallback 到视口内最大的可滚动容器。
SOM_SCROLL_JS = r"""
(function(dir){
  var H = window.innerHeight, W = window.innerWidth;
  var amount = dir * Math.round(H*0.85);
  function pack(before, after, mode, sh, ch){
    return JSON.stringify({
      ok:true, mode:mode, before:before, after:after,
      moved: Math.abs(after-before)>2,
      atBottom: (after + ch >= sh - 4),
      atTop: (after<=2)
    });
  }
  // 1) 先试整个窗口
  var wb = window.scrollY;
  window.scrollBy(0, amount);
  if(Math.abs(window.scrollY-wb)>2){
    return pack(wb, window.scrollY, 'window', document.documentElement.scrollHeight, H);
  }
  // 2) window 不动 → 找视口内可见面积最大的可滚动容器
  var best=null, bestArea=0;
  var all=document.querySelectorAll('*');
  for(var i=0;i<all.length;i++){
    var el=all[i];
    if(el.scrollHeight - el.clientHeight <= 4) continue;      // 不可滚先跳过（便宜判断）
    var cs=window.getComputedStyle(el);
    var oy=cs.overflowY;
    if(oy!=='auto' && oy!=='scroll') continue;
    var r=el.getBoundingClientRect();
    if(r.width<40 || r.height<40) continue;
    if(r.bottom<=0 || r.top>=H) continue;                     // 不在视口内
    var visW=Math.min(r.right,W)-Math.max(r.left,0);
    var visH=Math.min(r.bottom,H)-Math.max(r.top,0);
    var visArea=visW*visH;
    if(visArea>bestArea){ bestArea=visArea; best=el; }
  }
  if(best){
    var sb=best.scrollTop;
    best.scrollTop += amount;
    return pack(sb, best.scrollTop, 'container', best.scrollHeight, best.clientHeight);
  }
  // 3) 啥都滚不动
  return JSON.stringify({ok:true, mode:'none', before:wb, after:window.scrollY,
                         moved:false, atBottom:true, atTop:true});
})(__DIR__);
"""


def _cache_path() -> str:
    """缓存文件路径：<project_root>/.cache/web_locator_cache.json"""
    return os.path.join(_PROJECT_ROOT, '.cache', 'web_locator_cache.json')


def _load_cache() -> dict:
    """加载缓存，缺失/损坏时返回新结构。"""
    p = _cache_path()
    try:
        with open(p, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if not isinstance(data, dict) or 'entries' not in data:
            return {'version': 1, 'entries': {}}
        return data
    except FileNotFoundError:
        return {'version': 1, 'entries': {}}
    except Exception as e:
        _logger.warning('加载 selector 缓存失败（忽略）: %s', e)
        return {'version': 1, 'entries': {}}


def _save_cache(data: dict) -> None:
    """原子写：先写 .tmp 再 rename。失败静默降级。"""
    p = _cache_path()
    try:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        tmp = p + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, p)
    except Exception as e:
        _logger.warning('保存 selector 缓存失败（忽略）: %s', e)


def _norm_target(s: str) -> str:
    """归一化 target_desc：strip / 全小写 / 把空白和中文标点压成单空格。"""
    if not s:
        return ''
    s = s.strip().lower()
    # 中文标点和空白统一压成单空格
    s = re.sub(r'[\s\u3000，。；：！？,\.;:!\?\'"`、（）()\[\]【】《》<>]+', ' ', s)
    return s.strip()


def _url_key(url: str) -> str:
    """从完整 URL 提 host+前两段路径，去掉 query。"""
    if not url:
        return ''
    try:
        u = urlparse(url)
        host = u.hostname or ''
        parts = [p for p in (u.path or '').split('/') if p]
        path = '/'.join(parts[:2])
        return f"{host}/{path}" if path else host
    except Exception:
        return url


def _compose_key(url: str, target: str) -> str:
    """缓存 key：<host+path 前两段>::<target_desc 归一化>"""
    return f"{_url_key(url)}::{_norm_target(target)}"


def _current_url() -> str:
    """从浏览器拿当前页面 URL（CSS 像素无关，只取 location.href）。"""
    try:
        from ga import web_execute_js
        res = web_execute_js("location.href;", no_monitor=True)
        if isinstance(res, dict):
            v = res.get('js_return') or res.get('data') or res.get('result')
            if isinstance(v, str):
                return v
    except Exception as e:
        _logger.warning('获取当前 URL 失败: %s', e)
    return ''


def _parse_js_return(res):
    """从 web_execute_js 返回里提取 js_return 并解析 JSON。"""
    val = None
    if isinstance(res, dict):
        val = res.get('js_return')
        if val is None:
            val = res.get('data') or res.get('result')
    if isinstance(val, str):
        try:
            return json.loads(val)
        except Exception:
            return val
    return val


def _learn_and_cache(target_desc: str, x_css: int, y_css: int, url: str = None) -> dict:
    """VLM 命中点击成功后，反查 (x_css,y_css) 处的 DOM 元素，生成 selector，写缓存。

    Returns: {ok: bool, selector?: str, text_marker?: str, ...}
    """
    try:
        from ga import web_execute_js
        if not url:
            url = _current_url()
        js = SELECTOR_GEN_JS.replace('__X__', str(int(x_css))).replace('__Y__', str(int(y_css)))
        res = web_execute_js(js, no_monitor=True)
        info = _parse_js_return(res)
        if not isinstance(info, dict) or not info.get('success'):
            _logger.info('selector 反查失败: %s', info)
            return {'ok': False, 'reason': str(info)[:120]}
        selector = info.get('selector') or ''
        if not selector:
            return {'ok': False, 'reason': 'empty_selector'}

        cache = _load_cache()
        key = _compose_key(url, target_desc)
        now = datetime.now().isoformat(timespec='seconds')
        old = (cache.get('entries') or {}).get(key) or {}
        entry = {
            'url_pattern': _url_key(url),
            'target_desc': target_desc,
            'selector': selector,
            'text_marker': info.get('text_marker') or '',
            'tag': info.get('tag') or '',
            'rect_hint': {'w': int(info.get('w') or 0), 'h': int(info.get('h') or 0)},
            'learned_at': old.get('learned_at') or now,
            'last_verified': now,
            'hit_count': int(old.get('hit_count') or 0),
            'fail_count': 0,
        }
        cache.setdefault('entries', {})[key] = entry
        _save_cache(cache)
        return {'ok': True, 'selector': selector, 'text_marker': entry['text_marker'], 'key': key}
    except Exception as e:
        _logger.warning('_learn_and_cache 异常（忽略）: %s', e)
        return {'ok': False, 'reason': str(e)}


def _try_cached(target_desc: str) -> dict:
    """查缓存 → 验证 selector 仍有效 → 返回 fast path 坐标。

    Returns:
      {ok: True, x, y, source:'cache', selector, key} 命中
      {ok: False, reason, key?}                       未命中/失效
    """
    try:
        url = _current_url()
        if not url:
            return {'ok': False, 'reason': 'no_url'}
        key = _compose_key(url, target_desc)
        cache = _load_cache()
        entry = (cache.get('entries') or {}).get(key)
        if not entry:
            return {'ok': False, 'reason': 'miss', 'key': key}

        from ga import web_execute_js
        sel = entry.get('selector') or ''
        marker = entry.get('text_marker') or ''
        js = (SELECTOR_VERIFY_JS
              .replace('__SEL__', json.dumps(sel))
              .replace('__MARKER__', json.dumps(marker)))
        res = web_execute_js(js, no_monitor=True)
        info = _parse_js_return(res)
        if not isinstance(info, dict):
            return {'ok': False, 'reason': f'verify_bad_return:{info}', 'key': key}
        status = info.get('status')
        if status == 'valid':
            entry['hit_count'] = int(entry.get('hit_count') or 0) + 1
            entry['last_verified'] = datetime.now().isoformat(timespec='seconds')
            entry['fail_count'] = 0
            cache['entries'][key] = entry
            _save_cache(cache)
            return {
                'ok': True, 'x': int(info.get('cx') or 0), 'y': int(info.get('cy') or 0),
                'source': 'cache', 'selector': sel, 'key': key,
            }
        # 失败：fail_count++，超阈值删除
        entry['fail_count'] = int(entry.get('fail_count') or 0) + 1
        if entry['fail_count'] >= 3:
            try: del cache['entries'][key]
            except Exception: pass
        else:
            cache['entries'][key] = entry
        _save_cache(cache)
        return {'ok': False, 'reason': f'verify_{status}', 'key': key}
    except Exception as e:
        _logger.warning('_try_cached 异常（忽略）: %s', e)
        return {'ok': False, 'reason': f'exception:{e}'}


def _page_fingerprint() -> dict:
    """抓当前页面指纹：URL + DOM 规模 + body 文本长度 + 可见弹层数。
    用于点击前后对比，判断点击是否真的触发了页面变化。失败返回 {}。"""
    try:
        from ga import web_execute_js
        js = r"""(function(){
          try{
            var pops = document.querySelectorAll(
              '[class*="dropdown"],[class*="popover"],[class*="modal"],[class*="dialog"],[role="dialog"],[class*="select-dropdown"]');
            var vis = 0;
            for(var i=0;i<pops.length;i++){
              var r=pops[i].getBoundingClientRect();
              if(r.width>0 && r.height>0) vis++;
            }
            return JSON.stringify({
              url: location.href,
              nodes: document.getElementsByTagName('*').length,
              textlen: (document.body? document.body.innerText.length : 0),
              popups: vis
            });
          }catch(e){ return JSON.stringify({err:String(e)}); }
        })();"""
        info = _parse_js_return(web_execute_js(js, no_monitor=True))
        return info if isinstance(info, dict) else {}
    except Exception as e:
        _logger.warning('_page_fingerprint 异常: %s', e)
        return {}


def _fp_changed(before: dict, after: dict) -> bool:
    """对比两次指纹是否有"有意义的变化"：URL 变 / 节点数变 / 文本长度变 / 弹层数变。"""
    if not before or not after:
        return True  # 抓不到指纹时不误判为"没反应"，保守放行
    if before.get('url') != after.get('url'):
        return True
    if before.get('popups') != after.get('popups'):
        return True
    if abs(int(before.get('nodes', 0)) - int(after.get('nodes', 0))) >= 2:
        return True
    if abs(int(before.get('textlen', 0)) - int(after.get('textlen', 0))) >= 3:
        return True
    return False


# ─────────────────────────────────────────────────────────────────
# Set-of-Marks 执行层
# ─────────────────────────────────────────────────────────────────

def _som_mark() -> dict:
    """枚举当前视口可交互元素 + 画编号徽标。返回 {ok,count,elements,scroll,page} 或 {}。"""
    try:
        from ga import web_execute_js
        info = _parse_js_return(web_execute_js(SOM_MARK_JS, no_monitor=True))
        if isinstance(info, dict):
            _logger.info('[som] _som_mark 枚举到 %s 个可交互元素 scroll=%s page=%s',
                         info.get('count'), info.get('scroll'), info.get('page'))
            return info
        _logger.warning('[som] _som_mark 返回非 dict: %r', info)
        return {}
    except Exception as e:
        _logger.warning('_som_mark 异常: %s', e)
        return {}


def _som_clear() -> None:
    """清掉编号徽标层（截图后、点击前调用）。"""
    try:
        from ga import web_execute_js
        web_execute_js(SOM_CLEAR_JS, no_monitor=True)
        _logger.debug('[som] _som_clear 已清除徽标层')
    except Exception as e:
        _logger.warning('_som_clear 异常: %s', e)


def _som_scroll(direction: str) -> dict:
    """滚动一屏。direction='down'/'up'。返回 {ok,moved,atBottom,atTop}。"""
    try:
        from ga import web_execute_js
        d = -1 if direction == 'up' else 1
        info = _parse_js_return(web_execute_js(SOM_SCROLL_JS.replace('__DIR__', str(d)), no_monitor=True))
        if isinstance(info, dict):
            _logger.info('[som] _som_scroll dir=%s moved=%s %s->%s atBottom=%s atTop=%s',
                         direction, info.get('moved'), info.get('before'), info.get('after'),
                         info.get('atBottom'), info.get('atTop'))
            return info
        _logger.warning('[som] _som_scroll 返回非 dict: %r', info)
        return {}
    except Exception as e:
        _logger.warning('_som_scroll 异常: %s', e)
        return {}


def _som_locate(som_id: int) -> dict:
    """按 data-som-id 把元素滚进视口中央，返回当前视口中心坐标 {ok,cx,cy,...}。"""
    try:
        from ga import web_execute_js
        js = SOM_LOCATE_BY_ID_JS.replace('__SOMID__', str(int(som_id)))
        info = _parse_js_return(web_execute_js(js, no_monitor=True))
        if isinstance(info, dict):
            if info.get('ok'):
                _logger.info('[som] _som_locate #%s -> (%s,%s) w=%s h=%s inview=%s',
                             som_id, info.get('cx'), info.get('cy'),
                             info.get('w'), info.get('h'), info.get('inview'))
            else:
                _logger.warning('[som] _som_locate #%s 失败: %s', som_id, info.get('reason'))
            return info
        _logger.warning('[som] _som_locate #%s 返回非 dict: %r', som_id, info)
        return {}
    except Exception as e:
        _logger.warning('_som_locate 异常: %s', e)
        return {}


def _som_pick(target_desc: str, elements: list, screenshot_path: str) -> dict:
    """让 VLM 看带编号的截图做选择题。返回 {action:'click',id} / {action:'scroll',dir} / {action:'none'}。"""
    raw = ''
    try:
        # 元素清单文本（id / tag / text），控制长度
        lines = []
        for e in elements:
            t = (e.get('text') or '').strip()
            lines.append(f"[{e.get('id')}] {e.get('tag', '')}{('/'+e.get('role')) if e.get('role') else ''}: {t}")
        catalog = '\n'.join(lines) if lines else '(无)'
        _logger.info('[som] _som_pick target=%r 候选数=%d 截图=%s',
                     target_desc, len(elements), screenshot_path)
        data_url = image_to_url(screenshot_path)
        prompt = _SOM_PICK_PROMPT_TPL.format(catalog=catalog, target_desc=target_desc)
        payload = {
            'model': VLM_MODEL,
            'messages': [{
                'role': 'user',
                'content': [
                    {'type': 'text', 'text': prompt},
                    {'type': 'image_url', 'image_url': {'url': data_url, 'detail': 'high'}},
                ],
            }],
            'temperature': 0.1,
            'max_tokens': 256,
        }
        data = _http_post_json(CHAT_URL, payload)
        raw = (data['choices'][0]['message']['content'] or '').strip()
        obj = json.loads(_strip_md_fence(raw))
        action = str(obj.get('action') or 'none')
        out = {'action': action, 'reason': str(obj.get('reason', ''))[:120], 'raw': raw}
        if action == 'click':
            out['id'] = int(obj.get('id'))
        elif action == 'scroll':
            out['dir'] = 'up' if str(obj.get('dir')) == 'up' else 'down'
        _logger.info('[som] _som_pick VLM 决策: action=%s id=%s dir=%s reason=%s',
                     out.get('action'), out.get('id'), out.get('dir'), out.get('reason'))
        return out
    except Exception as e:
        _logger.warning('[som] _som_pick 异常: %s (raw=%s)', e, raw[:160])
        return {'action': 'none', 'reason': f'_som_pick 异常: {e}', 'raw': raw}


def som_click(target_desc: str, save_screenshot_to: str = None, verify: bool = True,
              max_scrolls: int = 6) -> dict:
    """Set-of-Marks 点击：枚举视口可交互元素→编号截图→VLM 选编号→按 ref scrollIntoView→CDP 点击。
    当前屏找不到时按 VLM 指示上下滚动再枚举重试（最多 max_scrolls 次）。
    点击后抓页面指纹验证是否真触发变化；命中则反查 selector 入缓存供下次 fast path。

    Returns:
      {'ok','verified','x','y','target','reason','screenshot','source':'som','som_id'?,'scrolls'?}
    """
    from ga import web_screenshot, cdp_mouse_click

    _logger.info('[som] som_click 开始 target=%r verify=%s max_scrolls=%s', target_desc, verify, max_scrolls)
    scrolled_dirs = []  # 记录滚过的方向，避免来回死循环
    last_reason = ''
    for attempt in range(max_scrolls + 1):
        _logger.info('[som] === attempt %d/%d (已滚动 %s) ===', attempt, max_scrolls, scrolled_dirs)
        mark = _som_mark()
        elements = mark.get('elements') if isinstance(mark, dict) else None
        if not elements:
            # 没枚举到元素：尝试向下滚一屏看看
            _logger.warning('[som] attempt %d 未枚举到元素，尝试向下滚动', attempt)
            sc = _som_scroll('down')
            if not sc.get('moved'):
                _logger.warning('[som] som_click 失败：未枚举到可交互元素且无法滚动')
                return {'ok': False, 'verified': False, 'x': 0, 'y': 0, 'target': target_desc,
                        'reason': f'未枚举到可交互元素且无法滚动: {mark}', 'screenshot': '',
                        'source': 'som', 'scrolls': attempt}
            time.sleep(0.4)
            continue

        # 带徽标截图 → VLM 选
        shot = web_screenshot(save_path=save_screenshot_to)
        if not isinstance(shot, dict) or 'path' not in shot:
            _logger.warning('[som] som_click 失败：截图失败 %s', shot)
            _som_clear()
            return {'ok': False, 'verified': False, 'x': 0, 'y': 0, 'target': target_desc,
                    'reason': f'截图失败: {shot}', 'screenshot': '', 'source': 'som', 'scrolls': attempt}
        shot_path = shot['path']
        pick = _som_pick(target_desc, elements, shot_path)
        action = pick.get('action')
        last_reason = pick.get('reason', '')

        if action == 'click':
            som_id = pick.get('id')
            _som_clear()  # 点之前先把徽标清掉，免得挡住
            loc = _som_locate(som_id)
            if not loc.get('ok'):
                _logger.warning('[som] som_click 失败：VLM 选了 #%s 但定位失败 %s', som_id, loc.get('reason'))
                return {'ok': False, 'verified': False, 'x': 0, 'y': 0, 'target': target_desc,
                        'reason': f"VLM 选了 #{som_id} 但定位失败: {loc.get('reason')}",
                        'screenshot': shot_path, 'source': 'som', 'som_id': som_id, 'scrolls': attempt}
            time.sleep(0.2)  # 等 scrollIntoView 落定
            cx, cy = int(loc['cx']), int(loc['cy'])
            fp_before = _page_fingerprint() if verify else {}
            _logger.info('[som] 物理点击 #%s @(%s,%s) verify=%s', som_id, cx, cy, verify)
            click_ret = cdp_mouse_click(cx, cy)
            if isinstance(click_ret, dict) and click_ret.get('error'):
                _logger.warning('[som] som_click 失败：CDP 点击报错 %s', click_ret.get('error'))
                return {'ok': False, 'verified': False, 'x': cx, 'y': cy, 'target': target_desc,
                        'reason': f"CDP 点击失败: {click_ret.get('error')}",
                        'screenshot': shot_path, 'source': 'som', 'som_id': som_id, 'scrolls': attempt}
            if not verify:
                _learn_and_cache(target_desc, cx, cy)
                _logger.info('[som] som_click 完成(未验证) #%s @(%s,%s)', som_id, cx, cy)
                return {'ok': True, 'verified': None, 'x': cx, 'y': cy, 'target': target_desc,
                        'reason': f"som #{som_id}: {pick.get('reason', '')}",
                        'screenshot': shot_path, 'source': 'som', 'som_id': som_id, 'scrolls': attempt}
            time.sleep(0.4)
            verified = _fp_changed(fp_before, _page_fingerprint())
            learn = _learn_and_cache(target_desc, cx, cy) if verified else {'ok': False}
            _logger.info('[som] som_click 完成 #%s @(%s,%s) verified=%s learned=%s selector=%s',
                         som_id, cx, cy, verified, bool(learn.get('ok')), learn.get('selector', ''))
            return {
                'ok': bool(verified), 'verified': bool(verified), 'x': cx, 'y': cy, 'target': target_desc,
                'reason': (f"som #{som_id}: {pick.get('reason', '')}" if verified
                           else f"已点击 #{som_id} 但页面无变化（可能不是目标/不可点），建议换 target_desc"),
                'screenshot': shot_path, 'source': 'som', 'som_id': som_id,
                'learned': bool(learn.get('ok')), 'selector': learn.get('selector', ''),
                'scrolls': attempt,
            }

        if action == 'scroll' and attempt < max_scrolls:
            d = pick.get('dir', 'down')
            _logger.info('[som] VLM 要求滚动 dir=%s（当前屏没看到目标）', d)
            _som_clear()
            sc = _som_scroll(d)
            scrolled_dirs.append(d)
            if not sc.get('moved'):
                # 已经到边界，换方向试一次；都到边界就放弃
                other = 'up' if d == 'down' else 'down'
                if other in scrolled_dirs:
                    _logger.info('[som] 两个方向都已到边界，停止滚动')
                    break
                _logger.info('[som] %s 已到边界，换方向 %s 再试', d, other)
                sc2 = _som_scroll(other)
                scrolled_dirs.append(other)
                if not sc2.get('moved'):
                    break
            time.sleep(0.5)
            continue

        # action == 'none' 或滚动次数用尽
        _logger.info('[som] VLM 返回 action=%s（放弃）reason=%s', action, last_reason)
        _som_clear()
        break

    _logger.warning('[som] som_click 未找到目标 target=%r（滚动 %d 次后放弃）: %s',
                    target_desc, len(scrolled_dirs), last_reason)
    return {'ok': False, 'verified': False, 'x': 0, 'y': 0, 'target': target_desc,
            'reason': f'Set-of-Marks 未找到目标（滚动 {len(scrolled_dirs)} 次后放弃）: {last_reason}',
            'screenshot': '', 'source': 'som', 'scrolls': len(scrolled_dirs)}


def visual_click(target_desc: str, save_screenshot_to: str = None, verify: bool = True) -> dict:
    """缓存命中 → fast path 直接 CDP 点击；未命中 → Set-of-Marks（VLM 做选择题选编号，按 ref
    精确 scrollIntoView+点击，自动滚动找视口外元素）；som 失败时再退回旧的"VLM 给归一化坐标"兜底。

    点击后会抓 URL+DOM 指纹对比：若点击前后页面毫无变化，返回 verified=False（点了没反应），
    避免"CDP 命令发出去没报错就当成功"的假信号。
    verify=False 时跳过指纹验证（用于"点击只为聚焦输入框"这种本就不改变 DOM 的场景，如 visual_fill）。

    Returns:
      {'ok': bool, 'verified': bool, 'x': int, 'y': int, 'target': str, 'reason': str, 'screenshot': str, 'source': 'cache'/'som'/'vlm'}
    """
    # lazy import 避免循环依赖
    from ga import web_screenshot, cdp_get_viewport, cdp_mouse_click

    _logger.info('[visual_click] 开始 target=%r verify=%s', target_desc, verify)

    # 1) 先查缓存
    hit = _try_cached(target_desc)
    if hit.get('ok'):
        cx, cy = hit['x'], hit['y']
        _logger.info('[visual_click] 缓存命中 fast-path @(%s,%s) selector=%s', cx, cy, hit.get('selector', '')[:60])
        fp_before = _page_fingerprint() if verify else {}
        click_ret = cdp_mouse_click(cx, cy)
        if isinstance(click_ret, dict) and click_ret.get('error'):
            _logger.warning('[visual_click] 缓存路径 CDP 点击报错: %s', click_ret.get('error'))
            return {
                'ok': False, 'verified': False, 'x': cx, 'y': cy, 'target': target_desc,
                'reason': f"CDP 点击失败(cache): {click_ret.get('error')}",
                'screenshot': '', 'source': 'cache',
            }
        if not verify:
            _logger.info('[visual_click] 缓存路径完成(未验证) @(%s,%s)', cx, cy)
            return {
                'ok': True, 'verified': None, 'x': cx, 'y': cy, 'target': target_desc,
                'reason': f"cache hit (未验证): {hit.get('selector', '')[:60]}",
                'screenshot': '', 'source': 'cache',
            }
        time.sleep(0.4)
        verified = _fp_changed(fp_before, _page_fingerprint())
        if verified:
            _logger.info('[visual_click] 缓存路径完成 verified=True @(%s,%s)', cx, cy)
            return {
                'ok': True, 'verified': True, 'x': cx, 'y': cy, 'target': target_desc,
                'reason': f"cache hit: {hit.get('selector', '')[:60]}",
                'screenshot': '', 'source': 'cache',
            }
        # 缓存点了没反应（可能坐标失效）→ 不直接失败，落到 Set-of-Marks 重定位
        _logger.info('[visual_click] cache 命中但点击无变化，回退 Set-of-Marks: %s', target_desc)
    else:
        _logger.info('[visual_click] 缓存未命中(%s)，走 Set-of-Marks', hit.get('reason'))

    # 2) Set-of-Marks：枚举编号 → VLM 选编号 → 按 ref 精确点击（支持滚动）
    som = som_click(target_desc, save_screenshot_to=save_screenshot_to, verify=verify)
    # som 成功，或 verify=False 下确实点了某编号（verified=None），直接采用
    if som.get('ok') or (som.get('verified') is None and som.get('som_id') is not None):
        _logger.info('[visual_click] 采用 Set-of-Marks 结果 ok=%s verified=%s som_id=%s',
                     som.get('ok'), som.get('verified'), som.get('som_id'))
        return som
    # som 明确点中某编号但页面无变化：返回它的诊断，不再用旧坐标法重复点
    if som.get('som_id') is not None:
        _logger.info('[visual_click] Set-of-Marks 点了 #%s 但无变化，返回诊断不再兜底', som.get('som_id'))
        return som

    # 2.5) closed-shadow 穿透兜底：小红书发布页底部「发布」按钮在
    # <xhs-publish-btn> 的 closed shadowRoot 里，JS/SoM 物理枚举不到（永远没编号），
    # 这是 web_visual_click 标不出这俩按钮的根因。SoM 没找到时，按描述关键词路由到 CDP 穿透。
    desc_l = (target_desc or '')
    pub_kind = None
    # ⚠️ 防误发布：含可见性/权限/设置类关键词的描述（如"…定时发布选项上方的公开可见按钮"）
    # 绝不路由到穿透"发布"——历史 bug：描述里"定时发布"的"发布"二字导致误点真发布按钮把笔记公开发出去。
    _no_publish_kw = ('公开可见', '仅自己可见', '权限', '下拉', '选项', '复选框',
                      '定时发布', '允许', '复制', '更多设置', '可见性')
    if '发布' in desc_l and not any(k in desc_l for k in _no_publish_kw):
        pub_kind = 'publish'
    if pub_kind is not None:
        try:
            from ga import cdp_find_xhs_publish_bar_btn
            _logger.info('[visual_click] SoM 未命中，尝试 closed-shadow 穿透发布栏按钮')
            bar = cdp_find_xhs_publish_bar_btn()
            if bar.get('ok'):
                bx, by = bar['x'], bar['y']
                fp_before = _page_fingerprint() if verify else {}
                click_ret = cdp_mouse_click(bx, by)
                if isinstance(click_ret, dict) and click_ret.get('error'):
                    return {'ok': False, 'verified': False, 'x': bx, 'y': by, 'target': target_desc,
                            'reason': f"CDP 点击失败(shadow-pierce): {click_ret.get('error')}",
                            'screenshot': '', 'source': 'shadow_pierce'}
                if not verify:
                    return {'ok': True, 'verified': None, 'x': bx, 'y': by, 'target': target_desc,
                            'reason': f'closed-shadow 穿透点击发布栏按钮({pub_kind})',
                            'screenshot': '', 'source': 'shadow_pierce'}
                time.sleep(0.6)
                verified = _fp_changed(fp_before, _page_fingerprint())
                return {'ok': bool(verified), 'verified': bool(verified), 'x': bx, 'y': by,
                        'target': target_desc,
                        'reason': (f'closed-shadow 穿透点击发布栏按钮({pub_kind})' if verified
                                   else f'穿透点了发布栏按钮({pub_kind})但页面无变化'),
                        'screenshot': '', 'source': 'shadow_pierce'}
            else:
                _logger.info('[visual_click] 穿透未找到发布栏按钮: %s', bar.get('reason'))
        except Exception as e:
            _logger.warning('[visual_click] closed-shadow 穿透异常: %s', e)

    # 3) 兜底：旧的"VLM 给归一化坐标"法（som 完全没找到元素时）
    _logger.info('[visual_click] Set-of-Marks 未定位到元素，回退旧坐标法兜底')
    shot = web_screenshot(save_path=save_screenshot_to)
    if not isinstance(shot, dict) or 'path' not in shot:
        return {'ok': False, 'verified': False, 'x': 0, 'y': 0, 'target': target_desc,
                'reason': f'截图失败: {shot}', 'screenshot': '', 'source': 'vlm'}

    shot_path = shot['path']
    loc = visual_locate(target_desc, shot_path)
    if not loc.get('found'):
        return {'ok': False, 'verified': False, 'x': 0, 'y': 0, 'target': target_desc,
                'reason': f"Set-of-Marks 与坐标兜底均未定位到目标: som={som.get('reason', '')[:60]} / vlm={loc.get('reason', '')}",
                'screenshot': shot_path, 'source': 'vlm'}

    vp = cdp_get_viewport() or {}
    vp_w = vp.get('w') or 0
    vp_h = vp.get('h') or 0
    if not vp_w or not vp_h:
        return {'ok': False, 'verified': False, 'x': 0, 'y': 0, 'target': target_desc,
                'reason': f'获取 viewport 失败: {vp}', 'screenshot': shot_path, 'source': 'vlm'}

    # 截图是物理像素（含 DPR），CDP Input.dispatchMouseEvent 用 CSS 像素。
    # 归一化坐标在两个坐标系下等价，所以直接乘 viewport 尺寸即可。
    cx = int(round(loc['x_pct'] * vp_w))
    cy = int(round(loc['y_pct'] * vp_h))
    fp_before = _page_fingerprint() if verify else {}
    click_ret = cdp_mouse_click(cx, cy)
    if isinstance(click_ret, dict) and click_ret.get('error'):
        return {'ok': False, 'verified': False, 'x': cx, 'y': cy, 'target': target_desc,
                'reason': f"CDP 点击失败(vlm): {click_ret.get('error')}",
                'screenshot': shot_path, 'source': 'vlm'}
    if not verify:
        learn = _learn_and_cache(target_desc, cx, cy)
        return {
            'ok': True, 'verified': None, 'x': cx, 'y': cy, 'target': target_desc,
            'reason': loc.get('reason', ''), 'screenshot': shot_path, 'source': 'vlm',
            'learned': bool(learn.get('ok')), 'selector': learn.get('selector', ''),
        }
    time.sleep(0.4)
    verified = _fp_changed(fp_before, _page_fingerprint())

    # 3) 反查 selector 入缓存（失败静默）；仅在点击确实生效时才学，避免把"点偏的坐标"写进缓存
    learn = _learn_and_cache(target_desc, cx, cy) if verified else {'ok': False}

    return {
        'ok': bool(verified), 'verified': bool(verified), 'x': cx, 'y': cy, 'target': target_desc,
        'reason': (loc.get('reason', '') if verified
                   else "VLM 定位并点击了该坐标，但页面无任何变化（很可能点偏/目标不可点）。建议换更精确的 target_desc 重试"),
        'screenshot': shot_path, 'source': 'vlm',
        'learned': bool(learn.get('ok')),
        'selector': learn.get('selector', ''),
    }


def visual_fill(target_desc: str, text: str) -> dict:
    """截图 → 定位输入框 → 点击聚焦 → CDP insertText 输入。

    Returns:
      {'ok': bool, 'x': int, 'y': int, 'len': int, 'reason': str, 'source': str}
    """
    click_r = visual_click(target_desc, verify=False)
    if not click_r.get('ok'):
        return {
            'ok': False,
            'x': click_r.get('x', 0),
            'y': click_r.get('y', 0),
            'len': 0,
            'reason': click_r.get('reason', '点击失败'),
            'source': click_r.get('source', ''),
        }

    # 等聚焦稳定（React/tiptap 需要事件循环 tick）
    time.sleep(0.3)

    from ga import cdp_type_text
    ret = cdp_type_text(text or '')
    if isinstance(ret, dict) and ret.get('error'):
        return {
            'ok': False,
            'x': click_r['x'], 'y': click_r['y'],
            'len': 0,
            'reason': f"insertText 失败: {ret.get('error')}",
            'source': click_r.get('source', ''),
        }
    return {
        'ok': True,
        'x': click_r['x'],
        'y': click_r['y'],
        'len': len(text or ''),
        'reason': click_r.get('reason', ''),
        'source': click_r.get('source', ''),
    }


# ─────────────────────────────────────────────────────────────────
# 主动学习：一次性扫整页交互元素入缓存
# ─────────────────────────────────────────────────────────────────

def _vlm_learn_elements(screenshot_path: str) -> list:
    """让 VLM 一次性识别整页主要交互元素。返回 list[{target_desc, x_pct, y_pct, kind}]。"""
    raw = ''
    try:
        data_url = image_to_url(screenshot_path)
        payload = {
            'model': VLM_MODEL,
            'messages': [{
                'role': 'user',
                'content': [
                    {'type': 'text', 'text': _LEARN_PAGE_PROMPT},
                    {'type': 'image_url', 'image_url': {'url': data_url, 'detail': 'high'}},
                ],
            }],
            'temperature': 0.1,
            'max_tokens': 1024,
        }
        data = _http_post_json(CHAT_URL, payload)
        raw = (data['choices'][0]['message']['content'] or '').strip()
        body = _strip_md_fence(raw)
        obj = json.loads(body)
        elems = obj.get('elements') or []
        out = []
        for e in elems:
            if not isinstance(e, dict): continue
            td = (e.get('target_desc') or '').strip()
            if not td: continue
            try:
                x = max(0.0, min(1.0, float(e.get('x_pct', 0.5))))
                y = max(0.0, min(1.0, float(e.get('y_pct', 0.5))))
            except Exception:
                continue
            out.append({
                'target_desc': td[:120],
                'x_pct': x,
                'y_pct': y,
                'kind': str(e.get('kind') or 'other')[:20],
            })
        return out[:12]
    except Exception as e:
        _logger.warning('_vlm_learn_elements 异常: %s (raw=%s)', e, raw[:120])
        return []


def learn_page(url_hint: str = None) -> dict:
    """主动学习当前页面：截图 → VLM 一次性识别交互元素 → 批量反查 selector 入缓存。

    Args:
      url_hint: 可选，当前页面 URL 描述（不用也行，会自动取 location.href）

    Returns:
      {ok, learned, total, key_examples, url, screenshot, items}
    """
    from ga import web_screenshot, cdp_get_viewport

    url = _current_url() or (url_hint or '')
    shot = web_screenshot()
    if not isinstance(shot, dict) or 'path' not in shot:
        return {'ok': False, 'learned': 0, 'total': 0, 'reason': f'截图失败: {shot}'}

    elems = _vlm_learn_elements(shot['path'])
    if not elems:
        return {
            'ok': False, 'learned': 0, 'total': 0,
            'reason': 'VLM 未返回任何元素',
            'url': url, 'screenshot': shot['path'],
        }

    vp = cdp_get_viewport() or {}
    vp_w = vp.get('w') or 0
    vp_h = vp.get('h') or 0
    if not vp_w or not vp_h:
        return {'ok': False, 'learned': 0, 'total': len(elems),
                'reason': f'viewport 异常: {vp}', 'url': url, 'screenshot': shot['path']}

    learned = 0
    items = []
    key_examples = []
    for e in elems:
        cx = int(round(e['x_pct'] * vp_w))
        cy = int(round(e['y_pct'] * vp_h))
        r = _learn_and_cache(e['target_desc'], cx, cy, url=url)
        item = {
            'target_desc': e['target_desc'],
            'kind': e['kind'],
            'x': cx, 'y': cy,
            'learned': bool(r.get('ok')),
            'selector': r.get('selector', ''),
        }
        items.append(item)
        if r.get('ok'):
            learned += 1
            if len(key_examples) < 6:
                key_examples.append(e['target_desc'])

    return {
        'ok': learned > 0,
        'learned': learned,
        'total': len(elems),
        'url': url,
        'url_pattern': _url_key(url),
        'screenshot': shot['path'],
        'key_examples': key_examples,
        'items': items,
    }


# ─────────────────────────────────────────────────────────────────
# 冒烟测试：仅做 visual_locate（不点击，不依赖 ga / bridge）
# ─────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    if len(sys.argv) < 3:
        print('用法: python3 tools/web_vision.py "<目标描述>" <截图路径>')
        sys.exit(1)
    desc = sys.argv[1]
    shot = sys.argv[2]
    r = visual_locate(desc, shot)
    print(json.dumps(r, ensure_ascii=False, indent=2))
