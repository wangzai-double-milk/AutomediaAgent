---
name: search_anywhere
description: "【渠道经纪层·贯穿】把手里每个登录态平台都当成一个『可查询的活知识源 + 可分发的喉舌』。任何模式、任何步骤缺信息/缺方法/缺SOP/缺背景，必须先经经纪层去合适的渠道『读』回来再做，不许凭空编。核心动作：判断该问哪几个渠道(可并行)→各渠道按方言或视觉探索抓取→交叉验证融合成答案→标出处。被 agent_core / research_mode / operate_mode / explore_mode 反复引用。"
tags:
  - agent_core
  - soft_skill
  - search
  - channel_broker
  - cross_platform
---
# 渠道经纪层（Channel Broker）—— 账号即感官与渠道

> 这不是某一步的工具，而是**贯穿所有模式的能力 + 纪律**。被 [`../../hard_skills/agent_core/SKILL.md`](../../hard_skills/agent_core/SKILL.md)、research_mode、operate_mode、explore_mode 反复引用。
> 核心理念：**手里每个登录态平台都是一个"活知识源（可读）+ 喉舌（可写）"。** 人做调研不会只搜一个地方——生活种草问小红书、深度原理翻知乎、实时热点看微博/X、教程看 B站、最后 Google 兜底，然后**自己融合**。Agent 也该如此。
> 因此「调研」和「运营」在底层是**同一套引擎**：调研 = 用渠道**读**；运营 = 用渠道**写** + 读反馈。区别只是目标。

## 核心纪律
**缺信息 / 缺方法 / 缺 SOP / 缺背景事实 → 必须"先查再做"，禁止凭空编。**
典型触发：写不出某赛道选题/SOP、不懂某平台机制、数据/事实要佐证、用户问的领域不熟、在陌生平台不知道怎么操作。
→ 不要硬编一个看似合理的答案，**先经经纪层查，再基于查到的内容产出。**

## 经纪流程（像人一样多源调研）

```
1. 拆需求    我到底缺什么？（一个选题方向？一套方法论？一条事实？一个平台的操作方式？）
2. 选渠道    根据"渠道画像"判断该问哪一个/哪几个（可并行问多个）
3. 各自抓取  每个渠道用它自己的方式：
              - 有专用方言工具(如小红书 xhs_search_viral) → 走工具，快
              - 没有专用工具的平台 → 借道 explore_mode/perception_action 视觉探索抓取
              - 技能库/网络 → 见下方通道
4. 交叉融合  多源结果对比去重、互相印证、矛盾处再追一刀；得出结论
5. 标出处    关键结论注明来自哪个渠道/哪篇内容/哪次搜索（可追溯）
```

## 渠道画像（该问谁？按需选，可并行）

> 维护在 `memory/world_model/` 各平台文件 + 下表默认认知。哪些平台**已登录可用**以环境实际为准。

| 渠道 | 擅长承载 | 接入方式 | 状态 |
|---|---|---|---|
| 小红书 | 生活/种草/避坑/本地/颜值向 | 方言工具 `xhs_search_viral` / `xhs_search_feeds`（已结晶，快） | 首发已熟 |
| 知乎 | 深度/原理/长文经验/理性讨论 | 视觉探索（暂无方言，走 explore） | 按需 |
| B站 | 教程/测评/长视频/二次元 | 视觉探索 | 按需 |
| 抖音/快手 | 趋势/爆款节奏/完播信号/下沉 | 视觉探索 | 按需 |
| 微博/X | 实时/突发/舆论/热点 | 视觉探索 | 按需 |
| 技能库 | "某件事怎么做"的方法论元知识（105K+技能卡） | `skill_search`（英文 query） | always |
| Google | 通用事实/平台官方规则/权威佐证 | `web_scan`+`web_execute_js` 开 Google | always |

选渠道速查：
| 你缺的是 | 优先问 |
|---|---|
| "别人这个选题/这类内容怎么做的" | 内容平台站内（小红书/抖音/B站，按主题选） |
| "运营/投流/某做法的方法论、SOP" | 技能库 `skill_search`（英文） |
| "深度原理 / 长文经验" | 知乎 |
| "实时热点 / 正在发生什么" | 微博/X / 抖音 |
| "平台官方规则、行业事实、权威数据" | Google |
| 多个说法互相矛盾要定夺 | 内容平台之后用 Google 佐证 |

## 各通道用法

### 内容平台站内 —— "同行/真实用户怎么做"
- 小红书（已结晶）：`xhs_search_viral(keyword, top_n=10, fetch_content=5)`；要精筛用 `xhs_search_feeds(keyword, filters=...)` + `xhs_get_feed_detail`。详见 [`../../hard_skills/xhs_search/SKILL.md`](../../hard_skills/xhs_search/SKILL.md)。
- 其它平台（知乎/B站/抖音…）：暂无方言工具 → 借道 [`../../hard_skills/explore_mode/SKILL.md`](../../hard_skills/explore_mode/SKILL.md)，像人一样打开它的搜索、读结果；跑通后把"在这个平台怎么搜"沉淀进该平台世界模型，下次更快。

### 技能库 `skill_search` —— "方法论 / SOP 怎么写"
```python
import sys; sys.path.append('../memory/skill_search')
from skill_search import search
results = search("how to grow a lifestyle account on social media")  # ⚠️ 必须英文 query
for r in results:
    s = r.skill
    print(f"[{r.final_score:.2f}] {s.name} — {s.one_line_summary}  (form={s.form})")
```
用 `code_run` 执行；query 翻成英文。适合：运营/起号/投流/数据分析等通用方法论。详见 [`../../../memory/skill_search/SKILL.md`](../../../memory/skill_search/SKILL.md)。

### 网络（Google）—— 外部事实 / 平台规则兜底
- `web_scan` + `web_execute_js` 打开 **Google**（RULES 禁用 duckduckgo）。
- 适合：平台官方规则（薯条/聚光投放机制等）、行业事实、权威佐证、站内说法矛盾时定夺。
- RULES 第7条：scan 为空/不完整先等再 rescan，别凭首屏下结论。

## 跨平台
技能库、网络与平台无关，永远可用。内容平台通道中，小红书已结晶为方言；**新平台首次使用时借道 explore_mode 探索，并把"在该平台怎么搜/怎么读"写进它的世界模型**，逐步从"视觉探索"升级为"方言快路径"。

## 纪律小结
1. 缺了就查，不许编。
2. 先想"该问哪几个渠道"，别只盯一个；必要时并行多源。
3. 多源结果要**交叉验证 + 融合**，矛盾处再追。
4. 技能库 query 必须英文；网络只用 Google。
5. 关键结论**标出处**（哪个渠道/哪篇/哪次搜索）。
6. 没有方言的平台借道 explore_mode，跑通后沉淀进世界模型。
