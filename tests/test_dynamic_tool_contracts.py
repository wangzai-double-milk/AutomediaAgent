"""Dynamic tools are advertised only when their schema and implementation agree."""

import json

import pytest

from agent_loop import agent_runner_loop, exhaust
from tools.dynamic import DynamicToolManager


def _write_tool(root, directory, schema_name=None, execute=True):
    folder = root / directory
    folder.mkdir()
    if schema_name is not None:
        (folder / "tool.json").write_text(json.dumps({
            "type": "function",
            "function": {
                "name": schema_name,
                "description": "fixture",
                "parameters": {"type": "object", "properties": {}},
            },
        }), encoding="utf-8")
    if execute:
        (folder / "execute.py").write_text(
            "def execute(args, handler, response):\n    return {'status': 'ok'}\n",
            encoding="utf-8",
        )


def _write_async_tool(root, name):
    _write_tool(root, name, name)
    (root / name / "execute.py").write_text(
        "async def execute(args, handler, response):\n    return {'status': 'ok'}\n",
        encoding="utf-8",
    )


def test_dynamic_discovery_requires_matching_schema_and_execute(monkeypatch, tmp_path):
    _write_tool(tmp_path, "valid", "valid")
    _write_tool(tmp_path, "schema_only", "schema_only", execute=False)
    _write_tool(tmp_path, "wrong_name", "different")
    _write_tool(tmp_path, "xhs_unguarded", "xhs_unguarded")
    _write_async_tool(tmp_path, "async_tool")
    monkeypatch.setattr("tools.dynamic.TOOLS_DIR", str(tmp_path))

    assert [item["function"]["name"] for item in DynamicToolManager.get_schemas()] == ["valid"]
    assert DynamicToolManager.has_tool("valid") is True
    assert DynamicToolManager.has_tool("schema_only") is False
    assert DynamicToolManager.has_tool("wrong_name") is False
    assert DynamicToolManager.has_tool("xhs_unguarded") is False
    assert DynamicToolManager.has_tool("async_tool") is False


def test_dynamic_tool_cannot_shadow_builtin(monkeypatch):
    schema = {
        "type": "function",
        "function": {"name": "file_read", "parameters": {"type": "object"}},
    }
    monkeypatch.setattr(DynamicToolManager, "get_schemas", lambda: [schema])
    handler = type("Handler", (), {"max_turns": 0})()
    with pytest.raises(ValueError, match="conflict"):
        exhaust(agent_runner_loop(None, "", "", handler, [schema], max_turns=0))
