"""Unknown browser outcomes do not justify replaying physical input."""

from types import SimpleNamespace

import ga
from tools import web_vision


def test_cached_click_with_unknown_fingerprint_is_not_repeated(monkeypatch):
    clicks = []
    monkeypatch.setattr(web_vision, "_try_cached", lambda _: {"ok": True, "x": 10, "y": 20})
    monkeypatch.setattr(web_vision, "_page_fingerprint", lambda: {})
    monkeypatch.setattr(web_vision, "time", SimpleNamespace(sleep=lambda _: None))
    monkeypatch.setattr(ga, "cdp_mouse_click", lambda *args: clicks.append(args) or {})

    def unexpected(*args, **kwargs):
        raise AssertionError("Must not retry with another click strategy")

    monkeypatch.setattr(web_vision, "som_click", unexpected)
    result = web_vision.visual_click("like button")
    assert clicks == [(10, 20)]
    assert result["ok"] is False
    assert result["verified"] is False
    assert result["dispatched"] is True
