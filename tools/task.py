"""tools.task - 小红书 agent 的轻量调试工作区。

每次会话调一次 init_task() 建一个 tasks/<ts>_<slug>/ 文件夹，
后续中间产物（识图 JSON、爆款图、修图、批注图）都丢这里方便回头排查。

各工具把关键动作和验证结果写入当前 task 的 `trace.log`。
"""

import os
import re
import json
import time
import shutil
import uuid
from contextvars import ContextVar

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.abspath(os.path.join(_THIS_DIR, '..'))
TASKS_ROOT = os.path.join(_PROJECT_ROOT, 'tasks')
os.makedirs(TASKS_ROOT, exist_ok=True)

_CURRENT_TASK_DIR_CTX = ContextVar('ga_current_task_dir', default=None)


def set_current(task_dir: str = None) -> None:
    """绑定当前执行上下文的 task_dir；不会影响其他线程/async context。"""
    _CURRENT_TASK_DIR_CTX.set(task_dir)


def _slugify(text: str, maxlen: int = 32) -> str:
    text = re.sub(r'[\s/\\:*?"<>|]+', '_', text.strip())
    text = re.sub(r'_+', '_', text).strip('_')
    return text[:maxlen] or 'task'


def init_task(user_request: str, image: str = None) -> dict:
    """新建任务工作区，返回 {task_dir, original_image, task_name}。"""
    ts = time.strftime('%Y%m%d-%H%M%S')
    slug = _slugify(user_request, 24) or 'task'
    task_name = f'{ts}_{slug}_{uuid.uuid4().hex[:8]}'
    task_dir = os.path.join(TASKS_ROOT, task_name)
    os.makedirs(task_dir, exist_ok=True)
    os.makedirs(os.path.join(task_dir, 'viral_refs'), exist_ok=True)

    original_image = None
    if image and os.path.isfile(image):
        ext = os.path.splitext(image)[1] or '.jpg'
        original_image = os.path.join(task_dir, f'original{ext}')
        shutil.copyfile(image, original_image)

    set_current(task_dir)
    info = {
        'task_dir': task_dir,
        'original_image': original_image,
        'task_name': task_name,
    }
    append_trace('task_init', {'user_request': user_request, **info})
    return info


def get_current() -> str:
    """返回当前活跃的 task_dir，None = 还没 init。"""
    return _CURRENT_TASK_DIR_CTX.get()


def ensure_task(hint: str = '') -> str:
    """确保有一个活跃 task_dir：已有就直接返回；没有则新建一个探索型工作区。

    用于 web_visual_* 这类不走 xhs_task_init 的探索/兜底操作，让它们的过程也能落盘 trace。
    返回当前 task_dir。
    """
    current = get_current()
    if current and os.path.isdir(current):
        return current
    ts = time.strftime('%Y%m%d-%H%M%S')
    slug = _slugify(hint, 24) or 'explore'
    task_name = f'{ts}_explore_{slug}_{uuid.uuid4().hex[:8]}'
    task_dir = os.path.join(TASKS_ROOT, task_name)
    os.makedirs(task_dir, exist_ok=True)
    set_current(task_dir)
    append_trace('explore_task_init', {'hint': hint, 'task_dir': task_dir, 'task_name': task_name})
    return task_dir


def append_trace(step: str, payload, task_dir: str = None) -> None:
    """向 task_dir/trace.log 追加一条记录。task_dir 默认用当前活跃的。"""
    target = task_dir or get_current()
    if not target or not os.path.isdir(target):
        return  # 没 init 就静默跳过，不破坏 GA 主流程
    line = json.dumps({
        'ts': time.strftime('%H:%M:%S'),
        'step': step,
        'payload': payload,
    }, ensure_ascii=False, default=str)
    with open(os.path.join(target, 'trace.log'), 'a', encoding='utf-8') as f:
        f.write(line + '\n')


def _trim(obj, maxlen: int = 200):
    """把长字符串截短，避免 trace 太胖。"""
    if isinstance(obj, str):
        return obj if len(obj) <= maxlen else obj[:maxlen] + f'…(+{len(obj)-maxlen})'
    if isinstance(obj, dict):
        return {k: _trim(v, maxlen) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_trim(v, maxlen) for v in obj]
    return obj
