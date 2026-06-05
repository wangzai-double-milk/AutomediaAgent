---
name: xhs_search
description: "触发：找爆款 / 检索 / 看看别人怎么拍 / 抓正文 / 抓评论 / 多图笔记"
tags:
  - xiaohongshu
  - agent_sop
---
# xhs_search_sop · 爆款检索 / 抓正文 / 多图 / 评论

> 触发：找爆款 / 检索 / 看看别人怎么拍 / 抓正文 / 抓评论 / 多图笔记
> 主入口工具：`xhs_search_viral`（已在 [`ga.py`](../ga.py) 注册），实现细节见 `do_xhs_search_viral`。

## 1. 工具签名

```
xhs_search_viral(
  keyword: str,           # Step 0 识菜给的 search_keywords[0]
  top_n: int = 10,        # 抓多少张封面（按点赞降序裁剪）
  fetch_content: int = 3, # 真实点击前 N 条卡片，抓正文+多图+评论
  skip_navigate: bool = False,  # 已经在搜索结果页可设 True 跳过导航
)
```

返回：
```json
{
  "reference_images": ["/.../viral_refs/00.webp", ...],   // top_n 张封面本地路径
  "covers": [{
    "cover_url": "...", "note_url": "...", "title": "...",
    "likes": "1.2万", "likes_num": 12000,
    "local_path": "/.../viral_refs/00.webp",
    "content": {                    // 仅当 i < fetch_content 时填充
      "title": "...", "desc": "...", "tags": [...],
      "image_urls": [...], "image_local_paths": ["/.../note_00/00.webp", ...],
      "image_texts": [...],         // 每张图 OCR 文本，攻略经常藏这里
      "comments": [...]             // 评论区前 8 条，攻略也可能在这
    }
  }, ...]
}
```

落盘：
- 封面 → `task_dir/viral_refs/{00..N-1}.webp`
- 笔记多图 → `task_dir/viral_refs/note_{i:02d}/{00..M-1}.webp`
- 元数据 → `task_dir/viral_refs/viral_meta.json`（**所有笔记跑完才整体写一次**）

## 2. 核心机制（写文案/选爆款时务必懂）

### 2.1 排序：按点赞降序
搜索页抓回的 covers 在 Python 端按 `likes_num` 降序排列后再裁 `top_n`。所以 `covers[0]` 永远是当前页最火的。

### 2.2 多图翻页（swiper 懒加载）
小红书帖子多图是 swiper 控件，**只有当前可见的几张被加载**，必须循环点右箭头才能拿全。
detail_js 实现（见 [ga.py#L728-799](../ga.py#L728-L799)）：
- 候选 selector：`.arrow-controller.right` / `.right.arrow-controller` / `.swiper-button-next` / `.next-arrow` / `div[class*="arrow"][class*="right"]`
- 每翻一页 sleep 300ms 等懒加载
- disabled 检测：`!classList.contains('swiper-button-disabled' / 'disabled')`
- 最多翻 15 张，连续 1 次没新增图就停（stale=1）
- 收集时只取 `.swiper-slide-active img / .swiper-slide-visible img / .note-slider-img img / .media-container img / picture img / .swiper-wrapper .swiper-slide img`，过滤 `naturalWidth<200` 的小图标

### 2.3 OCR 哨兵早停（省钱省时间）
攻略文字常在图里不在 desc。Python 端策略（[ga.py#L853-L930](../ga.py#L853-L930)）：

1. **第一阶段（哨兵）**：并发下载 + OCR 前 2 张图
2. **判定**：前 2 张 OCR 总文本长度 ≥ 20 字 → 视为"图里有教学文字"
3. **第二阶段**：
   - 有教学文字 → 并发（最多 8 路）下载/OCR 剩余所有图
   - 无教学文字（纯食物美图帖） → 剩余图全部跳过，标记 `skipped_no_teaching_text`，**完全不调 VLM、不下载**

终端进度行示例：
```
[1/3] 当我把日常交给豆包... (50 字, 10 图/8 OCR, 6 评论)
[2/3] 麻婆豆腐这样拍... (20 字, 2 图/0 OCR, 5 评论) [早停:2/10图,纯食物]
```

### 2.4 评论区抓取（前 8 条）
selector：`.comment-item .content / .list-container .content / .comments-container .content / .parent-comment .content / div[class*="comment"] .note-text`
长度 ≥5 字才计入。

### 2.5 OCR 单图工具
```python
from tools.image import extract_text_from_image
text = extract_text_from_image('/path/to/img.jpg')
# prompt 已经写死要求"原样抄写、不总结、不改写、不翻译"
# 无文字纯食物图返回空串
```

## 3. 风控 / 反爬

### 3.1 真实事件序列点击（绕过 React 监听器）
点击搜索卡片用 `mouseover → mousedown → mouseup → click`，不能只 `click()`：
```js
const r=el.getBoundingClientRect();
const o={bubbles:true,cancelable:true,view:window,clientX:r.left+r.width/2,clientY:r.top+r.height/2,button:0};
el.dispatchEvent(new MouseEvent('mouseover',o));
el.dispatchEvent(new MouseEvent('mousedown',o));
el.dispatchEvent(new MouseEvent('mouseup',o));
el.dispatchEvent(new MouseEvent('click',o));
```

### 3.2 风控检测
浮层 scope_text 出现 `当前笔记无法浏览 / 笔记不存在 / 系统繁忙 / 访问频繁` → 标记 `blocked=true`，跳过该笔记。
**连续 2 次 blocked → 停止抓正文**（保留封面），避免雪崩。

### 3.3 节奏调优（已经调过一轮）
- 跳搜索页等待：2.5s
- 浮层加载：1.2s
- 关浮层后：0.3s
- DOM 重抓兜底：0.8s
- 风控后多等：5.0s
- 笔记之间随机间隔：0.5–1.5s

如果再次出现"被风控"，先调高 §3.3 的等待，不要先去改 selector。

## 4. SOP：手工调试爆款抓取（不通过 ga.py 工具）

如果工具行为有 bug，先在已登录的 Chrome 里直连 CDP bridge 跑：

```python
# code_run 模板：直连 web_execute_js 看 DOM
from tools.web import web_scan, web_execute_js
print(web_scan('section.note-item'))   # 看搜索页是不是有卡片
print(web_execute_js("return document.querySelectorAll('.arrow-controller.right').length"))
```

发现 selector 不对 → 改 [ga.py](../ga.py) 的 `detail_js`，重启 ga 再跑工具。

## 5. 失败排查

| 现象 | 原因 | 解决 |
|---|---|---|
| `viral_meta.json` 没 `content` 字段 | 程序中途中断（OCR 串行卡死/Ctrl-C） | 已改并发，重跑应该不再发生 |
| 多图笔记只抓到 1-2 张 | 翻页 selector 漏 / disabled 检测漏 | 看 §2.2 候选 selector |
| `image_texts` 全空 | 帖子是纯食物美图，哨兵早停了 | 这是预期行为，不是 bug |
| 爆款图下载 403 | 缺 Referer | detail 下载已加 `Referer: https://www.xiaohongshu.com/` |
| 没登录限流 | CDP bridge Chrome 没登录 | 让用户在那个 Chrome 里登录 |
| `HTTP 429` 豆包 | OCR 并发太快 | 把 max_workers 从 8 调到 4 |
| 终端刷"笔记不存在"但视觉上在看帖子 | bridge echo 把风控关键词回显（不是真触发） | 已把判定移到 Python 端，JS 源码不再含中文风控词 |
