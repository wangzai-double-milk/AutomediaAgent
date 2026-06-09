---
name: xhs_main
description: "【入口大脑·必读】社交媒体运营 Agent 的冷启动与意图路由。任何与社交媒体（小红书/抖音/内容运营/查信息/养号/发帖/审图/数据/投流）相关的请求，第一步都先读本本，判定意图后再进对应模式。触发关键词：小红书 / 抖音 / 美食博主 / 帮我运营 / 我想知道 / 怎么 / 找信息 / 找爆款 / 笔记 / 审图 / 这张图 / 能不能发 / 准备发 / 帮我看看 / 帮我审 / 帮我 P / P 图 / 写文案 / 标题 / 看数据 / 涨粉 / 养号 / 营销 / 投放 / 投流 / 爆款"
tags:
  - social_media
  - agent_sop
  - bootstrap
  - router
---
# 社交媒体运营 Agent · 冷启动 & 意图路由（Layer 0 入口大脑）

> 这是**整个 Agent 的唯一入口**。收到任何社交媒体相关请求，**先读本本走冷启动**，判定意图后再跳对应模式的 SOP。
> 触发关键词见 frontmatter。**不要**一上来就调原子工具——先想清楚"用户到底要什么"。

## 0. 冷启动三步（每个新会话/新需求都跑一遍）

### Step 0a — 读用户偏好（已自动注入，确认一下）
system prompt 里的 `[User Preferences]` 段就是历史沉淀的偏好（来自 `../../memory/user_profile.json`）。
- 有偏好（如"做美食号/主打减脂餐/不喜欢标题党"）→ 决策时**优先沿用**，别每次都重新问。
- 没有偏好 → 正常往下走，运营过程中再用 `update_user_preference` 沉淀。

### Step 0b — 意图分类（核心）
把用户请求归到下面四类之一。**能自动判定就直接进，别没事就 ask_user**：

| 类别 | 用户想要什么 | 典型说法 | 进入 |
|---|---|---|---|
| **A 研究模式** | 来社交媒体**找信息/做功课**，要一份总结 | "我想知道怎么健身" / "有哪些减脂餐" / "大家都怎么拍 X" / "帮我调研下这个赛道" | [`../research_mode/SKILL.md`](../research_mode/SKILL.md) |
| **B 运营模式** | 让 agent**独自/持续运营账号** | "帮我运营这个号" / "你来打理" / "定个方向持续发" / "看看数据要不要投流" | [`../operate_mode/SKILL.md`](../operate_mode/SKILL.md) |
| **C 快捷任务** | **单点**就办完，不要大流程 | "审这张图" / "P 一下" / "把这条发了" / "就这张图帮我稍微弄下发出去" | 见 §1 快捷路由表 |
| **元/养号** | 刷推荐、点赞收藏评论、看某人主页 | "刷会推荐" / "给这条点赞" / "看看 ta 主页" | [`../xhs_interact/SKILL.md`](../xhs_interact/SKILL.md) |

判定原则：
- **A vs B 的分界**：A 只**消费/总结**信息，产出是文档，不发帖；B 要**生产**内容并对账号负责（定方向、发、看数据、投流）。
- **B vs C 的分界**：C 是"给了明确单点活、办完即止"；B 是"对账号长期负责、要自己定方向/选题/复盘"。
- **快捷任务里的"发个图"**：用户只想发某张图 → 走 C，**轻量**走一遍"稍微搜下同类怎么拍 → 快速 P/审 → 发"，不要套用 B 的完整运营流程。
- **置信度低才问**：实在分不清 A/B/C（比如"帮我搞下小红书"这种模糊话），**才**用 `ask_user` 问一句"你是想找信息、让我运营、还是单独帮你处理某张图/某条笔记？"。能猜准就别问。

### Step 0c — 进入对应模式
读对应模式的 SKILL.md，按它的 Step 执行。模式内任何一步"不知道怎么做" → 触发**搜索贯穿**（见 §2）。

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

## 2. 搜索贯穿（任何模式、任何步骤都可触发）

详见 [`../../soft_skills/search_anywhere/SKILL.md`](../../soft_skills/search_anywhere/SKILL.md)。

