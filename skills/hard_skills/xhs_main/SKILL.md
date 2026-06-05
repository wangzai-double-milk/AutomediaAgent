---
name: xhs_main
description: "触发关键词：小红书 / 美食博主 / 笔记 / 审图 / 这张图 / 这个图片 / 能不能发 / 准备发 / 帮我看看 / 帮我审 / 帮我 P / P 图 / 写文案 / 标题 / 看下数据 / 涨粉 / 营销 / 投放 / 爆款"
tags:
  - xiaohongshu
  - agent_sop
---
# 小红书美食博主 Agent · 总览 / 路由

> 触发关键词：小红书 / 美食博主 / 笔记 / 审图 / 这张图 / 这个图片 / 能不能发 / 准备发 / 帮我看看 / 帮我审 / 帮我 P / P 图 / 写文案 / 标题 / 看下数据 / 涨粉 / 营销 / 投放 / 爆款

## 0. 路由表（按用户意图选 SOP，再读对应那本就够）

| 用户意图 | 进入哪本 SOP |
|---|---|
| 审图 / 这张图能不能发 / 帮我看看 / 一站式从图到能发的笔记 | [`../xhs_review/SKILL.md`](../xhs_review/SKILL.md) （**主闭环**，会引用下面四本） |
| 找爆款 / 检索 / 看看别人怎么拍 / 抓正文/评论 | [`../xhs_search/SKILL.md`](../xhs_search/SKILL.md) |
| 刷推荐 / 搜笔记看数据 / 看评论 / 看某人主页 / 点赞 / 收藏 / 评论 / 回复 / 养号涨互动 | [`../xhs_interact/SKILL.md`](../xhs_interact/SKILL.md)（稳定 selector + __INITIAL_STATE__，零 VLM） |
| P 图 / 修图 / 改色调 / 加文字 / 画批注示意图 / 手绘 plog 风 | [`../xhs_edit/SKILL.md`](../xhs_edit/SKILL.md)（§4 含小红书爆款 prompt 灵感库）|
| 毒舌 / 调侃 / 评语风格怎么写 | [`../xhs_roast_style/SKILL.md`](../xhs_roast_style/SKILL.md) |
| 发笔记（仅自己可见） / 把这条发了 / 上传到创作中心 | [`../xhs_publish/SKILL.md`](../xhs_publish/SKILL.md)（DRAFT-ONLY，PASS 后由 review_sop §Step 5 自动衔接） |
| 看数据 / 涨粉 / 哪条爆了 ⏳ | `xhs_analytics_sop.md`（未实现） |
| 投流 / 薯条 / 聚光 ⏳ | `xhs_promotion_sop.md`（未实现） |

## 1. 这个 Agent 是干什么的

帮一个普通人**运营自己的小红书美食号**，覆盖完整营销工作流：
拍照 → 找爆款对照 → 审图 → 修图 → 写文案 → 发布仅自己可见 → 数据回流 → 投放建议。

**核心哲学**（用户原话）：
> "他应该先去小红书或者抖音搜索相关的食物，大家是怎么拍的，风格是什么，然后帮我 p 图，发现 p 不好之后，就甩给我，毒舌一点说，你看看别人拍的图都是怎么拍的，要想做的好，你得在图的哪里哪里加什么东西，或者把什么东西拍进去。"

## 2. 工具地图（GA 已注册的原子能力）

只有**原子能力**才注册成 tool，工作流编排靠对应的 SOP 用原子工具拼。

