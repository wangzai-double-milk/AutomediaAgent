# GitHub 展示与曝光优化

调研：2026-10-28。读取 GitHub 官方说明与相关仓库的原始 README，以下是可执行的展示建议；不承诺改标题会带来特定浏览量或 Star 增长。

## 先明确可见性

查询时 `wangzai-double-milk/AutomediaAgent` 是 **private**。私有仓库的 Topic 搜索只对有访问权限的人可见；README 和标题优化无法让公众搜到其私有内容。公开仓库会公开代码及 Actions 历史/日志，因此本轮保持可见性，未擅自转 public。

## 本轮采用的展示

| 项目 | 修改与原因 |
|---|---|
| README 标题 | **AutoMedia Agent｜可视化小红书 AI 创作与运营工作台**：同时表达品牌、平台与用途 |
| 首屏简介 | 一句话讲“真实样本 → 内容成稿 → 受控发布”，补英文 Xiaohongshu / AI workspace 搜索词 |
| About | Local AI workspace for Xiaohongshu research, multimodal content creation, and controlled publishing. 小红书 AI 创作与运营工作台。 |
| Topics | ai-agent、xiaohongshu、local-first、content-creation、content-automation、browser-automation、multimodal、python、skills、self-hosted |
| 界面证明 | 真实 Studio 截图，分别展示工作区、灵感与进展卡；标明本地示例 |
| 上手路径 | 安装、模型配置、启动、只读检查；明确 macOS 桌面依赖 |
| 可信度 | 能力矩阵区分历史实测、代码接入与未完成验收，提供验证与来源 |
| 分享素材 | 生成1280×640的 SOCIAL_PREVIEW.png，供后续上传；未宣称已配置仓库分享预览 |

仓库名和 URL 保持不变，避免已有链接、clone 地址与品牌引用迁移。README 的产品标题承担描述用途的任务；不加没有实证的“全平台全自动”“增长神器”或 MCP 服务标签。

## 后续最值得做的事

1. 获得明确公开授权并检查历史中的配置、运行日志与隐私，再将仓库开放给公众。
2. 在仓库设置中上传[分享预览图](SOCIAL_PREVIEW.png)。GitHub 建议1280×640、文件小于1MB；私有仓库存在上传与公开分享限制。
3. 补一段30–60秒的真实演示：提出目标、查看素材、预览内容、回读结果。不要用成功动画代替平台证据。
4. 根据真实改动发布 Release 与变更说明，并补双语上手说明；有稳定托管文档后再设置 Homepage。
5. 面向实际目标用户分享具体案例和已验收能力，保留明确未完成项；本轮没有代发社区帖子或消息。

## 原始来源

- GitHub：[About READMEs](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/about-readmes)：项目用途、价值、上手、求助与维护信息。
- GitHub：[Classifying your repository with topics](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/classifying-your-repository-with-topics)：主题发现与私有可见性；最多20个 Topic。
- GitHub：[Customizing your repository's social media preview](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/customizing-your-repositorys-social-media-preview)：分享图尺寸、文件大小与公开仓库条件。
- GitHub：[Setting repository visibility](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/managing-repository-settings/setting-repository-visibility)：可见性变更影响。
- [ZJU-REAL/Easel](https://github.com/ZJU-REAL/Easel)：双语、工作流和真实界面展示。
- [lucasygu/redbook](https://github.com/lucasygu/redbook)：标题中的小红书 CLI 与可执行上手入口。
