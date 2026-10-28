# 上游与借鉴来源

记录查阅到的原始仓库与采纳方向。外部项目声明不等于 AutoMedia 已实测通过，当前证据以[能力矩阵](CAPABILITY_MATRIX.md)为准。

| 来源 | 采纳方向与取舍 |
|---|---|
| [lsdefine/GenericAgent](https://github.com/lsdefine/GenericAgent) · MIT | 基础 Agent 框架；保留现有模型协议、流式、工具闸门与 Studio，按模块增量改造 |
| [xpzouying/xiaohongshu-mcp](https://github.com/xpzouying/xiaohongshu-mcp) · Apache-2.0 | 平台状态读取、视频与定时发布的流程参考；不引入完整服务 |
| [white0dew/XiaohongshuSkills](https://github.com/white0dew/XiaohongshuSkills) · MIT | 发布字段、账号区分、浏览器运行锁与核对方法 |
| [dreammis/social-auto-upload](https://github.com/dreammis/social-auto-upload) | 上传与发布分层、定时参数；不引入整套多平台依赖 |
| [Xiangyu-CAS/xiaohongshu-ops-skill](https://github.com/Xiangyu-CAS/xiaohongshu-ops-skill) | 选题假设、账号定位、证据与复盘方法 |
| [browser-use/browser-use](https://github.com/browser-use/browser-use) · MIT | 独立浏览器适配的候选，未替换现有有效通路 |
| [pablodelucca/pixel-agents](https://github.com/pablodelucca/pixel-agents) | 状态角色与可交互工作站的设计参考 |
| [toeverything/AFFiNE](https://github.com/toeverything/AFFiNE) | 工作区与个人便笺体验参考 |
| [xyflow/xyflow](https://github.com/xyflow/xyflow) | 任务空间与交互节点的设计参考 |

Studio 趣味界面使用原生 HTML/CSS/JavaScript 实现，没有移植以上前端框架。外部代码的复制或改编须保留原许可证与归属，不能因本仓库 MIT 许可覆盖来源条款。

README 展示参考 [ZJU-REAL/Easel](https://github.com/ZJU-REAL/Easel) 的工作流与真实界面表达，以及 [lucasygu/redbook](https://github.com/lucasygu/redbook) 的明确平台关键词与快上手入口。没有用星标数量证明设计选择会带来固定增长。
