"""Launch the AutoMedia Studio bridge and open its web interface."""

import os
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser

from aiohttp import web

from desktop_bridge import create_app


def _open_when_ready(url: str) -> None:
    for _ in range(80):
        try:
            urllib.request.urlopen(url + "status", timeout=0.5).read()
            webbrowser.open(url)
            return
        except Exception:
            time.sleep(0.25)


def _port_is_free(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
            return True
        except OSError:
            return False


def _listeners_on_port(port: int) -> list[int]:
    """Return PIDs listening on the given TCP port (best effort, macOS/Linux)."""
    try:
        out = subprocess.run(
            ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"],
            capture_output=True, text=True, timeout=5,
        ).stdout
    except Exception:
        return []
    pids = []
    for line in out.split():
        line = line.strip()
        if line.isdigit() and int(line) != os.getpid():
            pids.append(int(line))
    return pids


def _is_stale_studio(pid: int) -> bool:
    """True if the process looks like a previous Studio/bridge instance of ours."""
    try:
        cmd = subprocess.run(
            ["ps", "-o", "command=", "-p", str(pid)],
            capture_output=True, text=True, timeout=5,
        ).stdout
    except Exception:
        return False
    return "studio.py" in cmd or "desktop_bridge" in cmd


def _reclaim_port(host: str, port: int) -> bool:
    """Free the port if it is held by a stale Studio instance. Returns True if usable."""
    if _port_is_free(host, port):
        return True
    pids = _listeners_on_port(port)
    stale = [pid for pid in pids if _is_stale_studio(pid)]
    if not stale:
        others = ", ".join(str(p) for p in pids) or "未知进程"
        print(
            f"⚠️  端口 {port} 已被占用（进程 {others}），且不是遗留的 Studio 实例。\n"
            f"    请先关闭占用该端口的程序，或设置 BRIDGE_PORT 换一个端口后重试。"
        )
        return False
    for pid in stale:
        print(f"♻️  检测到遗留的 Studio 进程（PID {pid}），正在自动清理…")
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            continue
    for _ in range(20):
        if _port_is_free(host, port):
            return True
        time.sleep(0.2)
    for pid in stale:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            continue
    for _ in range(15):
        if _port_is_free(host, port):
            return True
        time.sleep(0.2)
    print(f"⚠️  已尝试清理但端口 {port} 仍被占用，请手动处理后重试。")
    return False


def main() -> None:
    host = os.environ.get("BRIDGE_HOST", "127.0.0.1")
    port = int(os.environ.get("BRIDGE_PORT", "14168"))
    public_host = "127.0.0.1" if host in ("0.0.0.0", "::") else host
    url = f"http://{public_host}:{port}/"
    if not _reclaim_port(host, port):
        sys.exit(1)
    threading.Thread(target=_open_when_ready, args=(url,), daemon=True).start()
    print(f"AutoMedia Studio: {url}")
    web.run_app(create_app(), host=host, port=port, print=None)


if __name__ == "__main__":
    main()
