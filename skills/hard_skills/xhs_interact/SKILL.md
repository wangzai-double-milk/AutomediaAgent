---
name: xhs_interact
description: "触发关键词：刷推荐 / 看首页 / 搜笔记 / 看这条笔记 / 看评论 / 看某人主页 / 点赞 / 收藏 / 评论 / 回复 / 涨互动 / 养号"
tags:
  - xiaohongshu
  - agent_sop
---
# 小红书读取与互动

> 触发关键词：刷推荐 / 看首页 / 搜笔记 / 看这条笔记 / 看评论 / 看某人主页 / 点赞 / 收藏 / 评论 / 回复 / 涨互动 / 养号

本能力负责「读数据 + 做互动」，优先使用页面结构化状态和稳定 selector。
代码在 [`tools/xhs_actions.py`](../../../tools/xhs_actions.py)，工具注册在
[`ga.py`](../../../ga.py)。

## 0. 前置（每次必须）

1. `transport=auto/desktop/cdp`。CDP已连接时使用原页面通路；desktop在已登录Chrome
   使用实际无障碍控件，不需开启CDP、修改策略或读取Cookie。
2. **先 `xhs_check_login`** 确认 `logged_in=true`。未登录直接停，让用户去登录，不要继续。
3. desktop写操作必须传入登录工具返回的真实`owner_id`。关注还需当前笔记`feed_id`，
   将真实作者绑定`user_id`。没有绑定不能操作其他账号。
4. desktop代码在`tools/xhs_interact_desktop.py`。点赞/收藏图标状态先观察，
   单次提交前原子落盘，重新打开真实来源URL核对，未知状态停止而非再次点击。

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
- desktop重新进入笔记后读取真实选中图标，`verified:true`才确认成功。
  图标动画可能让AXImage变化；出现不确定结果先只读核对，禁止重按。
  新的主动互动循环使用新的`operation_id`；同一已完成操作若当前状态改变不会伪报仍成功。
  当前CDP旧路径仍需连接后独立验收；不要把desktop通过称为CDP通过。
- 取消操作：`xhs_like_feed(..., unlike=true)` / `xhs_favorite_feed(..., unfavorite=true)`。
- 返回 `selector_hit:false` = 按钮没找到（页面改版/不在详情页）→ 见 §6 兜底。

## 5. 评论 / 回复

- desktop支持`action=prepare/send/clear`，prepare不提交，clear先核对原全文；
  send先绑定owner/note、落盘、单次发送，再重新打开详情查自己的相同正文评论。
  必须点击实际“说点什么...”展开，再正常键盘输入；只赋AXValue不能使发送按钮生效。
  自有私密测试评论单次发送及独立全文/作者核对已通过，不用“作者”徽标作正文。
- desktop回复使用真实`user_id`与完整`target_content`复合定位，要求唯一目标；
  先核对“回复 昵称”和引用原文，再单次发送。重开详情后核对目标原评论与
  同一线程缩进回复容器中的owner/fulltext，不能把全评论列表中的同文当回复成功。
  评论ID仍为null。自有测试顶层评论回复已实际通过，深层回复关系尚未验收。
  `action=prepare/send/clear`区分预填、提交和清除原文，不因作者昵称相同而选择其他评论。

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
- 批量之前先完整核对一个目标。平台额度没有可靠实证，不将外部项目传言视为当前账号额度。
- 拿不到 `__INITIAL_STATE__` 且重试无效，多半是风控弹窗 → 报告用户，不要硬刚。

## 8. 沉淀习惯

- 每个互动任务由工具自动建 `task_dir`，trace 落 `trace.log`（start/done/error 全留痕），便于复盘。
- 读取类结果已精简（只留 id/xsec_token/title/各种 count 等），省 token；要全量字段再单独读 `__INITIAL_STATE__`。
