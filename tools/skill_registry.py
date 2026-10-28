"""Shared registry access for skill lookup and generated resource registration."""

import json
import os
from pathlib import Path
import tempfile
import threading


LOCK = threading.RLock()
DEFAULT_RULES = {
    "router_can_depend_on": ["workflow", "capability"],
    "workflow_can_depend_on": ["capability", "resource"],
    "capability_can_depend_on": ["resource"],
    "resource_can_depend_on": [],
}


def load(project_root):
    path = Path(project_root) / "skills/registry.json"
    if not path.exists():
        return {"version": 1, "rules": DEFAULT_RULES.copy(), "skills": {}}
    with path.open(encoding="utf-8") as stream:
        registry = json.load(stream)
    if not isinstance(registry.get("skills"), dict):
        raise ValueError("skills/registry.json must contain a skills object")
    return registry


def resolve(project_root, name):
    root = (Path(project_root) / "skills").resolve()
    spec = load(project_root)["skills"].get(name)
    if not spec:
        raise ValueError(f"Skill is not registered: {name}")
    path = (root / spec["path"]).resolve()
    path.relative_to(root)
    return path


def tool_gate(project_root, tool_name):
    """Return the canonical SOP path required before a tool may run."""
    registry = load(project_root)
    skill_name = (registry.get("tool_skill_gates") or {}).get(tool_name)
    return resolve(project_root, skill_name) if skill_name else None


def register_resource(project_root, name, path):
    root = Path(project_root) / "skills"
    relative = Path(path).resolve().relative_to(root.resolve()).as_posix()
    with LOCK:
        registry = load(project_root)
        existing = registry["skills"].get(name)
        if existing and (existing["layer"] != "resource" or existing["path"] != relative):
            raise ValueError(f"Cannot replace an existing capability or another skill: {name}")
        registry["skills"][name] = {
            "path": relative, "layer": "resource",
            "routable": False, "depends_on": [],
        }
        owner = registry["skills"].get("operate_mode")
        if owner is not None:
            dependencies = owner.setdefault("depends_on", [])
            if name not in dependencies:
                dependencies.append(name)
        fd, temporary = tempfile.mkstemp(prefix=".registry.", suffix=".tmp", dir=root)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(registry, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
            os.replace(temporary, root / "registry.json")
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
