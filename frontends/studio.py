"""Launch the AutoMedia Studio bridge and open its web interface."""

import os
import socket
import sys
import threading
import time
import urllib.request
import webbrowser

from aiohttp import web
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))

try:
    from .desktop_bridge import create_app, studio_ui_revision
except ImportError:
    from desktop_bridge import create_app, studio_ui_revision


def _open_when_ready(url: str) -> None:
    for _ in range(80):
        try:
            urllib.request.urlopen(url + "status", timeout=0.5).read()
            webbrowser.open(url + "?ui=" + studio_ui_revision())
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


def _reclaim_port(host: str, port: int) -> bool:
    """Never terminate another process just because its command resembles Studio."""
    if _port_is_free(host, port):
        return True
    print(f"端口 {port} 已被占用。请使用已有窗口，或设置 BRIDGE_PORT 更换端口。")
    return False


def main() -> None:
    host = os.environ.get("BRIDGE_HOST", "127.0.0.1")
    if host not in ("127.0.0.1", "localhost", "::1"):
        raise SystemExit("Studio 仅支持本机访问；请使用 127.0.0.1，不要暴露到公网。")
    port = int(os.environ.get("BRIDGE_PORT", "14168"))
    public_host = "[::1]" if host == "::1" else host
    url = f"http://{public_host}:{port}/"
    if not _reclaim_port(host, port):
        sys.exit(1)
    if os.environ.get("STUDIO_NO_BROWSER") != "1":
        threading.Thread(target=_open_when_ready, args=(url,), daemon=True).start()
    print(f"AutoMedia Studio: {url}")
    web.run_app(create_app(), host=host, port=port, print=None)


if __name__ == "__main__":
    main()
