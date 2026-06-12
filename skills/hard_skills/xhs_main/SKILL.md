---
name: xhs_main
description: "【小红书方言入口】小红书这个平台的操作总览与快捷路由（第一个已结晶成熟的平台方言）。当 agent_core 判定任务落在『小红书』平台时委派到这里。包含小红书的快捷路由表、站内检索/互动/发布/审图工具地图、SOP-GATE 说明。触发关键词：小红书 / 红书 / 小红薯 / 笔记 / 审图这张图能不能发 / 发小红书 / 小红书数据 / 小红书爆款。注：通用意图分类与冷启动在 agent_core，不在本本。"
tags:
  - social_media
  - xhs
  - platform_dialect
  - router
---
# 小红书方言入口（Platform Dialect: 小红书）

> **本本是"小红书这一门方言"**，是整个通用自主体里**第一个结晶成熟的平台**。通用的冷启动 / 意图分类 / 跨平台决策在 [`../agent_core/SKILL.md`](../agent_core/SKILL.md)（唯一总入口）；当 agent_core 判定任务落在小红书时，委派进入本本。
> 这里只讲**小红书怎么操作**：快捷路由、工具地图、平台纪律。**不要**一上来就调原子工具——先确认意图（见 agent_core）。
> 落到小红书之外的平台（抖音/B站/知乎…）→ 不走本本，走 [`../explore_mode/SKILL.md`](../explore_mode/SKILL.md) 探索。

## 0. 小红书任务的意图速判（细节见 agent_core §0）

agent_core 已完成通用意图分类（A 研究 / B 运营 / C 快捷 / 元养号 / 探索）。落到小红书后按下表进对应 SOP：

| 类别 | 进入 |
|---|---|
| **A 研究**（找信息出文档，不发帖） | [`../research_mode/SKILL.md`](../research_mode/SKILL.md)（小红书是其首选渠道之一） |
| **B 运营**（持续运营这个号） | [`../operate_mode/SKILL.md`](../operate_mode/SKILL.md) |
| **C 快捷**（单点办完） | 见 §1 快捷路由表 |
| **元/养号**（刷推荐/点赞/看主页） | [`../xhs_interact/SKILL.md`](../xhs_interact/SKILL.md) |

判定原则（A只消费总结、B对账号负责、C单点即止、置信度低才 ask_user）统一见 [`../agent_core/SKILL.md`](../agent_core/SKILL.md) §0。本本不重复。

## 1. 快捷路由表（C 类单点任务，直接进，不走冷启动大流程）

| 用户意图 | 进入哪本 SOP |
|---|---|
| 审图 / 这张图能不能发 / 帮我看看 / 一站式从图到能发的笔记 | [`../xhs_review/SKILL.md`](../xhs_review/SKILL.md)（**审图主闭环**） |
| 就这张图帮我发一下 / 稍微弄下发出去 | 先轻量 [`../xhs_search/SKILL.md`](../xhs_search/SKILL.md)（看同类怎么拍）→ [`../xhs_review/SKILL.md`](../xhs_review/SKILL.md) 快速过 → [`../xhs_publish/SKILL.md`](../xhs_publish/SKILL.md) |
| 找爆款 / 检索 / 看看别人怎么拍 / 抓正文/评论 | [`../xhs_search/SKILL.md`](../xhs_search/SKILL.md) |
| 刷推荐 / 看数据 / 点赞 / 收藏 / 评论 / 回复 / 养号 | [`../xhs_interact/SKILL.md`](../xhs_interact/SKILL.md)（稳定 selector + __INITIAL_STATE__，零 VLM） |
| P 图 / 修图 / 改色调 / 加文字 / 画批注示意图 | [`../../soft_skills/xhs_edit/SKILL.md`](../../soft_skills/xhs_edit/SKILL.md) |
| 毒舌 / 评语风格怎么写 | [`../../soft_skills/xhs_roast_style/SKILL.md`](../../soft_skills/xhs_roast_style/SKILL.md) |
| 发笔记（仅自己可见） / 把这条发了 | [`../xhs_publish/SKILL.md`](../xhs_publish/SKILL.md)（DRAFT-ONLY） |

## 2. 渠道经纪 / 搜索贯穿（任何步骤都可触发）

详见 [`../../soft_skills/search_anywhere/SKILL.md`](../../soft_skills/search_anywhere/SKILL.md)（渠道经纪层）。

**核心纪律：缺信息/缺方法/缺 SOP/缺背景，必须"先查再做"，不许凭空编。** 小红书任务里最常用的通道：
1. **站内检索** `xhs_search_viral` —— 看同行/爆款怎么做（找选题、对照风格、抓正文/评论）。
2. **技能库检索** `skill_search`（英文 query）—— "某件事怎么做"的方法论。
3. **网络/其它平台** —— 站内不够时，由渠道经纪层去 Google / 知乎 / B站等补充。

