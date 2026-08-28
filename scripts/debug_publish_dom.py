"""Dump Xiaohongshu creator publish-page DOM candidates for diagnostics.

Usage:
    .venv/bin/python scripts/debug_publish_dom.py

The script navigates to the image publish page, captures likely editor/button
elements, and writes the full dump to temp/publish_dom_dump.json.
"""
import json
import os
import sys
import time

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT_DIR)

from ga import first_init_driver, web_execute_js  # noqa: E402


def main():
    try:
        first_init_driver()
    except Exception as e:
        print(f'[WARN] init_driver 失败：{e}')
        return 1

    time.sleep(2.0)

    nav_js = """
    (function(){
      if(location.href.indexOf('/publish/publish')<0){
        location.href='https://creator.xiaohongshu.com/publish/publish?source=official&from=menu&target=image';
        return 'navigating';
      }
      if(location.href.indexOf('target=video')>=0){
        function realClick(el){
          const r=el.getBoundingClientRect();
          const o={bubbles:true,cancelable:true,view:window,clientX:r.left+r.width/2,clientY:r.top+r.height/2,button:0};
          ['mouseover','mousedown','mouseup','click'].forEach(t=>el.dispatchEvent(new MouseEvent(t,o)));
        }
        const all=Array.from(document.querySelectorAll('div,span,a,button'));
        for(const t of all){
          const tx=(t.innerText||'').trim();
          if(tx==='上传图文'||tx==='发图文'){realClick(t);return 'clicked_tab:'+tx;}
        }
        return 'on_video_tab_no_match';
      }
      return 'already_on_image';
    })();
    """
    print('[INFO] 跳到图文发布页 ...')
    print('  ->', web_execute_js(nav_js))
    time.sleep(5.0)

    dump_js = r"""
    (function(){
      const out={url:location.href, title:document.title};
      out.contenteditables = Array.from(document.querySelectorAll('[contenteditable]')).map(el=>{
        const r=el.getBoundingClientRect();
        return {
          tag: el.tagName,
          cls: (el.className||'').toString().slice(0,80),
          id: el.id||'',
          role: el.getAttribute('role')||'',
          placeholder: el.getAttribute('placeholder')||el.getAttribute('data-placeholder')||'',
          w: Math.round(r.width), h: Math.round(r.height),
          visible: el.offsetParent !== null,
          innerText_len: (el.innerText||'').length,
          innerText_preview: (el.innerText||'').slice(0,40),
        };
      });
      out.inputs = Array.from(document.querySelectorAll('input')).map(el=>{
        const r=el.getBoundingClientRect();
        return {
          type: el.type, cls: (el.className||'').toString().slice(0,80),
          placeholder: el.placeholder||'', value_len: (el.value||'').length,
          w: Math.round(r.width), h: Math.round(r.height), visible: el.offsetParent!==null,
        };
      }).filter(o=>o.visible);
      const KW=['草稿','发布','保存','暂存','存为','发表'];
      out.buttons = [];
      Array.from(document.querySelectorAll('button,div,span,a')).forEach(el=>{
        const tx=(el.innerText||'').trim();
        if(!tx||tx.length>12) return;
        if(!KW.some(k=>tx.indexOf(k)>=0)) return;
        const r=el.getBoundingClientRect();
        out.buttons.push({
          tag: el.tagName, text: tx,
          cls: (el.className||'').toString().slice(0,80),
          w: Math.round(r.width), h: Math.round(r.height),
          visible: el.offsetParent!==null,
          disabled: el.disabled || el.getAttribute('aria-disabled')==='true',
          role: el.getAttribute('role')||'',
        });
      });
      out.iframes = Array.from(document.querySelectorAll('iframe')).map(f=>({
        src: (f.src||'').slice(0,100), w: f.width, h: f.height,
      }));
      return out;
    })();
    """

    print('[INFO] dump 当前活跃 tab 的 DOM ...')
    res = web_execute_js(dump_js, no_monitor=True)
    if isinstance(res, dict):
        val = res.get('js_return') or res.get('result') or res.get('value') or res
    else:
        val = res

    print('[INFO] 原始返回类型:', type(val).__name__)
    if isinstance(val, str):
        try:
            val = json.loads(val)
        except Exception:
            pass

    out_path = os.path.join(ROOT_DIR, 'temp', 'publish_dom_dump.json')
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(val, f, ensure_ascii=False, indent=2)

    print('\n=== URL ===')
    print(val.get('url') if isinstance(val, dict) else val)

    print('\n=== contenteditable (富文本编辑器候选) ===')
    for c in (val.get('contenteditables') or [])[:20]:
        print(f"  {c['tag']}.{c['cls'][:30]} ({c['w']}x{c['h']}) vis={c['visible']} role={c['role']} placeholder='{c['placeholder']}' text_len={c['innerText_len']}")

    print('\n=== input ===')
    for c in (val.get('inputs') or [])[:15]:
        print(f"  {c['type']}.{c['cls'][:30]} ({c['w']}x{c['h']}) ph='{c['placeholder']}' val_len={c['value_len']}")

    print('\n=== buttons (含 草稿/发布/保存) ===')
    for c in (val.get('buttons') or [])[:30]:
        print(f"  '{c['text']}' @{c['tag']}.{c['cls'][:30]} ({c['w']}x{c['h']}) vis={c['visible']} disabled={c['disabled']}")

    print('\n=== iframes ===')
    for f in val.get('iframes') or []:
        print(f"  {f['src']} ({f['w']}x{f['h']})")

    print(f'\n[DONE] 完整 dump 已写到 {out_path}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
