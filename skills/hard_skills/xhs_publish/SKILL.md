---
name: xhs_publish
description: "触发：发笔记 / 把这条发了 / 帮我发 / 准备发 / 上传到创作中心"
tags:
  - xiaohongshu
  - agent_sop
---
# xhs_publish_sop · 发笔记（仅自己可见，直接发布）

> 触发：发笔记 / 把这条发了 / 帮我发 / 准备发 / 上传到创作中心
> 唯一原子工具：`xhs_publish_note(images, title, desc, tags?)`（名字沿用历史，实际是"设仅自己可见后直接发布"）
> ⚠️ **硬纪律（2026-06 新流程）**：**先用 CDP 穿透设「仅自己可见」，验证通过后才点「发布」**。可见性未确认为 self 时工具直接报错中止，绝不点发布——防误公开。**不再走草稿箱/暂存离开**（因手机端电脑端草稿不互通，存了电脑草稿手机也看不到）。
> ⚠️ **前置：Chrome 窗口必须在前台聚焦**。`cdp_mouse_click`（`Input.dispatchMouseEvent`）依赖窗口聚焦，窗口在后台时合成器会丢弃输入事件 → 可见性下拉点不开、发布点不动。后台脚本无法验证点击，必须真实运行时盯着 Chrome 前台。
> 🔑 **可见性 DOM（2026-06 实测，普通 DOM 非 shadow）**：可见性卡片外层 `.permission-card-wrapper`（唯一锚点，默认在视口外 y≈1428 需 scrollIntoView）→ 内含 `.d-select`，其 `.d-select-description` 显示当前值（"公开可见"等），点它展开 → 选项 `.d-grid.d-options > .custom-option > .group-info > .name`，文本为「公开可见 / 仅自己可见 / 仅互关好友可见」。
>   - ⚠️ **触发器必须限定在 `.permission-card-wrapper` 子树内找**：页面有多个 `.d-select-description`（含空文本的别的下拉），全局匹配会命中错的，导致点击后下拉不展开、选项 getBoxModel 报 "Could not compute box model"。
>   - ⚠️ **d-select 只认可信(isTrusted)事件**：JS `dispatchEvent(click/mousedown)` 合成事件无法展开下拉，必须真实鼠标点击（CDP `Input.dispatchMouseEvent`）。
>   - 🛠️ 工具用 `cdp_set_xhs_visibility('self')`（ga.py）：在 permission 子树定位触发器 → scrollIntoView → getBoxModel 拿坐标 → cdp_mouse_click 展开 → 重新取树找 `.name`=「仅自己可见」选项 → 点击 → 重新读触发器值验证。
> 🔑 **关键事实（2026-06 实测确认）**：发布页底部提交栏是 Web Component `<xhs-publish-btn>`，其 shadowRoot 是 **closed**（普通 JS `document.querySelector` 完全进不去）。shadow 内有两个按钮：`button.ce-btn.white`=「暂存离开」、`button.ce-btn.bg-red`=「发布」。工具用 `cdp_find_xhs_publish_bar_btn('publish')` 走 CDP `DOM.getDocument(pierce:true)` 协议级穿透 closed shadow 拿「发布」坐标，再 CDP 物理点击。
> 🔑 **图片上传必须用 CDP `DOM.setFileInputFiles`（2026-06 实测确认）**：传本地文件路径、payload 极小。
>   - ❌ 绝不用 JS `DataTransfer + 内联 base64`：大 payload 经 WS 回传时 bridge 端 JSON 解析必崩（报 `Expecting value: line 2 column 5`），async 返回 Promise 也序列化不稳。实测无论 await 与否都失败，导致上传步骤误判失败 → 整个发布流程在第 3 步就 abort，永远走不到填标题/发布（这正是"发布走不通"的真实根因之一）。
>   - ✅ 工具用 `cdp_upload_files_to_input()`：`getDocument(pierce:true)` 递归找 file input 的 **backendNodeId**（持久 ID，跨 CDP 调用不失效；nodeId 会失效）→ `setFileInputFiles`。
>   - ✅ 上传成功判定靠 `cdp_wait_publish_editor()` **轮询等标题框/正文编辑器出现**（不看 JS 返回值，实测编辑器约 +7s 出现）。
> 🔑 **真实 selector（dump 实测）**：
>   - 标题：`input.d-text` placeholder=`填写标题会有更多赞哦`
>   - 正文：`div.tiptap.ProseMirror`（632x140）
>   - URL 必须带 `&target=image`，否则默认进 `target=video` 视频页
> 🔑 **话题标签必须是真 chip**：`tags` 不能当纯文本拼进正文（那样只是死字符串、点不开）。工具在正文填完后会逐个 tag 走真流程：focus 富文本末尾 → CDP `Input.insertText` 输入 `#`+词唤起话题下拉框 → 点选第一项变蓝色可点 chip（下拉框抓不到时用 `visual_click` VLM 兜底）。这一步在 ga.py 第 5.6 阶段。
> ⚠️ **visual_click 误发布历史漏洞（已堵）**：早期描述含"定时发布"字样会被 visual_click 的关键词路由误判成"发布"，CDP 直接点真发布把笔记公开发出去。现已在 web_vision.py 加否定关键词过滤（含 公开可见/仅自己可见/权限/定时发布/可见性 等词的描述绝不路由到 publish）。可见性操作一律走 `cdp_set_xhs_visibility`，不要再用 visual_click 点可见性。

## 0. 何时进这本 SOP

