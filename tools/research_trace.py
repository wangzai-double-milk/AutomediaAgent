"""Platform-neutral trace helpers for research convergence.

The agent can research on Xiaohongshu, Douyin, Bilibili, ordinary web pages, or
future platform dialects.  Convergence should not need to know every dialect's
private trace step names, so new code should emit the generic steps below.

Old Xiaohongshu steps are still supported by the caller for historical traces.
"""
import time


SEARCH_ROUND_STEP = 'research_search_round'
ITEM_READ_STEP = 'research_item_read'
KNOWLEDGE_DELTA_STEP = 'research_knowledge_delta'

# Historical Xiaohongshu trace names that predate SEARCH_ROUND_STEP.
LEGACY_SEARCH_STEPS = ('search_viral_done', 'search_feeds_done')


def make_round_id(platform, source, keyword=None):
    """Return a lightweight id shared by generic and legacy trace records."""
    ts = int(time.time() * 1000)
    parts = [str(platform or 'unknown'), str(source or 'search'), str(keyword or ''), str(ts)]
    return ':'.join(parts)


def append_search_round(task, platform, source, keyword=None, count=None,
                        round_id=None, **extra):
    """Append one generic "searched a batch" trace record.

    round_id should also be included in any legacy trace emitted for the same
    action, so convergence can de-duplicate the pair.
    """
    if not task:
        return None
    rid = round_id or make_round_id(platform, source, keyword)
    payload = {
        'round_id': rid,
        'platform': platform,
        'source': source,
        'keyword': keyword,
        'count': count,
    }
    payload.update({k: v for k, v in extra.items() if v is not None})
    task.append_trace(SEARCH_ROUND_STEP, payload)
    return rid


def append_item_read(task, platform, item_type, item_id=None, **extra):
    """Append one generic "deep-read one content item" trace record."""
    if not task:
        return None
    payload = {
        'platform': platform,
        'type': item_type,
        'feed_id': item_id,
    }
    payload.update({k: v for k, v in extra.items() if v is not None})
    task.append_trace(ITEM_READ_STEP, payload)
    return payload


def append_knowledge_delta(task, item_id, has_new, new_points=None,
                           revised_points=None, evidence_refs=None, **extra):
    """记录深读一个样本后对知识体系造成的真实增量。"""
    if not task:
        return None
    payload = {
        'item_id': item_id,
        'has_new': bool(has_new),
        'new_points': list(new_points or []),
        'revised_points': list(revised_points or []),
        'evidence_refs': list(evidence_refs or []),
    }
    payload.update({k: v for k, v in extra.items() if v is not None})
    task.append_trace(KNOWLEDGE_DELTA_STEP, payload)
    return payload


def search_round_key(step, payload, fallback):
    """Return a de-duplication key for generic and legacy search trace lines."""
    payload = payload or {}
    rid = payload.get('round_id')
    if rid:
        return ('round_id', rid)
    if step == SEARCH_ROUND_STEP:
        return (
            'generic',
            payload.get('platform', ''),
            payload.get('source', ''),
            payload.get('keyword', ''),
            fallback,
        )
    return ('legacy', step, fallback)


def item_read_key(payload, fallback):
    """Return a de-duplication key for a deep-read trace line."""
    payload = payload or {}
    fid = payload.get('feed_id') or payload.get('item_id') or payload.get('url') or fallback
    return (payload.get('platform', ''), payload.get('type', ''), fid)
