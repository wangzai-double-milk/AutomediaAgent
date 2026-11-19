---
name: xhs_publish
description: "小红书图文/视频发布、预填、定时和持久化队列；核对内容与可见性后提交。"
tags: [capability, publish, xiaohongshu]
---
# Xiaohongshu Publish

工具：`xhs_publish_note`、`xhs_publish_video`、`xhs_publish_longform`、
`xhs_publish_podcast`、`xhs_prepare_note`、`xhs_content_queue`。
三域母稿任务使用专用 `xhs_publish_content(content_id)`，只能提交此任务绑定且已经独立核对的
图文母稿。先读取本Skill，再初始化任务目录，再调用一次；文案、素材和账号从冻结记录取得，
不在执行时重新生成。只支持self；额度延后、结果未知或版本变化立即结束，禁止换发布工具重试。
图文与视频统一经过内容回读检查，旧图文分支已移除。
`visibility` 默认 `self`，明确参数可选 `public/friends`；原任务提交指纹继续防重复。

这是**真实发布**，不是保存草稿：

```text
上传图文/视频 → 等待处理 → 填标题/正文/话题并回读 → 核对请求的可见性与定时
        → 提交前记录指纹 → 单次点击发布 → 验证成功信号
        → desktop独立管理页核对账号/ID/时间/可见性，再编辑页核对标题正文
```

## 前置条件

1. CDP Bridge 连接或 macOS AX 可操作现有Chrome，创作中心已登录。
   `transport=auto/cdp/desktop`；desktop回读 `expected_account` 页眉名称。
   CDP尚未验证账号页眉；指定expected_account时上传前停止并提示使用desktop，不忽略账号绑定。
   desktop已实测单图发布/修改/删除与独立核对、视频草稿、两图真实话题草稿、
   长文原文→排版→草稿、播客音频/封面→草稿。长文/播客发布和多图发布尚未实际提交。
   话题AX正文独立子节点“#词”+“[话题]#”需同父级；只输入#词不算chip。
2. 当前任务已通过 `xhs_task_init` 建立工作目录。
3. `images` 使用 1–18 个存在的绝对路径，png/jpg/jpeg/webp、每张不超过32MB；
   或 `video` 使用单个视频绝对路径、不超过20GB；两者互斥。此限制来自本机真实网页。
   `title` 最多 20 个 UTF-16 字符；`desc` 最多 1000 个；话题最多 10 个。
4. Chrome 保持前台，验证码和登录由用户处理。

## 长文与播客

- `xhs_publish_longform(longform_body,title,desc)`：原文1–16000字符，
  逐字符正常键盘输入后核对非空白正文，点击一键排版、下一步，等待图片生成完成。
  最终标题仍20个UTF-16字符、desc仍1000；原文排版成图片，不冒充像素核对通过。
- `xhs_publish_podcast(audio,cover,title,desc)`：音频m4a/mp3/wav/flac/aac，
  实际时长10分钟至2小时、1GB以内；封面png/jpg/jpeg/webp、32MB以内，推荐1:1。
  先在上传对话框分别绑定音频与封面控件，等上传成功+设置封面，再点击“去发布”
  打开编辑器。“去发布”不是最终提交，最终“发布”才进入提交日志。
- 两种格式目前使用desktop；共用preview/save_draft/visibility/schedule_at与指纹日志。
- AXValue返回0不代表富文本变更成功；回读失败就用实际聚焦的正常键盘输入，
  每字等待Chrome处理。快捷键结束用Cmd+Down，不能连续推送20字Unicode块。

## 不可绕过的闸门

- `visibility` 默认 `self`，明确指定可使用 `public` 或 `friends`。按用户现有授权执行，不重复询问；
  页面实际可见性与请求不一致时停止，不点击发布。
