"""回归测试：守卫/连接生命周期的边界问题。

覆盖：
- TMWebDriver 的 HTTP session 过期检查不应递归到 RecursionError；
- 收敛守卫应把 search_feeds_done 也算作搜索轮次；
- "这份报告..." 这类中途澄清不应被误判为收尾。

可直接 `python3 tests/test_guard_edges.py` 跑，也兼容 pytest。
"""
import json
import os
import queue
import sys
import tempfile
import threading
import time
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ga  # noqa: E402
from agent_loop import exhaust  # noqa: E402
from TMWebDriver import (  # noqa: E402
    Session,
    TMWebDriver,
    WebDriverSessionError,
    WebDriverTimeoutError,
)
from tools import research_trace  # noqa: E402
from tools import task as task_mod  # noqa: E402


def _find_method(name):
    for obj_name in dir(ga):
        obj = getattr(ga, obj_name)
        if isinstance(obj, type) and hasattr(obj, name):
            return getattr(obj, name)
    raise RuntimeError(f"找不到 {name} 所在的类")


class _GuardStub:
    def _xhs_task(self):
        return task_mod


def _stub_with(method_name):
    stub = _GuardStub()
    setattr(stub, method_name, types.MethodType(_find_method(method_name), stub))
    return stub


def _append_trace(task_dir, step, payload=None):
    with open(os.path.join(task_dir, 'trace.log'), 'a', encoding='utf-8') as f:
        f.write(json.dumps({'step': step, 'payload': payload or {}}, ensure_ascii=False) + '\n')


def test_http_session_expiry_no_recursion():
    session = Session('sid', {'type': 'http', 'url': 'http://example.test'}, queue.Queue())
    session.connect_at = time.time() - 61

    assert session.is_active() is False
    first_disconnect_at = session.disconnect_at
    assert first_disconnect_at is not None

    # 重复标记不应递归，也不应刷新首次断开时间。
    session.mark_disconnected()
    assert session.disconnect_at == first_disconnect_at


def _local_driver():
    driver = TMWebDriver.__new__(TMWebDriver)
    driver.sessions = {}
    driver.results = {}
    driver.acks = {}
    driver._request_lock = threading.RLock()
    driver._pending_requests = set()
    driver.default_session_id = None
    driver.latest_session_id = None
    driver.is_remote = False
    return driver


def test_webdriver_explicit_missing_session_does_not_switch_tabs():
    driver = _local_driver()
    active = Session('active', {'type': 'ws', 'url': 'https://example.test'}, types.SimpleNamespace())
    driver.sessions['active'] = active
    driver.default_session_id = 'active'

    try:
        driver.execute_js('1 + 1', session_id='missing')
        assert False, 'expected WebDriverSessionError'
    except WebDriverSessionError:
        pass

    assert driver.default_session_id == 'active'


def test_webdriver_timeout_is_error_and_late_result_is_ignored():
    driver = _local_driver()
    client = types.SimpleNamespace(send_message=lambda payload: None)
    session = Session('sid', {'type': 'ws', 'url': 'https://example.test'}, client)
    driver.sessions['sid'] = session
    driver.default_session_id = 'sid'

    try:
        driver.execute_js('1 + 1', timeout=0.01, session_id='sid')
        assert False, 'expected WebDriverTimeoutError'
    except WebDriverTimeoutError:
        pass

    assert not driver._pending_requests
    assert not driver.acks
    assert not driver.results
    driver._record_result('late-request', True, 2)
    assert not driver.results


def test_finish_intent_does_not_match_plain_report_mention():
    is_finish = _find_method('_is_finish_intent')

    assert is_finish({}, '这份报告需要更偏入门还是专家？') is False
    assert is_finish({}, '这份报告可以交付吗？') is True


def test_convergence_counts_search_feeds_done_as_search_round():
    with tempfile.TemporaryDirectory() as d:
        try:
            task_mod.set_current(d)
            _append_trace(d, 'report_write', {'file': 'research_report.md'})
            _append_trace(d, 'search_feeds_done', {'count': 10})
            _append_trace(d, 'search_feeds_done', {'count': 8})
            for i in range(5):
                _append_trace(d, 'research_item_read', {
                    'platform': 'xiaohongshu',
                    'type': 'note',
                    'feed_id': f'note-{i}',
                })
                _append_trace(d, research_trace.KNOWLEDGE_DELTA_STEP, {
                    'item_id': f'note-{i}',
                    'has_new': False,
                    'new_points': [],
                    'revised_points': [],
                })

            stub = _stub_with('_research_convergence_gate')
            assert stub._research_convergence_gate() is None
        finally:
            task_mod.set_current(None)


