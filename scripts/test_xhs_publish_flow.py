#!/usr/bin/env python3
"""Smoke-test the Xiaohongshu publish visibility flow.

Usage:
    .venv/bin/python scripts/test_xhs_publish_flow.py

Run this only when Chrome is open on the Xiaohongshu creator publish page and
the CDP bridge is ready.  It verifies the "set visibility to self" step and
prints the button-detection result for manual inspection.
"""
import os
import sys

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT_DIR)


def main():
    print("=" * 60)
    print("测试 xhs_publish_note 新流程（先设仅自己可见，再直接发布）")
    print("=" * 60)

    try:
        from ga import first_init_driver, ensure_xhs_tab, cdp_set_xhs_visibility
        print("\n[1/3] ga.py 模块导入成功（cdp_* 函数就绪）")
    except ImportError as e:
        print(f"\n[ERROR] 模块导入失败: {e}")
        return 1

    try:
        driver_ok = first_init_driver()
        if not driver_ok:
            print("[WARN] CDP bridge 未初始化（ws://localhost:9222 连接可能不可用）")
            print("       请确认：Chrome 已启动、TMWebDriver 扩展已加载、ws:// 连接正常")
    except Exception as e:
        print(f"[WARN] CDP bridge 初始化失败: {e}")

    print("\n[2/3] 检查小红书标签页...")
    try:
        tab_info = ensure_xhs_tab(want_creator=True)
        print(f"  当前 tab: url={tab_info.get('url', '?')[:60]}, ok={tab_info.get('ok')}")
        if not tab_info.get('ok'):
            print(f"  找不到小红书标签页（reason={tab_info.get('reason')}）")
            print("  请在 Chrome 中打开 https://creator.xiaohongshu.com/publish/publish?target=image")
    except Exception as e:
        print(f"  检查 tab 失败: {e}")

    print("\n[3/3] 调用 cdp_set_xhs_visibility('self') 设可见性...")
    print("  （依赖 Chrome 窗口在前台聚焦，后台时合成器可能丢弃 Input.dispatchMouseEvent）")
    try:
        vis = cdp_set_xhs_visibility('self')
        print(f"\n  结果: {vis}")
        if vis.get('ok'):
            print(f"\n  可见性已设为「{vis.get('now', vis.get('text'))}」")
            if vis.get('already'):
                print("  当前已经是「仅自己可见」（无需切换）")
        else:
            print(f"\n  可见性设置失败: {vis.get('reason')}")
            print("  常见原因：")
            print("    - 窗口未聚焦：把 Chrome 拉到前台")
            print("    - 不在发布页：跳到 https://creator.xiaohongshu.com/publish/publish?target=image")
            print("    - 编辑器未出现：需要先上传图片才显示可见性卡片")
    except Exception as e:
        print(f"  调用异常: {e}")

    print("\n" + "=" * 60)
    print("测试完成")
    print("=" * 60)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
