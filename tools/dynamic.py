"""Validated discovery and dispatch for optional dynamic tools."""

import ast
import importlib.util
import json
import os
import sys


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS_DIR = os.path.join(PROJECT_ROOT, "tools")
RESERVED_PREFIXES = ("web_", "xhs_", "douyin_")


class DynamicToolManager:
    @staticmethod
    def _discover():
        discovered = {}
        for folder_name in os.listdir(TOOLS_DIR):
            folder_path = os.path.join(TOOLS_DIR, folder_name)
            schema_path = os.path.join(folder_path, "tool.json")
            execute_path = os.path.join(folder_path, "execute.py")
            if not (os.path.isfile(schema_path) and os.path.isfile(execute_path)):
                continue
            try:
                with open(schema_path, encoding="utf-8") as source:
                    schema = json.load(source)
                name = (schema.get("function") or {}).get("name")
                if name != folder_name:
                    raise ValueError(
                        f"schema name {name!r} must match directory {folder_name!r}")
                if name.startswith(RESERVED_PREFIXES):
                    raise ValueError(
                        f"{RESERVED_PREFIXES} prefixes are reserved for built-in guarded tools")
                with open(execute_path, encoding="utf-8") as source:
                    tree = ast.parse(source.read(), filename=execute_path)
                if not any(
                    isinstance(node, ast.FunctionDef)
                    and node.name == "execute"
                    for node in tree.body
                ):
                    raise ValueError("execute.py must export synchronous execute")
                discovered[name] = (schema, execute_path)
            except Exception as exc:
                sys.stderr.write(
                    f"[DynamicToolManager] Ignoring {folder_name}: {exc}\n")
        return discovered

    @classmethod
    def get_schemas(cls):
        return [schema for schema, _ in cls._discover().values()]

    @classmethod
    def has_tool(cls, name):
        return name in cls._discover()

    @classmethod
    def execute(cls, name, args, handler, response):
        from agent_loop import StepOutcome, try_call_generator

        entry = cls._discover().get(name)
        if entry is None:
            return StepOutcome(
                {"status": "error", "msg": f"Dynamic tool {name} is unavailable"},
                next_prompt=f"Tool {name} is unavailable.",
            )
        _, execute_path = entry
        try:
            spec = importlib.util.spec_from_file_location(
                f"dynamic_tool_{name}", execute_path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return (yield from try_call_generator(
                module.execute, args, handler, response))
        except Exception as exc:
            yield f"Tool {name} execution failed: {exc}\n"
            return StepOutcome(
                {"status": "error", "msg": str(exc)},
                next_prompt=f"Tool {name} failed.",
            )
