---
name: search_anywhere
description: "【搜索贯穿·软技能】任何模式、任何步骤，缺信息/缺方法/缺SOP/缺背景时必须『先搜再做』，不许凭空编。三通道按优先级：站内 xhs_search_viral（看同行怎么做）→ 技能库 skill_search（方法论，英文query）→ 网络 Google（外部事实兜底）。被 research_mode / operate_mode / xhs_main 引用。"
tags:
  - social_media
  - soft_skill
  - search
---
# 搜索贯穿（Search Anywhere）

> 这不是某一步的工具，而是**贯穿所有模式的纪律**。被 [`../../hard_skills/xhs_main/SKILL.md`](../../hard_skills/xhs_main/SKILL.md)、research_mode、operate_mode 反复引用。

## 核心纪律
**缺信息 / 缺方法 / 缺 SOP / 缺背景事实 → 必须"先搜再做"，禁止凭空编。**
典型触发：写不出某赛道的选题/SOP、不懂某平台机制、数据/事实需要佐证、用户问的领域你不熟。
→ 不要硬编一个看似合理的答案，**先搜，再基于搜到的内容产出。**

## 三通道（按优先级依次尝试）

### 通道 1：站内检索 `xhs_search_viral` —— "同行/真实用户怎么做"
最高频。看赛道里什么火、爆款套路、真实用户在评论区说什么。
```
xhs_search_viral(keyword, top_n=10, fetch_content=5)
```
- 适合：找选题、对照爆款风格、抓正文/图内OCR/评论里的真实经验。
- 需要精筛（最新/最多点赞/图文/一周内）→ 改用 `xhs_search_feeds(keyword, filters=...)` + `xhs_get_feed_detail`。
- 详见 [`../../hard_skills/xhs_search/SKILL.md`](../../hard_skills/xhs_search/SKILL.md)。

### 通道 2：技能库检索 `skill_search` —— "某件事的方法论 / SOP 怎么写"
从 105K+ 技能卡里语义检索做法。**写不出 SOP、不懂某个通用做法时用这个。**
```python
import sys; sys.path.append('../memory/skill_search')
from skill_search import search
results = search("how to grow a lifestyle account on social media")  # ⚠️ 必须英文 query，中文效果极差
for r in results:
    s = r.skill
    print(f"[{r.final_score:.2f}] {s.name} — {s.one_line_summary}  (form={s.form})")
```
- 用 `code_run` 执行；query 翻成英文再搜。
- 适合：运营/起号/投流/数据分析等**通用方法论**，站内搜不到"怎么做"的元知识。
- 详见 [`../../../memory/skill_search/SKILL.md`](../../../memory/skill_search/SKILL.md)。

### 通道 3：网络搜索（Google）—— 外部事实 / 平台规则兜底
站内和技能库都不够时，开浏览器查权威信息。
- 用 `web_scan` + `web_execute_js` 打开 **Google**（RULES 禁用 duckduckgo）。
- 适合：平台官方规则（如薯条/聚光投放机制）、行业事实、需要外部权威佐证、站内说法互相矛盾时定夺。
- 注意 RULES 第7条：scan 为空/不完整先等再 rescan，别凭首屏下结论。

## 选哪个通道？
| 你缺的是 | 走哪个 |
|---|---|
| "别人这个选题/这类内容怎么做的" | 通道 1 站内 |
| "运营/投流/某做法的方法论、SOP" | 通道 2 技能库 |
| "平台规则、行业事实、权威数据" | 通道 3 网络 |
| 站内说法互相矛盾要定夺 | 通道 1 之后用通道 3 佐证 |

## 跨平台
通道 2、3 与平台无关，永远可用。通道 1 当前是小红书 `xhs_*`；接入新平台后，**优先用当前任务所在平台的站内检索工具**，方法论/网络通道照旧复用。

## 纪律小结
1. 缺了就搜，不许编。
2. 通道按 1→2→3 优先级，够用即止。
3. 技能库 query 必须英文。
4. 网络搜索只用 Google。
5. 搜到的关键结论要在产出里**标出处**（哪篇笔记/哪次搜索）。
