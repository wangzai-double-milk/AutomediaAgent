"""Normal macOS Chrome accessibility/keyboard access when no CDP session exists.

This adapter does not enable developer policies, inspect cookies, or inject JS.
DOM-oriented platform tools still require CDP; accessibility evidence is separate.
"""

# macOS transport; platform logic lives in behavior.platforms.
import ctypes
import json
import sys
import threading
from uuid import uuid4


class _Point(ctypes.Structure):
    _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double)]


class _Size(ctypes.Structure):
    _fields_ = [("width", ctypes.c_double), ("height", ctypes.c_double)]


class ChromeAccessibility:
    def __init__(self):
        if sys.platform != "darwin":
            raise RuntimeError("macOS_required")
        self.ax = ctypes.CDLL("/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices")
        self.cf = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
        ptr = ctypes.c_void_p
        signatures = [
            (self.ax, "AXIsProcessTrusted", ctypes.c_bool, []),
            (self.ax, "AXUIElementCreateApplication", ptr, [ctypes.c_int]),
            (self.ax, "AXUIElementCopyAttributeValue", ctypes.c_int, [ptr, ptr, ctypes.POINTER(ptr)]),
            (self.ax, "AXUIElementSetAttributeValue", ctypes.c_int, [ptr, ptr, ptr]),
            (self.ax, "AXUIElementPerformAction", ctypes.c_int, [ptr, ptr]),
            (self.ax, "AXValueGetValue", ctypes.c_bool, [ptr, ctypes.c_int, ptr]),
            (self.cf, "CFStringCreateWithCString", ptr, [ptr, ctypes.c_char_p, ctypes.c_uint32]),
            (self.cf, "CFStringGetCString", ctypes.c_bool, [ptr, ctypes.c_void_p, ctypes.c_long, ctypes.c_uint32]),
            (self.cf, "CFGetTypeID", ctypes.c_ulong, [ptr]),
            (self.cf, "CFStringGetTypeID", ctypes.c_ulong, []),
            (self.cf, "CFArrayGetTypeID", ctypes.c_ulong, []),
            (self.cf, "CFBooleanGetTypeID", ctypes.c_ulong, []),
            (self.cf, "CFBooleanGetValue", ctypes.c_bool, [ptr]),
            (self.cf, "CFNumberGetTypeID", ctypes.c_ulong, []),
            (self.cf, "CFNumberGetValue", ctypes.c_bool, [ptr, ctypes.c_int, ptr]),
            (self.cf, "CFURLGetTypeID", ctypes.c_ulong, []),
            (self.cf, "CFURLGetString", ptr, [ptr]),
            (self.cf, "CFArrayGetCount", ctypes.c_long, [ptr]),
            (self.cf, "CFArrayGetValueAtIndex", ptr, [ptr, ctypes.c_long]),
            (self.cf, "CFRetain", ptr, [ptr]),
            (self.cf, "CFRelease", None, [ptr]),
        ]
        for lib, name, result, args in signatures:
            fn = getattr(lib, name)
            fn.restype, fn.argtypes = result, args
        if not self.ax.AXIsProcessTrusted():
            raise RuntimeError("accessibility_permission_required")
        from AppKit import NSRunningApplication
        apps = NSRunningApplication.runningApplicationsWithBundleIdentifier_("com.google.Chrome")
        if len(apps) != 1:
            raise RuntimeError("existing_chrome_not_identified")
        self.app = apps[0]
        self.root = self.ax.AXUIElementCreateApplication(self.app.processIdentifier())
        self.refs = {}
        self.snapshot_id = None

    def request_web_accessibility(self):
        """Standard screen-reader request; does not change Chrome policies."""
        value = ctypes.c_void_p.in_dll(self.cf, "kCFBooleanTrue")
        attempts = []
        for name in ("AXManualAccessibility", "AXEnhancedUserInterface"):
            attribute = self._string(name)
            try:
                code = self.ax.AXUIElementSetAttributeValue(self.root, attribute, value)
                attempts.append({"attribute": name, "error_code": code})
                if code == 0:
                    return {"ok": True, "attempts": attempts}
            finally:
                self.cf.CFRelease(attribute)
        return {"ok": False, "attempts": attempts}

    def _string(self, text):
        return self.cf.CFStringCreateWithCString(None, text.encode(), 0x08000100)

    def _activate(self):
        """Wait for actual foreground ownership before sending OS input events."""
        import time
        from AppKit import NSWorkspace
        from Foundation import NSDate, NSRunLoop
        self.app.activateWithOptions_(2)
        deadline = time.monotonic() + 3
        while NSWorkspace.sharedWorkspace().frontmostApplication().processIdentifier() != self.app.processIdentifier():
            if time.monotonic() >= deadline:
                raise RuntimeError("chrome_foreground_not_verified")
            NSRunLoop.currentRunLoop().runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(.025))

    def _attribute(self, element, name):
        key, value = self._string(name), ctypes.c_void_p()
        try:
            error = self.ax.AXUIElementCopyAttributeValue(element, key, ctypes.byref(value))
            return value.value if error == 0 else None
        finally:
            self.cf.CFRelease(key)

    def _scalar(self, element, name):
        value = self._attribute(element, name)
        if not value:
            return None
        try:
            kind = self.cf.CFGetTypeID(value)
            if kind == self.cf.CFStringGetTypeID():
                buf = ctypes.create_string_buffer(131072)
                if self.cf.CFStringGetCString(value, buf, len(buf), 0x08000100):
                    return buf.value.decode("utf-8")
            if kind == self.cf.CFBooleanGetTypeID():
                return self.cf.CFBooleanGetValue(value)
            if kind == self.cf.CFNumberGetTypeID():
                number = ctypes.c_double()
                if self.cf.CFNumberGetValue(value, 13, ctypes.byref(number)):
                    return number.value
            if kind == self.cf.CFURLGetTypeID():
                buf = ctypes.create_string_buffer(32768)
                if self.cf.CFStringGetCString(self.cf.CFURLGetString(value), buf, len(buf), 0x08000100):
                    return buf.value.decode("utf-8")
            return None
        finally:
            self.cf.CFRelease(value)

    def _children(self, element, attribute="AXChildren"):
        value = self._attribute(element, attribute)
        if not value:
            return []
        try:
            if self.cf.CFGetTypeID(value) != self.cf.CFArrayGetTypeID():
                return []
            return [self.cf.CFRetain(self.cf.CFArrayGetValueAtIndex(value, i))
                    for i in range(self.cf.CFArrayGetCount(value))]
        finally:
            self.cf.CFRelease(value)

    def _bounds(self, element):
        position, size = self._attribute(element, "AXPosition"), self._attribute(element, "AXSize")
        try:
            point, dimensions = _Point(), _Size()
            if (position and size and self.ax.AXValueGetValue(position, 1, ctypes.byref(point))
                    and self.ax.AXValueGetValue(size, 2, ctypes.byref(dimensions))):
                return {"x": point.x, "y": point.y, "width": dimensions.width, "height": dimensions.height}
        finally:
            if position:
                self.cf.CFRelease(position)
            if size:
                self.cf.CFRelease(size)
        return None

    def close(self):
        for element in self.refs.values():
            self.cf.CFRelease(element)
        self.refs.clear()
        if self.root:
            self.cf.CFRelease(self.root)
            self.root = None

    def snapshot(self, max_nodes=3500, max_depth=45):
        for element in self.refs.values():
            self.cf.CFRelease(element)
        self.refs.clear()
        self.snapshot_id = uuid4().hex
        nodes, visited, truncated = [], 0, False

        def walk(element, depth, parent):
            nonlocal visited, truncated
            if visited >= max_nodes or depth > max_depth:
                truncated = True
                return
            visited += 1
            role = self._scalar(element, "AXRole")
            title = self._scalar(element, "AXTitle") or self._scalar(element, "AXDescription")
            value = self._scalar(element, "AXValue")
            ref = parent
            if title or value or role in {"AXWebArea", "AXTextField", "AXTextArea", "AXButton", "AXLink",
                                        "AXCheckBox", "AXSwitch", "AXRadioButton", "AXGroup", "AXImage"}:
                ref = "a" + str(len(nodes) + 1)
                # Do not expose password fields.
                protected = self._scalar(element, "AXSubrole") == "AXSecureTextField"
                nodes.append({"ref": ref, "parent": parent, "role": role,
                              "title": (title or "")[:500],
                              "value": None if protected else (value[:16000] if isinstance(value, str) else value),
                              "enabled": self._scalar(element, "AXEnabled"),
                              "focused": self._scalar(element, "AXFocused"),
                              "bounds": self._bounds(element),
                              "placeholder": self._scalar(element, "AXPlaceholderValue"),
                              "url": self._scalar(element, "AXURL")})
                self.refs[ref] = self.cf.CFRetain(element)
            children = self._children(element)
            try:
                for child in children:
                    walk(child, depth + 1, ref)
            finally:
                for child in children:
                    self.cf.CFRelease(child)

        windows = self._children(self.root, "AXWindows")
        try:
            # Chrome find popup can precede the main window in AXWindows.
            if windows:
                def area(window):
                    bounds = self._bounds(window) or {}
                    return bounds.get("width", 0) * bounds.get("height", 0)
                main = max(windows, key=area)
                walk(main, 0, None)
                # Native leave-site/file dialogs are separate AXWindows. Include
                # them after the main tree, rather than silently losing controls.
                for window in windows:
                    if window != main:
                        walk(window, 0, None)
        finally:
            for window in windows:
                self.cf.CFRelease(window)
        focused = self._attribute(self.root, "AXFocusedUIElement")
        focus_info = None
        if focused:
            try:
                focus_info = {"role": self._scalar(focused, "AXRole"),
                              "title": self._scalar(focused, "AXTitle") or self._scalar(focused, "AXDescription"),
                              "value": self._scalar(focused, "AXValue"), "bounds": self._bounds(focused)}
            finally:
                self.cf.CFRelease(focused)
        # Chrome can expose window controls but omit the document until a screen
        # reader requests web accessibility. Try its standard AX request once;
        # never navigate, change browser policy, or treat missing content as read.
        if (nodes and not any(n["role"] == "AXWebArea" for n in nodes)
                and not getattr(self, "_web_accessibility_requested", False)):
            self._web_accessibility_requested = True
            requested = self.request_web_accessibility()
            self._web_accessibility_result = requested
            if requested.get("ok"):
                from Foundation import NSDate, NSRunLoop
                NSRunLoop.currentRunLoop().runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(.1))
                recovered = self.snapshot(max_nodes=max_nodes, max_depth=max_depth)
                recovered["web_accessibility_request"] = requested
                return recovered
        return {"ok": bool(nodes), "transport": "macOS_accessibility", "snapshot_id": self.snapshot_id,
                "nodes": nodes, "truncated": truncated, "visited": visited, "focused_element": focus_info,
                **({"web_accessibility_request": self._web_accessibility_result}
                   if hasattr(self, "_web_accessibility_result") else {})}

    def press(self, ref, snapshot_id):
        if snapshot_id != self.snapshot_id or ref not in self.refs:
            return {"ok": False, "reason": "stale_accessibility_ref"}
        bounds = self._bounds(self.refs[ref])
        if self._scalar(self.refs[ref], "AXEnabled") is False:
            return {"ok": False, "reason": "accessibility_control_disabled"}
        if not bounds or bounds["width"] < 2 or bounds["height"] < 2:
            return {"ok": False, "reason": "accessibility_control_not_visible"}
        self._activate()
        action = self._string("AXPress")
        try:
            role = self._scalar(self.refs[ref], "AXRole")
            code = (-25206 if role == "AXStaticText"
                    else self.ax.AXUIElementPerformAction(self.refs[ref], action))
            if code in {-25206, -25205}:
                bounds = self._bounds(self.refs[ref])
                if bounds and bounds["width"] > 0 and bounds["height"] > 0:
                    import Quartz
                    position = (bounds["x"] + bounds["width"] / 2, bounds["y"] + bounds["height"] / 2)
                    for kind in (Quartz.kCGEventLeftMouseDown, Quartz.kCGEventLeftMouseUp):
                        event = Quartz.CGEventCreateMouseEvent(None, kind, position, Quartz.kCGMouseButtonLeft)
                        Quartz.CGEventSetFlags(event, 0)
                        Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)
                    return {"ok": True, "method": "accessibility_coordinates", "verified": False}
            return {"ok": code == 0, "error_code": code, "verified": False}
        finally:
            self.cf.CFRelease(action)
            # Any press may change the tree; consume all refs.
            self.snapshot_id = None

    def set_text(self, ref, snapshot_id, text):
        if snapshot_id != self.snapshot_id or ref not in self.refs:
            return {"ok": False, "reason": "stale_accessibility_ref"}
        element = self.refs[ref]
        focus, value_key, value = self._string("AXFocused"), self._string("AXValue"), self._string(text)
        try:
            self._activate()
            true = ctypes.c_void_p.in_dll(self.cf, "kCFBooleanTrue")
            self.ax.AXUIElementSetAttributeValue(element, focus, true)
            code = self.ax.AXUIElementSetAttributeValue(element, value_key, value)
            # Chrome commits editable values asynchronously; verify in the next snapshot.
            if code == 0:
                return {"ok": True, "method": "accessibility_value", "verified": False, "length": len(text)}
            bounds = self._bounds(element)
            if not bounds or bounds["width"] < 2 or bounds["height"] < 2:
                return {"ok": False, "reason": "input_not_visible", "error_code": code}
            import Quartz
            self._activate()
            position = (bounds["x"] + bounds["width"] / 2, bounds["y"] + bounds["height"] / 2)
            for kind in (Quartz.kCGEventLeftMouseDown, Quartz.kCGEventLeftMouseUp):
                event = Quartz.CGEventCreateMouseEvent(None, kind, position, Quartz.kCGMouseButtonLeft)
                Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)
            self.shortcut(0, command=True)
            self.type_text(text)
            return {"ok": True, "method": "keyboard", "verified": False, "length": len(text)}
        finally:
            for pointer in (focus, value_key, value):
                self.cf.CFRelease(pointer)
            self.snapshot_id = None

    def toggle_checkbox(self, ref, snapshot_id, label_ref):
        """AXPress on a native hidden input paired with a rendered row label.

        Styled switches expose the actual checkbox with a zero-height input.
        No pixel click is used for this semantic accessibility action.
        """
        if snapshot_id != self.snapshot_id or ref not in self.refs or label_ref not in self.refs:
            return {"ok": False, "reason": "stale_accessibility_ref"}
        element, label = self.refs[ref], self.refs[label_ref]
        bounds, label_bounds = self._bounds(element), self._bounds(label)
        if (self._scalar(element, "AXRole") not in {"AXCheckBox", "AXSwitch"}
                or self._scalar(element, "AXEnabled") is False
                or not bounds or not label_bounds or label_bounds["height"] < 2
                or abs(bounds["y"] - label_bounds["y"]) > 30):
            return {"ok": False, "reason": "checkbox_row_not_observable"}
        action = self._string("AXPress")
        try:
            self._activate()
            code = self.ax.AXUIElementPerformAction(element, action)
            return {"ok": code == 0, "error_code": code, "verified": False, "method": "accessibility_checkbox"}
        finally:
            self.cf.CFRelease(action)
            self.snapshot_id = None

    def shortcut(self, key_code, command=False, shift=False):
        import Quartz
        from Foundation import NSDate, NSRunLoop
        self._activate()
        flags = (Quartz.kCGEventFlagMaskCommand if command else 0) | (Quartz.kCGEventFlagMaskShift if shift else 0)
        for down in (True, False):
            event = Quartz.CGEventCreateKeyboardEvent(None, key_code, down)
            Quartz.CGEventSetFlags(event, flags if down else 0)
            Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)
        NSRunLoop.currentRunLoop().runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(.05))
        self.snapshot_id = None

    def type_text(self, text):
        import Quartz
        from Foundation import NSDate, NSRunLoop
        self._activate()
        # Rich editors update selection on each input. Let Chrome process each
        # character before the next event; batched Unicode events reordered text.
        for chunk in text:
            for down in (True, False):
                event = Quartz.CGEventCreateKeyboardEvent(None, 0, down)
                Quartz.CGEventKeyboardSetUnicodeString(event, len(chunk.encode("utf-16-le")) // 2, chunk)
                Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)
            NSRunLoop.currentRunLoop().runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(.025))
        self.snapshot_id = None

    def fit_window(self, ref, snapshot_id):
        """Keep the observed browser window inside the actual main display."""
        if snapshot_id != self.snapshot_id or ref not in self.refs:
            return {"ok": False, "reason": "stale_accessibility_ref"}
        element = self.refs[ref]
        if self._scalar(element, "AXRole") != "AXWindow":
            return {"ok": False, "reason": "window_required"}
        from AppKit import NSScreen
        screen = NSScreen.mainScreen()
        frame, area = screen.frame(), screen.visibleFrame()
        self._activate()
        # Chrome accepts AXPosition without moving the window. Its ordinary
        # window scripting interface commits actual bounds (no webpage JS).
        import subprocess
        x = round(area.origin.x)
        y = round(frame.size.height - area.origin.y - area.size.height)
        bounds = [x, y, x + round(area.size.width), y + round(area.size.height)]
        script = 'tell application "Google Chrome" to set bounds of front window to {' + ",".join(map(str, bounds)) + '}'
        subprocess.run(["/usr/bin/osascript", "-e", script], check=True, capture_output=True, timeout=10)
        self.snapshot_id = None
        return {"ok": True, "requested_bounds": bounds, "verified": False}

    def capture(self, ref, snapshot_id, path):
        """Crop only an observed Chrome element; no unrelated desktop content."""
        if snapshot_id != self.snapshot_id or ref not in self.refs:
            return {"ok": False, "reason": "stale_accessibility_ref"}
        bounds = self._bounds(self.refs[ref])
        if not bounds or bounds["width"] < 2 or bounds["height"] < 2:
            return {"ok": False, "reason": "capture_target_not_visible"}
        from AppKit import NSScreen
        frame = NSScreen.mainScreen().frame()
        if (bounds["x"] < 0 or bounds["y"] < 0
                or bounds["x"] + bounds["width"] > frame.size.width
                or bounds["y"] + bounds["height"] > frame.size.height):
            return {"ok": False, "reason": "capture_target_outside_display"}
        import subprocess
        from Foundation import NSDate, NSRunLoop
        self._activate()
        NSRunLoop.currentRunLoop().runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(.1))
        region = ",".join(str(round(bounds[key])) for key in ("x", "y", "width", "height"))
        subprocess.run(["/usr/sbin/screencapture", "-x", "-R" + region, str(path)], check=True,
                       capture_output=True, timeout=10)
        return {"ok": True, "path": str(path), "bounds": bounds}


