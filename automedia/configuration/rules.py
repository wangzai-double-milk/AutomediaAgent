"""Strict, credential-free operator rules and model role selection."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path

from automedia.paths import WORKSPACE_ROOT
from automedia.infrastructure.store import DomainError

DEFAULT_RULES = {
    "schema_version": 1,
    "publishing": {"interval_seconds": 600, "visibility": "self"},
    "thinking": {"guidance": "", "max_revisions": 2},
    "talk": {"guidance": ""},
    "models": {role: {"connection": "native_oai_config", "model": ""}
               for role in ("thinking", "talk", "behavior")},
}


def settings_path(database=None):
    if os.environ.get("AUTOMEDIA_SETTINGS"):
        return Path(os.environ["AUTOMEDIA_SETTINGS"]).resolve()
    if database and Path(database).resolve() != (WORKSPACE_ROOT / "memory/operations.sqlite3").resolve():
        path = Path(database).resolve()
        return path.parent / (path.stem + ".settings")
    return WORKSPACE_ROOT / "settings"


def read_json(path):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate key")
            result[key] = value
        return result
    try:
        return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique)
    except (OSError, ValueError):
        raise DomainError(f"{path.name}不可读或JSON格式错误，配置未生效")


def validate_rules(value):
    def fields(item, keys):
        if not isinstance(item, dict) or set(item) != set(keys):
            raise DomainError("rules.json字段缺失或含未知字段，请对照配置示例")
    fields(value, DEFAULT_RULES)
    if type(value["schema_version"]) is not int or value["schema_version"] != 1:
        raise DomainError("不支持的规则版本")
    for part in ("publishing", "thinking", "talk", "models"):
        fields(value[part], DEFAULT_RULES[part])
    publishing = value["publishing"]
    if type(publishing["interval_seconds"]) is not int or not 600 <= publishing["interval_seconds"] <= 604800:
        raise DomainError("发帖间隔须为600至604800秒；当前授权至少间隔10分钟")
    if publishing["visibility"] != "self":
        raise DomainError("当前发布授权仅自己可见")
    thinking = value["thinking"]
    if type(thinking["max_revisions"]) is not int or not 0 <= thinking["max_revisions"] <= 4:
        raise DomainError("改写次数须为0至4")
    for part in ("thinking", "talk"):
        if not isinstance(value[part]["guidance"], str) or len(value[part]["guidance"]) > 8000:
            raise DomainError("判断/表达指导须为不超过8000字的文本")
    for route in value["models"].values():
        fields(route, ("connection", "model"))
        if (not isinstance(route["connection"], str) or not route["connection"].isidentifier()
                or route["connection"].startswith("_") or
                not isinstance(route["model"], str) or len(route["model"]) > 200):
            raise DomainError("模型路由需要有效的连接配置名和模型名，禁止填写凭据")
    return deepcopy(value)


def load_rules(directory=None):
    path = Path(directory or settings_path()) / "rules.json"
    if not path.exists() and path.with_name("character.json").exists():
        raise DomainError("已初始化的settings缺少rules.json，请恢复规则文件")
    return validate_rules(read_json(path) if path.exists() else DEFAULT_RULES)


def rules_digest(rules):
    return hashlib.sha256(json.dumps(rules, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def model_config(role, rules=None):
    rules = validate_rules(rules) if rules is not None else load_rules()
    route = rules["models"][role]
    import mykey
    connection = getattr(mykey, route["connection"], None)
    if not isinstance(connection, dict) or any(not connection.get(k) for k in ("model", "apibase", "apikey")):
        raise DomainError(f"{role}模型连接配置不可用，请检查连接名与本机凭据")
    config = dict(connection)
    if route["model"]:
        config["model"] = route["model"]
    return config
