"""Local, versioned appearance generation. No platform profile changes."""
import asyncio
import hashlib
import json
from pathlib import Path
import uuid
from urllib.parse import urlencode

import requests

from automedia.infrastructure.store import DomainError, now, revision

IMAGE_ENDPOINT = "https://copilot-cn.bytedance.net/api/ide/v1/tool_text_to_image"


class AppearanceService:
    def __init__(self, store):
        self.store = store
        self.root = store.path.parent / "character_assets"
        self.root.mkdir(exist_ok=True)
        self.task = None
        for row in self.history():
            if row["state"] in ("queued", "generating", "reviewing"):
                row.update(state="interrupted", message="生成进程已中断，已保留资产；不会自动重试。")
                self.save(row)

    def save(self, row):
        target = self.root / (row["id"] + ".json")
        temp = target.with_suffix(".tmp")
        temp.write_text(json.dumps(row, ensure_ascii=False, indent=2))
        temp.replace(target)

    def history(self):
        rows = []
        for p in self.root.glob("*.json"):
            # This directory also contains review reports; only UUID manifests
            # belong to the appearance lifecycle.
            if len(p.stem) != 32 or any(c not in "0123456789abcdef" for c in p.stem):
                continue
            try:
                row = json.loads(p.read_text())
                if (not isinstance(row, dict) or row.get("id") != p.stem or
                        not isinstance(row.get("created_at"), str) or
                        not isinstance(row.get("state"), str)):
                    continue
                rows.append(row)
            except (OSError, ValueError):
                continue
        return sorted(rows, key=lambda r: r["created_at"], reverse=True)[:20]

    def start(self, expected):
        revision(expected)
        if self.task and not self.task.done():
            raise DomainError("已有形象正在生成，请等待完成。", 409)
        character = self.store.character()
        if character["revision"] != expected:
            raise DomainError("角色已变化，请刷新后再生成。", 409)
        if not character["profile"]["appearance"]["description"]:
            raise DomainError("请先保存外貌方向。")
        row = {"id": uuid.uuid4().hex, "state": "queued", "created_at": now(),
               "expected_revision": expected, "message": "准备生成形象", "assets": []}
        self.save(row)
        self.task = asyncio.create_task(asyncio.to_thread(self.generate, row, character))
        return row

    def review(self, paths, direction):
        from tools import image
        payload = {
            "model": image.VLM_MODEL,
            "input": image._build_responses_input(
                "你是独立的人像质量审核者，不使用美食或流量评分标准。只检查图像可见事实。"
                "返回JSON：verdict为pass或revise，issues为字符串数组，description为简短中文。"
                "多图时还需核对是同一成年女性的五官/发色/气质。不得推断真实财富或出身。",
                "方向：" + direction + "。检查自然五官、手部、得体着装、无轻浮性感化、无文字水印。"
                "没有严重问题则pass，issues为空。", images=[image.image_to_url(str(p)) for p in paths]),
            "temperature": .2, "max_output_tokens": 2000}
        data = image._http_post_json(image.RESPONSES_URL, payload)
        result = json.loads(image._strip_md_fence(image._extract_output_text(data)))
        if result.get("verdict") != "pass" or result.get("issues") != []:
            raise DomainError("独立人像核对未通过：" + json.dumps(result.get("issues"), ensure_ascii=False))
        return result

    def generate(self, row, character):
        try:
            from PIL import Image
            from tools import image
            direction = character["profile"]["appearance"]["description"]
            prompt = ("Editorial portrait photograph of one fictional adult Chinese woman aged 26, "
                      "graceful educated warm personality, understated quiet luxury, cream cashmere cardigan "
                      "over modest high neck blouse, natural black shoulder length hair, gentle confident eyes, "
                      "subtle smile, minimal makeup, in an elegant sunlit library, walnut wood and warm ivory, "
                      "half body composition, realistic skin texture, 85mm lens, soft window light, "
                      "no text, no logo, no watermarks. Character direction: " + direction)
            row.update(state="generating", message="正在生成基础形象")
            row["web_source"] = IMAGE_ENDPOINT + "?" + urlencode({"prompt": prompt, "image_size": "portrait_4_3"})
            self.save(row)
            path = self.root / (row["id"] + "-reference.png")
            # Web graphics have a dedicated source. If its authentication is unavailable,
            # keep it explicit and generate the requested downloadable character asset.
            web_available = not any(r.get("web_available") is False for r in self.history())
            if web_available:
                response = requests.get(row["web_source"], timeout=240)
                if response.ok and response.headers.get("Content-Type", "").startswith("image/"):
                    if len(response.content) > 30_000_000:
                        raise DomainError("图片响应超过大小限制")
                    path.write_bytes(response.content)
                else:
                    web_available = False
            row["web_available"] = web_available
            if not web_available:
                row["web_message"] = "网页专用图像入口未通过认证；角色原图可单独打开。"
                self.save(row)
                image.generate_image(prompt, save_path=str(path))
            with Image.open(path) as im:
                im.verify()
            row["assets"].append({"role": "reference", "path": str(path),
                                  "source": row["web_source"] if web_available else image.GEN_MODEL,
                                  "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
            row.update(state="reviewing", message="正在独立核对基础形象")
            self.save(row)
            row["reference_review"] = self.review([path], direction)
            from tools import image
            scene = self.root / (row["id"] + "-scene.png")
            row.update(state="generating", message="Seedream 正在制作同一角色的场景版本")
            self.save(row)
            image.edit_image(str(path), "严格保持参考图同一位成年女性的面容、发型和气质。"
                             "生成一张真实摄影风格的竖版生活影像：坐在雅致书房的窗边，"
                             "穿高领米白针织衫，低头看一本无可辨识文字的书，自然手部，"
                             "暖木色和窗外柔光，构图留白，得体不轻浮，无品牌标志、无水印。", str(scene))
            row["assets"].append({"role": "scene", "path": str(scene), "source": image.EDIT_MODEL,
                                  "sha256": hashlib.sha256(scene.read_bytes()).hexdigest()})
            row.update(state="reviewing", message="正在核对场景版本的人物一致性")
            self.save(row)
            row["scene_review"] = self.review([path, scene], direction)
            profile = character["profile"]
            profile["appearance"]["references"] = [
                {"path": a["path"], "source": "AI生成虚拟角色；" + a["role"] + "；" +
                 (IMAGE_ENDPOINT if a["source"].startswith(IMAGE_ENDPOINT) else a["source"]) +
                 "；sha256=" + a["sha256"]}
                for a in row["assets"]]
            updated = self.store.update_character(character["revision"], profile)
            row.update(state="completed", character_revision=updated["revision"],
                       message="基础形象与场景版本已生成，独立核对通过。")
        except Exception as exc:
            # Never persist provider responses that may contain signed URLs or credentials.
            row.update(state="failed", message=str(exc) if isinstance(exc, DomainError)
                       else "生成未完成（" + type(exc).__name__ + "），原角色保持不变。")
        self.save(row)
        return row

    def asset(self, asset_id, role):
        if role not in ("reference", "scene") or len(asset_id) != 32 or not all(c in "0123456789abcdef" for c in asset_id):
            raise DomainError("形象资产不存在", 404)
        path = self.root / f"{asset_id}-{role}.png"
        if not path.is_file():
            raise DomainError("形象资产不存在", 404)
        return path

    async def close(self):
        if self.task:
            await asyncio.shield(self.task)
