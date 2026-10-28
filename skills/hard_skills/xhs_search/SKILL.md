---
name: xhs_search
description: "小红书搜索与深读能力：推荐流、关键词检索、详情、评论、作者和爆款样本。"
tags: [capability, search, xiaohongshu]
---
# Xiaohongshu Search

负责读取平台内容，不负责互动、改图或发布。实现位于
[`tools/xhs_actions.py`](../../../tools/xhs_actions.py) 与
[`ga.py`](../../../ga.py)。
桌面通路位于 [`tools/xhs_read_desktop.py`](../../../tools/xhs_read_desktop.py)；
`transport=auto` 使用实际已连接CDP或当前已登录Chrome的macOS无障碍控件。

## 工具选择

| 目标 | 工具 |
|---|---|
| 检查登录 | `xhs_check_login` |
| 读取首页推荐 | `xhs_list_feeds` |
| 关键词与筛选搜索 | `xhs_search_feeds` |
| 读取一条笔记和评论 | `xhs_get_feed_detail` |
| 读取作者主页 | `xhs_user_profile` |
| 建立爆款参考集并落盘 | `xhs_search_viral` |

## 标准流程

1. 先检查登录；失败时停止并让用户处理登录/验证码。
2. 搜索返回的 `id`、`xsec_token`、原始URL必须保留。desktop通过当前可见链接打开详情，
   不自行填写缺失来源；CDP则原样传递token/source。用户主页desktop跟随真实作者链接。
3. 每轮先取 3–5 个候选，再逐条深读正文、评论和媒体，不把标题列表当研究。
4. 图片或视频结论必须调用 `analyze_media` 观察原始素材。
5. 研究样本和来源写入当前 `task_dir`，由 workflow 负责去重和收敛。
6. desktop详情只提取正文、话题、真实作者与已加载评论，图片/视频未提取不能当已理解媒体。
   `comment_count`为网页显示总数，`loaded_comment_count`为已加载部分；未知评论ID保持null。
7. 搜索筛选由实际分组和选中色验证；`filters_applied`只列已验证选项。
   用户主页指标按实际相邻标签匹配，未知值与实际0不同。

## 爆款参考集

`xhs_search_viral(keyword, top_n=10, fetch_content=3)` 会：

- 按可解析的互动量排序封面；
- 下载参考封面和已深读笔记的多图素材；
- 对含教学文字的图片执行 OCR；
- 将结果写入 `task_dir/viral_refs/viral_meta.json`。

该结果是当前页面和登录状态下的样本，不代表平台全量排名。

## 失败纪律

- `no_initial_state`、访问频繁或内容不可见：等待一次后重试；仍失败就记录限制并停止。
- selector 失效：先用注册的 `web_scan` / `web_execute_js` 检查真实 DOM，再修改实现。
- 下载 403：检查来源 URL 与 Referer；不要把登录态或 token 写进日志。
- 不在 SOP 中复制 selector 和等待秒数；页面细节只在代码中维护。
