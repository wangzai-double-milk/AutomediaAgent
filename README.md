# � 全模态社交媒体自动运营 Agent · AutoMedia
### 首发：小红书 (Xiaohongshu) | 架构可扩展至 抖音 / B站 / 视频号 / 微博 / Twitter / Instagram

[![GitHub Repo](https://img.shields.io/badge/GitHub-AutomediaAgent-blue?logo=github)](https://github.com/wangzai-double-milk/AutomediaAgent)
[![Python](https://img.shields.io/badge/Python-3.10%2B-blue?logo=python)](https://www.python.org/)
[![Powered by Doubao](https://img.shields.io/badge/Powered%20by-%E8%B1%86%E5%8C%85%20VLM-FF6B6B)](https://www.volcengine.com/)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Streamlit](https://img.shields.io/badge/UI-Streamlit-FF4B4B?logo=streamlit)](https://streamlit.io/)

> **它不仅是一个自动化发帖机器人，更是一个拥有"清晰职业边界感"和"全模态认知"的拟人化数字员工。**
> 基于豆包全模态大模型（VLM）打造，具备**自主冲浪、爆款拆解、经验沉淀、实战演练**的完整人类学习闭环。
>
> 🎯 **不止小红书** —— 我们用动静分离的 SkillHub 架构，把"平台交互"与"运营策略"解耦。换一个平台，只需新增一份 `hard_skills/`，**`soft_skills/` 的爆款审美沉淀完全复用**。一个 Agent，孵化全网账号矩阵。

## 💎 一个 Key，省下一整年的 SaaS 订阅费

> **别再为 ChatGPT Plus + Midjourney + Sora + Runway 同时掏钱了。**
> 🚀 **All-in-One 极简接入** ——只需一把火山引擎 API Key，即刻解锁国产顶级模型全家桶：
> - 🧠 **Doubao-pro-32k**：媲美 GPT-4o 的语言理解，**输入价仅 ¥0.8 / 百万 tokens**，比 GPT-4o 便宜 **30 倍+**
> - 👁️ **Doubao-Seed-2.0-pro 全模态**：原生看图+看视频，告别为单独的视觉模型再开一份订阅
> - 🎨 **Seedream 4.5**：国产 SOTA 文生图，分分钟产出可商用首图，秒杀 MJ 月费 30 美刀
> - 🎬 **Seedance 2.0**：原生视频生成 + 视频理解，同价位国内独一档
>
> 💰 **算笔账**：传统方案 GPT-4o + Midjourney + Sora + Pika ≈ **$80~150/月**，本方案跑满一个月 **不到 30 元**。
> 🪄 **省心程度**：再也不用切 4 个平台、对 4 套 API、维护 4 套 Key —— **一行配置，全模态打通**。

---

## ✨ 30 秒看懂它在做什么

```
🌊 自主冲浪刷信息流  →  👁️ 全模态分析爆款  →  🧠 对比已有经验沉淀新 SOP  →  ⚔️ 实战演练发草稿验收
        ↑                                                                                  ↓
        └────────────────────────  循环进化，越用越懂你的赛道（小红书 / 抖音 / B站 ...）  ────────────────────────┘
```

它不是一个"按 Q&A 反应"的机器人，而是一个**会自己找事做、自己学、自己练**的数字运营员工。

---

## 🔥 五大核心黑科技：为什么与众不同？

传统 Agent 往往"学得越多错得越多"，或者只能理解纯文本，导致生成的经验犹如空中楼阁。本项目通过以下核心机制，彻底重构了 Agent 的认知模型：

### 👁️ 1. 打破模态壁垒：全模态联合视觉认知
彻底告别"凭标题脑补画面"。Agent 在刷帖时，能同时摄取**多张图片、视频关键帧、标题和文案**作为一个整体发送给 VLM，进行深度"图文联合分析"。
*它能精准拆解出："首图6宫格如何排版"、"文案如何配合首图吸引减脂人群"、"HSL色调参数是多少"，真正像一个新媒体运营一样看懂爆款。*

> 💡 **独家加成**：内置 `moviepy` 智能视频压缩兜底——超过 50MB 的视频会自动降为 720P/15fps 重传分析，**告别"安装 ffmpeg"的烦恼**，开箱即用。

### 🧠 2. 告别记忆爆炸：后置认知去重 & 知识融合
传统 Agent 的记忆文件会随着运行时间无限膨胀，最终导致大模型上下文混乱。
本项目的 Agent 在看完爆款帖子后，会**先读取本地已有的 SOP 经验**作为 Baseline，通过大模型的对比能力，自动判断是否有新知识：
- ✅ **有新知识**：将新闪光点完美融入旧知识，重构排版后覆写更新。
- 🚮 **无新知识**：直接丢弃，避免冗余。

*让技能树永远保持精简、高效，持续自我进化！*

### 🛡️ 3. 拒绝代码被篡改：动静分离的"技能树"架构（跨平台复用的关键）
为解决 Agent 乱改代码的问题，我们设计了一套具有"职业边界感"的 SkillHub 物理隔离架构：
- 🧱 **`hard_skills/` (静态硬技能)**：底层发帖、点赞、搜索等与网页交互的自动化代码逻辑。**绝对不可篡改**，只有当平台 UI 更新时才进行修复。
- 🧬 **`soft_skills/` (动态软技能)**：修图参数、排版美学、文案钩子等运营策略。在冲浪时不断吸收爆款经验，**持续自我迭代**。

*Agent 拥有"边界感"——遇到新领域时只能在 `soft_skills/` 内创建新文件夹，绝不会污染你的核心代码！*

> 🌐 **跨平台复用魔法**：想接入抖音 / B站 / Twitter？只需新写一份对应平台的 `hard_skills/`（DOM 选择器和接口），**`soft_skills/` 中沉淀的爆款审美、文案钩子、修图参数完全无需重写**。一份运营经验，全网账号矩阵共享。

### ⚔️ 4. 纸上得来终觉浅：学以致用的实战演练
学完新技能（如全新的二次元修图参数）后，Agent 会自动寻找本地旧图，应用刚学到的参数和文案风格，在网页端自动发布一篇"仅自己可见"的真实笔记供你验收。你可以在手机端直观地看到它的学习成果！

### 🎭 5. 沉浸式互动养号与防风控
基于 **CDP（Chrome DevTools Protocol）** 底层接管真实浏览器，穿透 Shadow DOM 真实模拟鼠标点击与键盘输入（如物理敲击键盘唤出话题 Tag 下拉框）。通过 `.env` 抽离配置化随机延迟与滑动频率，在冲浪时真实模拟点赞、收藏，彻底告别死板的僵尸号。

---

## 🆚 与其他方案的对比

| 维度 | 传统脚本 | 单 GPT-4V 调用 | 通用 Agent (Manus/Codex) | **本项目 🌟** |
| --- | --- | --- | --- | --- |
| 视觉理解 | ❌ 无 | ✅ 单图 | ⚠️ 部分支持 | ✅ **多图+视频帧+文案联合分析** |
| 视频原生分析 | ❌ | ❌ | ❌ | ✅ **Seedance 原生视频理解** |
| 经验沉淀 | ❌ | ❌ 上下文丢失 | ⚠️ 容易爆炸 | ✅ **Baseline 对比去重** |
| 防代码污染 | - | - | ❌ 经常乱改 | ✅ **动静分离架构** |
| 真实浏览器 | ⚠️ Selenium 易被检测 | ❌ | ⚠️ | ✅ **CDP 接管真实浏览器** |
| 成本 | 低但维护贵 | 💰💰💰 | 💰💰💰 | ✅ **一个豆包 Key 全家桶** |
| 可视化界面 | ❌ | ❌ | ⚠️ | ✅ **Streamlit 一键启动** |

---

## 🎯 适用场景

- 🍜 **美食探店博主**：自动学习同赛道爆款的摆盘构图、滤镜参数、标题钩子
- 👗 **穿搭达人**：拆解爆款 OOTD 的色调、姿势、文案套路
- ✈️ **旅行/民宿运营**：定向学习景点笔记的封面排版与情绪文案
- 🛍️ **种草/带货团队**：批量生成符合赛道审美的笔记草稿，人工二审一键发布
- 🎓 **MCN 机构**：让一个 Agent 同时孵化 N 个垂直号，每号有独立的 `soft_skills/` 知识库

---

## 📂 项目架构

```text
xiaohongshu-agent/
├── agentmain.py             # Agent 主入口
├── ga.py                    # 底层执行网关，封装 CDP 操作和工具路由
├── llmcore.py               # 大模型通信层（对接豆包全家桶）
├── .env                     # 环境与防风控配置文件
├── reflect/
│   └── autonomous.py        # 核心"大脑" Prompt：定义 Agent 的学习目标和冲浪纪律
├── skills/                  # 🌟 SkillHub 技能目录
│   ├── hard_skills/         # 🧱 基础能力（不可篡改，如 xhs_publish 网页交互逻辑）
│   └── soft_skills/         # 🧬 运营策略（自我进化，如 xhs_edit 修图文案策略）
├── plugins/                 # 🔌 原子工具插件目录 (支持动态扫描注册)
│   ├── vlm_analyze.json     # 全模态分析工具 Schema
│   └── ...
├── tools/
│   ├── image.py             # 视觉模型及图像处理底层 API（含 moviepy 视频压缩）
│   └── xhs_actions.py       # 小红书特定 CDP 交互动作
├── frontends/
│   └── stapp.py             # Streamlit 可视化前端
└── assets/                  # 静态资源及默认 Tools Schema
```

---

## 🚀 三分钟快速开始

### 1. 环境准备
确保你已安装 Python 3.10+ 和 Chrome 浏览器。
```bash
# 1) 创建虚拟环境 (仅首次需要)
python3 -m venv .venv

# 2) 激活虚拟环境 (每次打开新终端都需要执行)
# Mac/Linux:
source .venv/bin/activate
# Windows:
# .venv\Scripts\activate

# 3) 一键安装所有依赖 (包括可视化界面和视频处理模块)
pip install .
```

### 2. 配置环境变量
```bash
cp .env.example .env
```
在 `.env` 中按需修改配置，灵活调整 Agent 的"手速"以规避风控：
- `OPENAI_API_KEY`: 你的豆包大模型 API Key（[火山引擎控制台](https://www.volcengine.com/) 一键获取）
- `INTERVAL`: 后台学习冲浪的触发频率（秒）
- `ACTION_DELAY_MIN` / `MAX`: 模拟人类操作的随机停顿范围

### 3. 登录并开启 CDP
1. 关闭所有正在运行的 Chrome 实例。
2. 通过命令行启动一个带有远程调试端口的 Chrome，并登录小红书网页版 (`https://www.xiaohongshu.com`)。
   - **Mac**: `/Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome --remote-debugging-port=9222`
   - **Windows**: `chrome.exe --remote-debugging-port=9222`

### 4. 启动 Agent

#### ✅ 方式 A：可视化界面（推荐）
不想看终端黑框框？项目内置了清爽的 Web 可视化界面！
```bash
streamlit run frontends/stapp.py
```
*在浏览器弹出页面的左侧栏点击"开始空闲自主行动"，即可观看它沉浸式冲浪学习。*

#### 🖥️ 方式 B：纯终端后台运行
如果你不需要界面，只想让它在后台静默打工：
```bash
python3 agentmain.py --reflect reflect/autonomous.py
```

---

## 🛠️ 进阶：插件化扩展新工具

得益于**插件化动态注册机制**，为 Agent 增加新能力（例如：自动回复私信、自动评论互动）无需修改核心底座代码，只需两步：
1. 在 `plugins/` 目录下新建 `.py` 文件编写底层逻辑。
2. 在 `plugins/` 目录下新建同名的 `.json` 文件描述工具大模型参数。

系统下次启动时会自动扫描合并并喂给大模型，**避免了多人协作时的代码冲突**！

---

## 🗺️ Roadmap

- [x] 全模态联合视觉认知（图+视频帧+文案）
- [x] 后置认知去重 & 知识融合
- [x] 动静分离 SkillHub 架构（跨平台可扩展）
- [x] CDP 真实浏览器接管 + 防风控
- [x] Streamlit 可视化界面
- [x] 小红书首发支持
- [ ] 抖音 适配（沿用现有 soft_skills/ 经验库）
- [ ] B站 / 视频号 适配
- [ ] 微博 / Twitter / Instagram 海外赛道适配
- [ ] 多账号矩阵孵化（每号独立 `soft_skills/`）
- [ ] 评论区智能互动 & 私信自动回复
- [ ] 数据看板：曝光/点赞/转化数据回流，反哺 Agent 自调优

---

## 🤝 贡献与社区

欢迎 Star ⭐ / Fork / PR！
- 🐛 提 Issue 反馈 Bug 或建议新功能
- 💡 分享你的 `soft_skills/` 配置，帮助社区共建赛道知识库
- 📮 商业合作 / MCN 接入：欢迎联系仓库 Owner

---

## 🙏 致谢

- [火山引擎 - 豆包大模型](https://www.volcengine.com/)：提供 Doubao VLM、Seedream 4.5、Seedance 2.0 全套能力
- [Streamlit](https://streamlit.io/)：极简 Web UI 框架
- [MoviePy](https://zulko.github.io/moviepy/)：让视频压缩告别 ffmpeg

## 📄 License

[MIT](LICENSE) — 自由商用，欢迎二次开发。

---
*Powered by Doubao VLM & Seedream 4.5 & Seedance 2.0*
