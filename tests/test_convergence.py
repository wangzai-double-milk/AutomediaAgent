"""tools.convergence.research_converged 的纯逻辑测试。

覆盖用户诉求："调研要像人一样边搜边看、一直挖到没有新知识为止才收尾"。
关键：**不用报告行数判断收敛**（归纳/精简会让行数不涨，用行数判＝惩罚归纳）；
代码同时校验阅读量/检索面、连续零新增和未解决媒体失败。
可直接 `python3 tests/test_convergence.py` 跑，也兼容 pytest。
"""
import os, sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from tools.convergence import research_converged


def test_enough_reading_released():
    assert research_converged(
        notes_read=10, search_rounds=3, consecutive_no_new=5, unresolved_media=0
    ) is True


def test_too_few_notes_blocked():
    # 只深读 2 条就想收尾：看得太少 → 拦。
    assert research_converged(2, 3, consecutive_no_new=5) is False


def test_too_few_searches_blocked():
    # 只搜过 1 轮，检索面没铺开 → 拦，哪怕看了不少条。
    assert research_converged(8, 1, consecutive_no_new=5) is False


def test_lines_no_longer_matter():
    # 函数没有报告行数参数，归纳精简不会影响判定。
    assert research_converged(5, 2, consecutive_no_new=5) is True


def test_no_semantic_saturation_is_blocked():
    assert research_converged(20, 5, consecutive_no_new=4) is False


def test_boundary_min_notes():
    # 恰好 5 条（默认 min_notes）→ 放行；4 条 → 拦。
    assert research_converged(5, 2, consecutive_no_new=5) is True
    assert research_converged(4, 2, consecutive_no_new=5) is False


def test_none_inputs_safe():
    # 传入 None 不崩，按"不足"处理。
    assert research_converged(None, None, consecutive_no_new=None) is False


def test_unresolved_media_blocks_completion():
    assert research_converged(
        notes_read=10, search_rounds=3, consecutive_no_new=6, unresolved_media=1
    ) is False


if __name__ == '__main__':
    fns = [v for k, v in sorted(globals().items()) if k.startswith('test_') and callable(v)]
    passed = 0
    for fn in fns:
        fn()
        passed += 1
        print(f'  ok  {fn.__name__}')
    print(f'\n{passed}/{len(fns)} passed')
