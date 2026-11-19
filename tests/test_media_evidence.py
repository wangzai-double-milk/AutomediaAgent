"""Input provenance and cleanup: model claims cannot supply missing media facts."""
from types import SimpleNamespace

import pytest

from tools import image
from tools.media_evidence import analyze_observed_video


def test_storage_quota_does_not_trigger_second_upload_or_download(monkeypatch):
    attempts = []
    def upload(*args, **kwargs):
        attempts.append(args)
        raise RuntimeError("AccountFileStorageQuotaExceeded")
    monkeypatch.setattr(image, "upload_video_for_understanding", upload)
    monkeypatch.setattr(image, "download_video_to_temp",
                        lambda *a, **k: pytest.fail("quota must not cause another download/upload"))
    result = image._prepare_one_video("https://v1.douyinvod.com/video/tos/observed")
    assert len(attempts) == 1
    assert result["video_url"] == "https://v1.douyinvod.com/video/tos/observed"
    assert result["uploaded_fid"] is None


def test_unobserved_host_rejected_before_download():
    api = SimpleNamespace(download_video_to_temp=lambda *a: pytest.fail("must not download"))
    with pytest.raises(ValueError):
        analyze_observed_video("https://douyinvod.com.evil.test/test.mp4", "", api)


@pytest.mark.parametrize("audio", [False, True])
def test_track_evidence_and_cleanup_survive_model_failure(tmp_path, monkeypatch, audio):
    path = tmp_path / "download.mp4"
    path.write_bytes(b"isolated input")
    stderr = (b"Duration: 00:00:02.00\n  Stream #0:0: Video: h264\n"
              + (b"  Stream #0:1: Audio: aac\n" if audio else b""))
    monkeypatch.setattr("subprocess.run", lambda *a, **k: SimpleNamespace(stderr=stderr))
    def analyze(files, prompt, **kwargs):
        assert files == [str(path)] and path.exists()
        assert ("禁止声称听到" in prompt) is (not audio)
        kwargs["evidence_callback"]({"input_transport": ["observed_url"]})
        return "[Error] provider unavailable"
    api = SimpleNamespace(download_video_to_temp=lambda _: str(path), analyze_image=analyze)
    result = analyze_observed_video("https://v1.douyinvod.com/video/tos/observed", "", api, 2)
    assert result["analysis"].startswith("[Error]")
    assert result["media_evidence"]["audio_track"] is audio
    assert result["media_evidence"]["input_transport"] == ["observed_url"]
    assert not path.exists()


def test_duration_mismatch_cleans_download_without_model_call(tmp_path, monkeypatch):
    path = tmp_path / "wrong.mp4"
    path.write_bytes(b"wrong video")
    monkeypatch.setattr("subprocess.run", lambda *a, **k: SimpleNamespace(
        stderr=b"Duration: 00:00:02.00\n  Stream #0:0: Video: h264"))
    api = SimpleNamespace(download_video_to_temp=lambda _: str(path),
                          analyze_image=lambda *a, **k: pytest.fail("wrong video must not reach model"))
    with pytest.raises(ValueError, match="不一致"):
        analyze_observed_video("https://v1.douyinvod.com/video/tos/observed", "", api, 300)
    assert not path.exists()


def test_current_reference_uses_original_observation_without_url_retyping(monkeypatch):
    from ga import GenericAgentHandler
    from agent_loop import exhaust
    url = "https://v1.douyinvod.com/video/tos/observed?signature=exact%2Bvalue"
    captured = []
    def analyze(actual, prompt, api, duration):
        captured.append(actual)
        return {"analysis": "可见画面", "media_scope": "visual_track_only", "media_evidence": {}}
    monkeypatch.setattr("tools.media_evidence.analyze_observed_video", analyze)
    handler = GenericAgentHandler(SimpleNamespace(runtime_job={"kind": "douyin_research"}))
    handler.working["douyin_observed_media"] = {
        "stream_url": url, "content_kind": "video", "duration": 2, "evidence_ref": "local-observation"}
    handler._xhs_task = lambda: SimpleNamespace(append_trace=lambda *a: None)
    handler._xhs_import = lambda: SimpleNamespace(
        _is_video_page_url=lambda _: False, _is_direct_video_url=lambda _: True)
    handler._get_anchor_prompt = lambda **k: ""
    result = exhaust(handler.do_analyze_media({"video_url": "current", "prompt": "分析"}, None))
    assert captured == [url]
    assert result.data["media_evidence"]["source_observation"] == "local-observation"
    assert result.data["media_scope"] == "visual_track_only"


def test_retyped_or_missing_current_source_is_rejected():
    from ga import GenericAgentHandler
    from agent_loop import exhaust
    handler = GenericAgentHandler(SimpleNamespace(runtime_job={"kind": "douyin_research"}))
    for args in ({"video_url": "current", "prompt": "分析"},
                 {"video_url": "https://v1.douyinvod.com/guessed", "prompt": "分析"}):
        result = exhaust(handler.do_analyze_media(args, None))
        assert result.should_exit and result.data["status"] == "no_media_source"