def test_convergence_dedupes_generic_and_legacy_search_round():
    with tempfile.TemporaryDirectory() as d:
        try:
            task_mod.set_current(d)
            round_id = 'xiaohongshu:xhs_search_viral:test:1'
            _append_trace(d, 'report_write', {'file': 'research_report.md'})
            _append_trace(d, 'search_viral_done', {'round_id': round_id, 'keyword': 'test', 'count': 5})
            _append_trace(d, research_trace.SEARCH_ROUND_STEP, {
                'round_id': round_id,
                'platform': 'xiaohongshu',
                'source': 'xhs_search_viral',
                'keyword': 'test',
                'count': 5,
            })
            for i in range(5):
                _append_trace(d, research_trace.ITEM_READ_STEP, {
                    'platform': 'xiaohongshu',
                    'type': 'note',
                    'feed_id': f'note-{i}',
                })

            stub = _stub_with('_research_convergence_gate')
            # 只有一轮搜索，虽然写了新旧两条 trace，也不能被算成两轮。
            assert stub._research_convergence_gate() is not None
        finally:
            task_mod.set_current(None)


def test_bare_filename_resolves_only_when_unique_in_task_dir():
    with tempfile.TemporaryDirectory() as cwd, tempfile.TemporaryDirectory() as task_dir:
        parent = types.SimpleNamespace(task_dir=task_dir)
        handler = ga.GenericAgentHandler(parent, cwd=cwd)

        unique = os.path.join(task_dir, 'only.txt')
        with open(unique, 'w', encoding='utf-8') as f:
            f.write('unique')
        assert handler._get_abs_path('only.txt') == unique

        os.makedirs(os.path.join(task_dir, 'a'), exist_ok=True)
        os.makedirs(os.path.join(task_dir, 'b'), exist_ok=True)
        with open(os.path.join(task_dir, 'a', 'dup.txt'), 'w', encoding='utf-8') as f:
            f.write('a')
        with open(os.path.join(task_dir, 'b', 'dup.txt'), 'w', encoding='utf-8') as f:
            f.write('b')

        # 多处同名时不要静默任选一个，保持项目根相对路径，逼上层传绝对路径。
        assert handler._get_abs_path('dup.txt') == os.path.join(cwd, 'dup.txt')


def test_related_sop_reminder_is_marked_internal():
    with tempfile.TemporaryDirectory() as cwd, tempfile.TemporaryDirectory() as task_dir:
        parent = types.SimpleNamespace(task_dir=task_dir, verbose=False)
        handler = ga.GenericAgentHandler(parent, cwd=cwd)
        handler.working['related_sop'] = 'research_mode, explore_mode'

        prompt = handler._get_anchor_prompt()

        assert '<internal_sop_reminder>' in prompt
        assert '</internal_sop_reminder>' in prompt
        assert '这不是用户新消息' in prompt
        assert '收尾、交付或停止继续调查' in prompt
        assert '有不清晰的地方请再次读取' not in prompt


def test_anchor_uses_project_root_not_workspace():
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with tempfile.TemporaryDirectory() as workspace, tempfile.TemporaryDirectory() as task_dir:
        parent = types.SimpleNamespace(task_dir=task_dir, verbose=False)
        handler = ga.GenericAgentHandler(parent, cwd=workspace, project_root=project_root)

        prompt = handler._get_anchor_prompt()

        assert f'项目根: {project_root}' in prompt
        assert f'运行工作区(workspace): {workspace}' in prompt
        expected = os.path.join(project_root, 'skills', 'hard_skills', 'agent_core', 'SKILL.md')
        assert expected in prompt
        assert os.path.join(workspace, 'skills') not in prompt


def test_direct_final_is_blocked_when_research_not_converged():
    with tempfile.TemporaryDirectory() as workspace, tempfile.TemporaryDirectory() as task_dir:
        task_mod.set_current(task_dir)
        _append_trace(task_dir, 'report_write', {'file': 'research_report.md'})
        _append_trace(task_dir, 'research_search_round', {'round_id': 'only-one'})
        parent = types.SimpleNamespace(task_dir=task_dir, verbose=False)
        handler = ga.GenericAgentHandler(parent, cwd=workspace)
        response = types.SimpleNamespace(content='<summary>准备交付</summary>\n报告完成', thinking='')

        outcome = exhaust(handler.do_no_tool({}, response))

        assert isinstance(outcome, ga.StepOutcome)
        assert outcome.next_prompt
        assert outcome.data['status'] == 'error'


