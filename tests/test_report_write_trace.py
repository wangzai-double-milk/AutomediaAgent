"""report_write trace 埋点测试。

验证 GA._trace_report_write 的核心行为：
- 报告类路径（含 report/报告/总结/调研 等）写入时落一条 report_write，带 turn/total_lines/total_chars/mode；
- 非报告类路径（世界模型、脚本等）不落埋点；
- 没有活跃 task_dir 时静默跳过、不抛异常。

不实例化完整 GA（太重），用 stub 对象绑定被测方法，注入真实 task 模块 + 临时 task_dir。
可直接 `python3 tests/test_report_write_trace.py`，也兼容 pytest。
"""
import json
import os
import sys
import tempfile
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ga
from tools import task as task_mod


class _Stub:
    """最小 stub：提供 _trace_report_write 依赖的 current_turn 和 _xhs_task。"""
    def __init__(self, turn, task_module):
        self.current_turn = turn
        self._task_module = task_module

    def _xhs_task(self):
        return self._task_module


def _mk(turn=7):
    stub = _Stub(turn, task_mod)
    stub._trace_report_write = types.MethodType(_find_method(), stub)
    return stub


def _find_method():
    # 兼容不同类名：在 ga 模块里找定义了 _trace_report_write 的类
    for name in dir(ga):
        obj = getattr(ga, name)
        if isinstance(obj, type) and '_trace_report_write' in obj.__dict__:
            return obj.__dict__['_trace_report_write']
    raise RuntimeError("找不到 _trace_report_write 所在的类")


def _read_traces(task_dir):
    p = os.path.join(task_dir, 'trace.log')
    if not os.path.exists(p):
        return []
    with open(p, encoding='utf-8') as f:
        return [json.loads(l) for l in f if l.strip()]


def _set_current(task_dir):
    task_mod.set_current(task_dir)


def test_report_path_records_trace():
    with tempfile.TemporaryDirectory() as d:
        _set_current(d)
        report = os.path.join(d, 'research_report.md')
        body = "# 标题\n第一行\n第二行\n"
        with open(report, 'w', encoding='utf-8') as f:
            f.write(body)
        stub = _mk(turn=7)
        stub._trace_report_write(report, None, 'overwrite')
        traces = [t for t in _read_traces(d) if t['step'] == 'report_write']
        assert len(traces) == 1, traces
        p = traces[0]['payload']
        assert p['turn'] == 7
        assert p['mode'] == 'overwrite'
        assert p['total_lines'] == body.count('\n') + 1
        assert p['total_chars'] == len(body)
        assert p['file'] == 'research_report.md'


def test_non_report_path_no_trace():
    with tempfile.TemporaryDirectory() as d:
        _set_current(d)
        other = os.path.join(d, 'douyin.md')  # 世界模型，不该埋点
        with open(other, 'w', encoding='utf-8') as f:
            f.write("some world model note\n")
        stub = _mk()
        stub._trace_report_write(other, "some world model note\n", 'overwrite')
        traces = [t for t in _read_traces(d) if t['step'] == 'report_write']
        assert traces == []


def test_patch_mode_recorded():
    with tempfile.TemporaryDirectory() as d:
        _set_current(d)
        report = os.path.join(d, '总结报告.md')
        body = "l1\nl2\nl3\nl4\n"
        with open(report, 'w', encoding='utf-8') as f:
            f.write(body)
        stub = _mk(turn=12)
        stub._trace_report_write(report, None, 'patch')
        traces = [t for t in _read_traces(d) if t['step'] == 'report_write']
        assert len(traces) == 1
        assert traces[0]['payload']['mode'] == 'patch'
        assert traces[0]['payload']['total_lines'] == body.count('\n') + 1
        assert traces[0]['payload']['turn'] == 12


def test_no_active_task_no_crash():
    _set_current(None)
    stub = _mk()
    # 不该抛异常，也不该落盘（没有 task_dir）
    stub._trace_report_write('/tmp/research_report.md', 'x\n', 'overwrite')


def _main():
    _set_current(None)
    fns = [v for k, v in sorted(globals().items()) if k.startswith('test_') and callable(v)]
    passed = failed = 0
    for fn in fns:
        try:
            fn()
            print(f"  PASS  {fn.__name__}")
            passed += 1
        except AssertionError as e:
            print(f"  FAIL  {fn.__name__}: {e}")
            failed += 1
        except Exception as e:
            print(f"  ERROR {fn.__name__}: {type(e).__name__}: {e}")
            failed += 1
    print(f"\n{passed} passed, {failed} failed (total {passed + failed})")
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(_main())
