"""Media input evidence: inspect observed video before making audiovisual claims."""
import hashlib
import os
import re
import subprocess
from urllib.parse import urlsplit


def analyze_observed_video(url, prompt, media_api, expected_duration=None):
    """Use one downloaded file; an upload-quota fallback uses that same observed URL.

    Container tracks prove availability, not that the model heard every word.
    No browser history, private store, alternate stream or provider file inventory is read.
    """
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or parsed.username or parsed.password
            or not (parsed.hostname == "douyinvod.com" or
                    (parsed.hostname or "").endswith(".douyinvod.com"))):
        raise ValueError("仅接受已观察到的抖音当前播放器HTTPS源")
    path = media_api.download_video_to_temp(url)
    try:
        import imageio_ffmpeg
        probe = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-i", path],
                               capture_output=True, timeout=30)
        info = probe.stderr.decode("utf-8", errors="replace")
        streams = [line for line in info.splitlines() if re.match(r"\s*Stream #\d+:\d+", line)]
        has_video = any("Video:" in line for line in streams)
        has_audio = any("Audio:" in line for line in streams)
        match = re.search(r"Duration:\s*(\d+):(\d+):([\d.]+)", info)
        duration = int(match[1]) * 3600 + int(match[2]) * 60 + float(match[3]) if match else None
        if not has_video or duration is None:
            raise ValueError("下载文件未能验证为有时长的视频")
        if expected_duration is not None and abs(duration - expected_duration) > max(2, expected_duration * .02):
            raise ValueError("下载媒体时长与页面播放器不一致，未提交模型分析")
        sha = hashlib.sha256()
        with open(path, "rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                sha.update(chunk)
        evidence = {"sha256": sha.hexdigest(), "bytes": os.path.getsize(path),
                    "duration_seconds": duration, "video_track": True, "audio_track": has_audio,
                    "probe": "ffmpeg_container", "page_duration": expected_duration,
                    "coverage": "完整下载文件；模型按视频采样处理，不能据此声称逐帧或逐字核验"}
        scope = "video_with_audio_track" if has_audio else "visual_track_only"
        boundary = ("\n输入文件含音轨。区分实际可辨认声音、画面字幕和推测；"
                    "听不清的内容标为未知，不声称逐字准确转写。"
                    if has_audio else "\n输入文件没有音轨，只能分析画面与字幕；禁止声称听到口播或音乐。")
        # Direct URL fallback is bounded by the same 50MB input limit; larger files remain failed.
        fallbacks = {path: url} if evidence["bytes"] <= 50 * 1024**2 else {}
        analysis = media_api.analyze_image([path], prompt + boundary + "\n" + evidence["coverage"],
                                           video_fallbacks=fallbacks, evidence_callback=evidence.update)
        return {"analysis": analysis, "media_scope": scope, "media_evidence": evidence}
    finally:
        os.remove(path)
