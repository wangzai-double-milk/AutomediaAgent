"""Frozen content briefs, reviewed platform copy and one-shot self publication."""
import hashlib
import json
from pathlib import Path
import re
import uuid

from automedia.infrastructure.store import DomainError, encode, now, text


from automedia.talk.contracts import CONTENT_TALK
from automedia.thinking.review import CONTENT_REVIEW
PUBLISH_TOOLS = frozenset({"file_read", "xhs_task_init", "xhs_publish_content", "no_tool", "ask_user"})


def asset_record(item):
    if not isinstance(item, dict) or set(item) != {"path", "source"}:
        raise DomainError("素材需要path和source")
    path = Path(text(item["path"], "素材路径", 2000))
    if not path.is_absolute() or not path.is_file() or path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
        raise DomainError("当前母稿链路支持本地绝对路径图片")
    size = path.stat().st_size
    if not 0 < size <= 32 * 1024**2:
        raise DomainError("图片大小须为1字节至32MB")
    try:
        from PIL import Image
        with Image.open(path) as image:
            image.verify()
    except Exception as exc:
        raise DomainError("素材不是可解码图片") from exc
    return {"path": str(path.resolve()), "source": text(item["source"], "素材来源", 4000),
            "bytes": size, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


class ContentStore:
    def init_content(self, db):
        db.executescript("""
            CREATE TABLE IF NOT EXISTS content_briefs (
                id TEXT PRIMARY KEY, request_key TEXT UNIQUE NOT NULL, input_digest TEXT NOT NULL,
                draft_job TEXT UNIQUE NOT NULL REFERENCES runtime_jobs(id),
                publish_job TEXT UNIQUE REFERENCES runtime_jobs(id), state TEXT NOT NULL,
                brief TEXT NOT NULL, payload TEXT, fingerprint TEXT, receipt TEXT,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS content_submissions (
                owner_id TEXT NOT NULL, fingerprint TEXT NOT NULL, content_id TEXT NOT NULL,
                created_at TEXT NOT NULL, PRIMARY KEY(owner_id,fingerprint));
        """)

    def _content(self, db, content_id):
        row = db.execute("SELECT * FROM content_briefs WHERE id=?", (content_id,)).fetchone()
        if not row:
            raise DomainError("内容母稿不存在", 404)
        row = dict(row)
        for k in ("brief", "payload", "receipt"):
            row[k] = json.loads(row[k]) if row[k] else None
        return row

    def content(self, content_id):
        with self.connect() as db:
            return self._content(db, content_id)

    def contents(self):
        with self.connect() as db:
            return [self._content(db, r["id"]) for r in db.execute(
                "SELECT id FROM content_briefs ORDER BY created_at DESC LIMIT 100")]

    def create_content(self, objective, account_id, assets, sources, binding, request_key=None, fresh_workspace=False):
        objective = text(objective, "内容目标", 8000)
        request_key = text(request_key or uuid.uuid4().hex, "请求标识", 100)
        if not account_id:
            raise DomainError("请选择本地账号档案")
        if not isinstance(binding, dict) or not re.fullmatch(r"[a-f0-9]{24}", binding.get("owner_id", "")):
            raise DomainError("需要已绑定的平台稳定账号；本地档案ID不能替代平台身份")
        binding = {"owner_id": binding["owner_id"], "nickname": text(binding.get("nickname"), "绑定昵称", 100)}
        if not isinstance(assets, list) or not 1 <= len(assets) <= 18:
            raise DomainError("需要1至18张图片及来源")
        assets = [asset_record(a) for a in assets]
        if not isinstance(sources, list) or not 1 <= len(sources) <= 20:
            raise DomainError("需要1至20条事实或创作材料来源")
        clean_sources = []
        for i, s in enumerate(sources):
            if not isinstance(s, dict):
                raise DomainError("来源需要content和source")
            clean_sources.append({"id": f"source-{i + 1}", "content": text(s.get("content"), "材料", 6000),
                                  "source": text(s.get("source"), "来源", 2000)})
        if type(fresh_workspace) is not bool:
            raise DomainError("fresh_workspace必须为布尔值")
        brief = {"objective": objective, "assets": assets, "sources": clean_sources,
                 "binding": binding, "visibility": "self", "fresh_workspace": fresh_workspace}
        input_digest = hashlib.sha256(encode([account_id, brief]).encode()).hexdigest()
        with self.connect(True) as db:
            old = db.execute("SELECT id,input_digest FROM content_briefs WHERE request_key=?", (request_key,)).fetchone()
            if old:
                if old["input_digest"] != input_digest:
                    raise DomainError("请求标识已用于不同内容或素材版本", 409)
                return self._content(db, old["id"])
            job = self._create_run(db, objective, "content_draft", account_id, "content:" + request_key)
            cid = "content-" + uuid.uuid4().hex
            job["context"]["content"] = {"id": cid, **brief}
            db.execute("UPDATE runtime_jobs SET context=? WHERE id=?", (encode(job["context"]), job["id"]))
            db.execute("""INSERT INTO content_briefs
                (id,request_key,input_digest,draft_job,state,brief,created_at,updated_at)
                VALUES(?,?,?,?,'preparing',?,?,?)""",
                (cid, request_key, input_digest, job["id"], encode(brief), now(), now()))
            return self._content(db, cid)

    def validate_content_draft(self, job, draft):
        from tools.xhs_publish import normalize
        if not isinstance(draft, dict) or set(draft) != {"title", "desc", "tags", "source_ids", "limitations"}:
            raise DomainError("母稿结构不完整")
        for k in ("title", "desc"):
            text(draft[k], k, 2000)
        for k in ("tags", "source_ids", "limitations"):
            if not isinstance(draft[k], list) or any(not isinstance(s, str) for s in draft[k]):
                raise DomainError("母稿引用、话题或限制需要字符串数组")
        brief = job["context"]["content"]
        if set(draft["source_ids"]) - {s["id"] for s in brief["sources"]}:
            raise DomainError("母稿引用未知来源")
        payload = {k: draft[k] for k in ("title", "desc", "tags")}
        payload.update(images=[a["path"] for a in brief["assets"]], visibility="self",
                       owner_id=brief["binding"]["owner_id"], expected_account=brief["binding"]["nickname"],
                       transport="desktop")
        normalize(payload)
        return payload

    def _content_assets_valid(self, row):
        try:
            return all(asset_record({"path": a["path"], "source": a["source"]}) == a
                       for a in row["brief"]["assets"])
        except (OSError, DomainError):
            return False

    def collect_content(self):
        from tools.xhs_publish import fingerprint, normalize
        with self.connect(True) as db:
            for r in db.execute("SELECT id FROM content_briefs WHERE state='preparing'").fetchall():
                row = self._content(db, r["id"])
                job = self._get_job(db, row["draft_job"])
                if job["state"] in ("queued", "thinking", "talk", "behavior"):
                    continue
                state, payload, digest = "blocked", None, None
                if job["state"] == "completed":
                    if self._snapshot_valid(db, job["context"]) and self._content_assets_valid(row):
                        result = job["result"] or {}
                        review = result.get("review") or {}
                        if review.get("verdict") == "pass" and review.get("issues") == []:
                            payload = self.validate_content_draft(job, result["draft"])
                            digest, state = fingerprint(normalize(payload)), "ready"
                    else:
                        state = "stale"
                db.execute("UPDATE content_briefs SET state=?,payload=?,fingerprint=?,updated_at=? WHERE id=?",
                           (state, encode(payload) if payload else None, digest, now(), row["id"]))
            for r in db.execute("SELECT id FROM content_briefs WHERE state IN ('queued','publishing','submitting')").fetchall():
                row = self._content(db, r["id"])
                job = self._get_job(db, row["publish_job"])
                if job["state"] not in ("queued", "thinking", "behavior", "talk", "running"):
                    # A crash/stop after the final claim must never result in an automatic replay.
                    state = "unknown" if row["state"] == "submitting" else "blocked"
                    db.execute("UPDATE content_briefs SET state=?,updated_at=? WHERE id=?", (state, now(), row["id"]))

    def queue_content(self, content_id, expected_fingerprint):
        with self.connect(True) as db:
            row = self._content(db, content_id)
            if not row["fingerprint"] or row["fingerprint"] != expected_fingerprint:
                raise DomainError("母稿指纹不一致，请重新读取", 409)
            if row["publish_job"]:
                return self._get_job(db, row["publish_job"])
            if row["state"] != "ready":
                raise DomainError("母稿尚未核对通过", 409)
            draft = self._get_job(db, row["draft_job"])
            if not self._snapshot_valid(db, draft["context"]) or not self._content_assets_valid(row):
                raise DomainError("角色、账号或素材已变化，请重新准备", 409)
            job = self._create_run(db, "发布已核对母稿，仅自己可见：" + row["payload"]["title"],
                                   "content_publish", draft["context"]["account_id"], "publish:" + content_id)
            # Never rebind a reviewed draft to a newer profile/character.
            job["context"] = draft["context"]
            job["context"]["platform"] = "xiaohongshu"
            job["context"]["policy"] = {**job["context"]["policy"], "platform_write": "self_publication"}
            db.execute("UPDATE runtime_jobs SET context=? WHERE id=?", (encode(job["context"]), job["id"]))
            db.execute("UPDATE content_briefs SET publish_job=?,state='queued',updated_at=? WHERE id=?",
                       (job["id"], now(), content_id))
            return self._get_job(db, job["id"])

    def check_content_publish(self, job_id, args, claim=False):
        from tools.xhs_publish import fingerprint, normalize
        with self.connect(True) as db:
            job = self._get_job(db, job_id)
            row = self._content(db, job["context"]["content"]["id"])
            clean = {k: v for k, v in args.items() if not k.startswith("_")}
            if (job["kind"] != "content_publish" or row["publish_job"] != job_id or
                    row["state"] not in ("queued", "publishing") or not self._job_valid(db, job)):
                raise DomainError("内容已停止、过期或已提交；禁止重放", 409)
            if clean != row["payload"] or not self._content_assets_valid(row):
                raise DomainError("提交文案、权限或素材与核对版本不一致", 409)
            if fingerprint(normalize(clean)) != row["fingerprint"]:
                raise DomainError("提交素材指纹已变化", 409)
            if claim:
                from tools.xhs_test_control import status
                binding = status().get("binding")
                if not binding or any(binding.get(k) != v for k, v in row["brief"]["binding"].items()):
                    raise DomainError("平台绑定已变化，请重新准备", 409)
                prior = db.execute("SELECT content_id FROM content_submissions WHERE owner_id=? AND fingerprint=?",
                    (row["brief"]["binding"]["owner_id"], row["fingerprint"])).fetchone()
                if prior:
                    raise DomainError("同账号已有相同内容提交记录；只能核对，不能重发", 409)
                db.execute("INSERT INTO content_submissions VALUES(?,?,?,?)",
                    (row["brief"]["binding"]["owner_id"], row["fingerprint"], row["id"], now()))
                db.execute("UPDATE content_briefs SET state='submitting',updated_at=? WHERE id=?", (now(), row["id"]))
                self._event(db, job_id, "behavior", "content_submit_claim", {"fingerprint": row["fingerprint"]})
            else:
                db.execute("UPDATE content_briefs SET state='publishing',updated_at=? WHERE id=?", (now(), row["id"]))
            return row

    def retry_unsubmitted_content(self, content_id, expected_fingerprint, request_key):
        """Explicit recovery of a foreground-check failure, never an unknown submit."""
        from tools.xhs_test_control import status
        key = "retry:" + content_id + ":" + text(request_key, "重试标识", 40)
        with self.connect(True) as db:
            row = self._content(db, content_id)
            if row["fingerprint"] != expected_fingerprint:
                raise DomainError("母稿指纹已变化", 409)
            prior = db.execute("SELECT * FROM runtime_jobs WHERE request_key=?", (key,)).fetchone()
            if prior:
                return self._decode_job(prior)
            if (row["state"] != "blocked" or not row["publish_job"] or
                    row["receipt"] != {"status": "error", "msg": "chrome_foreground_not_verified"}):
                raise DomainError("只有确认停在浏览器前台检查、尚未提交的母稿可恢复", 409)
            old = self._get_job(db, row["publish_job"])
            if old["state"] not in ("blocked", "failed") or old["cancel_requested"]:
                raise DomainError("原任务尚未停止或已被取消", 409)
            draft = self._get_job(db, row["draft_job"])
            if not self._snapshot_valid(db, draft["context"]) or not self._content_assets_valid(row):
                raise DomainError("角色、账号或素材变化，请重新准备", 409)
            control = status()
            binding = control.get("binding") or {}
            if (not isinstance(control.get("reservations"), list) or
                    any(binding.get(k) != v for k, v in row["brief"]["binding"].items())):
                raise DomainError("无法确认原账号与提交记录", 409)
            if (any(r["operation"] == "publish:" + row["fingerprint"] for r in control["reservations"]) or
                    db.execute("SELECT 1 FROM content_submissions WHERE owner_id=? AND fingerprint=?",
                        (binding["owner_id"], row["fingerprint"])).fetchone()):
                raise DomainError("已经存在提交记录，只能回读核对，不能重试", 409)
            job = self._create_run(db, old["objective"], "content_publish",
                                   draft["context"]["account_id"], key)
            db.execute("UPDATE runtime_jobs SET context=? WHERE id=?", (encode(old["context"]), job["id"]))
            db.execute("UPDATE content_briefs SET publish_job=?,state='queued',receipt=NULL,updated_at=? WHERE id=?",
                       (job["id"], now(), content_id))
            self._event(db, job["id"], "behavior", "publication_retry",
                        {"previous_job": old["id"], "source": "controller",
                         "reason": "浏览器前台检查已恢复；未发现提交登记", "fingerprint": row["fingerprint"]})
            return self._get_job(db, job["id"])

    def finish_content_publish(self, job_id, result):
        with self.connect(True) as db:
            job = self._get_job(db, job_id)
            row = self._content(db, job["context"]["content"]["id"])
            independent = result.get("independent_verification") or {}
            claimed = db.execute("SELECT 1 FROM content_submissions WHERE content_id=?", (row["id"],)).fetchone()
            verified = (bool(claimed) and row["state"] in ("submitting", "unknown") and result.get("ok") is True
                        and independent.get("verified") is True and bool(result.get("note_id"))
                        and result.get("visibility") == "self"
                        and result.get("attempt_id") == row["fingerprint"])
            state = "verified" if verified else "unknown" if claimed else "blocked"
            db.execute("UPDATE content_briefs SET state=?,receipt=?,updated_at=? WHERE id=?",
                       (state, encode(result), now(), row["id"]))
            return self._content(db, row["id"])

    def recover_content(self):
        """A restart never resumes a queued external write, even before its first click."""
        with self.connect(True) as db:
            for r in db.execute("SELECT id FROM content_briefs WHERE state IN ('queued','publishing','submitting')").fetchall():
                row = self._content(db, r["id"])
                state = "unknown" if row["state"] == "submitting" else "blocked"
                db.execute("UPDATE content_briefs SET state=?,updated_at=? WHERE id=?", (state, now(), row["id"]))
                db.execute("""UPDATE runtime_jobs SET state='interrupted',error=?,updated_at=?
                    WHERE id=? AND state IN ('queued','thinking','behavior','talk','running')""",
                    ("服务重启；内容发布不自动恢复，保留证据", now(), row["publish_job"]))

    def cancel_content(self, content_id):
        with self.connect(True) as db:
            row = self._content(db, content_id)
            if row["state"] in ("verified", "unknown", "submitting"):
                # Cancellation stops subsequent work, but never erases possible external effects.
                state = row["state"]
            else:
                state = "cancelled"
            for key in ("draft_job", "publish_job"):
                if row[key]:
                    db.execute("UPDATE runtime_jobs SET cancel_requested=1 WHERE id=?", (row[key],))
            db.execute("UPDATE content_briefs SET state=?,updated_at=? WHERE id=?", (state, now(), content_id))
            return self._content(db, content_id)
