# 启动指令

每次想跑这个小红书运营 agent，按下面来即可。

## 终端 UI（推荐）

```bash
请用请用帮我打开小红书创作中心（creator.xiaohongshu.com），
切到那个标签页，然后用 web_execute_js 在页面里 fetch 我的笔记列表接口，
把最近 10 篇笔记的标题、阅读量、点赞、收藏、评论 列成表格给我。

如果你不确定接口路径，可以先用 web_scan 看页面，
或者打开 F12 网络面板那种思路：让我去"数据中心 → 笔记数据"页面，
你再观察当前页面 fetch 出去的请求，找到正确接口再调。
```

## 桌面端（Streamlit / Webview）

```bash
cd /Users/bytedance/Desktop/各种agent的使用尝试/xiaohongshu-agent
source .venv/bin/activate
python launch.pyw
```

## 备注

- 模型后端：豆包 `doubao-seed-2-0-pro-260215`（已在 `mykey.py` 里配好）。
- 虚拟环境：`.venv`，Python 3.12。如果命令找不到 `python`，先确认已 `source .venv/bin/activate`。
- 退出 TUI：`Ctrl+C`。
