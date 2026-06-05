---
name: xhs_review
description: "触发：审图 / 这张图能不能发 / 帮我看看 / 帮我审 / 帮我 P / 准备发"
tags:
  - xiaohongshu
  - agent_sop
---
# xhs_review_sop · 审图主闭环

> 触发：审图 / 这张图能不能发 / 帮我看看 / 帮我审 / 帮我 P / 准备发
> 这是小红书 agent 的主工作流。**Step 0~6 顺序硬约束，违反 = 任务失败**。

## 0. 硬性纪律（重申总览 §4）

- ❌ 跳过 Step 0b/1 直接审图（会被 BLOCKED）
- ❌ Step 4 跑 ≥ 3 次（会被 BLOCKED）
- ❌ 用 `code_run` 自己 import 豆包 SDK 绕开工具
- ❌ 流程中 `ask_user`（**唯一例外**：Step 0b confidence < 0.5）
- ✅ 全程必须有 `task_dir`（Step 0a 建好）

## Step 0️⃣ 任务初始化 + 识图

### 0a — 建工作区
```
xhs_task_init(user_request="<原话>", image="<用户图绝对路径>")
```
原图自动拷到 `task_dir/original.*`，后续所有产物（爆款图、审图 JSON、修图、批注图）都进这个目录。

### 0b — 识菜拿搜索关键词（永远第二步，不许跳）
```
xhs_identify_dish(image="<task_dir/original.xxx>")
```
返回 `{dish_name, category, search_keywords, confidence}`，自动存 `task_dir/dish_info.json`。

⚠️ confidence < 0.5 才允许 `ask_user` 让用户确认菜名（**唯一**允许 ask 的场景）。

## Step 1️⃣ 找爆款对照（详见 [../xhs_search/SKILL.md](../xhs_search/SKILL.md)）

```
xhs_search_viral(keyword=search_keywords[0], top_n=10, fetch_content=3)
```

要点：
- `fetch_content=3` 默认会抓 top3 笔记的正文 + 多图 OCR + 评论 → `viral_meta.json`
- **Step 4.5 写文案/标题/tag 全靠这些数据**，不要凭空编
- 只想审图不写文案 → 传 `fetch_content=0` 跳过

返回 `reference_images`（本地路径列表）+ `covers`（含 `likes_num` 已解析）。

⚠️ 绝对不要 `ask_user` 问"换关键词吗"——直接用 `search_keywords[0]` 跑。

## Step 2️⃣ 严格档审图（带参考图）

```
xhs_image_review(
  image="<task_dir/original.jpg>",
  hint="<dish_name>",
  reference_images=["<viral_refs/00.jpg>", ...],   # 必传，否则 BLOCKED
)
```

返回 JSON 字段：`score / verdict / dimensions / viral_patterns / gaps_vs_viral / issues / edit_prompts / additional_shots_needed / roast / title_hints / summary`

判定标准（详见 `tools/image.py` SYSTEM_PROMPT_*）：
- score ≥ 80 → **PASS**
- 60–79 → **REWORK**（仅当问题修图能解决；任一差距属"必须重拍"则强制 REPHOTO）
- < 60 → **REPHOTO**
- 主菜失焦 / 严重模糊 / 角度错误 → 强制 REPHOTO

## Step 3️⃣ 按 verdict 分支

```
PASS    → 进 Step 4.5 输出最终交付清单 → Step 5 自动发仅自己可见
REWORK  → 进 Step 4 修图闭环
REPHOTO → 进 Step 6 调侃 + 画批注示意图
```

## Step 4️⃣ REWORK 闭环（最多 2 轮，详见 [../xhs_edit/SKILL.md](../xhs_edit/SKILL.md)）

⚠️ **每一轮都基于 `original.jpg` 重 P，不要在上一轮的 edited 上再 P**（Seedream 多轮叠加会越 P 越坏，已被坑过 → Turn5 P 完 Turn7 就被判 REPHOTO 的就是这个 bug）。

```
original = task_dir/original.*    # 锁死，整个闭环不变
last_review = review               # Step 2 的初次审图结果
for idx in 0..1:
    # 关键：image 永远传 original，不是上一轮的 edited
    edited = xhs_image_edit(image=original, edit_prompts=last_review.edit_prompts,
                            save_path=f'{task_dir}/edited_{idx+1:02d}.png')
    review2 = xhs_image_review(image=edited, hint=hint, reference_images=...)
    if review2.verdict == 'PASS':
        final_cover = edited; goto Step 4.5
    if review2.verdict == 'REPHOTO':
        # 第 1 轮就判 REPHOTO = prompt 写坏了，不是原图的锅
        if idx == 0:
            last_review = review2   # 用新的 edit_prompts 重写一次
            continue                # 不要降级，再 P 一次（仍基于 original）
        else:
            goto Step 6              # 第 2 轮还 REPHOTO，原图确实救不动了
    last_review = review2            # REWORK，下一轮换新 prompt
goto Step 6  # 跑完 2 轮还没 PASS，降级
```

⚠️ 硬纪律：
- `xhs_image_edit` 的 `image` 参数**永远传 original**，不能传 `edited_01.png`
- 每轮 `save_path` 用 `edited_<idx+1>.png`，方便对比
- 第 1 轮判 REPHOTO **不允许直接降级**，必须基于新 edit_prompts 再 P 一次（说明上次 prompt 把图 P 坏了，不是原图问题）

