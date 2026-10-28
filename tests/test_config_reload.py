"""Configuration failures should stay actionable and data changes reload."""

import json

import pytest

import llmcore
import agentmain


def test_initial_configuration_error_is_not_swallowed(monkeypatch):
    monkeypatch.delitem(llmcore.__dict__, "mykeys", raising=False)
    monkeypatch.setattr(llmcore, "_mykey_path", None)
    monkeypatch.setattr(llmcore, "_mykey_mtime", None)

    def load():
        raise FileNotFoundError("config.json is missing")

    monkeypatch.setattr(llmcore, "_load_mykeys", load)
    with pytest.raises(FileNotFoundError, match="config.json"):
        llmcore.reload_mykeys()


def test_configuration_data_change_reloads_adapter(monkeypatch, tmp_path):
    adapter = tmp_path / "mykey.py"
    adapter.write_text("# adapter\n", encoding="utf-8")
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"model": "first"}), encoding="utf-8")
    monkeypatch.setenv("AUTOMEDIA_CONFIG", str(config))
    monkeypatch.setattr(llmcore, "_mykey_path", str(adapter))
    monkeypatch.setattr(llmcore, "_mykey_mtime", None)
    monkeypatch.setitem(llmcore.__dict__, "mykeys", {})
    monkeypatch.setattr(llmcore, "_load_mykeys", lambda: json.loads(config.read_text()))

    first, changed = llmcore.reload_mykeys()
    assert first == {"model": "first"}
    assert changed is True
    unchanged, changed = llmcore.reload_mykeys()
    assert unchanged == first
    assert changed is False

    config.write_text(json.dumps({"model": "second-longer"}), encoding="utf-8")
    updated, changed = llmcore.reload_mykeys()
    assert changed is True
    assert updated == {"model": "second-longer"}


def test_invalid_mixin_is_not_retained_as_client(monkeypatch):
    backend = type("Backend", (), {"history": []})()
    valid = type("Client", (), {"backend": backend})()
    configs = {
        "native_oai_config": {"name": "primary"},
        "mixin_config": {"llm_nos": ["missing"]},
    }
    monkeypatch.setattr(agentmain, "reload_mykeys", lambda: (configs, True))
    monkeypatch.setattr(
        agentmain, "resolve_client",
        lambda name: valid if name == "native_oai_config" else None,
    )
    monkeypatch.setattr(
        agentmain, "MixinSession",
        lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("bad mixin")),
    )
    agent = agentmain.GenericAgent.__new__(agentmain.GenericAgent)
    agent.llm_no = 0

    agent.load_llm_sessions()

    assert agent.llmclients == [valid]
    assert agent.llmclient is valid
