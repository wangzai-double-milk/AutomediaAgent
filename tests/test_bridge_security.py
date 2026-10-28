"""Local UI endpoints must not become an arbitrary website's tool API."""

import asyncio
import base64
from pathlib import Path
from types import SimpleNamespace

import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request

from frontends import desktop_bridge as bridge


@pytest.mark.parametrize("origin,allowed", [
    ("http://127.0.0.1:14168", True),
    ("https://foreign.example", False),
    ("null", False),
])
def test_bridge_validates_origin_before_route(origin, allowed):
    calls = []
    request = make_mocked_request("POST", "/session/new", headers={
        "Host": "127.0.0.1:14168", "Origin": origin,
    })

    async def handler(request):
        calls.append(request.path)
        return web.json_response({"ok": True})

    async def run():
        try:
            response = await bridge.cors_middleware(request, handler)
        except web.HTTPException as exc:
            return exc.status
        return response.status

    status = asyncio.run(run())
    assert status == (200 if allowed else 403)
    assert calls == (["/session/new"] if allowed else [])


@pytest.mark.parametrize("item_id", ["../escape", "/tmp/escape", "..\\escape"])
def test_inline_upload_rejects_path_ids(monkeypatch, tmp_path, item_id):
    monkeypatch.setattr(bridge, "_UPLOAD_DIR", tmp_path)
    data = "data:image/png;base64," + base64.b64encode(b"test-image").decode()
    with pytest.raises((ValueError, web.HTTPBadRequest)):
        bridge._save_image_data(data, item_id)
    assert list(tmp_path.iterdir()) == []


def test_inline_upload_stays_in_upload_directory(monkeypatch, tmp_path):
    monkeypatch.setattr(bridge, "_UPLOAD_DIR", tmp_path)
    data = "data:image/png;base64," + base64.b64encode(b"test-image").decode()
    path = Path(bridge._save_image_data(data, "media-1234abcd"))
    assert path.parent == tmp_path
    assert path.read_bytes() == b"test-image"


def test_generated_artifact_is_sandboxed(monkeypatch, tmp_path):
    (tmp_path / "result.html").write_text("<script>alert(1)</script>", encoding="utf-8")
    session = SimpleNamespace(agent=SimpleNamespace(task_dir=str(tmp_path)))
    monkeypatch.setattr(bridge, "manager", SimpleNamespace(get_session=lambda sid: session))
    request = make_mocked_request(
        "GET", "/session/test/artifact?path=result.html",
        match_info={"sid": "test"},
    )
    response = asyncio.run(bridge.artifact_handler(request))
    csp = response.headers.get("Content-Security-Policy", "")
    assert "sandbox" in csp
    assert "allow-scripts" not in csp
    assert "allow-same-origin" not in csp


def test_agent_creation_does_not_change_process_cwd(monkeypatch, tmp_path):
    agent = SimpleNamespace(run=lambda: None, inc_out=False, verbose=False)
    monkeypatch.setattr(
        bridge.importlib, "import_module",
        lambda name: SimpleNamespace(GenericAgent=lambda: agent),
    )
    monkeypatch.setattr(
        bridge.threading, "Thread",
        lambda **kwargs: SimpleNamespace(start=lambda: None),
    )
    monkeypatch.setattr(
        bridge.os, "chdir",
        lambda path: (_ for _ in ()).throw(AssertionError("process cwd changed")),
    )

    manager = bridge.AgentManager()
    result = manager.make_agent(bridge.Session(id="session", cwd=str(tmp_path)))

    assert result is agent
    assert result.inc_out is True
    assert result.verbose is True