- `schedule_at` 使用 ISO 8601；未指定时区按北京时间，支持当前 1 小时至 14 天内。页面未能回读则停止。
- `xhs_prepare_note` 和 `preview=true` 只预填，不提交、不称作已保存草稿。
- `save_draft=true` 通过desktop保存浏览器本地草稿并核对标题；返回draft、
  saved_as_draft=true，不进入提交日志；不能称作云端或手机同步草稿。
- 找不到上传区、编辑器或发布按钮：停止并返回具体阶段。
- 点击后未观察到成功页或成功提示：返回 `unverified` 并进入
  `WAITING_USER`，禁止自动再发。
- desktop即使成功提示出现，独立管理核对失败仍为unverified。
  媒体像素独立一致不在已核对字段内，scheduled管理核对尚未实现；
  不把定时提交成功信号等同正式按时发布。
- 同一任务中相同媒体内容、标题、正文、标签、可见性和时间只提交一次。提交记录保存在
  `task_dir/publication_attempts.json`。

## 返回语义

| 状态 | 含义 | 下一步 |
|---|---|---|
| `ok` | 已核对请求参数且观察到发布成功 | 可交付结果，定时作品仍需管理页核对 |
| `prepared` | 已预填与回读，没有提交 | 保留页面预览 |
| `unverified` | 已点击，但未获得成功证据 | 人工查看创作中心，不重试 |
| `deferred` | 账号未绑定、冷却或额度不足，尚未提交 | 停止本轮并报告retry_after |
| `error` | 发布前置或页面操作失败 | 修复明确原因后再执行 |

## 内容纪律

- `tags` 来自本轮真实检索结果，不凭空编造热词。
- 话题必须通过同名页面候选形成可点击 chip，不能只把 `#词` 写入正文。
- 发布能力不负责审图和改图；完整闭环由 `xhs_review` workflow 编排。

## 内容队列

`enqueue` 保存待发布参数、媒体指纹、任务目录及 `run_after`；`run_due` 执行到期项。
`run_after` 是本地开始执行时间，`schedule_at` 是平台发布时间，两者不同。
重启后 `running` / `uncertain` 不会自动重发。`retry` 仅用于明确未提交的 `failed`；
`reconcile` 读取管理页匹配候选，标题匹配本身不能证明全部媒体与文案一致。
队列在 `tasks/content_queue.json`；多进程通过文件锁协调。

Studio 启动时运行常驻 `QueueWorker`，默认15秒检查一次；可用CDP或已绑定的desktop执行到期项。
desktop队列必须传expected_account，目标创作中心页眉不匹配、存在未保存编辑器或草稿抽屉时
保持queued；auto降级desktop也必须有账号绑定。后台与前台共用浏览器锁、多实例通过worker文件锁选出一个。
关闭工作台只停止后续调度，不中断已经开始的提交。

macOS 可通过 `web_desktop` 使用真实无障碍控件执行预填和“暂存离开”：
本机已验证测试草稿数量5→6、标题与正文回读、私密设置、再次暂存不重复增加。
网页明确草稿只存在当前浏览器本地，清除浏览器数据会删除，不称云端或手机同步草稿。
`xhs_prepare_note` 仍只预填；完整桌面发布/草稿工具接入状态见功能矩阵。

当前新账号测试模式优先于历史调度说明。后台默认暂停，手动run_due也先检查owner_id、
expected_account和共享额度，拒绝时不领取、attempts=0；队列publication须显式绑定稳定owner_id。
两次发帖间隔10分钟，不设每日或滚动24小时次数上限；
修改、删除、互动、消息、合集、RSS和保存草稿不占发帖间隔。保存草稿不是零副作用的预填。
原提交记录已存在时只返回记录；预算领取在新journal之前，失败/不确定不退还。
不要立即发布再删除，也不要通过其他任务/账号/transport绕过间隔。
目前旧本地草稿没有稳定账号归属，测试模式保留原草稿，不执行draft publish/delete。
参见[测试控制与接续](../../../docs/NEW_ACCOUNT_TEST_CONTROL.md)。