_chrome = None
_lock = threading.RLock()


def toggle_checkbox(ref, snapshot_id, label_ref):
    with _lock:
        if _chrome is None:
            raise RuntimeError("accessibility_snapshot_required")
        return _chrome.toggle_checkbox(ref, snapshot_id, label_ref)


def open_empty_tab(snapshot_id):
    """Open a new tab without navigating or discarding the current editor."""
    with _lock:
        if _chrome is None or snapshot_id != _chrome.snapshot_id:
            raise RuntimeError("fresh_chrome_snapshot_required")
        _chrome.shortcut(17, command=True)  # Cmd+T
        _chrome.snapshot_id = None


def operate(args):
    """One action on a fresh observed ref; callers verify through a new snapshot."""
    global _chrome
    with _lock:
        if _chrome is None:
            _chrome = ChromeAccessibility()
        action = args.get("action", "snapshot")
        if action == "snapshot":
            return _chrome.snapshot()
        if action == "fit_window":
            return _chrome.fit_window(args.get("ref"), args.get("snapshot_id"))
        if action == "capture":
            from pathlib import Path
            path = Path(args.get("path") or "")
            if not path.is_absolute() or not path.parent.is_dir() or path.suffix.lower() != ".png":
                raise ValueError("capture requires an absolute PNG path with an existing parent")
            return _chrome.capture(args.get("ref"), args.get("snapshot_id"), path)
        if action == "press":
            return _chrome.press(args.get("ref"), args.get("snapshot_id"))
        if action == "fill":
            text = args.get("text")
            if not isinstance(text, str) or len(text) > 5000:
                raise ValueError("text 必须为不超过 5000 字符的字符串")
            ref = args.get("ref")
            if args.get("snapshot_id") != _chrome.snapshot_id or ref not in _chrome.refs:
                return {"ok": False, "reason": "stale_accessibility_ref"}
            if _chrome._scalar(_chrome.refs[ref], "AXRole") not in {"AXTextField", "AXTextArea", "AXComboBox"}:
                return {"ok": False, "reason": "target_is_not_text_input"}
            return _chrome.set_text(ref, args["snapshot_id"], text)
        if action == "key":
            keys = {"enter": (36, False, False), "tab": (48, False, False),
                    "escape": (53, False, False), "select_all": (0, True, False),
                    "address": (37, True, False), "go_to_folder": (5, True, True),
                    "back": (33, True, False), "end": (125, True, False)}
            if args.get("key") not in keys:
                raise ValueError("不支持的桌面快捷键")
            _chrome.shortcut(*keys[args["key"]])
            return {"ok": True, "verified": False}
        if action == "type":
            value = args.get("text")
            if not isinstance(value, str) or len(value) > 16000:
                raise ValueError("text 必须为不超过16000字符的字符串")
            # Require an observed focused editable field, never type blindly.
            ref = args.get("ref")
            if args.get("snapshot_id") != _chrome.snapshot_id or ref not in _chrome.refs:
                return {"ok": False, "reason": "stale_accessibility_ref"}
            element = _chrome.refs[ref]
            if (_chrome._scalar(element, "AXRole") not in {"AXTextField", "AXTextArea", "AXComboBox"}
                    or not _chrome._scalar(element, "AXFocused")):
                return {"ok": False, "reason": "focused_text_input_required"}
            if args.get("replace"):
                _chrome.shortcut(0, command=True)
                from Foundation import NSDate, NSRunLoop
                NSRunLoop.currentRunLoop().runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(.05))
            _chrome.type_text(value)
            return {"ok": True, "verified": False, "length": len(value)}
        if action == "scroll":
            ref = args.get("ref")
            if args.get("snapshot_id") != _chrome.snapshot_id or ref not in _chrome.refs:
                return {"ok": False, "reason": "stale_accessibility_ref"}
            bounds = _chrome._bounds(_chrome.refs[ref])
            if not bounds or bounds["width"] < 2 or bounds["height"] < 2:
                return {"ok": False, "reason": "scroll_target_not_visible"}
            amount = args.get("amount", -600)
            if not isinstance(amount, int) or not -1200 <= amount <= 1200:
                raise ValueError("amount 必须为 -1200 至 1200 像素，负值向下")
            import Quartz
            _chrome._activate()
            point = (bounds["x"] + bounds["width"] / 2, bounds["y"] + bounds["height"] / 2)
            Quartz.CGEventPost(Quartz.kCGHIDEventTap,
                              Quartz.CGEventCreateMouseEvent(None, Quartz.kCGEventMouseMoved, point, Quartz.kCGMouseButtonLeft))
            event = Quartz.CGEventCreateScrollWheelEvent(None, Quartz.kCGScrollEventUnitPixel, 1, amount)
            Quartz.CGEventSetLocation(event, point)
            Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)
            _chrome.snapshot_id = None
            return {"ok": True, "verified": False}
        if action == "hover":
            ref = args.get("ref")
            if args.get("snapshot_id") != _chrome.snapshot_id or ref not in _chrome.refs:
                return {"ok": False, "reason": "stale_accessibility_ref"}
            bounds = _chrome._bounds(_chrome.refs[ref])
            if not bounds or bounds["width"] < 2 or bounds["height"] < 2:
                return {"ok": False, "reason": "hover_target_not_visible"}
            import Quartz
            _chrome._activate()
            point = (bounds["x"] + bounds["width"] / 2, bounds["y"] + bounds["height"] / 2)
            event = Quartz.CGEventCreateMouseEvent(None, Quartz.kCGEventMouseMoved, point,
                                                  Quartz.kCGMouseButtonLeft)
            Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)
            _chrome.snapshot_id = None
            return {"ok": True, "verified": False}
        if action == "reveal":
            ref = args.get("ref")
            if args.get("snapshot_id") != _chrome.snapshot_id or ref not in _chrome.refs:
                return {"ok": False, "reason": "stale_accessibility_ref"}
            action_name = _chrome._string("AXScrollToVisible")
            try:
                code = _chrome.ax.AXUIElementPerformAction(_chrome.refs[ref], action_name)
                return {"ok": code == 0, "error_code": code, "verified": False}
            finally:
                _chrome.cf.CFRelease(action_name)
                _chrome.snapshot_id = None
        raise ValueError("action 必须为 snapshot/press/fill/type/key/scroll/reveal/hover")


if __name__ == "__main__":
    chrome = ChromeAccessibility()
    try:
        print(json.dumps(chrome.snapshot(), ensure_ascii=False))
    finally:
        chrome.close()
