# 日常文档导航与整理清单

日常使用从前三项开始，其余按需要阅读。

| 文档 | 什么时候看 |
|---|---|
| [README](../README.md) | 看项目做什么、界面是什么样、第一次安装 |
| [启动速查](../HOW_TO_RUN.md) | 每次运行、切换端口或终端模式 |
| [安装与排障](installation_zh.md) | 配置模型、安装桌面依赖、排查浏览器连接 |
| [能力矩阵](CAPABILITY_MATRIX.md) | 确认实测范围与未完成项目 |
| [账号与测试控制](NEW_ACCOUNT_TEST_CONTROL.md) | 绑定账号、查看冷却、恢复未知结果 |
| [执行进展卡](EXECUTION_PRESENTATION.md) | 理解任务卡片与状态含义 |
| [技能架构](../skills/ARCHITECTURE.md) / [贡献说明](../CONTRIBUTING.md) | 扩展工具、Skill 或界面 |
| [来源说明](PROJECT_SOURCES.md) | 查看上游与借鉴项目 |
| [曝光建议](OPEN_SOURCE_DISCOVERABILITY.md) | 修改 GitHub 展示与传播素材 |
| [本轮验证](RELEASE_VALIDATION.md) | 查看当前源码与界面的检查结果 |

## 已移出日常入口

2026-10-28 整理了四份重复阶段文档：

| 原文档 | 现行替代入口 | 本机归档 |
|---|---|---|
| HANDOFF.md | README、安装与贡献说明 | docs/_local/HANDOFF_20261028.md |
| IMPLEMENTATION_CHECKPOINT.md | 当前能力矩阵与验证记录 | docs/_local/IMPLEMENTATION_CHECKPOINT_20261028.md |
| PUBLISH_TEST_MIGRATION.md | 账号控制与技能架构 | docs/_local/PUBLISH_TEST_MIGRATION_20261028.md |
| NEW_ACCOUNT_TEST_PLAN.md | NEW_ACCOUNT_TEST_CONTROL.md | docs/_local/NEW_ACCOUNT_TEST_PLAN_20261028.md |

这些原文件从公共入口移除，历史先归档本机。旧能力矩阵、账号控制和日志展示说明也保留归档副本。`docs/_local/` 不进入 Git 或源码分发。

## 保留在本机的方法与证据

`USER_REQUIREMENTS.md`、`.uicraft.md`、`docs/CONTINUATION.md`、账号限制、真实浏览器验收和旧交付记录继续保留。它们包含个人授权、运行历史或具体账号状态，已从公共分发排除；下载仓库的人不会获得这些私人记录。

个人开发技能 `.trae/skills/` 保留本机；应用 `skills/` 随源码提供。公共能力与账号控制说明继续随源码提供，避免应用 Skill 指向被删文档。配置、任务、素材日志、会话记忆与扩展密钥仍属于本机运行资产。
