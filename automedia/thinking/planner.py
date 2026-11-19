"""Fan-out / join planner with independent requests and a complete review case."""
from __future__ import annotations

import asyncio
import json
import time

from .methods import METHODS, versions
from automedia.infrastructure.store import DomainError, now
from automedia.infrastructure.models import ModelGateway, ModelError


def strings(value):
    return isinstance(value, list) and len(value) <= 30 and all(isinstance(v, str) for v in value)


def validate(branch, output, context):
    if not isinstance(output.get("summary"), str) or not strings(output.get("next_actions")):
        raise ModelError("模型产物缺少摘要或下一步，已保留失败状态")
    memory_ids = {m["id"] for m in context["memories"]}

    def refs(items):
        if not strings(items) or set(items) - memory_ids:
            raise ModelError("模型引用了不存在的记忆，计划已阻断")

    if branch == "content":
        ideas = output.get("ideas")
        if not isinstance(ideas, list) or len(ideas) > 3:
            raise ModelError("选题格式不符合契约")
        for idea in ideas:
            if (not isinstance(idea, dict) or not isinstance(idea.get("title"), str)
                    or not isinstance(idea.get("opening"), str)
                    or not strings(idea.get("steps")) or not strings(idea.get("missing"))):
                raise ModelError("选题缺少标题、开头、步骤或证据缺口")
            refs(idea.get("evidence_ids"))
    elif branch == "diagnosis":
        refs(output.get("evidence_ids"))
        decision = output.get("decision")
        if (decision not in ("wait", "prepare", "propose_revision")
                or type(output.get("action_recommended")) is not bool
                or not strings(output.get("missing")) or not strings(output.get("observation_plan"))):
            raise ModelError("运营分析格式不符合契约")
        if decision == "wait" and output["action_recommended"]:
            raise ModelError("等待观察与建议行动相矛盾，计划已阻断")
    else:
        if (output.get("case_id") != context["case_id"]
                or output.get("verdict") not in ("pass", "revise") or not strings(output.get("issues"))):
            raise ModelError("评审案例或结构不匹配，计划已阻断")
        if output["verdict"] == "pass" and output["issues"]:
            raise ModelError("评审通过却仍有待修问题，计划已阻断")


class Planner:
    def __init__(self, store, gateway=None):
        self.store = store
        self.gateway = gateway or ModelGateway()
        self.tasks = {}

    def start(self, account_id, objective, request_key, memory_query=""):
        job, created = self.store.create_job(account_id, objective, request_key, memory_query, versions())
        if created:
            task = asyncio.create_task(self.run(job))
            self.tasks[job["id"]] = task
            task.add_done_callback(lambda _: self.tasks.pop(job["id"], None))
        return job

    async def run(self, job):
        account_id, job_id, context = job["account_id"], job["id"], job["context"]
        self.store.transition(account_id, job_id, "running")
        envelope = {"case_id": job_id, "objective": job["objective"], "context": context}

        async def branch(branch_id):
            start = time.monotonic()
            record = {"id": branch_id, "name": METHODS[branch_id]["name"], "started_at": now()}
            try:
                output, telemetry = await self.gateway.call(METHODS[branch_id], envelope)
                record.update(raw_output=output, **telemetry)
                validate(branch_id, output, context)
                record.update(state="completed", output=output, **telemetry)
            except ModelError as exc:
                record.update(state="failed", error=str(exc))
            record["elapsed_seconds"] = round(time.monotonic() - start, 2)
            record["finished_at"] = now()
            self.store.transition(account_id, job_id, "running", branch=record)
            return record

        try:
            # return_exceptions keeps siblings owned by this task until completion/cancellation.
            results = await asyncio.gather(branch("content"), branch("diagnosis"), return_exceptions=True)
            if any(isinstance(r, BaseException) for r in results):
                raise ModelError("生成过程出现内部错误，已返回的产物保留")
            if any(r["state"] == "failed" for r in results):
                raise ModelError("部分分支失败，完整计划未通过；可查看已返回内容后重新生成")
            if self.store.job(account_id, job_id)["outdated"]:
                self.store.transition(account_id, job_id, "stale", error="人设或记忆已变化，请按最新资料重新生成")
                return
            self.store.transition(account_id, job_id, "reviewing")
            review_envelope = {
                **envelope, "scenario_type": "account_plan",
                "branches": {r["id"]: r["output"] for r in results},
                "allowed_actions": ["local_plan_only"],
                "validation_scope": "用户提供资料上的本地策划，无平台读取或写入",
            }
            start = time.monotonic()
            output, telemetry = await self.gateway.call(METHODS["review"], review_envelope)
            record = {"raw_output": output, **telemetry, "elapsed_seconds": round(time.monotonic() - start, 2)}
            try:
                validate("review", output, context)
            except ModelError as exc:
                record.update(state="failed", error=str(exc))
                self.store.transition(account_id, job_id, "failed", review=record, error=str(exc))
                return
            record.update(state="completed", output=output)
            self.store.transition(account_id, job_id,
                                  "completed" if output["verdict"] == "pass" else "blocked", review=record)
        except asyncio.CancelledError:
            self.store.transition(account_id, job_id, "cancelled", error="生成已停止，已返回产物保留")
            raise
        except Exception as exc:
            message = str(exc) if isinstance(exc, (ModelError, DomainError)) else "计划处理失败，请重新生成"
            self.store.transition(account_id, job_id, "failed", error=message)

    def cancel(self, account_id, job_id):
        self.store.job(account_id, job_id)
        result = self.store.transition(account_id, job_id, "cancelled", error="生成已停止，已返回产物保留")
        task = self.tasks.get(job_id)
        if task:
            task.cancel()
        return result

    async def close(self):
        tasks = list(self.tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
