# 抖音浏览能力

`douyin_browse`提供status、搜索、详情、推荐及有限播放器操作。
平台ID与播放器连续性标识分别记录；缺失字段保持未知，翻页前后必须有实际变化证据。
登录、验证、直播预览、未知播放器或不支持的动作按工具边界停止。

视频理解使用本任务实际观察到的媒体来源；分析成功仍不代表独立准确语音转写。
当前未接入抖音发布、私信与互动。首次使用先读取`skills/hard_skills/douyin_browse/SKILL.md`。

浏览器搜索流程部分改编自MIT项目`pazwusimple-netizen/douyin-mcp`，
保留许可证[DOUYIN_UPSTREAM_LICENSE.txt](DOUYIN_UPSTREAM_LICENSE.txt)。
没有引入上游Cookie、私有签名、隐身或绕过验证代码。
