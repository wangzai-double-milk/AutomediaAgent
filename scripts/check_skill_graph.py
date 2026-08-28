#!/usr/bin/env python3
"""Validate the one-way Skill dependency graph."""

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILLS_ROOT = ROOT / "skills"


def validate(registry_path=SKILLS_ROOT / "registry.json"):
    registry = json.loads(Path(registry_path).read_text(encoding="utf-8"))
    skills = registry["skills"]
    rules = registry["rules"]
    errors = []

    allowed = {
        "router": set(rules["router_can_depend_on"]),
        "workflow": set(rules["workflow_can_depend_on"]),
        "capability": set(rules["capability_can_depend_on"]),
        "resource": set(rules["resource_can_depend_on"]),
    }
    for name, spec in skills.items():
        path = SKILLS_ROOT / spec["path"]
        if not path.is_file():
            errors.append(f"{name}: missing {path.relative_to(ROOT)}")
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
