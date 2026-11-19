"""Configuration integration invariants using isolated databases only."""
from copy import deepcopy
import importlib

import pytest

from automedia.application.runtime import RuntimeStore
from automedia.configuration.character import atomic_json
from automedia.configuration.rules import DEFAULT_RULES, load_rules, model_config
from automedia.infrastructure.store import DomainError


@pytest.fixture
def configured(tmp_path, monkeypatch):
    monkeypatch.delenv("AUTOMEDIA_SETTINGS", raising=False)
    store = RuntimeStore(tmp_path / "isolated.sqlite3")
    atomic_json(store.settings_dir / "rules.json", DEFAULT_RULES)
    store.character_file.apply(store)
    return store


@pytest.mark.parametrize("old,new", [
    ("operations.runtime", "automedia.application.runtime"),
    ("operations.engine", "automedia.thinking.engine"),
    ("operations.broker", "automedia.infrastructure.broker"),
    ("tools.xhs_publish", "automedia.behavior.platforms.xhs_publish"),
    ("tools.xhs_account", "automedia.behavior.platforms.xhs_account"),
    ("agent_loop", "automedia.legacy.agent_loop"),
])
def test_import_aliases_share_state(old, new):
    assert importlib.import_module(old) is importlib.import_module(new)


def test_file_edit_blocks_old_task_and_workbench_overwrite(configured):
    store = configured
    job = store.create_run("before file edit", "draft")
    candidate = store.character_file.document()
    candidate["profile"]["persona"] = "仅隔离测试角色"
    atomic_json(store.character_file.path, candidate)
    assert not store.valid(job["id"])
    with pytest.raises(DomainError):
        store.update_character(1, store.character()["profile"])
    updated = store.character_file.apply(store)
    assert updated["revision"] == 2
    assert updated["profile"] == candidate["profile"]
    assert store.character_file.matches(updated)
    assert store.character_file.apply(store)["revision"] == 2


def test_file_conflict_preserves_both_versions(configured):
    store = configured
    previous = store.character_file.document()
    profile = store.character()["profile"]
    profile["persona"] = "工作台版本"
    store.update_character(1, profile)
    previous["profile"]["persona"] = "文件另一版本"
    atomic_json(store.character_file.path, previous)
    with pytest.raises(DomainError):
        store.character_file.apply(store)
    assert store.character()["profile"]["persona"] == "工作台版本"
    assert store.character_file.document()["profile"]["persona"] == "文件另一版本"


def test_changed_rules_invalidate_frozen_work(configured):
    store = configured
    job = store.create_run("rules before", "draft")
    rules = deepcopy(DEFAULT_RULES)
    rules["talk"]["guidance"] = "简洁"
    atomic_json(store.settings_dir / "rules.json", rules)
    assert not store.valid(job["id"])
    assert store.create_run("rules after", "draft")["context"]["rules"] == rules


@pytest.mark.parametrize("interval", [True, 599, 0, "600"])
def test_invalid_interval_never_relaxes_policy(configured, interval):
    rules = deepcopy(DEFAULT_RULES)
    rules["publishing"]["interval_seconds"] = interval
    atomic_json(configured.settings_dir / "rules.json", rules)
    with pytest.raises(DomainError):
        load_rules(configured.settings_dir)


def test_missing_initialized_rules_do_not_use_defaults(configured):
    (configured.settings_dir / "rules.json").unlink()
    with pytest.raises(DomainError):
        load_rules(configured.settings_dir)


def test_model_selection_does_not_mutate_connection(monkeypatch):
    import sys
    from types import SimpleNamespace
    connection = {"model": "original", "apibase": "https://example.test", "apikey": "test-placeholder"}
    monkeypatch.setitem(sys.modules, "mykey", SimpleNamespace(native_oai_config=connection))
    rules = deepcopy(DEFAULT_RULES)
    for role in rules["models"]:
        rules["models"][role]["model"] = "test-" + role
        assert model_config(role, rules)["model"] == "test-" + role
    assert connection["model"] == "original"
