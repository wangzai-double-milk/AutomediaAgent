"""Skill architecture regression tests."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.check_skill_graph import validate  # noqa: E402


def test_skill_dependency_graph_is_acyclic_and_layered():
    assert validate() == []


def _main():
    tests = [value for name, value in sorted(globals().items()) if name.startswith("test_") and callable(value)]
    for test in tests:
        test()
        print(f"  PASS  {test.__name__}")
    print(f"\n{len(tests)} passed, 0 failed (total {len(tests)})")


if __name__ == "__main__":
    _main()