| 场景 | 进入方式 |
|---|---|
| 完整 PASS 路径走完 review_sop §Step 4.5 | review_sop §Step 5 **自动**调本工具，不再 ask_user |
| 用户已经有图 + 文案，单独让发 | 直接调 `xhs_publish_note`（无需先 review） |
| REWORK / REPHOTO 路径 | **默认不进**，先按 review_sop 修完图再说；用户明确要求直接发现有图的例外 |

## 1. 工具签名

```python
xhs_publish_note(
  images=["<path1.png>", "<path2.png>", ...],   # 必填，1-9 张，按顺序上传
  title="<≤20 字最佳>",                          # 必填
  desc="<≥200 字成稿>",                          # 必填
  tags=["美食", "#早餐", ...],                    # 可选，工具自动加 # 前缀
)
```

返回 `{status, click_result, visibility, message}`。成功时 `click_result='published_self_visible'`、`visibility='仅自己可见'`。可见性未确认为 self 时返回 `status='error'`（绝不发布）。

## 2. 前置条件（缺一不可）

1. **CDP bridge 已接管 Chrome**（参考 [memory/tmwebdriver_sop.md](../../../memory/tmwebdriver_sop.md)）
2. **创作中心已登录**（`https://creator.xiaohongshu.com/` 能直接进，不弹登录）
3. **task_dir 存在**（review_sop §Step 0a 建好）
4. **图片真实存在**：建议传 `task_dir/edited_*.png` 绝对路径
5. **Chrome 窗口在前台聚焦**：否则 CDP 鼠标点击被丢弃，可见性/发布点不动

## 3. 内部阶段（工具自动跑，agent 不用拆）

| 阶段 | 动作 | 失败影响 |
|---|---|---|
| 1 | 跳 `creator.xiaohongshu.com/publish/publish?...&target=image` | 致命 |
| 2 | 找"上传图文"tab realClick 切过去 | 不致命（默认可能就是图文） |
| 3 | **CDP `setFileInputFiles` 上传** + 轮询等编辑器出现 | 致命 |
| 4 | 标题：`Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set` 绕 React | 警告，留给用户手填 |
| 5 | 正文 + tags：`execCommand('insertText')` 优先，`innerText` 兜底（ProseMirror/Quill 系） | 警告 |
| 5.6 | 逐个 tag 唤起话题下拉 → 点选变真 chip | 警告 |
| 6 | 派 input/blur 触发 React 感知内容变更（按钮 enable） | 不致命 |
| 7 | ⭐ `cdp_set_xhs_visibility('self')` 设「仅自己可见」+ 验证 | **致命**：未确认 self 直接中止，绝不发布 |
| 8 | `cdp_find_xhs_publish_bar_btn('publish')` 穿透拿坐标 + CDP 点「发布」 | 致命 |

⚠️ 阶段 7 是发布前的**强制闸门**：只有 `vis.ok==True`（验证当前值=「仅自己可见」）才会进阶段 8 点发布，否则报错返回。

## 4. tags 数据来源（硬纪律）

tags 必须**真的从** `task_dir/viral_meta.json[covers[*].content.tags]` 抽取，**不许 LLM 凭空编**。

```python
# 推荐组装方式（agent 自己拼）
import json, collections
meta = json.load(open(f'{task_dir}/viral_meta.json'))
all_tags = []
for c in meta.get('covers', []):
    all_tags += (c.get('content') or {}).get('tags') or []
top_tags = [t for t,_ in collections.Counter(all_tags).most_common(12)]
```

凭空编的 tags 容易没流量池，被算法降权 → 发了等于白发。

## 5. 失败排查

工具每个阶段都会 `yield` 一行日志到终端，形如 `[3/6] 上传 1 张: editor_appeared=True`。看到下面关键词按表对：

| 现象（终端关键词） | 原因 | 解决 |
|---|---|---|
| 上传后 `editor_appeared=False` | 没切到图文 tab / 创作中心改版 | `web_scan` 看页面，按新结构改 selector |
| `[5/6] desc_editor_not_found\|dump=...` | 富文本 selector 没命中 | 看 dump 里实际的 contenteditable 标签/类名，加进 sels 列表 |
| `[7] 设可见性...ok=False, reason=未找到可见性下拉触发器` | permission 卡片没渲染/改版 | 确认在图文发布页且编辑器已出现；用 `web_scan` 看 `.permission-card-wrapper` 是否存在 |
| `[7] 设可见性...ok=False, now=公开可见` | 下拉点不开（窗口未聚焦） | **把 Chrome 窗口拉到前台聚焦**后重试（CDP 鼠标点击依赖聚焦） |
| `[7] ...Could not compute box model` | 触发器锚点命中错下拉/在视口外 | 已用 `.permission-card-wrapper` 限定 + scrollIntoView；若仍报，检查卡片是否被遮挡 |
| `[8] 已设仅自己可见但未定位到发布按钮` | closed shadow 改版 | 看 `cdp_find_xhs_publish_bar_btn` 双校验逻辑（class 含 bg-red + 文本含"发布"） |
| `发布按钮 CDP 点击报错` | 窗口未聚焦/坐标越界 | 确认窗口前台 + 视口尺寸 |
| 跳转后空白 | 未登录 | 用户先在浏览器登录 creator.xiaohongshu.com |
| `image 不存在` | 相对路径漂移 | 用绝对路径 |

## 6. 跟其它 SOP 的衔接

- 自动入口：`../xhs_review/SKILL.md §Step 5（PASS 后自动发布）`
- 数据源：`../xhs_search/SKILL.md` 抓的 `viral_meta.json` 提供 tags
- 文案源：`../xhs_review/SKILL.md §Step 4.5` 模板生成的 desc + title_hints
- ⚠️ 不调 `../xhs_edit/SKILL.md`，发帖前修图应已完成
