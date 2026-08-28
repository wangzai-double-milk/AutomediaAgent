"""视频流识别 / 抠取的纯逻辑测试（抖音 + 小红书双站）。

覆盖 tools/image.py 里 4 个纯函数，全部离线、无网络、无费用：
  - _is_direct_video_url     判断是否为可直传 Files API 的真实流
  - _is_video_page_url       判断是否为需要触发抠流的详情页 URL
  - _referer_for             按 host 选分站 Referer（抖音传 xhs Referer 会 403）
  - _extract_video_candidates 从 HTML/JSON 文本抠流并按可信度排序

真实的浏览器抠流 + Files API 整段理解属联网测试，另见 tests/README（不在 CI 跑）。

两用：
  python3 tests/test_video_stream_extract.py   # 直跑，打印逐条结果
  pytest tests/test_video_stream_extract.py    # 若装了 pytest
"""
import os
import sys

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.abspath(os.path.join(_THIS_DIR, '..'))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from tools import image as im  # noqa: E402


# ── 样例数据 ──────────────────────────────────────────────────────
DOUYIN_VOD = ("https://v26-weba.douyinvod.com/abc/video/tos/cn/tos-cn-ve-0/"
              "xxxx/?a=6383&mime_type=video_mp4")
XHS_STREAM_MP4 = ("https://sns-video-v3.xhscdn.com/stream/1/110/301/"
                  "01ea2a30b13c6269010370019eb4dce53a_301.mp4"
                  "?sign=c23dd1e07ff55a87075076cadbb553b5&t=6a500d7a")
XHS_STREAM_NO_SUFFIX = ("https://sns-video-v2.xhscdn.com/stream/110/259/"
                        "01e9abcd1234deadbeef?sign=deadbeef&t=6a500000")


# ── _is_direct_video_url ─────────────────────────────────────────
def test_direct_accepts_douyinvod():
    assert im._is_direct_video_url(DOUYIN_VOD) is True


def test_direct_accepts_xhs_stream_mp4():
    assert im._is_direct_video_url(XHS_STREAM_MP4) is True


def test_direct_accepts_xhs_stream_without_suffix():
    # 小红书流有时省略 .mp4 后缀，靠 xhscdn + /stream/ 命中
    assert im._is_direct_video_url(XHS_STREAM_NO_SUFFIX) is True


def test_direct_rejects_blob():
    assert im._is_direct_video_url("blob:https://www.xiaohongshu.com/uuid") is False


def test_direct_rejects_image():
    assert im._is_direct_video_url("https://ci.xiaohongshu.com/a.webp") is False
    assert im._is_direct_video_url("https://p.douyin.com/b.jpeg") is False


def test_direct_rejects_client_installer():
    # 抖音 PC 客户端安装包 / eden 静态资源不是视频流
    assert im._is_direct_video_url(
        "https://www.douyin.com/download/douyin_pc_client.exe") is False
    assert im._is_direct_video_url(
        "https://static.bytednsdoc.com/obj/eden-cn/foo.mp4") is False


def test_direct_rejects_non_url_input():
    assert im._is_direct_video_url(None) is False
    assert im._is_direct_video_url(123) is False


# ── _is_video_page_url ───────────────────────────────────────────
def test_page_douyin_video():
    assert im._is_video_page_url("https://www.douyin.com/video/7412345678901234567") is True


def test_page_douyin_modal_id_search():
    # 回归：搜索页/推荐页点开视频是弹层路由 ?modal_id=<id>，不含 /video/，
    # 曾因此漏判导致抠流不触发、URL 当图片喂 API 报 HTTP 400。
    assert im._is_video_page_url(
        "https://www.douyin.com/search/奢侈品广告拍摄"
        "?modal_id=7125593823710858530&type=general") is True


def test_page_douyin_modal_id_on_user_home():
    assert im._is_video_page_url(
        "https://www.douyin.com/user/MS4wxxxx?modal_id=7412345678901234567") is True


def test_page_iesdouyin_share():
    assert im._is_video_page_url(
        "https://www.iesdouyin.com/share/video/7412345678901234567/") is True


def test_page_xhs_explore():
    assert im._is_video_page_url(
        "https://www.xiaohongshu.com/explore/6a2a30b10000000007025858"
        "?xsec_token=ABC&xsec_source=pc_feed") is True


def test_page_xhs_discovery_item():
    assert im._is_video_page_url(
        "https://www.xiaohongshu.com/discovery/item/6a2a30b1?xsec_token=ABC") is True


def test_page_rejects_direct_stream():
    # 真实流不是"页面"，不该触发抠流
    assert im._is_video_page_url(XHS_STREAM_MP4) is False
    assert im._is_video_page_url(DOUYIN_VOD) is False


def test_page_rejects_home_and_search():
    assert im._is_video_page_url("https://www.xiaohongshu.com/") is False
    assert im._is_video_page_url(
        "https://www.xiaohongshu.com/search_result?keyword=foo") is False


# ── _referer_for ─────────────────────────────────────────────────
def test_referer_douyin():
    assert im._referer_for(DOUYIN_VOD) == "https://www.douyin.com/"


def test_referer_xhscdn():
    # 关键：给小红书 CDN 必须用 xhs Referer，否则 403
    assert im._referer_for(XHS_STREAM_MP4) == "https://www.xiaohongshu.com/"


def test_referer_xiaohongshu_page():
    assert im._referer_for(
        "https://www.xiaohongshu.com/explore/abc") == "https://www.xiaohongshu.com/"


# ── _extract_video_candidates ────────────────────────────────────
def test_extract_from_plain_html():
    html = f'<video src="{DOUYIN_VOD}"></video>'
    cands = im._extract_video_candidates(html)
    assert any('douyinvod.com' in c for c in cands)


def test_extract_xhs_from_escaped_json():
    # 页面 __INITIAL_STATE__ 里 URL 常带 \/ 转义
    escaped = XHS_STREAM_MP4.replace('/', r'\/')
    blob = '{"stream":{"h265":[{"master_url":"%s"}]}}' % escaped
    cands = im._extract_video_candidates(blob)
    assert any('xhscdn.com/stream/' in c and '\\/' not in c for c in cands)


def test_extract_ranks_real_stream_above_image():
    mixed = (f'"cover":"https://ci.xiaohongshu.com/cover.webp",'
             f'"video":"{XHS_STREAM_MP4}"')
    cands = im._extract_video_candidates(mixed)
    assert cands, "应至少抠到真实流"
    # 图片被过滤（负分/不合格），真实流排第一
    assert 'xhscdn.com/stream/' in cands[0]
    assert not any('.webp' in c for c in cands)


def test_extract_dedup():
    blob = f'{DOUYIN_VOD} {DOUYIN_VOD} {DOUYIN_VOD}'
    cands = im._extract_video_candidates(blob)
    assert len(cands) == len(set(cands))


def test_extract_empty_text():
    assert im._extract_video_candidates("") == []
    assert im._extract_video_candidates(None) == []


# ── runner（无 pytest 时可直跑）───────────────────────────────────
def _run_all():
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith('test_') and callable(f)]
    passed, failed = 0, 0
    for name, fn in tests:
        try:
            fn()
            passed += 1
            print(f"  PASS  {name}")
        except AssertionError as e:
            failed += 1
            print(f"  FAIL  {name}: {e}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"  ERROR {name}: {type(e).__name__}: {e}")
    print(f"\n{passed} passed, {failed} failed (total {len(tests)})")
    return failed == 0


if __name__ == '__main__':
    ok = _run_all()
    sys.exit(0 if ok else 1)
