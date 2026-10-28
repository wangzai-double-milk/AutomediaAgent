"""Skill loading and registration must agree with runtime tool gates."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_loop import exhaust
from ga import GenericAgentHandler


@pytest.fixture
def handler(tmp_path):
    root = tmp_path / "project"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    skill = root / "skills/hard_skills/xhs_publish/SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("# Publish\nVerify visibility before publishing.\n", encoding="utf-8")
    registry = {
        "version": 1,
        "rules": {
            "router_can_depend_on": ["workflow", "capability"],
            "workflow_can_depend_on": ["capability", "resource"],
            "capability_can_depend_on": ["resource"],
            "resource_can_depend_on": [],
        },
        "tool_skill_gates": {"xhs_publish_note": "xhs_publish"},
        "skills": {
            "xhs_publish": {
                "path": "hard_skills/xhs_publish/SKILL.md",
                "layer": "capability",
                "routable": False,
                "depends_on": [],
            }
        },
    }
    (root / "skills/registry.json").write_text(json.dumps(registry), encoding="utf-8")
    return GenericAgentHandler(
        SimpleNamespace(task_dir=None, verbose=False),
        cwd=str(workspace), project_root=str(root),
    )


def test_skill_path_is_relative_to_project(handler):
    relative = "skills/hard_skills/xhs_publish/SKILL.md"
    assert handler._get_abs_path(relative) == str(Path(handler.project_root) / relative)


@pytest.mark.parametrize("index", [0, 1, 3])
def test_sop_gate_checks_every_tool(handler, index):
    result = handler._xhs_sop_gate("xhs_publish_note", {"_index": index})
    assert result is not None
    assert result.data["status"] == "error"


@pytest.mark.parametrize("exists", [False, True])
def test_read_gate_requires_successful_canonical_document(handler, exists):
    path = Path(handler.project_root) / "skills/hard_skills/xhs_publish/SKILL.md"
    if not exists:
        path.unlink()
    exhaust(handler.do_file_read({"path": str(path)}, SimpleNamespace(content="")))
    gate = handler._xhs_sop_gate("xhs_publish_note", {})
    assert (gate is None) is exists
    if not exists:
        assert str(path) not in handler.working.get("read_sops", set())


def test_sop_gate_rejects_similar_unregistered_path(handler):
    handler.working["read_sops"] = {"/tmp/impostor_xhs_publish/SKILL.md"}
    assert handler._xhs_sop_gate("xhs_publish_note", {}) is not None


@pytest.mark.parametrize("evidence", [[""], ["   "], [None], [{}]])
def test_create_skill_rejects_empty_evidence(handler, evidence):
    result = exhaust(handler.do_create_skill({
        "skill_name": "new_playbook", "description": "A playbook",
        "sop_content": "# Steps\nReview source media.", "evidence_manifest": evidence,
    }, SimpleNamespace(content="")))
    assert result.data["status"] == "error"
    assert not (Path(handler.project_root) / "skills/soft_skills/new_playbook/SKILL.md").exists()


def test_create_skill_registers_resource_and_preserves_existing_entries(handler):
    result = exhaust(handler.do_create_skill({
        "skill_name": "new_playbook", "description": "Quoted \"title\"\nSecond line",
        "sop_content": "# Steps\nReview source media.",
        "evidence_manifest": ["xiaohongshu:note-1:media-reviewed"],
    }, SimpleNamespace(content="")))
    assert result.data["status"] == "ok"
    registry = json.loads((Path(handler.project_root) / "skills/registry.json").read_text())
    assert "xhs_publish" in registry["skills"]
    assert registry["skills"]["new_playbook"] == {
        "path": "soft_skills/new_playbook/SKILL.md",
        "layer": "resource", "routable": False, "depends_on": [],
    }
    assert Path(result.data["path"]).is_file()
