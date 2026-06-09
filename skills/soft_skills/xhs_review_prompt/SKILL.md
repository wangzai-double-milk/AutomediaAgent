---
name: xhs_review_prompt
description: "小红书审图/识菜/OCR 的可编辑提示词（VLM system prompt）。被 tools/image.py 在调用豆包 VLM 时加载。想调整审图风格、评判标准、毒舌调性，改这里的 .md 文件即可，无需动 Python 源码。"
tags:
  - xiaohongshu
  - agent_sop
  - prompt
---
# xhs_review_prompt · 审图提示词（用户可编辑）

> 这是**给用户改的**：审图的判定标准、毒舌调性、输出 JSON 协议都在这里。
> [`tools/image.py`](../../../tools/image.py) 只是个"调用器"，运行时从本目录加载下面 4 个提示词文件喂给豆包 VLM。
> 文件读取失败时，image.py 会回退到内置默认值，保证不崩。

## 文件清单

| 文件 | 用途 | 被 image.py 哪个函数加载 |
|---|---|---|
| [`review_solo.md`](review_solo.md) | 单图审核（无爆款对照） | `review_image()` 单图模式 |
| [`review_with_refs.md`](review_with_refs.md) | 带爆款对照的审核 | `review_image()` 对比模式 |
| [`identify_dish.md`](identify_dish.md) | 识菜 → 输出搜索关键词 | `identify_dish()` |
| [`ocr.md`](ocr.md) | 图片文字提取 / 读图 | `extract_text_from_image()` |

## 改提示词注意事项

1. **JSON 协议字段不要乱删**：`review_solo.md` / `review_with_refs.md` 末尾的 JSON 结构是和
   [`../xhs_review/SKILL.md`](../../hard_skills/xhs_review/SKILL.md) 主闭环约定好的，删字段会导致下游解析失败。
2. **毒舌调性**：和 [`../xhs_roast_style/SKILL.md`](../xhs_roast_style/SKILL.md) 保持一致（不带 emoji、不软化）。
3. 改完直接生效，无需重启 import（image.py 每次调用都重新读文件）。
