"""Workbench read model of actual work; no invented experiences or successes."""
import json
import re
from datetime import datetime
from pathlib import Path

LABELS = {"douyin_browse": "抖音观察", "xhs_search": "小红书搜索",
          "xhs_read": "阅读笔记", "analyze_media": "理解视频", "xhs_publish": "发布核对",
          "generate_image": "生成画面", "edit_image": "调整画面"}
STATUS = {"not_ready": "尚未就绪", "login_required": "需要登录", "restricted": "访问受限",
          "deferred": "等待额度", "partial": "部分信息", "error": "未完成", "success": "工具返回",
          "ok": "工具返回", "completed": "工具返回"}


def recent_work(store, task_root):
    items = []
    jobs = store.runs()
    covered = set()
    for job in jobs:
        result = job.get("result") or {}
        task_dir = result.get("task_dir") or (result.get("behavior") or {}).get("task_dir")
        if task_dir:
            covered.add(str(Path(task_dir).resolve()))
        draft = (job.get("result") or {}).get("draft") or {}
        controller_text = ""
        if job["kind"] == "agent":
            with store.connect() as db:
                response = db.execute("""SELECT payload FROM runtime_events
                    WHERE job_id=? AND kind='controller_response' ORDER BY sequence DESC LIMIT 1""",
                    (job["id"],)).fetchone()
            if response:
                controller_text = str(json.loads(response["payload"]).get("text") or "")
                if "🛠️" in controller_text:
                    controller_text = "任务在工具执行阶段停止，尚未形成最终手记。已取得的观察保留在过程记录中。"
                else:
                    controller_text = re.sub(r"<(thinking|summary)>[\s\S]*?(?:</\1>|$)", "", controller_text,
                                             flags=re.IGNORECASE).strip()
        title = draft.get("title") or job["objective"].splitlines()[0].split("。")[0][:80]
        source = {"content_draft": "图文母稿 · 本地", "draft": "表达草稿 · 本地",
                  "douyin_research": "抖音观察", "agent": "任务手记"}.get(job["kind"], "三域任务")
        items.append({"id": job["id"], "kind": "runtime", "title": title,
                      "state": job["state"], "at": job["created_at"],
                      "excerpt": (draft.get("text") or draft.get("desc") or controller_text or job.get("error") or
                                  "结果还未形成，打开记录查看进展。")[:900],
                      "source": source, "job_id": job["id"]})
    task_root = Path(task_root)
    for folder in sorted((p for p in task_root.glob("*") if p.is_dir()), reverse=True)[:24]:
        if str(folder.resolve()) in covered:
            continue
        try:
            at = datetime.strptime(folder.name[:15], "%Y%m%d-%H%M%S").astimezone().isoformat()
            path = folder / "trace.log"
            with path.open("rb") as f:
                f.seek(0, 2)
                size = f.tell()
                f.seek(max(0, size - 180_000))
                lines = f.read().decode("utf-8", errors="replace").splitlines()
            records = []
            for line in lines[-80:]:
                try:
                    ev = json.loads(line)
                    if isinstance(ev, dict):
                        records.append(ev)
                except ValueError:
                    continue
            notes = []
            title = ""
            observed_title = ""
            for ev in records:
                p = ev.get("payload")
                if not isinstance(p, dict):
                    continue
                step = str(ev.get("step", ""))
                if step == "task_init":
                    title = str(p.get("user_request", ""))[:100]
                evidence = p.get("data") if step == "task_terminal" and isinstance(p.get("data"), dict) else p
                evidence_path = p.get("evidence_path")
                if evidence_path:
                    try:
                        ep = Path(evidence_path).resolve()
                        if ep.is_relative_to(folder.resolve()) and ep.suffix == ".json" and ep.stat().st_size < 2_000_000:
                            value = json.loads(ep.read_text())
                            if isinstance(value, dict):
                                evidence = value
                    except (ValueError, OSError):
                        pass
                status = str(evidence.get("status", ""))
                media = evidence.get("media") if isinstance(evidence.get("media"), dict) else {}
                observed = str(media.get("title") or evidence.get("title") or evidence.get("message") or "")[:280]
                if media.get("title"):
                    observed_title = str(media["title"]).splitlines()[0][:75]
                label = next((v for k, v in LABELS.items() if k in step), None)
                if step == "research_search_round":
                    label, observed = "寻找参考", str(p.get("keyword") or "")[:180]
                elif step == "research_knowledge_delta":
                    label, observed = "留下的发现", "；".join(str(x) for x in p.get("new_points", [])[:3])[:500]
                elif step == "task_terminal":
                    label = "最后停在这里"
                if label:
                    action = {"status": "查看页面", "read": "阅读内容", "search": "搜索",
                              "open": "打开内容", "next": "下一条", "resume": "继续播放"}.get(p.get("action"), "")
                    notes.append({"label": label, "time": str(ev.get("ts", "")),
                                  "text": (action + " · " if action else "") +
                                  (observed or STATUS.get(status, "已保留工具记录")),
                                  "status": STATUS.get(status, status[:50])})
            if not notes and not title:
                continue
            items.append({"id": folder.name, "kind": "archive", "title": title or observed_title or notes[-1]["label"],
                          "at": at, "state": "archived", "excerpt": notes[-1]["text"] if notes else
                          "已保留原任务。历史记录不代表本轮已完成。",
                          "source": "历史工具记录", "notes": notes[-8:]})
        except (OSError, ValueError):
            continue
    ordered = sorted(items, key=lambda r: datetime.fromisoformat(r["at"]).timestamp(), reverse=True)
    result, groups = [], {}
    for item in ordered:
        key = (item["title"], datetime.fromisoformat(item["at"]).astimezone().date())
        if item["kind"] == "archive":
            if key in groups:
                previous = groups[key]
                previous["related_sources"].append(item["id"])
                previous["notes"] = sorted(previous["notes"] + item["notes"], key=lambda n: n["time"])[-16:]
                previous["source"] = f'历史工具记录 · {len(previous["related_sources"])}段'
                continue
            item["related_sources"] = [item["id"]]
            groups[key] = item
        result.append(item)
    return result[:40]
