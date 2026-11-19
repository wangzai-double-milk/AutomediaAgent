---
name: operate_mode
description: "内容策划与持续运营工作流：选题、脚本、制作、发布、数据回流和复盘。"
tags: [workflow, operate, cross_platform]
---
# Content & Operate Workflow

用于一次性内容策划或持续运营账号。单次搜索、修图或发布不进入本工作流。依赖见 `skills/registry.json`。

## 循环

1. **明确交付**：一次性需求直接产出选题/脚本/素材方案；持续运营读取账号偏好、历史和 `personal_operating_method` resource。
2. **选题**：调用搜索 capability 获取近期样本、评论需求和内容缺口。
3. **制作**：
   - 素材评估、修图和生图使用 `xhs_edit` capability。
   - 商业广告调用 `commercial_ad_production` resource。
   - Git/开发者内容调用 `git_content_creation` resource。
   - 文案和标签必须基于本轮样本，不凭空编平台热词。
4. **发布**：调用目标平台发布 capability。小红书显式指定可见性，可预填、发布图文/视频、平台定时或内容排队。
5. **数据回流**：使用 `xhs_manage` 读取作品、账号/粉丝数据与通知并落盘，缺失字段不补零。
6. **复盘**：比较选题、封面、文案和发布时间，记录有效/无效假设。
7. 回到选题，形成下一轮。

## 平台选择

抖音调研/学习使用 `douyin_browse`，与现有视频理解和研究记忆衔接；此能力没有互动或发布入口。
主工作流不绑定平台。每个平台使用自己的 capability；没有 capability 时用
`explore_mode` 完成当前动作，不在本工作流内描述页面操作细节。

## 边界

- 本工作流只编排，不复制 capability 的工具参数和 selector。
- 完整“审图到发布”属于 `xhs_review` 主工作流；如用户改目标，应重新路由，不在这里跨 workflow 调用。
- 写操作核对已有授权；已授权范围不重复确认。输入或提交结果不确定时读取页面核对。
- 可复用的赛道打法写为 resource，不允许 resource 再调用本工作流。
