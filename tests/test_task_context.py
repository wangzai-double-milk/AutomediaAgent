"""Fresh threads/contexts must not adopt another agent's task."""

from contextvars import Context

from tools import task


def test_new_context_has_no_task_even_when_another_context_is_bound(tmp_path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    try:
        task.set_current(str(first))
        fresh = Context()
        assert fresh.run(task.get_current) is None
        fresh.run(task.set_current, str(second))
        assert fresh.run(task.get_current) == str(second)
        assert task.get_current() == str(first)
    finally:
        task.set_current(None)
