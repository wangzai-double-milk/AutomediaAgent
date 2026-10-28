"""An unconfigured checkout supports imports and offline tests."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from tools import image as media


def test_media_import_does_not_require_local_secrets(tmp_path):
    root = Path(__file__).resolve().parents[1]
    shutil.copy2(root / "mykey.py", tmp_path / "mykey.py")
    (tmp_path / "tools").mkdir()
    shutil.copy2(root / "tools/image.py", tmp_path / "tools/image.py")
    env = {k: v for k, v in os.environ.items()
           if k not in ("AUTOMEDIA_CONFIG", "VOLC_API_KEY", "PYTHONPATH")}
    result = subprocess.run(
        [sys.executable, "-c",
         "import runpy; runpy.run_path('tools/image.py'); print('IMPORT_OK')"],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stderr
    assert "IMPORT_OK" in result.stdout


def test_media_explicit_config_without_adapter(monkeypatch, tmp_path):
    config = tmp_path / "config.json"
    config.write_text(json.dumps({
        "volc_apikey": "test-only-not-a-secret",
        "volc_apibase": "https://api.example.test/v3",
        "models": {"vlm": "test-vision"},
    }), encoding="utf-8")
    monkeypatch.setattr(media, "mykey", None)
    monkeypatch.setenv("AUTOMEDIA_CONFIG", str(config))
    cfg, models = media._load_media_config()
    assert cfg["apikey"] == "test-only-not-a-secret"
    assert cfg["apibase"] == "https://api.example.test/v3"
    assert models["vlm"] == "test-vision"
