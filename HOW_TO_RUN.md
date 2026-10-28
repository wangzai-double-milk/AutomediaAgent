# 启动速查

首次安装与模型配置见 [README](README.md#快速开始)；排障见[安装说明](docs/installation_zh.md)。

macOS / Linux，在项目根目录：

```bash
source .venv/bin/activate
python -m ga_cli studio
```

Windows：

```powershell
.\.venv\Scripts\python.exe -m ga_cli studio
```

默认打开 <http://127.0.0.1:14168>，保持终端运行，`Ctrl+C` 停止。端口已占用时：

```bash
BRIDGE_PORT=14169 python -m ga_cli studio
```

- 当前本地 Run 配置使用14169；命令本身默认14168。已有服务不会被自动终止。
- 无需 Node.js。服务重启后刷新旧标签，让页面重新连接；未被服务接收的输入和素材会保留。
- macOS 桌面操作需安装 `".[desktop]"`，授权辅助功能并保持 Chrome 登录。
- CDP 通路需加载 `assets/tmwd_cdp_bridge/`；只开启9222不足以连接。
- 文本终端：`python -m ga_cli cli`。
- 自主模式：`python -m ga_cli autonomous`，会产生真实账号动作，需先完成对应授权和账号控制。
- 发布默认 self；preview 只预填，save_draft 是写操作。后台队列默认暂停，结果未知只读核对。
- 账号限制时停止写操作，保留任务与草稿。规则见[账号与测试控制](docs/NEW_ACCOUNT_TEST_CONTROL.md)。
