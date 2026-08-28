"""Browser side-effect verification tests without touching a real account."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools import web_vision  # noqa: E402
from tools import xhs_actions  # noqa: E402


def test_missing_page_fingerprint_is_not_verified():
    valid = {'url': 'https://example.test', 'nodes': 10, 'textlen': 20, 'popups': 0}
    assert web_vision._fp_changed({}, valid) is False
    assert web_vision._fp_changed(valid, {}) is False
    assert web_vision._fp_changed({'url': 'https://example.test'}, valid) is False
    assert web_vision._fp_changed(valid, dict(valid, popups=1)) is True


def test_like_waits_for_state_without_second_toggle():
    originals = {
        name: getattr(xhs_actions, name)
        for name in (
            '_navigate', '_wait_initial_state', '_check_accessible',
            '_read_interact_state', '_wait_selector', '_click_selector', 'time',
        )
    }
    states = iter([(False, False), (False, False), (True, False)])
    clicks = []
    try:
        xhs_actions._navigate = lambda *args, **kwargs: None
        xhs_actions._wait_initial_state = lambda: True
        xhs_actions._check_accessible = lambda: None
        xhs_actions._read_interact_state = lambda feed_id: next(states)
        xhs_actions._wait_selector = lambda selector: True
        xhs_actions._click_selector = lambda selector: clicks.append(selector) or True
        xhs_actions.time = type('Clock', (), {
            'time': staticmethod(__import__('time').time),
            'sleep': staticmethod(lambda seconds: None),
        })

        result = xhs_actions.like_feed('feed-1', 'token')

        assert result['ok'] is True
        assert result['verified'] is True
        assert len(clicks) == 1
    finally:
        for name, value in originals.items():
            setattr(xhs_actions, name, value)


def test_post_comment_requires_visible_new_comment():
    names = (
        '_navigate', '_check_accessible', '_comment_occurrences',
        '_wait_selector', '_click_selector', '_input_into', 'time',
    )
    originals = {name: getattr(xhs_actions, name) for name in names}
    counts = iter([0, 1])
    try:
        xhs_actions._navigate = lambda *args, **kwargs: None
        xhs_actions._check_accessible = lambda: None
        xhs_actions._comment_occurrences = lambda content: next(counts)
        xhs_actions._wait_selector = lambda selector: True
        xhs_actions._click_selector = lambda selector: True
        xhs_actions._input_into = lambda selector, content: True
        xhs_actions.time = type('Clock', (), {
            'time': staticmethod(__import__('time').time),
            'sleep': staticmethod(lambda seconds: None),
        })

        result = xhs_actions.post_comment('feed-1', 'token', 'unique comment')

        assert result['ok'] is True
        assert result['verified'] is True
        assert result['comment_matches_before'] == 0
        assert result['comment_matches_after'] == 1
    finally:
        for name, value in originals.items():
            setattr(xhs_actions, name, value)


def _main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith('test_') and callable(v)]
    for test in tests:
        test()
        print(f'  PASS  {test.__name__}')
    print(f'\n{len(tests)} passed, 0 failed (total {len(tests)})')


if __name__ == '__main__':
    _main()
