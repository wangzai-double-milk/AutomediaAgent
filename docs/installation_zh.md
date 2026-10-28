# AutoMedia 安装与排障

完整命令统一维护在 [README 的快速开始](../README.md#快速开始)，日常启动见 [HOW_TO_RUN](../HOW_TO_RUN.md)。本仓库不是上游 GenericAgent，旧版远程一键安装器、EXE/DMG 说明不适用于本次源码交付。

## 环境

- Python 3.10–3.13，推荐 3.12；在独立 `.venv` 中安装。
- `python -m pip install -e .` 即可启动 Studio，无需 Node.js、Streamlit 或 Qt。
- macOS 需要小红书桌面操作时安装 `python -m pip install -e ".[desktop]"`，
  包含 Pillow、Cocoa 与 Quartz 适配；桌面通路是主要真实验收环境。
- 超过 512MB 的本地视频需要自动压缩代理时，额外安装 `python -m pip install -e ".[media]"`。
- Windows 可直接调用 `.venv\Scripts\python.exe`，无需修改 PowerShell 执行策略。

## 配置

复制 `config.example.json` 为 `config.json`，填入接收方自己的 Key 和已开通的模型。已有文件不要覆盖。`mykey.py` 是适配器，不应改为真实密钥文件。

自定义位置：

```bash
export AUTOMEDIA_CONFIG="$HOME/.config/automedia/config.json"
python -m ga_cli studio
```

也可直接设置 `VOLC_API_KEY`、`VOLC_API_BASE` 和 `VOLC_LLM_MODEL` / `VOLC_VLM_MODEL` / `VOLC_GEN_MODEL` / `VOLC_EDIT_MODEL`。环境变量覆盖配置文件同名含义的字段。不要把 Key 放到命令记录截图或聊天中。

## 浏览器

macOS 桌面通路使用现有 Chrome 的系统辅助功能，不要求 CDP 扩展。
为实际运行 Python/终端的应用授权系统辅助功能，保持正常已登录 Chrome，
先做只读登录与账号检查，再按当前实例授权绑定稳定账号 ID。
`transport=auto` 在扩展已连接时走 CDP，否则使用桌面通路；也可明确指定。
验证码、平台限制和权限申请由用户在平台正常处理。

需要 CDP 通路时：

启动工作台并初始化普通对话后，加载 `assets/tmwd_cdp_bridge/` 为 Chrome 已解压扩展。首次运行会生成该目录的 `config.js`。这是本机生成文件，不能从他人环境复制。

连接链路：

```text
Studio -> Python tools -> TMWD (18765/18766) -> Chrome extension -> active page
```

出现“没有标签页”时检查扩展是否安装在当前 Chrome 用户配置、是否启用、是否打开普通 HTTP(S) 网页。只开启9222调试端口不足以建立这条桥接链路。
扩展页、空白页并不能替代平台页面。Windows/Linux 可以运行核心与工作台，但平台操作尚未完成与 macOS 同等的真实验收。

## 服务端口

默认 `127.0.0.1:14168`，只允许本机访问。不支持直接将 `BRIDGE_HOST` 设为 `0.0.0.0` 对外开放。

```bash
BRIDGE_PORT=14169 python -m ga_cli studio
```

已有服务不会被自动终止。服务重启会丢失进程内会话，停止前确认没有正在执行的任务。

升级后页面会使用当前界面文件的版本地址，HTML 不缓存，CSS/JS 使用内容指纹。
如果已有旧标签仍停留在旧布局，刷新该标签；确认地址是当前运行端口。
旧进程可能加载新磁盘页面却保留旧路由，出现资源404时应按进程所属与任务状态处理，
不要把“端口仍在监听”当成新版服务健康。

## 升级

先备份自己的配置、技能和成果；检查工作区改动，再进行合并：

```bash
git status
git pull --ff-only
python -m pip install -e .
```

有冲突时人工处理，不要通过 `reset --hard` 丢弃本地素材或配置。

## 验证

```bash
python -m pip install -e ".[dev]"
python -m pytest -q
python scripts/check_skill_graph.py
```

离线测试不要求真实密钥，不触碰平台账号。实际使用前还需验证模型 API、浏览器连接及只读搜索。API 成功不代表平台发布成功，发布结果未确认时应停止并核对创作中心。