**核心纪律：缺信息/缺方法/缺 SOP/缺背景，必须"先搜再做"，不许凭空编。** 三个通道按优先级：
1. **站内检索** `xhs_search_viral` —— 看同行/爆款怎么做（找选题、对照风格、抓正文/评论）。
2. **技能库检索** `memory/skill_search/`（远程技能库）—— "某件事怎么做"的方法论（英文 query）。
3. **网络搜索** 用 `web_scan` + `web_execute_js` 开 Google —— 站内/技能库都没有时的兜底。

典型触发：写不出某赛道的 SOP、不懂某个平台机制、需要外部事实佐证 → 先搜。

## 3. 工具地图（GA 已注册的原子能力）

只有**原子能力**才注册成 tool，工作流编排靠对应模式 SOP 用原子工具拼。

| 类别 | 工具 | 用途 |
|---|---|---|
| 任务 | `xhs_task_init(user_request, image)` | 建调试工作区 |
| 图像 | `xhs_identify_dish` / `xhs_image_review` / `xhs_image_edit` / `xhs_image_generate` / `xhs.extract_text_from_image` | 识图 / 审图 / 修图 / 生图 / OCR |
| 浏览器 | `web_scan` / `web_execute_js` | CDP 接管已登录 Chrome（也是网络搜索的载体） |
| 检索 | `xhs_search_viral(keyword, top_n, fetch_content)` | 站内搜索 + 抓爆款 + 翻页/OCR/评论 |
| 数据 | `xhs_check_login` / `xhs_list_feeds` / `xhs_search_feeds` / `xhs_get_feed_detail` / `xhs_user_profile` | 登录态 / 推荐流 / 搜索 / 笔记详情 / 用户主页（读 __INITIAL_STATE__，零 VLM） |
| 互动 | `xhs_like_feed` / `xhs_favorite_feed` / `xhs_post_comment` / `xhs_reply_comment` | 点赞 / 收藏 / 评论 / 回复 |
| 发布 | `xhs_publish_note(images, title, desc, tags)` | 自动设仅自己可见后发布 |
| 偏好 | `update_user_preference(category, content)` | 沉淀用户偏好（写 `memory/user_profile.json`，会被自动读回） |
| 通用 | `code_run` / `file_read` / `file_patch` / `file_write` / `create_skill` | 跑 Python / 文件操作 / 沉淀新技能 |

## 4. 跨平台架构（当前首发小红书，预留扩展）

本 Agent 设计为**平台无关**：模式 SOP（research / operate）描述的是**通用运营方法论**，不绑定具体平台；
平台相关的原子工具目前是 `xhs_*`（小红书）。未来接抖音/B站时：
- **不改**模式 SOP 的主流程（定方向→选题→创作→发布→数据→投流 是通用的）。
- **只加**该平台的原子工具 + 平台专属 SOP（建议放 `skills/hard_skills/<platform>_*/`）。
- 选题/检索时优先用当前任务所在平台的检索工具。

> 当某能力被多平台复用 ≥ 2 次，再考虑抽象成平台无关的 adapter（现在不过早抽象）。

## 5. 跨流程硬性纪律（违反 = 任务失败）

1. **入口先冷启动**：社交媒体相关请求先读本本判意图，不要跳过直接调工具。
2. **缺信息先搜**：见 §2，凭空编选题/文案/SOP = 失败。
3. 审图/发布等核心工具受 **SOP-GATE** 保护：首次调用前必须先 `file_read` 对应 SKILL.md，否则代码层 BLOCKED。
4. **绝对禁止**在流程中滥用 `ask_user` 把决策推给用户。**例外**：意图实在分不清（§0b）、识菜 confidence < 0.5、不可逆操作（如真正公开发布）。
5. 不许自己 import `volcenginesdkarkruntime` —— 用现成图像工具。
6. 文件操作用绝对路径，避免 cwd 漂移。
7. **发布永远人工把关**：publish 工具只发"仅自己可见"，真正公开由用户决定。

## 6. 长期路线

1. ✅ 图像审核 / 修图 / 生图
2. ✅ 找爆款对照 + 调侃 + 翻页 + OCR + 评论
3. ✅ 文案 + 标题生成
4. ✅ 自动发布（仅自己可见）
5. ✅ 冷启动 + 意图路由（A/B/C）+ 偏好读回 + 搜索贯穿（本次重构）
6. ⏳ 数据回流（创作中心抓赞/藏/评/涨粉）—— operate_mode §5 已定义流程，待原子工具补齐
7. ⏳ 投放建议（薯条/聚光）—— operate_mode §6 已定义判断逻辑，待数据闭环
8. ⏳ 第二平台（抖音等）接入
