"""Studio adapter. Account IDs are local namespaces, never platform bindings."""
import json
import asyncio
import os
from pathlib import Path
from aiohttp import web

from automedia.thinking.methods import METHODS
from automedia.thinking.planner import Planner, ModelGateway
from automedia.infrastructure.store import DomainError, Store
from .runtime import RuntimeStore
from automedia.thinking.engine import Engine
from .messages import MessageDelivery
from automedia.behavior.appearance import AppearanceService


def install(app, root):
    store = RuntimeStore(os.environ.get("AUTOMEDIA_OPERATIONS_DB") or Path(root) / "memory" / "operations.sqlite3")
    concurrency = max(1, min(16, int(os.environ.get("AUTOMEDIA_MODEL_CONCURRENCY", "4"))))
    planner = Planner(store, ModelGateway(concurrency))
    engine = Engine(store, planner.gateway)
    delivery = MessageDelivery(store)
    appearance = None

    async def appearance_handle(request):
        try:
            if request.match_info.get("asset_id"):
                from PIL import Image
                path = appearance.asset(request.match_info["asset_id"], request.match_info["role"])
                with Image.open(path) as image:
                    content_type = Image.MIME.get(image.format, "application/octet-stream")
                return web.FileResponse(path, headers={"Content-Type": content_type})
            if request.method == "POST":
                data = await request.json()
                if not isinstance(data, dict):
                    raise DomainError("请求需要JSON对象")
                result = appearance.start(data.get("expected_revision"))
            else:
                result = {"versions": appearance.history()}
            return web.json_response(result, headers={"Cache-Control": "no-store"})
        except (DomainError, ValueError, TypeError) as exc:
            return web.json_response({"error": str(exc)}, status=getattr(exc, "status", 400))

    app.router.add_get("/appearance", appearance_handle)
    app.router.add_post("/appearance", appearance_handle)
    app.router.add_get("/appearance/{asset_id}/{role}", appearance_handle)

    async def journal_handle(request):
        from .journal import recent_work
        items = await asyncio.to_thread(recent_work, store, Path(root) / "tasks")
        from datetime import datetime
        for version in appearance.history():
            if version["state"] == "completed":
                items.append({"id": version["id"], "kind": "appearance", "title": "她的新形象，已经留档",
                              "state": "completed", "at": version["created_at"], "source": "形象创作",
                              "excerpt": version.get("reference_review", {}).get("description") or version["message"]})
        items.sort(key=lambda r: datetime.fromisoformat(r["at"]).timestamp(), reverse=True)
        return web.json_response({"items": items}, headers={"Cache-Control": "no-store"})

    app.router.add_get("/journal", journal_handle)

    async def content_handle(request):
        try:
            from tools.xhs_test_control import status, preflight, TestDeferred
            action = request.match_info["action"]
            data = await request.json() if request.method == "POST" else {}
            if not isinstance(data, dict):
                raise DomainError("请求需要JSON对象")
            if action == "state":
                store.collect_content()
                control = status()
                result = {"contents": store.contents(), "publish_ready": control.get("publish_ready"),
                          "binding_available": bool(control.get("binding"))}
            elif action == "create":
                result = store.create_content(data.get("objective"), data.get("account_id"),
                    data.get("assets"), data.get("sources"), status().get("binding"), data.get("request_key"),
                    fresh_workspace=data.get("fresh_workspace", True))
            elif action in ("publish", "retry"):
                row = store.content(data.get("id"))
                binding = row["brief"]["binding"]
                if not row["publish_job"] or action == "retry":
                    try:
                        preflight("publish", binding["owner_id"], binding["nickname"], "desktop")
                    except TestDeferred as exc:
                        store.event(row["draft_job"], "behavior", "publication_deferred", exc.result)
                        return web.json_response(exc.result)
                result = (store.retry_unsubmitted_content(row["id"], data.get("fingerprint"), data.get("request_key"))
                          if action == "retry" else store.queue_content(row["id"], data.get("fingerprint")))
            elif action == "cancel":
                result = store.cancel_content(data.get("id"))
                for key in ("draft_job", "publish_job"):
                    if result[key]:
                        engine.cancel(result[key])
            else:
                raise DomainError("接口不存在", 404)
            return web.json_response(result, headers={"Cache-Control": "no-store"})
        except (DomainError, ValueError, TypeError, OSError) as exc:
            return web.json_response({"error": str(exc)}, status=getattr(exc, "status", 400))

    app.router.add_get("/content/{action:state}", content_handle)
    app.router.add_post("/content/{action:create|publish|retry|cancel}", content_handle)

    async def messages_handle(request):
        try:
            action = request.match_info["action"]
            data = await request.json() if request.method == "POST" else {}
            if not isinstance(data, dict):
                raise DomainError("请求需要JSON对象")
            if action == "state":
                result = store.message_status()
                result["environment"] = engine.message_environment
            elif action == "contacts":
                result = store.create_contact(data.get("platform"), data.get("owner_id"), data.get("peer_id"),
                                              data.get("label"), data.get("auto_draft", False))
            elif action == "conversation":
                result = store.conversation(request.query.get("contact_id"))
            elif action == "import":
                # Public endpoint cannot forge an adapter observation or outbound receipt.
                result = store.ingest_message(data.get("contact_id"), data.get("request_key"),
                                              data.get("content"), source="controller_import")
            elif action == "reply":
                result = store.prepare_reply(data.get("contact_id"), data.get("instruction"), data.get("account_id"))
            elif action == "edit":
                result = store.edit_outbox(data.get("id"), data.get("expected_revision"),
                                           data.get("content"), data.get("commitments"))
            elif action == "authorize":
                result = store.authorize_outbox(data.get("id"), data.get("expected_revision"),
                                                data.get("digest"), data.get("expires_at"))
            elif action == "cancel":
                result = store.cancel_outbox(data.get("id"), data.get("expected_revision"))
            elif action == "deliver":
                result = await delivery.deliver(data.get("id"))
            elif action == "resolve":
                result = store.resolve_commitment(data.get("id"), data.get("evidence"))
            elif action == "events":
                result = {"events": store.message_events(request.query.get("id"))}
            else:
                raise DomainError("接口不存在", 404)
            return web.json_response(result, headers={"Cache-Control": "no-store"})
        except (DomainError, ValueError, TypeError) as exc:
            return web.json_response({"error": str(exc)}, status=getattr(exc, "status", 400))

    app.router.add_get("/messages/{action:state|conversation|events}", messages_handle)
    app.router.add_post("/messages/{action:contacts|import|reply|edit|authorize|cancel|deliver|resolve}", messages_handle)

    async def runtime_handle(request):
        try:
            action = request.match_info["action"]
            data = await request.json() if request.method == "POST" else {}
            if not isinstance(data, dict):
                raise DomainError("请求需要JSON对象")
            if action == "character":
                result = store.update_character(data.get("expected_revision"), data.get("profile")) if request.method == "POST" else store.character()
            elif action == "jobs":
                result = engine.submit(data) if request.method == "POST" else {"jobs": store.runs()}
            elif action == "events":
                result = {"events": store.events(request.query.get("job_id"))}
            elif action == "cancel":
                result = engine.cancel(data.get("job_id"))
            elif action == "goals":
                result = (store.create_goal(data.get("objective"), data.get("success_criteria"),
                                           data.get("account_id"), data.get("request_key"),
                                           data.get("allowed_kinds")) if request.method == "POST"
                          else {"goals": store.goals()})
            elif action == "goal-events":
                result = {"events": store.goal_events(request.query.get("goal_id"))}
            elif action == "goal-signal":
                result = store.goal_signal(data.get("goal_id"), data.get("content"), data.get("request_key"))
            elif action == "goal-control":
                result = engine.control_goal(data)
            elif action == "goal-watch":
                result = (store.watch_goal(data.get("goal_id"), data.get("expected_revision"),
                    data.get("kind"), data.get("source_id"), data.get("enabled", True))
                    if request.method == "POST" else store.goal_watches(request.query.get("goal_id")))
            elif action == "health":
                result = engine.health()
            else:
                raise DomainError("接口不存在", 404)
            return web.json_response(result, headers={"Cache-Control": "no-store"})
        except (DomainError, ValueError, TypeError) as exc:
            return web.json_response({"error": str(exc)}, status=getattr(exc, "status", 400))

    app.router.add_get("/runtime/{action:character|jobs|events|goals|goal-events|health|goal-watch}", runtime_handle)
    app.router.add_post("/runtime/{action:character|jobs|cancel|goals|goal-signal|goal-control|goal-watch}", runtime_handle)

    async def handle(request):
        try:
            action = request.match_info.get("action", "")
            account_id = request.match_info.get("account_id")
            data = {}
            if request.method == "POST":
                if request.content_length and request.content_length > 65536:
                    raise DomainError("内容过长")
                try:
                    raw = bytearray()
                    async for chunk in request.content.iter_chunked(8192):
                        raw.extend(chunk)
                        if len(raw) > 65536:
                            raise DomainError("内容过长")
                    data = json.loads(raw)
                    if not isinstance(data, dict):
                        raise ValueError()
                except (ValueError, UnicodeError):
                    raise DomainError("请求需要JSON对象")
            if not account_id:
                result = (store.create_account(data.get("name")) if request.method == "POST"
                          else {"accounts": store.accounts(), "methods": [
                              {"id": key, "name": m["name"], "version": m["version"]}
                              for key, m in METHODS.items()]})
            elif action == "profile":
                result = (store.update_profile(account_id, data.get("expected_revision"), data.get("fields"))
                          if request.method == "POST" else store.account(account_id))
            elif action == "history":
                result = {"history": store.history(account_id)}
            elif action == "memories":
                if request.method == "POST":
                    result = {"id": store.add_memory(account_id, data)}
                else:
                    result = {"memories": store.memories(
                        account_id, request.query.get("q", ""), request.query.get("all") == "1")}
            elif action == "retract":
                store.retract_memory(account_id, data.get("id"))
                result = {"ok": True}
            elif action == "plans":
                result = (planner.start(account_id, data.get("objective"), data.get("request_key"),
                                        data.get("memory_query", ""))
                          if request.method == "POST" else {"plans": store.jobs(account_id)})
            elif action == "cancel":
                result = planner.cancel(account_id, data.get("id"))
            else:
                raise DomainError("接口不存在", 404)
            return web.json_response(result, headers={"Cache-Control": "no-store"})
        except DomainError as exc:
            return web.json_response({"error": str(exc)}, status=exc.status)

    app.router.add_get("/ops/accounts", handle)
    app.router.add_post("/ops/accounts", handle)
    app.router.add_get("/ops/{account_id}/{action:profile|history|memories|plans}", handle)
    app.router.add_post("/ops/{account_id}/{action:profile|memories|retract|plans|cancel}", handle)

    async def startup(_):
        nonlocal appearance
        store.acquire_owner()
        appearance = AppearanceService(store)
        store.recover_interrupted()
        await engine.start()

    async def cleanup(_):
        if appearance:
            await appearance.close()
        await engine.close()
        await planner.close()
        store.release_owner()

    app.on_startup.append(startup)
    app.on_cleanup.append(cleanup)
