"""Local Studio supervisor and macOS user-session launch agent.

Only the child created by this process can be terminated. Health checks identify
that child by PID and a random instance ID; another listener is never reclaimed.
"""
import argparse
from collections import deque
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import plistlib
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import uuid

from .broker import try_lock

LABEL = "local.automedia.studio"
from automedia.paths import WORKSPACE_ROOT

ROOT = WORKSPACE_ROOT


def save_state(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.chmod(0o600)
    temp.replace(path)


def probe(port, instance, pid):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/runtime/health", timeout=2) as response:
            data = json.load(response)
        if data.get("instance_id") != instance or data.get("process_id") != pid:
            return "foreign_listener", data
        return data.get("scheduler", "unknown"), data
    except (OSError, ValueError):
        return "unreachable", None


def port_available(port):
    with socket.socket() as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def stop_child(child):
    if child.poll() is None:
        child.terminate()
        try:
            child.wait(timeout=15)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait(timeout=5)


def supervise(args):
    state_path = Path(args.state).resolve()
    state_path.parent.mkdir(parents=True, exist_ok=True)
    lock = try_lock("resident:" + str(state_path))
    if lock is None:
        raise SystemExit("此状态文件已有常驻进程")
    logger = logging.getLogger("automedia.resident")
    logger.setLevel(logging.INFO)
    handler = RotatingFileHandler(state_path.with_suffix(".log"), maxBytes=5 * 1024**2,
                                  backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
    logger.addHandler(handler)
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    state = {"status": "starting", "supervisor_pid": os.getpid(), "port": args.port,
             "started_at": time.time(), "restarts": 0, "events": [],
             "scope": "本次值守的进程健康记录；不证明平台在线或连续24小时"}
    try:
        old = json.loads(state_path.read_text())
        state["previous_session"] = {k: old.get(k) for k in ("started_at", "updated_at", "status", "restarts")}
    except (OSError, ValueError):
        pass

    def record(status, **fields):
        state.update(status=status, updated_at=time.time(), **fields)
        save_state(state_path, state)

    def event(kind, **fields):
        state["events"] = (state["events"] + [{"kind": kind, "at": time.time(), **fields}])[-100:]

    crashes, child = deque(), None
    try:
        while not stop.is_set():
            if not port_available(args.port):
                record("blocked", reason="端口已有监听；保留现有进程")
                return
            instance = uuid.uuid4().hex
            env = {**os.environ, "BRIDGE_HOST": "127.0.0.1", "BRIDGE_PORT": str(args.port),
                   "STUDIO_NO_BROWSER": "1", "AUTOMEDIA_SERVICE_INSTANCE": instance,
                   "AUTOMEDIA_OPERATIONS_DB": str(Path(args.database).resolve()),
                   "PYTHONUNBUFFERED": "1"}
            child = subprocess.Popen([sys.executable, "-m", "frontends.studio"], cwd=ROOT, env=env,
                                     stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                     text=True)

            def drain(process):
                for line in process.stdout:
                    logger.info("studio[%s] %s", process.pid, line.rstrip())
                process.stdout.close()

            reader = threading.Thread(target=drain, args=(child,), daemon=True)
            reader.start()
            event("child_started", pid=child.pid, instance_id=instance)
            record("starting", child_pid=child.pid, instance_id=instance, last_healthy_at=None)
            booted, failures, reason = time.monotonic(), 0, "process_exit"
            while not stop.wait(2):
                if child.poll() is not None:
                    break
                status, health = probe(args.port, instance, child.pid)
                if status == "foreign_listener":
                    reason = "foreign_listener"
                    break
                if status == "running":
                    if not state.get("last_healthy_at"):
                        print(f"AutoMedia Studio: http://127.0.0.1:{args.port}/", flush=True)
                    failures = 0
                    record("running", last_healthy_at=time.time(), health=health)
                elif time.monotonic() - booted > 30:
                    failures += 1
                    record("degraded", reason=status)
                    if failures >= 3:
                        reason = "health_timeout"
                        break
            stop_child(child)
            reader.join(timeout=2)
            event("child_stopped", pid=child.pid, returncode=child.returncode, reason=reason)
            if stop.is_set():
                break
            if reason == "foreign_listener":
                record("blocked", reason="健康接口属于其他进程；未操作该进程")
                return
            crashes.append(time.monotonic())
            while crashes and crashes[0] < time.monotonic() - 300:
                crashes.popleft()
            if len(crashes) >= 5:
                record("blocked", reason="五分钟内恢复失败五次；保留日志，等待修复")
                return
            state["restarts"] += 1
            record("recovering", reason=reason)
            if stop.wait(min(2 ** len(crashes), 30)):
                break
        record("stopped")
    finally:
        if child is not None:
            stop_child(child)
        lock.close()
        handler.close()
        logger.removeHandler(handler)


def launch_path():
    return Path.home() / "Library/LaunchAgents" / (LABEL + ".plist")


def install(args):
    if sys.platform != "darwin":
        raise SystemExit("此安装入口仅支持macOS用户登录会话")
    if not port_available(args.port):
        raise SystemExit("端口已占用；先正常停止属于本项目的旧服务")
    target = launch_path()
    command = [sys.executable, "-m", "operations.resident", "serve", "--port", str(args.port),
               "--database", str(Path(args.database).resolve()), "--state", str(Path(args.state).resolve())]
    config = {"Label": LABEL, "ProgramArguments": command, "WorkingDirectory": str(ROOT),
              "RunAtLoad": True, "KeepAlive": {"SuccessfulExit": False}, "ThrottleInterval": 30,
              "EnvironmentVariables": {"PATH": os.environ.get("PATH", os.defpath)},
              "ProcessType": "Background", "ExitTimeOut": 25}
    if target.exists() and plistlib.loads(target.read_bytes()) != config:
        raise SystemExit("已有不同常驻配置；未覆盖，请先检查 " + str(target))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(plistlib.dumps(config))
    target.chmod(0o600)
    subprocess.run(["launchctl", "enable", f"gui/{os.getuid()}/{LABEL}"], check=True)
    subprocess.run(["launchctl", "bootstrap", f"gui/{os.getuid()}", str(target)], check=True)
    print(json.dumps({"installed": str(target), "port": args.port, "state": args.state}, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["start", "serve", "install", "status", "stop"])
    parser.add_argument("--port", type=int, default=14170)
    parser.add_argument("--database", default=str(ROOT / "memory/operations.sqlite3"))
    parser.add_argument("--state", default=str(ROOT / "memory/resident.json"))
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("端口必须在1024到65535之间")
    if args.action == "start":
        if not port_available(args.port):
            import psutil
            try:
                state = json.loads(Path(args.state).read_text())
                process = psutil.Process(state["child_pid"])
                status, _ = probe(args.port, state["instance_id"], process.pid)
                if (status != "running" or Path(process.cwd()).resolve() != ROOT
                        or process.cmdline()[-2:] != ["-m", "frontends.studio"]
                        or Path(process.environ().get("AUTOMEDIA_OPERATIONS_DB", "")).resolve()
                        != Path(args.database).resolve()):
                    raise ValueError()
            except (OSError, ValueError, KeyError, psutil.Error):
                raise SystemExit("端口已占用但不能确认属于本项目；未启动或停止任何进程")
            print(f"AutoMedia Studio: http://127.0.0.1:{args.port}/")
        else:
            supervise(args)
    elif args.action == "serve":
        supervise(args)
    elif args.action == "install":
        install(args)
    elif args.action == "status":
        print(Path(args.state).read_text(encoding="utf-8"))
    else:
        target = launch_path()
        config = plistlib.loads(target.read_bytes())
        if config.get("WorkingDirectory") != str(ROOT) or config.get("Label") != LABEL:
            raise SystemExit("配置不属于当前项目，未停止")
        subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}/{LABEL}"], check=True)


if __name__ == "__main__":
    main()
