#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""验证 Set-of-Marks 点击通路（枚举编号 → 截图 → VLM 选编号 → 按 ref scrollIntoView + 点击）。

分三段验证：
  [A] 纯枚举：_som_mark() 在当前页能枚举到带编号的可交互元素 + 画出徽标层
  [B] 滚动：_som_scroll('down') 能真正滚动页面
  [C] 端到端：som_click(target_desc) 选中目标并点中（看 verified）

target_desc 通过命令行传入，默认点一个常见元素。

跑法（必须用项目 venv）：
  .venv/bin/python test_som_click.py "页面右上角的发布笔记按钮"
"""
import sys
import time
import json
import ga
from ga import first_init_driver, ensure_xhs_tab
from tools import web_vision as wv


def main():
    target = sys.argv[1] if len(sys.argv) > 1 else "搜索框"
    want_creator = '--creator' in sys.argv

    print('[1] 初始化 driver ...')
    first_init_driver()
    if ga.driver is None or len(ga.driver.get_all_sessions()) == 0:
        print('   ✗ 没有可用浏览器标签页，请确认 CDP bridge 已连接')
        return

    print('[2] 切到小红书 tab ...')
    tab = ensure_xhs_tab(want_creator=want_creator)
    print('   tab_info =', tab)
    if not tab.get('ok'):
        print('   ✗ 无法定位小红书 tab，测试中止')
        return
    time.sleep(1.0)

    print('\n[A] 枚举视口可交互元素 + 画编号徽标 ...')
    mark = wv._som_mark()
    cnt = mark.get('count') if isinstance(mark, dict) else None
    print(f'   枚举到 {cnt} 个元素，page={mark.get("page") if isinstance(mark, dict) else None}')
    if isinstance(mark, dict):
        for e in (mark.get('elements') or [])[:12]:
            print(f'     [{e.get("id")}] {e.get("tag")}{("/"+e.get("role")) if e.get("role") else ""} '
                  f'({e.get("cx")},{e.get("cy")}) text={e.get("text")!r}')
    if not cnt:
        print('   ✗ 没枚举到元素，后续测试可能无意义')
    wv._som_clear()

    print('\n[B] 滚动一屏测试 ...')
    sc = wv._som_scroll('down')
    print('   scroll down =', sc)
    time.sleep(0.3)
    sc2 = wv._som_scroll('up')
    print('   scroll up   =', sc2)
    time.sleep(0.3)

    print(f'\n[C] 端到端 som_click(target_desc={target!r}) ...')
    r = wv.som_click(target, verify=True)
    print('   som_click 返回 =', json.dumps({k: v for k, v in r.items() if k != 'raw'},
                                            ensure_ascii=False))

    print('\n========== 结论 ==========')
    if r.get('ok'):
        print(f'✓ Set-of-Marks 选中并点中目标：som #{r.get("som_id")} @({r.get("x")},{r.get("y")}) '
              f'verified={r.get("verified")} scrolls={r.get("scrolls")}')
    elif r.get('som_id') is not None:
        print(f'△ 选中了 #{r.get("som_id")} 并点了，但页面指纹无变化（可能目标本就不改变 DOM，或选偏）')
        print(f'  reason = {r.get("reason")}')
    else:
        print(f'✗ 未能定位目标：{r.get("reason")}')
    print('==========================')


if __name__ == '__main__':
    main()
