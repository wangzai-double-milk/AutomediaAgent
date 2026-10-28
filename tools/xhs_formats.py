"""Longform and podcast creator workflows using observed macOS controls."""

import re
import subprocess
from pathlib import Path

from tools import xhs_desktop as desktop


def audio_duration(path):
    import imageio_ffmpeg
    result = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-i", str(path)],
                            capture_output=True, text=True, timeout=15)
    match = re.search(r"Duration:\s*(\d+):(\d+):([\d.]+)", result.stderr)
    if not match:
        raise ValueError("无法读取音频实际时长")
    return int(match[1]) * 3600 + int(match[2]) * 60 + float(match[3])


def prepare(spec, expected_account):
    d = desktop
    current = d.snapshot()
    if d.editor(current)["has_editor"] or any(
            n["role"] == "AXTextArea" and d.visible(n) for n in current["web"]):
        raise RuntimeError("desktop_unsaved_editor_present")
    opened = d.open_area(spec["kind"])
    if not opened["ok"]:
        raise RuntimeError("desktop_format_entry_unavailable")
    current = d.snapshot()
    account = d.identity(current)
    if not account or (expected_account and account != expected_account):
        raise RuntimeError("desktop_account_mismatch")
    if spec["kind"] == "longform":
        stages = _longform(spec)
    else:
        stages = _podcast(spec)
    contents = d.fill_editor(spec)
    visibility = d.set_visibility(spec["visibility"])
    schedule = d.set_schedule(spec["schedule_at"])
    if d.identity() != account:
        raise RuntimeError("desktop_format_account_changed")
    return {"snapshot": contents, "visibility": visibility, "schedule": schedule,
            "account": account, "format_stages": stages, "transport": "macOS_accessibility"}


def _longform(spec):
    d = desktop
    d.press_label(["新的创作"])
    current = d.wait(lambda s: any(n["role"] == "AXTextArea" and n.get("placeholder") == "输入标题"
                                   for n in s["web"]), 12)
    d.act("fill", current, d.choose(current, lambda n: n["role"] == "AXTextArea"
                                    and n.get("placeholder") == "输入标题"), text=spec["title"])
    current = d.wait(lambda s: any(n["role"] == "AXTextArea" and n.get("placeholder") == "输入标题"
                                   and n.get("value") == spec["title"] for n in s["web"]), 5)
    body = d.choose(current, lambda n: n["role"] == "AXTextArea" and not n.get("placeholder"))
    d.act("press", current, body)
    current = d.wait(lambda s: any(n["role"] == "AXTextArea" and n.get("focused") for n in s["web"]), 4)
    d.act("type", current, d.choose(current, lambda n: n["role"] == "AXTextArea" and n.get("focused")),
          text=spec["longform_body"], replace=True)
    compact = lambda text: "".join(text.split())
    current = d.wait(lambda s: any(n["role"] == "AXTextArea" and not n.get("placeholder")
                                   and compact(str(n.get("value") or "")) == compact(spec["longform_body"])
                                   for n in s["web"]), 10)
    content = {"title": spec["title"], "body": spec["longform_body"], "verified": True,
               "comparison": "non_whitespace_text"}
    d.press_label(["一键排版"], current)
    current = d.wait(lambda s: any(d.text(n) == "选择模板" for n in s["web"])
                     and any(n["role"] == "AXButton" and d.text(n) == "下一步" for n in s["web"]), 20)
    formatted = d.page_summary(current)
    d.press_label(["下一步"], current)
    d.wait(lambda s: d.editor(s)["has_editor"] and not any(
        "笔记图片生成中" in d.text(n) for n in s["web"]), 60)
    return {"source": content, "formatted": formatted, "image_pixels_verified": False}


def _podcast(spec):
    d = desktop
    d.press_label(["上传音频"])
    current = d.wait(lambda s: any(d.text(n) == "上传音频封面" for n in s["web"]), 8)
    files = [n for n in current["web"] if n["role"] == "AXButton"
             and d.text(n).startswith("选择文件") and d.visible(n)]
    if len(files) != 2:
        raise RuntimeError("desktop_podcast_audio_cover_controls_ambiguous")
    files.sort(key=lambda n: n["bounds"]["y"])
    d.act("press", current, files[0])
    d.select_open_file(spec["files"][0])
    d.wait(lambda s: any(d.text(n) == Path(spec["files"][0]).name for n in s["web"]), 20)
    current = d.snapshot()
    label = d.choose(current, lambda n: d.text(n) == "上传音频封面")
    nodes = {n["ref"]: n for n in current["web"]}
    ancestor = label.get("parent")
    while ancestor in nodes:
        files = [n for n in d.descendants(current, ancestor) if n["role"] == "AXButton"
                 and d.text(n).startswith("选择文件") and d.visible(n)]
        if files:
            break
        ancestor = nodes[ancestor].get("parent")
    if len(files) != 1:
        raise RuntimeError("desktop_podcast_cover_control_changed")
    d.act("press", current, files[0])
    d.select_open_file(spec["files"][1])
    current = d.wait(lambda s: any(d.text(n) == "上传成功" for n in s["web"])
                     and any(d.text(n) == "设置封面" for n in s["web"]), 180)
    uploaded = d.page_summary(current)
    d.press_label(["去发布"], current)
    d.wait(lambda s: d.editor(s)["has_editor"] and any(
        d.text(n) == Path(spec["files"][0]).name for n in s["web"]), 30)
    return {"upload": uploaded, "audio_duration": spec["audio_duration"],
            "cover_uploaded": True, "media_pixels_verified": False}
