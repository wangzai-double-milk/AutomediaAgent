#!/usr/bin/env python3
"""Validate the one-way Skill dependency graph."""

import ast
import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILLS_ROOT = ROOT / "skills"


def validate(registry_path=SKILLS_ROOT / "registry.json"):
    registry_path = Path(registry_path)
    skills_root = registry_path.parent.resolve()
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    skills = registry["skills"]
    rules = registry["rules"]
    errors = []

    allowed = {
        "router": set(rules["router_can_depend_on"]),
        "workflow": set(rules["workflow_can_depend_on"]),
        "capability": set(rules["capability_can_depend_on"]),
        "resource": set(rules["resource_can_depend_on"]),
    }
    registered_paths = set()
    for name, spec in skills.items():
        path = (skills_root / spec["path"]).resolve()
        if not path.is_relative_to(skills_root):
            errors.append(f"{name}: path escapes skills directory")
            continue
        registered_paths.add(path)
        if not path.is_file() and not spec.get("optional"):
            errors.append(f"{name}: missing {spec['path']}")
        elif path.is_file():
            match = re.search(r"^name:\s*['\"]?([^'\"\n]+)", path.read_text(encoding="utf-8"), re.M)
            if not match or match.group(1).strip() != name:
                errors.append(f"{name}: frontmatter name does not match registry")
        if spec.get("layer") not in allowed:
            errors.append(f"{name}: invalid layer {spec.get('layer')}")
            continue
        if spec["layer"] != "router" and spec.get("routable"):
            errors.append(f"{name}: only router skills may be routable")
        for dependency in spec.get("depends_on", []):
            target = skills.get(dependency)
            if not target:
                errors.append(f"{name}: unknown dependency {dependency}")
                continue
            if target["layer"] not in allowed[spec["layer"]]:
                errors.append(
                    f"{name}: {spec['layer']} cannot depend on "
                    f"{dependency} ({target['layer']})"
                )
    for group in ("hard_skills", "soft_skills"):
        for path in (skills_root / group).glob("*/SKILL.md"):
            if path.resolve() not in registered_paths:
                errors.append(f"unregistered skill: {path.relative_to(skills_root)}")

    schema_path = ROOT / "assets/tools_schema.json"
    schemas = json.loads(schema_path.read_text(encoding="utf-8"))
    tool_names = [(item.get("function") or {}).get("name") for item in schemas]
    if invalid_names := [name for name in tool_names if not isinstance(name, str) or not name]:
        errors.append(f"invalid tool schema names: {invalid_names}")
    duplicates = sorted(
        {name for name in tool_names if isinstance(name, str) and tool_names.count(name) > 1})
    if duplicates:
        errors.append(f"duplicate tool schemas: {duplicates}")

    source = ast.parse((ROOT / "automedia/legacy/ga.py").read_text(encoding="utf-8"))
    handlers = {
        node.name[3:]
        for cls in source.body if isinstance(cls, ast.ClassDef)
        and cls.name == "GenericAgentHandler"
        for node in cls.body if isinstance(node, ast.FunctionDef)
        and node.name.startswith("do_")
    }
    schema_names = set(tool_names)
    if missing_handlers := sorted(schema_names - handlers):
        errors.append(f"tool schemas without handlers: {missing_handlers}")
    if missing_schemas := sorted(handlers - schema_names - {"no_tool"}):
        errors.append(f"tool handlers without schemas: {missing_schemas}")

    for tool_name, skill_name in (registry.get("tool_skill_gates") or {}).items():
        if tool_name not in schema_names:
            errors.append(f"tool gate references unknown tool: {tool_name}")
        if skill_name not in skills:
            errors.append(f"tool gate references unknown skill: {skill_name}")
        elif skills[skill_name]["layer"] != "capability":
            errors.append(f"tool gate must reference a capability: {tool_name} -> {skill_name}")

    for skill_path in registered_paths:
        if not skill_path.is_file():
            continue
        text = skill_path.read_text(encoding="utf-8")
        for target in re.findall(r"\[[^\]]+\]\(([^)]+)\)", text):
            if "://" in target or target.startswith("#"):
                continue
            resolved = (skill_path.parent / target.split("#", 1)[0]).resolve()
            if not resolved.exists():
                errors.append(
                    f"{skill_path.relative_to(skills_root)}: broken link {target}")

    dynamic_names = set()
    tools_root = ROOT / "tools"
    for directory in (path for path in tools_root.iterdir() if path.is_dir()):
        schema_file, execute_file = directory / "tool.json", directory / "execute.py"
        if not (schema_file.exists() or execute_file.exists()):
            continue
        if not (schema_file.is_file() and execute_file.is_file()):
            errors.append(f"dynamic tool {directory.name}: tool.json and execute.py are both required")
            continue
        schema = json.loads(schema_file.read_text(encoding="utf-8"))
        name = (schema.get("function") or {}).get("name")
        if name != directory.name:
            errors.append(f"dynamic tool {directory.name}: schema name mismatch ({name})")
        if isinstance(name, str) and name.startswith(("web_", "xhs_")):
            errors.append(f"dynamic tool {directory.name}: reserved platform prefix")
        if name in schema_names or name in dynamic_names:
            errors.append(f"duplicate tool name: {name}")
        tree = ast.parse(execute_file.read_text(encoding="utf-8"))
        if not any(
            isinstance(node, ast.FunctionDef)
            and node.name == "execute"
            for node in tree.body
        ):
            errors.append(
                f"dynamic tool {directory.name}: execute.py must export synchronous execute")
        dynamic_names.add(name)

    visiting, visited = set(), set()

    def visit(name, chain):
        if name in visiting:
            errors.append("cycle: " + " -> ".join(chain + [name]))
            return
        if name in visited:
            return
        visiting.add(name)
        for dependency in skills[name].get("depends_on", []):
            if dependency in skills:
                visit(dependency, chain + [name])
        visiting.remove(name)
        visited.add(name)

    for name in skills:
        visit(name, [])
    return errors


if __name__ == "__main__":
    failures = validate()
    if failures:
        for failure in failures:
            print("FAIL", failure)
        raise SystemExit(1)
    print("Skill graph: OK")
