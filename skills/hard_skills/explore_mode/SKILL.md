---
name: explore_mode
description: "未知平台能力：为完成当前动作观察页面、试探可逆操作并记录世界模型。"
tags: [capability, browser, exploration]
---
# Explore Capability

本 Skill 是未知平台的操作能力，不是用户任务工作流。

## 操作循环

1. 用 `web_scan` / `web_learn_page` 确认当前页面和可交互元素。
2. 为当前动作形成一个最小假设。
3. 用 `web_visual_click` / `web_visual_fill` 执行一个可逆步骤。
4. 检查 `verified` 或重新扫描，确认页面是否按预期变化。
5. 记录本任务证据；高频且稳定的流程再通过 `create_skill` 沉淀。
6. 连续三次失败后停止盲试并请求必要的人类介入。

## 边界

- 只完成上层工作流委派的当前动作，不重新决定用户目标。
- 不调用 `research_mode`、`operate_mode` 或任何 Router。
- 不把“探索整个平台”强加给一个单点任务。
- 公开发布、删除、付款等不可逆动作不用于试探。
- 读取视频内容时必须取得真实视频并调用 `analyze_media`。
