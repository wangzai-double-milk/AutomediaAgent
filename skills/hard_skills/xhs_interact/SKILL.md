---
name: xhs_interact
description: "触发关键词：刷推荐 / 看首页 / 搜笔记 / 看这条笔记 / 看评论 / 看某人主页 / 点赞 / 收藏 / 评论 / 回复 / 涨互动 / 养号"
tags:
  - xiaohongshu
  - agent_sop
---
# 小红书 数据读取 / 互动 SOP（稳定 selector + __INITIAL_STATE__，零 VLM）

> 触发关键词：刷推荐 / 看首页 / 搜笔记 / 看这条笔记 / 看评论 / 看某人主页 / 点赞 / 收藏 / 评论 / 回复 / 涨互动 / 养号

本本管「读数据 + 做互动」这条线。移植自参考项目 xiaohongshu-mcp，核心是**不靠 VLM、靠前端全局 JSON + 稳定 CSS selector**，又快又稳。
代码在 [`tools/xhs_actions.py`](../tools/xhs_actions.py)，工具注册在 [`ga.py`](../ga.py)。

## 0. 前置（每次必须）

1. **CDP bridge 已接管已登录 Chrome**（和审图/发布共用同一条 bridge）。
2. **先 `xhs_check_login`** 确认 `logged_in=true`。未登录直接停，让用户去登录，不要继续。
3. 所有读取都靠 `window.__INITIAL_STATE__`：工具内部已自动导航 + 轮询等待它出现；若返回 `reason: no_initial_state`，多半是没在小红书页 / 页面没加载完 / 被风控，重试一次仍失败就停下报告。

## 1. 工具地图（本线 9 个原子能力）

| 工具 | 用途 | 必填参数 | 读/写 |
|---|---|---|---|
| `xhs_check_login` | 登录状态 | — | 读 |
| `xhs_list_feeds` | 首页推荐流 | — | 读 |
| `xhs_search_feeds` | 关键词搜索（可筛选） | keyword | 读 |
| `xhs_get_feed_detail` | 笔记详情 + 评论 | feed_id, xsec_token | 读 |
| `xhs_user_profile` | 用户主页 + 笔记列表 | user_id, xsec_token | 读 |
| `xhs_like_feed` | 点赞 / 取消点赞 | feed_id, xsec_token | 写 |
| `xhs_favorite_feed` | 收藏 / 取消收藏 | feed_id, xsec_token | 写 |
| `xhs_post_comment` | 发一级评论 | feed_id, xsec_token, content | 写 |
| `xhs_reply_comment` | 回复指定评论 | feed_id, xsec_token, content + (comment_id 或 user_id) | 写 |

## 2. feed_id / xsec_token 从哪来（最关键纪律）

**几乎所有工具都要 `feed_id` + `xsec_token`，它们必须从上游读取结果里拿，不许自己编造、不许复用旧 token。**

正确链路：
- 想互动首页某条 → 先 `xhs_list_feeds` → 从返回 `feeds[i]` 取 `id` 和 `xsec_token`
- 想互动搜索结果某条 → 先 `xhs_search_feeds(keyword)` → 从返回取 `id` / `xsec_token`
- `xsec_token` 是带时效的访问令牌，**同一条笔记不同来源的 token 不通用**；拿到后尽快用。
- 用户主页需要的 `user_id` / `xsec_token` 从笔记结果的 `author_id` 或搜索结果里取。

## 3. 搜索筛选（filters）

`xhs_search_feeds` 可带 `filters`（对象，键全可选）：

| 键 | 可选值 |
|---|---|
| `sort_by` | 综合 / 最新 / 最多点赞 / 最多评论 / 最多收藏 |
| `note_type` | 不限 / 视频 / 图文 |
| `publish_time` | 不限 / 一天内 / 一周内 / 半年内 |
| `search_scope` | 不限 / 已看过 / 未看过 / 已关注 |
| `location` | 不限 / 同城 / 附近 |

例：找近期美食爆款 → `filters={"sort_by":"最多点赞","note_type":"图文","publish_time":"一周内"}`。
返回里 `filters_applied` 是实际生效的筛选；为空说明面板没点上，可放宽或重试。

## 4. 点赞 / 收藏（幂等 + 状态校验）

- 工具**先读** `interactInfo` 的 `liked` / `collected`，**已是目标态就跳过**（返回 `skipped:true`），不会重复点。
- 点击后等 2.5s 再读状态校验；没成功会**自动再点一次兜底**。返回 `verified:true` 才算确认成功。
- 取消操作：`xhs_like_feed(..., unlike=true)` / `xhs_favorite_feed(..., unfavorite=true)`。
- 返回 `selector_hit:false` = 按钮没找到（页面改版/不在详情页）→ 见 §6 兜底。

## 5. 评论 / 回复

- `xhs_post_comment`：点输入框 → 注入文本（contenteditable 用 execCommand）→ 点提交。
- `xhs_reply_comment`：滚动评论区定位目标评论（优先 `comment_id`→`#comment-{id}`，否则按 `user_id`→`[data-user-id]`）→ 点该评论的回复按钮 → 输入 → 提交。`comment_id` / `user_id` 至少给一个，从 `xhs_get_feed_detail` 的 `comments[i].id` / `author_id` 取。
- **内容合规**：评论要像真人、和笔记相关；不刷屏、不发广告/外链/联系方式，避免风控。

## 6. selector 失效兜底（selector 优先 + VLM 兜底）

> 实测校正（2026-06 真实页面）：笔记主体互动栏是 `.engage-bar-style .left`，点赞点 `.like-wrapper`、收藏点 `.collect-wrapper`（点可 click 的外层 span，不是内部 svg）；评论输入框/提交必须锚定 `.engage-bar`（否则 `.input-box` 会误命中页面顶部搜索框）。导航后 DOM 渐进渲染，工具已用 `_wait_selector` 轮询等待目标出现再点击。

本线默认走稳定 selector。当返回 `selector_hit:false`（页面改版导致按钮/输入框找不到）时：
1. 先确认是不是 §0 的可访问性问题（笔记被删/私密/违规，返回 `blocked:true` 时**不要兜底**，直接放弃这条）。
2. 确实是 selector 失效 → 回退 VLM：用 `web_visual_click` / `web_visual_fill`，target_desc 写清「位置+外观+文字」。例：点赞用 `web_visual_click(target_desc="笔记详情页底部的点赞爱心图标")`。
3. VLM 命中后会自动缓存 selector；若发现 selector 长期失效，把新 selector 同步回 `tools/xhs_actions.py` 的 `SELECTORS`。

## 7. 可见性 / 风控限制

- 笔记可能「已删除/私密/违规/仅作者可见」→ 工具返回 `blocked:true` + `reason`，直接跳过，别重试别兜底。
- 互动节奏别太猛（参考项目实测日发 50 篇没事，但点赞/评论刷太快会触发验证）；批量操作时工具间留间隔。
- 拿不到 `__INITIAL_STATE__` 且重试无效，多半是风控弹窗 → 报告用户，不要硬刚。

## 8. 沉淀习惯

- 每个互动任务由工具自动建 `task_dir`，trace 落 `trace.log`（start/done/error 全留痕），便于复盘。
- 读取类结果已精简（只留 id/xsec_token/title/各种 count 等），省 token；要全量字段再单独读 `__INITIAL_STATE__`。
