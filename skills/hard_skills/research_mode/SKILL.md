---
name: research_mode
description: "【模式A·研究】用户来社交媒体找信息/做功课，要一份总结。如『我想知道怎么健身』『有哪些减脂餐』『大家都怎么拍X』『帮我调研下这个赛道』。目的：自己浏览大量帖子→交叉验证→产出结构化文档给用户，不发帖。由 xhs_main 冷启动判定为 A 类后进入。"
tags:
  - social_media
  - agent_sop
  - research
---
# 模式 A · 研究模式（消费信息 → 出文档）

> 由 [`../xhs_main/SKILL.md`](../xhs_main/SKILL.md) 冷启动判定为 **A 类**后进入。
> 本模式只**消费/总结**信息，**产出是一份文档**，**不发帖、不动账号**。
> 任何一步"不知道怎么做/信息不够" → 触发[`../../soft_skills/search_anywhere/SKILL.md`](../../soft_skills/search_anywhere/SKILL.md)。

## 何时进入
用户想"找信息/做功课/要个总结"，例如：
- "我想知道怎么健身 / 新手减脂怎么吃"
- "大家都怎么拍 ootd / 探店笔记怎么写"
- "帮我调研下『露营』这个赛道现在什么内容火"

## 流程（6 步）

### Step 1 — 明确研究主题 + 范围
- 从用户原话提炼**主题**和**关注点**（如主题=健身，关注点=新手/在家/无器械）。
- **能从原话推断就别问**。只有主题实在太模糊（如"帮我查点东西"）才用 `ask_user` 问一句。
- 先建工作区：`xhs_task_init(user_request=...)` 拿到 `task_dir`。
- 把主题拆成 1~3 个**检索关键词**（站内搜索一次只吃一个 keyword）。

### Step 2 — 站内检索抓 Top-N 高赞笔记
- 用 `xhs_search_viral(keyword, top_n=10, fetch_content=5)` 抓爆款封面 + 前 N 篇正文。
  - `top_n` 控制看多少封面，`fetch_content` 控制真正点进去抓正文+多图+评论的篇数。
  - 多个关键词就多调几次。需要更精准的筛选（最新/最多点赞/图文/一周内）可改用 `xhs_search_feeds(keyword, filters=...)` 再 `xhs_get_feed_detail` 逐条取详情。
- 结果已落盘到 `task_dir/viral_refs/`（封面 + 多图 + `viral_meta.json`）。

### Step 3 — 逐篇深读（正文 + 图内文字 + 高赞评论）
- `viral_meta.json` 里每篇的 `content` 含 `desc`（正文）、`image_texts`（**图内 OCR，攻略经常藏这**）、`comments`（评论区，干货/避坑也常在这）。
- 攻略型笔记的关键信息**大概率在图里不在正文** → 一定要读 `image_texts`。
- 单张图缺 OCR 时用 `xhs.extract_text_from_image(path)` 补抓。
- 评论区高赞回复要重点看（真实反馈、补充、踩坑）。

### Step 4 — 去重、归纳、交叉验证
- 把多篇里**反复出现**的观点视为高可信共识；**只有 1 篇提**的存疑，标注"少数说法"。
- 互相矛盾的点 → 不要直接采信。**触发搜索贯穿**：用技能库/网络搜索补充权威信息再下结论（见 §搜索贯穿）。
- 严禁凭空编：文档里每条结论都应能追溯到某篇笔记/某次搜索。

### Step 5 — 产出结构化文档
- 用 `file_write` 写到 `task_dir/research_report.md`，结构建议：
  1. **结论速览**（3~5 条要点，用户最忙时只看这段）
  2. **分主题展开**（每个子主题：共识做法 + 注意事项 + 出处笔记标题/链接）
  3. **避坑 / 少数派说法**（标注可信度）
  4. **信息来源清单**（笔记标题 + note_url + 点赞数，体现热度）
- 文末附"本次共看了 N 篇笔记 + M 次外部搜索"，让用户知道覆盖面。
- 把报告路径回报给用户。

### Step 6 — 沉淀用户偏好
- 用 `update_user_preference(category="interest", content="关注X主题/Y方向")` 记下用户这次关心什么。
- 下次该用户再来，`xhs_main` 冷启动会自动读回，能更快对上需求。

## 搜索贯穿（本模式高频）
研究模式经常需要补外部信息，按优先级走三通道（详见 [`../../soft_skills/search_anywhere/SKILL.md`](../../soft_skills/search_anywhere/SKILL.md)）：
1. **站内** `xhs_search_viral` —— 看同行/真实用户怎么说（本模式主力）。
2. **技能库** `memory/skill_search/` —— "某件事怎么做"的方法论（英文 query）。
3. **网络** `web_scan`+`web_execute_js` 开 Google —— 站内说法矛盾/需要权威佐证时兜底。

## 纪律
1. **不发帖、不互动**：研究模式只读不写账号；要点赞/评论/发布说明判错了模式，回 `xhs_main` 重新分类。
2. **结论必有出处**：每条都能追到笔记或搜索，禁止编。
3. **够用即止**：覆盖到能回答用户问题就出文档，不要无限刷帖。
4. 文件用绝对路径（`task_dir` 下）。
