"""Host installation evidence only; installed apps do not establish accounts."""
from datetime import datetime, timezone
from pathlib import Path
import plistlib
import sys


def inspect_message_environment(roots=None):
    roots = roots if roots is not None else [Path("/Applications"), Path.home() / "Applications"]
    found = {"wechat": [], "qq": []}
    errors = []
    for root in roots:
        for app in Path(root).glob("*.app"):
            try:
                with (app / "Contents/Info.plist").open("rb") as handle:
                    bundle = plistlib.load(handle).get("CFBundleIdentifier")
                platform = {"com.tencent.xinWeChat": "wechat", "com.tencent.qq": "qq"}.get(bundle)
                if platform:
                    found[platform].append(str(app))
            except (OSError, ValueError, plistlib.InvalidFileException):
                errors.append(app.name)
    return {"observed_at": datetime.now(timezone.utc).isoformat(),
            "coverage": [str(p) for p in roots], "unreadable_bundles": errors,
            "platforms": {p: {"status": "installed_unverified" if paths else
                "not_found_in_checked_locations" if sys.platform == "darwin" else "unsupported_host",
                "app_paths": paths, "identity_verified": False, "listener_connected": False}
                for p, paths in found.items()},
            "scope": "仅检查应用安装元数据；没有读取会话、核验账号或建立监听"}
