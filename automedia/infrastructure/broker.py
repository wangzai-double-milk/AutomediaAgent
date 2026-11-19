"""Process-shared local resources; locks last until release or process exit."""
from contextlib import contextmanager, asynccontextmanager
import asyncio
import hashlib
import os
from pathlib import Path
import time


def lock_root():
    root = Path(os.environ.get("AUTOMEDIA_RESOURCE_DIR") or
                Path.home() / ".local/share/automedia/resource-locks")
    root.mkdir(parents=True, exist_ok=True)
    return root


def try_lock(name):
    path = lock_root() / hashlib.sha256(name.encode()).hexdigest()
    handle = open(path, "a+b")
    try:
        if os.name == "nt":
            import msvcrt
            handle.write(b"0"); handle.flush(); handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return handle
    except OSError:
        handle.close()
        return None


def acquire_set(names):
    handles = []
    for name in sorted(set(names)):
        handle = try_lock(name)
        if handle is None:
            for held in handles:
                held.close()
            return None
        handles.append(handle)
    return handles


@contextmanager
def resources(*names, cancelled=lambda: False, timeout=180):
    deadline = time.monotonic() + timeout
    handles = None
    try:
        while handles is None:
            if cancelled():
                raise InterruptedError("任务已停止，未取得执行资源")
            if time.monotonic() >= deadline:
                raise TimeoutError("等待执行资源超时，未开始动作")
            handles = acquire_set(names)
            if handles is None:
                time.sleep(.05)
        yield
    finally:
        for handle in handles or []:
            handle.close()


# One shared pool across planner, agent and media calls on this machine.
# Fixed pool size prevents processes with different configuration from overbooking.
# Two slots are reserved for short Talk calls; ordinary calls use the other six.
def try_model(lane):
    slots = range(8) if lane == "talk" else range(2, 8)
    for slot in slots:
        handle = try_lock(f"model:{slot}")
        if handle is not None:
            return handle
    return None


@contextmanager
def model_slot(lane="thinking", timeout=240):
    deadline, handle = time.monotonic() + timeout, None
    try:
        while handle is None:
            if time.monotonic() >= deadline:
                raise TimeoutError("等待模型容量超时")
            handle = try_model(lane)
            if handle is None:
                time.sleep(.05)
        yield
    finally:
        if handle is not None:
            handle.close()


@asynccontextmanager
async def async_model_slot(lane="thinking", timeout=240):
    deadline, handle = time.monotonic() + timeout, None
    try:
        while handle is None:
            if time.monotonic() >= deadline:
                raise TimeoutError("等待模型容量超时")
            handle = try_model(lane)
            if handle is None:
                await asyncio.sleep(.05)
        yield
    finally:
        if handle is not None:
            handle.close()
