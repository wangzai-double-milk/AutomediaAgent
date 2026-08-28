---
name: operate_mode
description: "持续运营工作流：定位、选题、制作、发布、数据回流和复盘。"
tags: [workflow, operate, cross_platform]
---
# Operate Workflow

用于持续运营账号。单次搜索、修图或发布不进入本工作流。依赖见 `skills/registry.json`。

## 循环

1. **账号定位**：读取既有偏好与账号历史。长期方向发生变化时让用户确认一次。
2. **选题**：调用搜索 capability 获取近期样本、评论需求和内容缺口。
3. **制作**：
   - 素材评估与修图使用 `xhs_edit` / `xhs_review`。
   - 文案和标签必须基于本轮样本，不凭空编平台热词。
4. **发布**：调用目标平台发布 capability。小红书固定仅自己可见。
5. **数据回流**：读取作品互动、账号变化与评论反馈并落盘。
6. **复盘**：比较选题、封面、文案和发布时间，记录有效/无效假设。
7. 回到选题，形成下一轮。

## 平台选择

主工作流不绑定平台。每个平台使用自己的 capability；没有 capability 时用
`explore_mode` 完成当前动作，不在本工作流内描述页面操作细节。

## 边界

- 本工作流只编排，不复制 capability 的工具参数和 selector。
- 公开发布、删除和付费投放必须确认。
- 可复用的赛道打法写为 resource，不允许 resource 再调用本工作流。