| 类别 | 工具 | 用途 | 详细见 |
|---|---|---|---|
| 任务 | `xhs_task_init(user_request, image)` | 建调试工作区，原图自动 → `task_dir/original.*` | review_sop |
| 图像 | `xhs_identify_dish(image)` | 看图认菜 → `{dish_name, search_keywords, confidence}` | review_sop §Step 0b |
| 图像 | `xhs_image_review` | 严格档审图（带 reference_images 多图对比） | review_sop §Step 2 |
| 图像 | `xhs_image_edit` | Seedream 4.5 i2i 修图 / 画批注 | edit_sop |
| 图像 | `xhs_image_generate` | Seedream 4.5 t2i 文生图 | edit_sop |
| 图像 | `xhs.extract_text_from_image(path)` | VLM 单图 OCR（搜爆款时哨兵 + 全量用） | search_sop |
| 浏览器 | `web_scan` / `web_execute_js` | CDP bridge 接管已登录 Chrome | search_sop |
| 检索 | `xhs_search_viral(keyword, top_n, fetch_content)` | 跳搜索 + 抓爆款 + 翻页/OCR/抓评论 | search_sop |
| 数据 | `xhs_check_login` / `xhs_list_feeds` / `xhs_search_feeds` | 登录态 / 首页推荐流 / 关键词搜索（读 __INITIAL_STATE__，零 VLM） | interact_sop |
| 数据 | `xhs_get_feed_detail` / `xhs_user_profile` | 笔记详情+评论 / 用户主页+笔记列表 | interact_sop |
| 互动 | `xhs_like_feed` / `xhs_favorite_feed` / `xhs_post_comment` / `xhs_reply_comment` | 点赞 / 收藏 / 评论 / 回复（稳定 selector + 状态校验） | interact_sop |
| 发布 | `xhs_publish_note(images, title, desc, tags)` | 极简签名，**自动设仅自己可见后发布** | publish_sop |
| 通用 | `code_run` / `file_read` / `file_patch` / `file_write` | 跑 Python / 文件操作 | — |

## 3. 模型矩阵（写在 [`tools/image.py`](../tools/image.py) 顶部）

- 审图 / 识菜 / OCR：`doubao-seed-2-0-pro-260215`（全模态 VLM）
- 修图 / 生图 / 多图融合：`doubao-seedream-4-5-251128`（带 image = i2i，不带 = t2i）
- ⚠️ 不要用 `doubao-seededit-3-0-i2i-250628` —— 默认未在方舟开通会 404
- ⚠️ Seedream 4.5 **不支持** `seed` / `guidance_scale`；`size` 必须用 `1K/2K/4K` 或具体宽高

## 4. 跨流程硬性纪律（违反 = 任务失败）

1. 收到"审图 / 这张图能不能发"等需求 → **必须**按 `../xhs_review/SKILL.md` 顺序调原子工具，不允许 `code_run` 写代码绕开
2. **绝对禁止**在流程中调 `ask_user` 把决策推给用户。**唯一例外**：识菜 confidence < 0.5 时确认菜名
3. 不许自己 import `volcenginesdkarkruntime` —— 用现成工具
4. 文件操作用绝对路径，避免 cwd 漂移导致 `image 不存在`

## 5. 沉淀习惯

- 每个任务都建 `task_dir`（由 `xhs_task_init` 自动建）：原图、爆款图、审图结果、修图、批注图全落到 `tasks/<ts>_<req>/` 下
- 同 keyword 24h 内可复用 `viral_refs/` 缓存（暂未自动复用，后续优化）
- 用 3 次以上的 JS 模板才考虑封装成新 tool

## 6. 长期路线（提醒 agent 不要走偏）

正确顺序：**先内容，后数据**。
1. ✅ 图像审核 / 修图 / 生图（已完成）
2. ✅ 找爆款对照 + 调侃机制 + 翻页 + OCR + 评论（已完成）
3. ✅ 文案 + 标题生成（review_sop §Step 4.5 最终交付清单）
4. ✅ 自动发布（仅自己可见）（publish_sop，DRAFT-ONLY，发布人工把关）
5. ⏳ 数据回流（创作中心抓数据）
6. ⏳ 投放建议（薯条/聚光，金额/方向）

数据闭环是末端，不是开端。除非用户已经有发出去的笔记，否则不要先做数据模块。