def test_analyze_media_error_string_is_not_success():
    with tempfile.TemporaryDirectory() as workspace, tempfile.TemporaryDirectory() as task_dir:
        task_mod.set_current(task_dir)
        parent = types.SimpleNamespace(task_dir=task_dir, verbose=False)
        handler = ga.GenericAgentHandler(parent, cwd=workspace)
        handler._xhs_import = lambda: types.SimpleNamespace(
            _is_video_page_url=lambda url: False,
            _is_direct_video_url=lambda url: False,
            analyze_image=lambda media, prompt: '[Error] API 请求失败: token exceeded'
        )
        response = types.SimpleNamespace(content='', thinking='')

        outcome = exhaust(handler.do_analyze_media({
            'images': ['/tmp/example.webp'],
            'prompt': '分析图片',
            '_index': 0,
        }, response))

        assert outcome.data['status'] == 'error'
        traces = []
        with open(os.path.join(task_dir, 'trace.log'), encoding='utf-8') as f:
            traces = [json.loads(line) for line in f if line.strip()]
        steps = [r['step'] for r in traces]
        assert 'vlm_analyze_failed' in steps
        assert 'vlm_analyze_done' not in steps


def test_task_context_isolated_between_threads():
    with tempfile.TemporaryDirectory() as root:
        old_root = task_mod.TASKS_ROOT
        task_mod.TASKS_ROOT = root
        barrier = threading.Barrier(2)
        results = {}

        def worker(name):
            info = task_mod.init_task(name)
            barrier.wait()
            task_mod.append_trace('worker_trace', {'worker': name})
            results[name] = info['task_dir']

        try:
            a = threading.Thread(target=worker, args=('alpha',))
            b = threading.Thread(target=worker, args=('beta',))
            a.start(); b.start(); a.join(); b.join()
            assert results['alpha'] != results['beta']
            for name, task_dir in results.items():
                traces = []
                with open(os.path.join(task_dir, 'trace.log'), encoding='utf-8') as f:
                    traces = [json.loads(line) for line in f if line.strip()]
                workers = [r['payload']['worker'] for r in traces if r['step'] == 'worker_trace']
                assert workers == [name], (name, workers)
        finally:
            task_mod.TASKS_ROOT = old_root
            task_mod.set_current(None)


def test_create_skill_requires_evidence_and_writes_soft_skills():
    with tempfile.TemporaryDirectory() as project_root, tempfile.TemporaryDirectory() as workspace:
        parent = types.SimpleNamespace(task_dir=None, verbose=False)
        handler = ga.GenericAgentHandler(parent, cwd=workspace, project_root=project_root)
        response = types.SimpleNamespace(content='', thinking='')
        args = {
            'skill_name': 'tested_skill',
            'description': 'A tested workflow',
            'sop_content': '## Steps\n\n1. Verify evidence.',
            'evidence_manifest': ['xiaohongshu:note-1:verified'],
        }

        outcome = exhaust(handler.do_create_skill(args, response))

        expected = os.path.join(project_root, 'skills', 'soft_skills', 'tested_skill', 'SKILL.md')
        assert outcome.data['status'] == 'ok'
        assert outcome.data['path'] == expected
        assert os.path.isfile(expected)
        with open(expected, encoding='utf-8') as f:
            content = f.read()
        assert 'xiaohongshu:note-1:verified' in content

        duplicate = exhaust(handler.do_create_skill(args, response))
        assert duplicate.data['status'] == 'error'
        assert '拒绝静默覆盖' in duplicate.data['msg']

        args['overwrite'] = True
        args['sop_content'] = '## Updated'
        replaced = exhaust(handler.do_create_skill(args, response))
        assert replaced.data['status'] == 'ok'
        with open(expected, encoding='utf-8') as f:
            assert '## Updated' in f.read()


def test_create_skill_rejects_invalid_name_and_missing_evidence():
    with tempfile.TemporaryDirectory() as project_root, tempfile.TemporaryDirectory() as workspace:
        parent = types.SimpleNamespace(task_dir=None, verbose=False)
        handler = ga.GenericAgentHandler(parent, cwd=workspace, project_root=project_root)
        response = types.SimpleNamespace(content='', thinking='')
        base = {
            'description': 'description',
            'sop_content': 'content',
            'evidence_manifest': ['source:item:verified'],
        }

        invalid = exhaust(handler.do_create_skill({'skill_name': '../escape', **base}, response))
        assert invalid.data['status'] == 'error'
        assert not os.path.exists(os.path.join(project_root, 'escape'))

        no_evidence = exhaust(handler.do_create_skill({
            'skill_name': 'valid_skill',
            'description': 'description',
            'sop_content': 'content',
            'evidence_manifest': [],
        }, response))
        assert no_evidence.data['status'] == 'error'


def _main():
    task_mod.set_current(None)
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
