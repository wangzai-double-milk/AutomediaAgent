"""Shared SQLite authority for profiles, sourced memories and planning snapshots."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import uuid


FIELDS = {
    "identity": "身份与定位", "audience": "目标受众", "pillars": "内容栏目",
    "voice": "表达风格", "boundaries": "价值与边界", "goal": "运营目标",
}
ACTIVE = ("queued", "running", "reviewing")


class DomainError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def now():
    return datetime.now(timezone.utc).isoformat()


def encode(value):
    return json.dumps(value, ensure_ascii=False)


def text(value, label, limit=4000, required=True):
    if not isinstance(value, str) or len(value) > limit or (required and not value.strip()):
        raise DomainError(f"{label}不能为空且不能超过 {limit} 个字符")
    return value.strip()


def revision(value):
    if type(value) is not int or value < 1:
        raise DomainError("需要有效的版本号，请重新读取")
    return value


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self._owner = None
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS accounts (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, revision INTEGER NOT NULL,
                    memory_revision INTEGER NOT NULL DEFAULT 1, profile TEXT NOT NULL,
                    created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS profile_history (
                    account_id TEXT NOT NULL, revision INTEGER NOT NULL, profile TEXT NOT NULL,
                    actor TEXT NOT NULL, created_at TEXT NOT NULL,
                    PRIMARY KEY(account_id, revision));
                CREATE TABLE IF NOT EXISTS memories (
                    id TEXT PRIMARY KEY, account_id TEXT NOT NULL, kind TEXT NOT NULL,
                    content TEXT NOT NULL, source TEXT NOT NULL, occurred_at TEXT NOT NULL,
                    valid_until TEXT, state TEXT NOT NULL DEFAULT 'active',
                    created_at TEXT NOT NULL, retracted_at TEXT);
                CREATE INDEX IF NOT EXISTS memories_account ON memories(account_id, state);
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY, account_id TEXT NOT NULL, request_key TEXT NOT NULL,
                    objective TEXT NOT NULL, state TEXT NOT NULL, context TEXT NOT NULL,
                    branches TEXT NOT NULL DEFAULT '{}', review TEXT,
                    error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    UNIQUE(account_id, request_key));
                CREATE UNIQUE INDEX IF NOT EXISTS one_active_job ON jobs(account_id)
                    WHERE state IN ('queued','running','reviewing');
            """)

    @contextmanager
    def connect(self, write=False):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA foreign_keys=ON")
            if write:
                db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def _account(self, db, account_id):
        row = db.execute("SELECT * FROM accounts WHERE id=?", (account_id,)).fetchone()
        if not row:
            raise DomainError("账号档案不存在", 404)
        result = dict(row)
        result["profile"] = json.loads(result["profile"])
        return result

    def accounts(self):
        with self.connect() as db:
            return [dict(r) for r in db.execute(
                "SELECT id,name,revision,memory_revision FROM accounts ORDER BY created_at")]

    def create_account(self, name):
        name = text(name, "档案名称", 80)
        account_id = "acct-" + uuid.uuid4().hex
        profile = {key: {"value": "", "locked": key in ("identity", "boundaries"),
                         "source": "user", "updated_at": now()} for key in FIELDS}
        with self.connect(True) as db:
            db.execute("INSERT INTO accounts VALUES(?,?,1,1,?,?)",
                       (account_id, name, encode(profile), now()))
            db.execute("INSERT INTO profile_history VALUES(?,1,?,'user',?)",
                       (account_id, encode(profile), now()))
            return self._account(db, account_id)

    def account(self, account_id):
        with self.connect() as db:
            return self._account(db, account_id)

    def update_profile(self, account_id, expected_revision, fields, actor="user"):
        revision(expected_revision)
        if actor not in ("user", "agent") or not isinstance(fields, dict) or not fields:
            raise DomainError("人设变更格式不正确")
        if set(fields) - set(FIELDS):
            raise DomainError("包含不支持的人设字段")
        with self.connect(True) as db:
            account = self._account(db, account_id)
            if account["revision"] != expected_revision:
                raise DomainError("档案已在其他窗口更新。输入已保留，请读取最新版本后再保存。", 409)
            profile = account["profile"]
            for key, patch in fields.items():
                if not isinstance(patch, dict):
                    raise DomainError("人设字段格式不正确")
                value = text(patch.get("value"), FIELDS[key], required=False)
                locked = patch.get("locked", profile[key]["locked"])
                if type(locked) is not bool:
                    raise DomainError("字段锁必须为布尔值")
                if actor == "agent" and (profile[key]["locked"] or locked != profile[key]["locked"]):
                    raise DomainError("AI不能修改锁定字段或变更字段锁", 403)
                if value != profile[key]["value"] or locked != profile[key]["locked"]:
                    profile[key] = dict(value=value, locked=locked, source=actor, updated_at=now())
            new_revision = expected_revision + 1
            db.execute("UPDATE accounts SET revision=?,profile=? WHERE id=?",
                       (new_revision, encode(profile), account_id))
            db.execute("INSERT INTO profile_history VALUES(?,?,?,?,?)",
                       (account_id, new_revision, encode(profile), actor, now()))
            return self._account(db, account_id)

    def history(self, account_id):
        with self.connect() as db:
            self._account(db, account_id)
            return [{**dict(row), "profile": json.loads(row["profile"])} for row in db.execute(
                "SELECT * FROM profile_history WHERE account_id=? ORDER BY revision DESC LIMIT 20",
                (account_id,))]

    def add_memory(self, account_id, data):
        kind = data.get("kind")
        if kind not in ("experience", "observation", "hypothesis"):
            raise DomainError("请选择真实经历、观察记录或待验证假设")
        content = text(data.get("content"), "记忆内容")
        source = text(data.get("source"), "来源说明", 1000)
        occurred = text(data.get("occurred_at"), "发生时间", 100)
        expiry = data.get("valid_until") or None
        if expiry:
            try:
                parsed = datetime.fromisoformat(expiry)
                if parsed.tzinfo is None:
                    raise ValueError()
                expiry = parsed.astimezone(timezone.utc).isoformat()
                if expiry <= now():
                    raise ValueError()
            except (TypeError, ValueError):
                raise DomainError("有效期需要带时区的未来时间")
        memory_id = "mem-" + uuid.uuid4().hex
        with self.connect(True) as db:
            self._account(db, account_id)
            db.execute("""INSERT INTO memories
                (id,account_id,kind,content,source,occurred_at,valid_until,created_at)
                VALUES(?,?,?,?,?,?,?,?)""",
                       (memory_id, account_id, kind, content, source, occurred, expiry, now()))
            db.execute("UPDATE accounts SET memory_revision=memory_revision+1 WHERE id=?", (account_id,))
        return memory_id

    def retract_memory(self, account_id, memory_id):
        with self.connect(True) as db:
            self._account(db, account_id)
            row = db.execute("SELECT state FROM memories WHERE account_id=? AND id=?",
                             (account_id, memory_id)).fetchone()
            if not row:
                raise DomainError("记忆不存在", 404)
            if row["state"] == "active":
                db.execute("UPDATE memories SET state='retracted',retracted_at=? WHERE id=?",
                           (now(), memory_id))
                db.execute("UPDATE accounts SET memory_revision=memory_revision+1 WHERE id=?", (account_id,))

    def _memories(self, db, account_id, query="", include_inactive=False, limit=50):
        # Substring search works for unsegmented Chinese; always filter scope in SQL.
        query = text(query, "关键词", 200, required=False)
        escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        active = "" if include_inactive else "AND state='active' AND (valid_until IS NULL OR valid_until>?)"
        params = [account_id] + ([] if include_inactive else [now()])
        params += [f"%{escaped}%", f"%{escaped}%", limit]
        return [dict(r) for r in db.execute(f"""SELECT * FROM memories WHERE account_id=?
            {active} AND (content LIKE ? ESCAPE '\\' OR source LIKE ? ESCAPE '\\')
            ORDER BY created_at DESC LIMIT ?""", params)]

    def memories(self, account_id, query="", include_inactive=False):
        with self.connect() as db:
            self._account(db, account_id)
            return self._memories(db, account_id, query, include_inactive)

    def create_job(self, account_id, objective, request_key, memory_query, versions):
        objective = text(objective, "本轮目标", 2000)
        request_key = text(request_key, "请求标识", 100)
        with self.connect(True) as db:
            account = self._account(db, account_id)
            existing = db.execute("SELECT * FROM jobs WHERE account_id=? AND request_key=?",
                                  (account_id, request_key)).fetchone()
            if existing:
                original = json.loads(existing["context"])
                if existing["objective"] != objective or original["memory_query"] != memory_query:
                    raise DomainError("同一请求标识不能用于不同任务", 409)
                return self._job(db, account_id, existing["id"]), False
            if db.execute("SELECT id FROM jobs WHERE account_id=? AND state IN ('queued','running','reviewing')",
                          (account_id,)).fetchone():
                raise DomainError("这个账号已有计划正在生成，可以查看进展或停止后再试", 409)
            memories = self._memories(db, account_id, memory_query, limit=30)
            context = {
                "account_id": account_id, "profile_revision": account["revision"],
                "memory_revision": account["memory_revision"], "profile": account["profile"],
                "memories": memories, "memory_query": memory_query,
                "retrieval": "账号内关键词匹配；未填关键词时取最近30条有效记忆",
                "skill_versions": versions, "captured_at": now(),
                "capabilities": {"platform_read": False, "platform_write": False},
                "evidence_boundary": "用户录入的来源说明尚未经外部核验；假设不能当亲身经历",
            }
            if hasattr(self, "_character"):
                context["character"] = self._character(db)
                from automedia.configuration.rules import load_rules
                context["rules"] = load_rules(self.settings_dir)
                if self.character_file.path.exists() and not self.character_file.matches(context["character"]):
                    raise DomainError("角色文件有待应用修改，请先应用配置", 409)
            job_id = "plan-" + uuid.uuid4().hex
            context["case_id"] = job_id
            db.execute("""INSERT INTO jobs
                (id,account_id,request_key,objective,state,context,created_at,updated_at)
                VALUES(?,?,?,?,'queued',?,?,?)""",
                       (job_id, account_id, request_key, objective, encode(context), now(), now()))
            return self._job(db, account_id, job_id), True

    def _job(self, db, account_id, job_id):
        row = db.execute("SELECT * FROM jobs WHERE account_id=? AND id=?", (account_id, job_id)).fetchone()
        if not row:
            raise DomainError("计划不存在", 404)
        job = dict(row)
        for key in ("context", "branches", "review"):
            job[key] = json.loads(job[key]) if job[key] else None
        account = self._account(db, account_id)
        ctx = job["context"]
        job["outdated"] = (ctx["profile_revision"] != account["revision"]
                           or ctx["memory_revision"] != account["memory_revision"]
                           or any(m["valid_until"] and m["valid_until"] <= now() for m in ctx["memories"]))
        if ctx.get("character") and hasattr(self, "_character"):
            job["outdated"] |= ctx["character"]["revision"] != self._character(db)["revision"]
            from automedia.configuration.rules import load_rules
            job["outdated"] |= ctx.get("rules") is not None and ctx["rules"] != load_rules(self.settings_dir)
            job["outdated"] |= self.character_file.path.exists() and not self.character_file.matches(self._character(db))
        job["executable"] = False
        return job

    def job(self, account_id, job_id):
        with self.connect() as db:
            return self._job(db, account_id, job_id)

    def jobs(self, account_id):
        with self.connect() as db:
            self._account(db, account_id)
            rows = db.execute("SELECT id FROM jobs WHERE account_id=? ORDER BY created_at DESC LIMIT 20",
                              (account_id,)).fetchall()
            return [self._job(db, account_id, row["id"]) for row in rows]

    def transition(self, account_id, job_id, state, branch=None, review=None, error=None):
        with self.connect(True) as db:
            job = self._job(db, account_id, job_id)
            if job["state"] not in ACTIVE:
                return job
            branches = job["branches"]
            if branch:
                branches[branch["id"]] = branch
            if state == "completed" and job["outdated"]:
                state = "stale"
            db.execute("UPDATE jobs SET state=?,branches=?,review=?,error=?,updated_at=? WHERE id=?",
                       (state, encode(branches), encode(review) if review else job["review"] and encode(job["review"]),
                        error, now(), job_id))
            return self._job(db, account_id, job_id)

    def recover_interrupted(self):
        with self.connect(True) as db:
            db.execute("""UPDATE jobs SET state='interrupted',error=?,updated_at=?
                WHERE state IN ('queued','running','reviewing')""",
                       ("服务已重启，本次计划中断。已返回的分支保留，可重新生成。", now()))

    def acquire_owner(self):
        """One planner process per DB; another Studio cannot interrupt live jobs."""
        handle = open(str(self.path) + ".lock", "a+b")
        try:
            if __import__("os").name == "nt":
                import msvcrt
                handle.write(b"0")
                handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            raise RuntimeError("运营数据库已由另一工作台使用，请打开已有服务或指定独立数据库")
        self._owner = handle

    def release_owner(self):
        if self._owner:
            self._owner.close()
            self._owner = None
