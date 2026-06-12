---
name: operate_mode
description: "【模式B·运营·平台无关】用户让 Agent 独自/持续运营账号。如『帮我运营这个号』『你来打理』『定个方向持续发』『看看数据要不要投流』。流程：定方向→选题→创作→发布→数据回流→分析决策（含投流判断）→复盘。生产内容并对账号负责。由 agent_core 冷启动判定为 B 类后进入。"
tags:
  - agent_core
  - agent_sop
  - operate
  - cross_platform
---
# 模式 B · 运营模式（生产内容 → 对账号负责）

> 由 [`../agent_core/SKILL.md`](../agent_core/SKILL.md) 冷启动判定为 **B 类**后进入。
> 与研究模式（只读、出文档）不同，本模式要**生产内容、发帖、看数据、判断投流**，对账号长期负责。
> **平台无关**：下文以小红书方言工具举例，流程本身（定方向→选题→创作→发布→数据→投流→复盘）通用于任意平台。运营的是哪个平台的账号、用什么工具，由[渠道经纪层](../../soft_skills/search_anywhere/SKILL.md)与平台方言决定；没有方言的平台借道 [`../explore_mode/SKILL.md`](../explore_mode/SKILL.md) 操作。
> 任何一步"不知道怎么做" → 触发[渠道经纪层](../../soft_skills/search_anywhere/SKILL.md)。**禁止凭空编方向/选题/文案/SOP。**

## 何时进入
用户想"让你来打理/持续运营这个号"，例如：
- "帮我运营这个小红书号 / 你来定方向持续发"
- "这周更几条 / 给我做内容规划"
- "看看最近数据要不要投流"

## 总流程（7 步闭环）
定方向 → 选题 → 创作 → 发布 → 数据回流 → 分析&决策（投流） → 复盘沉淀 → （回到选题，循环）

### Step 1 — 定方向（账号定位）
1. **先读偏好**：system prompt 的 `[User Preferences]` 有没有既定方向（来自 `memory/user_profile.json`）。
   - 有 → 直接沿用，跳到 Step 2。
   - 没有 → 往下分析。
2. **分析账号现状**：`xhs_user_profile(user_id, xsec_token)` 看历史笔记题材/数据，判断已有调性。
3. **赛道调研**：用 `xhs_search_viral` / `xhs_search_feeds` 搜候选赛道，看什么内容火、竞争度、有没有缺口。不懂赛道打法 → 搜索贯穿（技能库/网络）。
4. **提议方向**：综合成 1~2 个候选方向（含定位、目标人群、内容形式、差异点），用 `ask_user` 让用户**拍板一次**（方向是长期不可逆决策，值得确认）。
5. 定下后 `update_user_preference(category="direction", content="...")` 写回偏好。

### Step 2 — 选题（找缺口）
- 用 `xhs_search_viral(keyword, fetch_content=5)` 搜本方向爆款，分析：什么选题火、标题套路、封面风格、评论区还在问什么（= 内容缺口）。
- 选 1~3 个**有热度又有差异**的选题，避免无脑抄爆款。
- 选题逻辑说不清 → 搜索贯穿补方法论。

### Step 3 — 创作（复用现有技能）
- **图**：用 `xhs_image_review` 审图、`xhs_image_edit` P 图、`xhs_image_generate` 生图（详见 [`../xhs_review/SKILL.md`](../xhs_review/SKILL.md)、[`../../soft_skills/xhs_edit/SKILL.md`](../../soft_skills/xhs_edit/SKILL.md)）。
- **文案/标题**：参考爆款套路写，风格可参 [`../../soft_skills/xhs_roast_style/SKILL.md`](../../soft_skills/xhs_roast_style/SKILL.md)。
- **tags**：从 `viral_meta.json` 抽取同赛道高频标签，别乱编。

### Step 4 — 发布（仍人工把关）
- 用 `xhs_publish_note(images, title, desc, tags)` 发布（详见 [`../xhs_publish/SKILL.md`](../xhs_publish/SKILL.md)）。
- **硬纪律：publish 只发"仅自己可见"，真正公开由用户决定。** 运营模式也不例外。
- 记下本条笔记的 feed_id（后续数据回流要用）。

### Step 5 — 数据回流（发布后追踪）
> ⏳ 部分原子工具待补齐；当前用现有读取工具尽量覆盖。
- 用 `xhs_get_feed_detail(feed_id, xsec_token)` 读这条笔记的赞/藏/评/转发等 `interactInfo`。
- 用 `xhs_user_profile` 看账号涨粉趋势。
- 评论区回复：用 `xhs_post_comment` / `xhs_reply_comment` 维护互动（合规、像真人、别刷屏，见 [`../xhs_interact/SKILL.md`](../xhs_interact/SKILL.md)）。
- 把每条数据落盘到 `task_dir`（或 `memory/`）便于跨周期对比。

### Step 6 — 分析 & 决策（含投流判断）
- **判断表现**：和同期/同赛道均值比，这条是高于/持平/低于预期？哪类选题数据好？
- **投流判断（薯条/聚光）**：
  - 自然流量已经不错、互动率高、还在上升 → **值得投**，放大爆款。
  - 数据平平/互动差 → **先别投**，是选题/封面/文案的问题，回 Step 2 改，别用钱填内容的坑。
  - 投多少：从小额测试起，看 ROI（涨粉/互动成本）再决定加不加码。
  - 投流机制不熟 → 搜索贯穿查官方规则 + 同行经验。
- **结论给建议，不替用户花钱**：是否真投、投多少，最终 `ask_user` 让用户确认（涉及花钱 = 不可逆）。

### Step 7 — 复盘 & 沉淀
- 总结这轮：什么选题/封面/标题有效，什么无效。
- `update_user_preference` 写回有效打法、用户口味（如"标题别太标题党""偏好治愈系封面"）。
- 可重复的打法用 `create_skill` 沉淀成新 SOP，下次直接复用。
- 回到 Step 2 进入下一轮。

## 搜索贯穿（本模式的生命线）
运营模式**每一步都可能卡在"不知道怎么做"**，这时必须搜，不许编。三通道（详见 [`../../soft_skills/search_anywhere/SKILL.md`](../../soft_skills/search_anywhere/SKILL.md)）：
1. **站内** `xhs_search_viral` —— 找选题、对照爆款打法、看竞品。
2. **技能库** `memory/skill_search/` —— "运营/投流/起号 SOP 怎么写"的方法论（英文 query）。
3. **网络** Google —— 平台规则、行业事实、外部权威信息兜底。

## 纪律
1. **对账号负责，不是刷帖**：要主动定方向、选题、看数据、做决策，不能只被动浏览。
2. **缺信息先搜**：方向/选题/文案/投流策略，缺了就搜，禁止凭空编。
3. **不可逆操作必确认**：定方向、真正公开发布、花钱投流 —— 这三类 `ask_user`；其余自主推进，别滥用 ask_user。
4. **核心工具受 SOP-GATE**：审图/发布等首次调用前先 `file_read` 对应 SKILL.md。
5. **发布永远人工把关**：publish 只发仅自己可见。
6. 数据决策要交叉验证，别只看摘要数字（见 RULES）。