## Step 4.5️⃣ PASS 路径【最终交付清单】（强制 6 块，缺一不可）

PASS 之后**绝对不要**只丢"图路径 + 几条标题候选"——`viral_meta.json[covers[i].content]` 里有完整爆款正文 + tags，必须用上。

```markdown
✅ 封面已达爆款标准

📌 **最终封面**：<edited_cover.png 绝对路径>

📝 **推荐标题**（3 条，都要带情绪 + 具体卖点 + 数字/感叹）：
1. ...
2. ...
3. ...

📄 **完整正文**（200~400 字，必须是真能发的成稿，模仿 viral_meta covers[0~2].content.desc 的结构）：
- 开头 1 句钩子（疑问/惊叹/数字）
- 主体 2~3 个分点（用 emoji + 加粗短句开头），每点写口感/做法/为什么爆，仿爆款正文写法
- 结尾 1 句行动召唤（"评论区告诉我..."、"想吃的扣 1"等）
✏️ 直接给出可复制粘贴的成稿，不要让用户自己组装。

🏷️ **话题标签**（8~12 个，必须**真的从 viral_meta.json[covers[*].content.tags]里挑**，不允许凭空编）：
#xxx #yyy #zzz ...

🔥 **审图毒舌点评**（原样复述 review.roast 字段，不许改写得更礼貌，详见 ../xhs_roast_style/SKILL.md）：
"..."

💡 **本次优化做了什么**：
1. ...（对应 edit_prompts 每一条）
2. ...
```

⚠️ 正文字数硬指标：≥200 字。少于 200 字直接重写。
⚠️ 标签必须真实：从 viral_meta 里抽取，不许 LLM 自己编（编出来的容易没流量池）。
⚠️ desc 太短或没料 → 主动翻 `viral_meta.json[covers[*].content.image_texts]`（图里 OCR）和 `comments`（评论区）找攻略素材。
⚠️ 缺正文 = 任务没完成。

## Step 5️⃣ PASS 后自动发仅自己可见（详见 [../xhs_publish/SKILL.md](../xhs_publish/SKILL.md)）

Step 4.5 给完用户最终交付清单后，**直接**调发帖工具直接发布（设为仅自己可见），不要再 ask_user：

```python
# 从 viral_meta.json 抽 tags（硬纪律：不许凭空编）
import json, collections
meta = json.load(open(f'{task_dir}/viral_meta.json'))
all_tags = []
for c in meta.get('covers', []):
    all_tags += (c.get('content') or {}).get('tags') or []
top_tags = [t for t,_ in collections.Counter(all_tags).most_common(12)]

xhs_publish_note(
  images=[<edited_cover.png 绝对路径>],     # PASS 的最终封面，可加更多张
  title=<Step 4.5 推荐标题里挑 1 条>,
  desc=<Step 4.5 完整正文成稿>,
  tags=top_tags,
)
```

⚠️ **DRAFT-ONLY**：本 Step 只触发"仅自己可见"并发布。最终发布永远人工把关。
⚠️ 工具失败（`save_draft_btn_not_found` 等）→ 不要重试 3 次，直接告诉用户去创作中心手动检查。

## Step 6️⃣ REPHOTO（修不好就调侃 + 画批注）

### 6a — 先画批注示意图（详见 [../xhs_edit/SKILL.md §批注模板](../xhs_edit/SKILL.md#批注模板)）
```
xhs_image_edit(
  image="<task_dir/original.jpg>",
  edit_prompts=[
    '在图片上用粗红色箭头和中文文字标注以下要点（保持原图内容不变，仅在图层上叠加标注）：',
    '主菜上方画一个红色箭头标注「这里要冒蒸汽」',
    '盘子右侧画红色箭头标注「加几片葱花点缀」',
    '画面整体偏暗的区域画虚线圈出，标注「补一束顶光」',
  ],
)
```

### 6b — 5 件东西按以下严格顺序回给用户

1. **roast 整段**（直接复制 review JSON 的 `roast` 字段）—— **回复第 1 行就要损**
2. **annotated 示意图本地路径**
3. **gaps_vs_viral** —— 跟爆款的关键差距清单
4. **additional_shots_needed** —— 具体重拍角度
5. **3 张爆款参考图本地路径** —— 直观对比

格式禁令 / 调性示例：详见 [../xhs_roast_style/SKILL.md](../xhs_roast_style/SKILL.md)。

## 失败排查（review 闭环常见）

| 现象 | 原因 | 解决 |
|---|---|---|
| Step 2 BLOCKED 提示缺 reference_images | 跳过了 Step 1 | 回去跑 search_viral |
| 修后图变形太大 | edit_prompts 太宽泛 | 拆细，加"**仅**修复 xxx 区域"，详见 edit_sop |
| 一直死循环 REWORK | 模型在边界打转 | 已有"最多 2 轮"硬约束，跑完降 REPHOTO |
| Step 0b confidence 很低 | 用户图模糊/小众菜 | 这时**才**允许 ask_user 确认菜名 |
| `image 不存在` | 相对路径 cwd 不对 | 用绝对路径 |
| `HTTP 401/403` 调豆包 | apikey 失效 | 检查 [`mykey.py`](../mykey.py) |
