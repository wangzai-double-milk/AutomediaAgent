# 启动速查

首次安装、模型配置、Chrome插件与macOS权限请按[README](README.md#快速开始)执行。

```bash
source .venv/bin/activate
python -m ga_cli studio
```

默认地址<http://127.0.0.1:14168/>，保持终端运行，`Ctrl+C`停止。

```bash
BRIDGE_PORT=14170 python -m ga_cli studio
python -m ga_cli cli
python -m operations.resident start --port 14170
```

Windows使用`.venv\Scripts\python.exe`代替`python`。平台桌面操作以macOS为主要验收环境。
CDP通路需要仓库自带Chrome桥接扩展，加载前先初始化Agent生成本机`config.js`。
不要同时启动普通Studio与监督进程占用同一端口或数据库。

编辑人设/规则后：

```bash
python -m automedia.configuration check
python -m automedia.configuration apply
```

先只读核对登录与页面，再授权具体写操作。后台队列默认暂停，发布仅自己可见，
未知提交先回读，不重新发送。`autonomous`命令会触发真实平台动作，不属于普通启动。
