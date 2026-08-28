"""历史压缩逻辑测试：36K 阈值 + LLM 压缩 + 失败回退 + skill_doc 豁免。

纯逻辑测试，用假的 sess（mock raw_ask）替代真实大模型，不花钱、不联网。
既能直接 `python3 tests/test_history_compress.py` 跑，也兼容 pytest。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import llmcore as L


class _FakeSess:
    """假 session：raw_ask 把输入原样压成固定短串，用于验证 LLM 压缩分支被走到。"""
    def __init__(self, compress_cfg=None, fail=False):
        self.compress_cfg = compress_cfg
        self._fail = fail
        self.calls = 0

    def raw_ask(self, messages):
        self.calls += 1
        if self._fail:
            raise RuntimeError("模拟 LLM 调用失败")
        return iter(["【LLM压缩后的要点】保留了关键事实"])


def _wrap_tool_result(text):
    """构造一条带 tool_result 的历史消息（trim 真实操作的就是这种结构）。"""
    return {"role": "user", "content": [{"type": "tool_result", "content": text}]}


def _reset_cd():
    # compress_history_tags 有个 interval 冷却计数，force=True 会清零并强制执行
    L.compress_history_tags._cd = 0


def _run(msgs, sess=None, keep_recent=0):
    _reset_cd()
    return L.compress_history_tags(msgs, keep_recent=keep_recent, force=True, sess=sess)


def test_short_segment_untouched():
    # 短于 36K 的段落原样保留
    short = "这是一段很短的历史内容"
    msgs = [_wrap_tool_result(short)]
    _run(msgs)
    assert msgs[0]["content"][0]["content"] == short


def test_long_segment_llm_compressed():
    # 超过 36K 且传入 sess → 走 LLM 压缩，留下压缩标记
    long_text = "废话内容" * 12000  # 约 48K 字
    assert len(long_text) > L.HISTORY_COMPRESS_THRESHOLD
    sess = _FakeSess()
    msgs = [_wrap_tool_result(long_text)]
    _run(msgs, sess=sess)
    out = msgs[0]["content"][0]["content"]
    assert sess.calls == 1
    assert "...[压缩]" in out
    assert "LLM压缩后的要点" in out
    assert len(out) < len(long_text)


def test_long_segment_fallback_when_llm_fails():
    # LLM 压缩失败 → 回退头尾截断，不抛异常
    long_text = "报错前文" * 12000
    sess = _FakeSess(fail=True)
    msgs = [_wrap_tool_result(long_text)]
    _run(msgs, sess=sess)
    out = msgs[0]["content"][0]["content"]
    assert "...[Truncated]..." in out
    assert "...[压缩]" not in out


def test_long_segment_truncate_when_no_sess():
    # 不传 sess（无法调 LLM）→ 直接头尾截断
    long_text = "无模型可用" * 12000
    msgs = [_wrap_tool_result(long_text)]
    _run(msgs, sess=None)
    out = msgs[0]["content"][0]["content"]
    assert "...[Truncated]..." in out


def test_skill_doc_exempt_even_when_huge():
    # 被 <skill_doc> 包裹的技能文档，哪怕超 36K 也整体豁免、绝不压缩
    doc = L.SKILL_DOC_OPEN + ("技能正文" * 12000) + L.SKILL_DOC_CLOSE
    sess = _FakeSess()
    msgs = [_wrap_tool_result(doc)]
    _run(msgs, sess=sess)
    out = msgs[0]["content"][0]["content"]
    assert out == doc
    assert sess.calls == 0  # 根本没调 LLM


def test_already_compressed_can_be_recompressed():
    # 已压缩过的段落若仍超阈值，可以再次压缩（非 skill 内容无重复压缩限制）
    already = "...[压缩] 上一轮的要点" + ("尾巴" * 20000)
    assert len(already) > L.HISTORY_COMPRESS_THRESHOLD
    sess = _FakeSess()
    msgs = [_wrap_tool_result(already)]
    _run(msgs, sess=sess)
    assert sess.calls == 1  # 不因带压缩标记而被跳过


def test_threshold_is_36k():
    assert L.HISTORY_COMPRESS_THRESHOLD == 36000


def test_compression_cooldown_is_per_session():
    a = _FakeSess()
    b = _FakeSess()
    short = [_wrap_tool_result("short")]

    L.compress_history_tags(short, interval=2, sess=a)
    assert a._history_compress_cd == 1
    assert not hasattr(b, '_history_compress_cd')

    L.compress_history_tags(short, interval=2, sess=b)
    assert b._history_compress_cd == 1
    assert a._history_compress_cd == 1


def test_trim_enforces_hard_cap_even_with_many_skill_docs():
    sess = _FakeSess()
    sess.context_win = 1000
    sess.trim_keep_rate = 0.6
    sess.cut_msg_interval = 5
    doc = L.SKILL_DOC_OPEN + ("技能正文" * 3000) + L.SKILL_DOC_CLOSE
    history = [_wrap_tool_result(doc) for _ in range(12)]

    L.trim_messages_history(history, sess)

    cost = sum(len(L.json.dumps(m, ensure_ascii=False)) for m in history)
    assert cost <= sess.context_win * 3
    assert len(history) >= 1


def _main():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = 0
    failed = 0
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


if __name__ == "__main__":
    sys.exit(_main())
