#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""验证 CDP 点击修复（buttons bitmask + 返回值检查）是否生效。

原理：不依赖具体网站按钮，而是在当前小红书 tab 注入一个全局 click 监听器，
然后用 cdp_mouse_click 点击页面中心，再读回 JS 侧是否真的收到了 click 事件
以及事件的 buttons 值。若收到 mousedown 且 buttons==1，说明修复生效。

跑法（必须用项目 venv，system python 缺 bottle 会连不上 bridge）：
  .venv/bin/python test_click_fix.py
"""
import time, json
import ga
from ga import (first_init_driver, ensure_xhs_tab, web_execute_js,
                cdp_mouse_click, cdp_get_viewport)


def js(script):
    res = web_execute_js(script, no_monitor=True)
    if isinstance(res, dict):
        v = res.get('js_return')
        if v is None:
            v = res.get('data') or res.get('result')
        if isinstance(v, str):
            try:
                return json.loads(v)
            except Exception:
                return v
        return v
    return res


def main():
    print('[1] 初始化 driver ...')
    first_init_driver()
    if ga.driver is None or len(ga.driver.get_all_sessions()) == 0:
        print('   ✗ 没有可用浏览器标签页，请确认 CDP bridge 已连接')
        return

    print('[2] 切到小红书 tab ...')
    tab = ensure_xhs_tab(want_creator=False)
    print('   tab_info =', tab)
    if not tab.get('ok'):
        print('   ✗ 无法定位小红书 tab，测试中止')
        return
    time.sleep(1.0)

    print('[3] 注入全局点击监听器 ...')
    inject = """
    (function(){
      window.__clickProbe = {down:null, click:null, count:0};
      window.__probeDown = function(e){
        window.__clickProbe.down = {x:e.clientX, y:e.clientY, buttons:e.buttons, button:e.button};
      };
      window.__probeClick = function(e){
        window.__clickProbe.click = {x:e.clientX, y:e.clientY,
          tag:(e.target&&e.target.tagName)||'', cls:(e.target&&e.target.className)||''};
        window.__clickProbe.count++;
      };
      document.addEventListener('mousedown', window.__probeDown, true);
      document.addEventListener('click', window.__probeClick, true);
      return 'installed';
    })();
    """
    print('   inject =', js(inject))

    vp = cdp_get_viewport() or {}
    print('   viewport =', vp)
    cx = int((vp.get('w') or 800) / 2)
    cy = int((vp.get('h') or 600) / 2)
    print(f'[4] CDP 点击页面中心 ({cx},{cy}) ...')
    ret = cdp_mouse_click(cx, cy)
    print('   cdp_mouse_click 返回 =', ret)
    if isinstance(ret, dict) and ret.get('error'):
        print('   ✗ CDP 命令本身报错（修复后的返回值检查已捕获）')

    time.sleep(0.5)
    probe = js("JSON.stringify(window.__clickProbe||{});")
    print('[5] JS 侧捕获结果 =', probe)

    ok = isinstance(probe, dict) and probe.get('count', 0) >= 1
    down_buttons = (probe.get('down') or {}).get('buttons') if isinstance(probe, dict) else None
    print('\n========== 结论 ==========')
    if ok:
        print(f'✓ 页面真实收到了 click 事件（count={probe.get("count")}）→ 点击生效')
        print(f'  mousedown.buttons = {down_buttons}（修复后应为 1，旧代码为 0）')
    else:
        print('✗ 页面没有收到 click 事件 → 点击仍未生效，需进一步排查')
    print('==========================')

    # 清理监听器
    js("""(function(){
      try{document.removeEventListener('mousedown',window.__probeDown,true);
      document.removeEventListener('click',window.__probeClick,true);}catch(e){}
      return 'cleaned';})();""")


if __name__ == '__main__':
    main()
