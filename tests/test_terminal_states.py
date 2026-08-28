"""Terminal-state propagation regression tests."""

import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent_loop import (  # noqa: E402
    TERMINAL_ABORTED,
    TERMINAL_COMPLETED,
    TERMINAL_MAX_TURNS,
    agent_runner_loop,
    exhaust,
    iter_agent_chunks,
)


def test_iter_agent_chunks_preserves_generator_return():
    def source():
        yield 'chunk'
        return {'result': TERMINAL_COMPLETED, 'data': {'ok': True}}

    stream = iter_agent_chunks(source(), lambda: False)
    assert next(stream) == 'chunk'
    result = exhaust(stream)
    assert result == {'result': TERMINAL_COMPLETED, 'data': {'ok': True}}


def test_iter_agent_chunks_reports_abort_and_closes_source():
    closed = []
    checks = iter([False, True])

    def source():
        try:
            yield 'chunk'
            yield 'unreachable'
        finally:
            closed.append(True)

    stream = iter_agent_chunks(source(), lambda: next(checks))
    assert next(stream) == 'chunk'
    result = exhaust(stream)
    assert result == {'result': TERMINAL_ABORTED}
    assert closed == [True]


def test_agent_runner_reports_max_turns_explicitly():
    handler = types.SimpleNamespace(max_turns=0)
    result = exhaust(agent_runner_loop(
        client=types.SimpleNamespace(),
        system_prompt='system',
        user_input='user',
        handler=handler,
        tools_schema=[],
        max_turns=0,
    ))
    assert result == {'result': TERMINAL_MAX_TURNS}


def _main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith('test_') and callable(v)]
    for test in tests:
        test()
        print(f'  PASS  {test.__name__}')
    print(f'\n{len(tests)} passed, 0 failed (total {len(tests)})')


if __name__ == '__main__':
    _main()
