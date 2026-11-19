---
name: douyin_browse
description: "抖音网页浏览、搜索、读取视频与已加载评论，复用当前Chrome；理解内容交给analyze_media。"
tags: [capability, douyin, browse, research]
---
# 抖音浏览与学习

工具：`douyin_browse`。仅本工具的固定动作可直接操作抖音；不要改用小红书工具，
也不要关闭账号保护、注入Cookie、签名或切换工具绕过实际验证码。

## 一条完整流程

1. `douyin_browse(action="status")` 读取现有抖音页状态。没有标签时正常打开
   `https://www.douyin.com/`；已有多个标签用 `web_scan(tabs_only=true)` 选出抖音tab_id，
   后续始终传同一tab_id。不得覆盖其他站点或未保存表单。
2. `action="search", keyword="主题", limit=6`：正常打开抖音视频搜索网页，
   返回实际DOM中的视频链接、卡片文字、作者和可见统计。字段缺失保留null。
   `action="feed"` 可看推荐页，`action="read"` 只读当前页，不刷新不翻页。
3. 从返回items选实际链接，`action="open", url=该链接`。可传
   `expected_url=刚刚的完整url` 避免用户换页后继续操作。读取当前播放源类型与详情。
4. 使用 `analyze_media(images=[], video_url=media.stream_url或media.source_url, prompt=...)`
   看真实视频再总结。没有视频源、只有blob或模型分析失败时，明确“仅看到了页面文字”；
   三域研究改传`video_url="current"`引用本次已观察源，由工具保管长播放地址；
   不手动复述、截断、修正或拼接播放链接。没有已观察源时该引用会被拒绝。
   不根据封面、标题或播放时间猜视频内容，不把 `understood=false` 改成已理解。
   模型提示需包含来源、标题、研究目标和已知方法基线。
   当analyze_media返回media_scope=visual_track_only，只能将画面/字幕记为证据，
   不采信模型关于听到声音或完整口播的自我声明。完整音画理解需后续补齐音频证据。
5. 仅在首页推荐流、有明确当前video_id或player_key时使用 `action="next"`；
   已在推荐页直接next，不先feed刷新。最多翻一条并核对变化。
   player_key来自当前真实播放源路径与标题，只证明播放器连续性；它不是平台ID，
   不据此拼接分享链接。降级翻页须源路径和标题同时改变，清晰度/签名变化不算翻页。
   changed=false/not_ready不能当作成功。搜索页用open选择已知链接。
6. 用 `record_research_delta` 留下真实来源与方法增量，先形成候选，避免照搬作者原文。
   本工具保存每次页面证据到任务目录，搜索回合接入已有研究追踪。

## 读取边界与恢复

- 显示 `verification_required/restricted/login_required` 时停止，交给用户正常处理后再读。
  即使旧版返回not_ready，只要页面明确“登录后即可搜索更多精彩视频”也必须停止，
  不接着跳推荐或换账号。此为平台登录前提，不能用其他工具避开。
  页面出现“登录”按钮不自动等于强制登录；login_state=unknown如实保留。
- 三域研究遇直播预览或不可识别播放器由工具强制结束，保留当前证据。
  不继续feed/search或使用媒体工具绕过终止；此时任务为blocked，报告写完不等于研究完成。
- 浏览范围仅www.douyin.com的首页、搜索、视频和用户网页；不访问私信、商城、创作者发布页。
  首页可能跳到/jingxuan；feed优先使用页面实际“推荐”链接，不能把精选网格当单视频流。
- comments只包含页面已展开、已加载且可识别的评论行，空数组不等于没有评论。
- status=ok是页面可读，不是已登录、数据全量或视频已理解。
- 专用工具没有点赞、收藏、关注、评论、私信、下载重发布和发布入口。
  用户对小红书的互动授权不自动迁移为抖音互动；后续扩展需实现独立状态和回读。
- 浏览器断连或改版时保留evidence_path，至多一次根据新证据修正，不反复盲刷。
- DOM缺少明确控件且需诊断时，可用status/read的capture=true保存页面截图。
  截图只用于界面诊断，不把单帧截图当成全视频理解。
- `idle_pause=true`表示“长时间无操作，已暂停播放”，不是登录验证。
  目标需要继续观看/翻页时可`action="resume"`一次，只点击唯一“继续播放”并回读播放状态。
  `resumed=false`时停止，不能不停按下一条或误点客户端安装入口。
- 小红书仍遵循当前用户“仅自己可见”的发布要求；抖音阅读不会消耗其平台写额度。

来源与融合范围见 [接入说明](../../../docs/DOUYIN_INTEGRATION.md)。