典型触发：写不出某赛道的 SOP、不懂某平台机制、需要外部事实佐证 → 先查。

## 3. 工具地图（GA 已注册的原子能力）

只有**原子能力**才注册成 tool，工作流编排靠对应模式 SOP 用原子工具拼。

| 类别 | 工具 | 用途 |
|---|---|---|
| 任务 | `xhs_task_init(user_request, image)` | 建调试工作区 |
| 图像 | `xhs_identify_dish` / `xhs_image_review` / `xhs_image_edit` / `xhs_image_generate` / `xhs.extract_text_from_image` | 识图 / 审图 / 修图 / 生图 / OCR |
| 浏览器 | `web_scan` / `web_execute_js` | CDP 接管已登录 Chrome（读 __INITIAL_STATE__ / 改地址 / 网络搜索载体） |
| 视觉引擎 | `web_learn_page` / `web_visual_click` / `web_visual_fill` | 看屏幕→VLM认元素→物理点击/输入；专用工具失败或无方言时的通用通路（见 [`../perception_action/SKILL.md`](../perception_action/SKILL.md)） |
| 检索 | `xhs_search_viral(keyword, top_n, fetch_content)` | 站内搜索 + 抓爆款 + 翻页/OCR/评论 |
| 数据 | `xhs_check_login` / `xhs_list_feeds` / `xhs_search_feeds` / `xhs_get_feed_detail` / `xhs_user_profile` | 登录态 / 推荐流 / 搜索 / 笔记详情 / 用户主页（读 __INITIAL_STATE__，零 VLM） |
| 互动 | `xhs_like_feed` / `xhs_favorite_feed` / `xhs_post_comment` / `xhs_reply_comment` | 点赞 / 收藏 / 评论 / 回复 |
| 发布 | `xhs_publish_note(images, title, desc, tags)` | 自动设仅自己可见后发布 |
| 偏好 | `update_user_preference(category, content)` | 沉淀用户偏好（写 `memory/user_profile.json`，会被自动读回） |
| 通用 | `code_run` / `file_read` / `file_patch` / `file_write` / `create_skill` | 跑 Python / 文件操作 / 沉淀新技能 |

## 4. 小红书在通用架构里的位置

小红书是这个通用自主体**第一个结晶成熟的平台方言**（`xhs_*` 工具 = 在小红书探索熟练后沉淀出的 L3 确定性快路径）。
- 通用方法论（research / operate）和通用引擎（explore / perception_action / 渠道经纪）都**不绑定**小红书。
- 接抖音/B站时：**不改**模式主流程，新平台先走 [`../explore_mode/SKILL.md`](../explore_mode/SKILL.md) 探索 + 建世界模型 `memory/world_model/<platform>.md`，成熟后再像 `xhs_*` 一样结晶成该平台方言（`skills/hard_skills/<platform>_*/`）。
- 当某能力被多平台复用 ≥ 2 次，再考虑抽象成平台无关 adapter（现在不过早抽象）。

## 5. 小红书平台纪律（违反 = 任务失败）

1. 受 **SOP-GATE** 保护的核心工具（search_viral / image_* / publish）首次调用前必须先 `file_read` 对应 SKILL.md，否则代码层 BLOCKED。
2. **缺信息先查**：见 §2，凭空编选题/文案/SOP = 失败。
3. **专用工具失败别硬刚**：`xhs_*` 工具失败（改版/token错配/被风控页拦）→ 降级到视觉引擎 [`../perception_action/SKILL.md`](../perception_action/SKILL.md) 像人一样操作，别直接报错放弃。
4. **绝对禁止**在流程中滥用 `ask_user`。**例外**：意图实在分不清、识菜 confidence < 0.5、不可逆操作（真正公开发布/花钱投流）。
5. 不许自己 import `volcenginesdkarkruntime` —— 用现成图像工具。
6. 文件操作用绝对路径，避免 cwd 漂移。
7. **发布永远人工把关**：publish 工具只发"仅自己可见"，真正公开由用户决定。

## 6. 长期路线

1. ✅ 图像审核 / 修图 / 生图
2. ✅ 找爆款对照 + 调侃 + 翻页 + OCR + 评论
3. ✅ 文案 + 标题生成
4. ✅ 自动发布（仅自己可见）
5. ✅ 冷启动 + 意图路由（A/B/C）+ 偏好读回 + 搜索贯穿
6. ✅ 通用自主体重构：agent_core 总入口 + 感知优先(perception_action) + 陌生平台探索(explore_mode) + 渠道经纪层 + 世界模型沉淀（本次）
7. ⏳ 数据回流（创作中心抓赞/藏/评/涨粉）—— operate_mode §5 已定义流程，待原子工具补齐
8. ⏳ 投放建议（薯条/聚光）—— operate_mode §6 已定义判断逻辑，待数据闭环
9. ⏳ 第二平台（抖音等）：先 explore 建世界模型 → 成熟后结晶成方言
