"""Versioned character file bridge; explicit conflicts preserve both edits."""
import json
import os
from pathlib import Path
import tempfile
import fcntl
from contextlib import contextmanager

from automedia.infrastructure.store import DomainError
from .rules import read_json


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix="." + path.name, dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


class CharacterFile:
    def __init__(self, directory):
        self.path = Path(directory) / "character.json"

    @contextmanager
    def locked(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.with_suffix(".lock").open("a") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    def document(self):
        value = read_json(self.path)
        if (not isinstance(value, dict) or set(value) != {"schema_version", "base_revision", "profile"}
                or type(value["schema_version"]) is not int or value["schema_version"] != 1
                or type(value["base_revision"]) is not int or value["base_revision"] < 1):
            raise DomainError("character.json版本字段无效")
        return value

    def write(self, character):
        atomic_json(self.path, {"schema_version": 1, "base_revision": character["revision"],
                                "profile": character["profile"]})

    def matches(self, character):
        if not self.path.exists():
            return False
        value = self.document()
        return value["base_revision"] == character["revision"] and value["profile"] == character["profile"]

    def apply(self, store):
        """Startup/explicit apply; optimistic database revision and file CAS."""
        with self.locked():
            current = store._read_character()
            if not self.path.exists():
                self.write(current)
                return current
            raw = self.path.read_bytes()
            value = self.document()
            if value["base_revision"] != current["revision"]:
                # Interrupted export is recoverable only when content is identical.
                if value["profile"] == current["profile"]:
                    self.write(current)
                    return current
                raise DomainError("角色文件与数据库版本冲突，两份内容均已保留，请先合并再应用", 409)
            if value["profile"] == current["profile"]:
                return current
            updated = store._update_character(value["base_revision"], value["profile"])
            if self.path.read_bytes() != raw:
                raise DomainError("应用期间角色文件再次变化，数据库已保存上一候选；请合并版本", 409)
            self.write(updated)
            return updated

    def update(self, store, expected, profile):
        with self.locked():
            current = store._read_character()
            if self.path.exists() and not self.matches(current):
                raise DomainError("角色文件有待应用修改，工作台未覆盖它；请先应用或合并文件", 409)
            raw = self.path.read_bytes() if self.path.exists() else None
            updated = store._update_character(expected, profile)
            if (self.path.read_bytes() if self.path.exists() else None) != raw:
                raise DomainError("保存期间角色文件变化，数据库已保存工作台版本；请合并文件", 409)
            self.write(updated)
            return updated
