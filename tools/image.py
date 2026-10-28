"""
AutoMedia 媒体原子能力（工作流编排见 skills/registry.json）：
  - review_image(image, hint='', reference_images=None)   严格档审图（支持多图对比）
  - edit_image(image, edit_prompts, save_path=None)       image editing
  - generate_image(prompt, size='2K', save_path=None)     image generation
  - extract_text_from_image(image)                        VLM OCR / 读图（提攻略文字）

所有函数返回普通 dict（review）或本地路径 str（edit/generate）。
所有对外 API 调用都经 _http_post_json 统一落盘到 logs/image.log（规则1）。
"""

import os
import sys
import json
import time
import re
import base64
import logging
import mimetypes
import urllib.request
import urllib.error
import urllib.parse
import tempfile
import uuid as uuid_mod
import requests
from requests_toolbelt.multipart.encoder import MultipartEncoder
from concurrent.futures import ThreadPoolExecutor, as_completed

# 让 import mykey 不依赖 cwd
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.abspath(os.path.join(_THIS_DIR, '..'))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

try:
    import mykey  # noqa: E402
except (ImportError, FileNotFoundError, ValueError):
    # Offline imports/tests do not require credentials; API calls validate below.
    mykey = None

# ─────────────────────────────────────────────────────────────────
# 日志（规则1：调用外部接口必须落盘，方便查问题）
# 统一写入项目根的 logs/image.log；同时回显到控制台。
# ─────────────────────────────────────────────────────────────────
_LOG_DIR = os.path.join(_PROJECT_ROOT, 'logs')
os.makedirs(_LOG_DIR, exist_ok=True)
log = logging.getLogger('tools.image')
if not log.handlers:
    log.setLevel(logging.INFO)
    _fmt = logging.Formatter('%(asctime)s %(levelname)s [image] %(message)s',
                             datefmt='%Y-%m-%d %H:%M:%S')
    _fh = logging.FileHandler(os.path.join(_LOG_DIR, 'image.log'), encoding='utf-8')
    _fh.setFormatter(_fmt)
    log.addHandler(_fh)
    _sh = logging.StreamHandler()
    _sh.setFormatter(_fmt)
    log.addHandler(_sh)


# ─────────────────────────────────────────────────────────────────
# 配置
# ─────────────────────────────────────────────────────────────────
def _load_media_config():
    if mykey is not None and hasattr(mykey, 'native_oai_config'):
        cfg = dict(mykey.native_oai_config)
        return cfg, dict(getattr(mykey, 'volc_models', {}) or {})

    explicit = os.environ.get('AUTOMEDIA_CONFIG')
    if explicit and not os.path.isfile(explicit):
        raise FileNotFoundError('AUTOMEDIA_CONFIG points to a missing file')
    candidates = [explicit] if explicit else [
        os.path.join(os.getcwd(), 'config.json'),
        os.path.expanduser('~/.config/automedia/config.json'),
        os.path.join(_PROJECT_ROOT, 'config.json'),
    ]
    for path in candidates:
        if not path or not os.path.isfile(path):
            continue
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
        cfg = {
            'apikey': data.get('volc_apikey') or data.get('apikey') or '',
            'apibase': data.get('volc_apibase') or data.get('apibase') or '',
        }
        return cfg, dict(data.get('models') or {})

    return {
        'apikey': os.environ.get('VOLC_API_KEY', ''),
        'apibase': os.environ.get(
            'VOLC_API_BASE',
            'https://ark.cn-beijing.volces.com/api/v3',
        ),
    }, {}


_CFG, _MODELS = _load_media_config()
API_BASE = (_CFG.get('apibase') or 'https://ark.cn-beijing.volces.com/api/v3').rstrip('/')
API_KEY = _CFG.get('apikey') or ''

# 模型名优先从 mykey.volc_models 读取，也兼容 README 约定的 config.json。
VLM_MODEL = _MODELS.get('vlm', 'doubao-seed-2-0-pro-260215')   # 审图：全模态 VLM (Seed 2.0 Pro)
# 修图和生图使用配置文件中对应的模型端点。
EDIT_MODEL = _MODELS.get('edit', 'doubao-seedream-4-5-251128')  # 改图（i2i）
GEN_MODEL = _MODELS.get('gen', 'doubao-seedream-4-5-251128')    # 生图（t2i）

CHAT_URL = API_BASE + '/chat/completions'
RESPONSES_URL = API_BASE + '/responses'
IMAGES_URL = API_BASE + '/images/generations'
FILES_URL = API_BASE + '/files'

# 长视频理解：通过 Files API 上传整段视频（最大 512MB），由模型按 fps 抽帧理解全片，
# 而不是本地压缩成关键帧。fps 越低、token 越省；默认 1.0，长视频可降到 0.3。
# 想调采样率只改 mykey.video_sample_fps 即可。
VIDEO_SAMPLE_FPS = float(getattr(mykey, 'video_sample_fps', 1.0) if mykey else 1.0)

# 火山 Files API 单文件上限 512MB。4K/长片很容易贴近或超过该上限，
# 超过时先用 moviepy（底层走 imageio-ffmpeg 自带二进制，无需系统装 ffmpeg）压成审查代理，
# 只降传输/审查体积，不改动创作原片；代理用完即删。
_ARK_FILES_MAX_BYTES = 512 * 1024 * 1024
# 代理目标体积：压到 480MB 以内，给网关/元数据留裕量。
_VLM_PROXY_TARGET_BYTES = int(getattr(mykey, 'vlm_proxy_target_bytes', 480 * 1024 * 1024) if mykey else 480 * 1024 * 1024)
# 视频原生理解（推理模型）单次推理常 >180s，长片/4K 整片更久，给足超时。
VIDEO_UNDERSTAND_TIMEOUT = int(getattr(mykey, 'video_understand_timeout', 1800) if mykey else 1800)
# 视频审查输出上限：尽量拉大，避免长视频逐镜头审查被中途截断。可用 mykey 覆盖。
VLM_MAX_OUTPUT_TOKENS = int(getattr(mykey, 'vlm_max_output_tokens', 32768) if mykey else 32768)

OUTPUT_DIR = os.path.join(_PROJECT_ROOT, 'temp', 'image_review')
os.makedirs(OUTPUT_DIR, exist_ok=True)


# ─────────────────────────────────────────────────────────────────
# 提示词加载（prompt 不再硬编码在此，统一放 skills/soft_skills/xhs_review_prompt/，
# 用户想改审图风格/识菜/OCR 调性，直接改那些 .md 即可，无需动本文件）
# ─────────────────────────────────────────────────────────────────
_PROMPT_DIR = os.path.join(_PROJECT_ROOT, 'skills', 'soft_skills', 'xhs_review_prompt')

# 读文件失败时的极简兜底（保证断网/缺文件也不崩；完整版在上述 skill 目录）
_FALLBACK_PROMPTS = {
    'review_solo': '你是毒舌但专业的小红书美食审图官。严格判 PASS/REWORK/REPHOTO，'
                   '只输出 JSON：{"verdict","issues","edit_prompts","additional_shots_needed",'
                   '"annotation_prompts","roast","title_hints","summary"}。',
    'review_with_refs': '你是毒舌但专业的小红书美食审图官，第1张为原图、其余为爆款封面。'
                        '对比后严格判 PASS/REWORK/REPHOTO，只输出 JSON：'
                        '{"verdict","viral_patterns","gaps_vs_viral","issues","edit_prompts",'
                        '"additional_shots_needed","annotation_prompts","roast","title_hints","summary"}。',
    'identify_dish': '你是美食类目识别专家。只输出 JSON：'
                     '{"dish_name","category","search_keywords","confidence"}。',
    'ocr': '你是图像文字提取助手。把图里所有文字完整原样抄出，不总结不改写；无文字输出空串。',
}


def _load_prompt(name: str) -> str:
    """从 skills/soft_skills/xhs_review_prompt/<name>.md 加载提示词；失败回退内置兜底。
    每次调用都重新读取，便于用户改完 .md 立刻生效。"""
    path = os.path.join(_PROMPT_DIR, f'{name}.md')
    try:
        with open(path, encoding='utf-8') as f:
            text = f.read().strip()
        if text:
            return text
    except Exception as e:
        log.warning('加载提示词 %s.md 失败，使用内置兜底: %s', name, e)
    return _FALLBACK_PROMPTS.get(name, '')


# ─────────────────────────────────────────────────────────────────
# 工具函数
# ─────────────────────────────────────────────────────────────────
def _require_api_key():
    if not API_KEY or API_KEY.startswith('ark-在这里'):
        raise RuntimeError(
            '未配置火山引擎 API Key。请设置 VOLC_API_KEY，或在当前目录/config '
            '路径放置 config.json（也可用 AUTOMEDIA_CONFIG 指定）。'
        )


def _http_post_json(url: str, payload: dict, timeout: int = 180) -> dict:
    _require_api_key()
    # 规则1：调用外部接口前留日志（记录目标 URL + 模型，敏感的 image base64 不展开）
    model = payload.get('model', '?')
    log.info('POST %s model=%s timeout=%s', url, model, timeout)
    _t0 = time.time()
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode('utf-8'),
        headers={
            'Authorization': f'Bearer {API_KEY}',
            'Content-Type': 'application/json',
        },
        method='POST',
    )
    try:
        resp = urllib.request.urlopen(req, timeout=timeout).read().decode('utf-8')
    except urllib.error.HTTPError as e:
        body = e.read().decode('utf-8', errors='replace')
        log.error('POST %s 失败 HTTP %s (%.1fs): %s', url, e.code, time.time() - _t0, body[:500])
        # 如果是视频过大等特殊情况，这里可以自定义异常或者把 body 带出
        raise RuntimeError(f'HTTP {e.code} @ {url}: {body}')
    except Exception as e:
        log.error('POST %s 异常 (%.1fs): %s', url, time.time() - _t0, e)
        raise
    log.info('POST %s 成功 model=%s (%.1fs)', url, model, time.time() - _t0)
    return json.loads(resp)


def _build_responses_input(sys_prompt: str, user_text: str, images: list = None, videos: list = None) -> list:
    """构造符合火山引擎 Responses API 格式的 input 数组"""
    input_items = []
    
    # 构建 content 列表
    content = []
    
    # 优先添加文本
    if user_text:
        content.append({"type": "input_text", "text": user_text})
        
    # 添加图片
    if images:
        for img_url in images:
            content.append({"type": "input_image", "image_url": img_url})
            
    # 添加视频
    if videos:
        for vid in videos:
            # 通过 Files API 上传后拿到的 file_id（推荐：整段长视频理解），形如 "file-2025..."
            if isinstance(vid, str) and vid.startswith('file-'):
                content.append({"type": "input_video", "file_id": vid})
            else:
                content.append({"type": "input_video", "video_url": vid})
            
    # 只有 user 角色，system prompt 也是作为 input_text 放在前面（或者通过 instructions 传，这里直接放前面）
    if sys_prompt:
        content.insert(0, {"type": "input_text", "text": f"【System Instructions】\n{sys_prompt}\n\n"})
        
    input_items.append({
        "role": "user",
        "content": content
    })
    
    return input_items


def image_to_url(path_or_url: str) -> str:
    """本地路径 → data URL；公网 URL 原样返回。"""
    if path_or_url.startswith(('http://', 'https://', 'data:')):
        return path_or_url
    path = os.path.expanduser(path_or_url)
    if not os.path.exists(path):
        raise FileNotFoundError(f'图片不存在: {path}')
    mime, _ = mimetypes.guess_type(path)
    if mime not in ('image/jpeg', 'image/png', 'image/webp', 'image/gif'):
        mime = 'image/jpeg'
    with open(path, 'rb') as f:
        b64 = base64.b64encode(f.read()).decode('ascii')
    return f'data:{mime};base64,{b64}'


def _download(url: str, save_path: str) -> str:
    urllib.request.urlretrieve(url, save_path)
    return save_path


def _referer_for(url: str) -> str:
    """按视频流所属站点选 Referer。抖音/小红书 CDN 会校验 Referer，
    带错站点的 Referer（例如给小红书 CDN 传抖音 Referer）会直接 403。"""
    host = ''
    try:
        host = urllib.parse.urlparse(url).netloc.lower()
    except Exception:
        pass
    if 'xhscdn.com' in host or 'xiaohongshu.com' in host:
        return 'https://www.xiaohongshu.com/'
    # 抖音系（douyinvod.com / douyin.com / *.byteimg 等）默认走抖音
    return 'https://www.douyin.com/'


def _video_request(url: str, method: str = 'GET'):
    headers = {
        'User-Agent': ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                       'AppleWebKit/537.36 (KHTML, like Gecko) '
                       'Chrome/126.0.0.0 Safari/537.36'),
        'Accept': '*/*',
        'Referer': _referer_for(url),
    }
    return urllib.request.Request(url, headers=headers, method=method)


def _is_video_page_url(url: str) -> bool:
    try:
        p = urllib.parse.urlparse(url)
        host = p.netloc.lower()
        query = p.query.lower()
        if 'douyin.com' in host:
            # 三种打开视频的路由都算详情页：
            #   1. /video/<id>            —— 直接分享链接
            #   2. /search|/discover 等 ?modal_id=<id> —— 搜索页/推荐页点开的弹层播放（实测最常见）
            #   3. /user/xxx?modal_id=<id> —— 主页点开的弹层
            if '/video/' in p.path or 'modal_id=' in query:
                return True
        if 'iesdouyin.com/share/video' in url:
            return True
        # 小红书笔记详情页（视频笔记也走这个路由）：/explore/<id> 或 /discovery/item/<id>
        if 'xiaohongshu.com' in host and ('/explore/' in p.path or '/discovery/item/' in p.path):
            return True
        return False
    except Exception:
        return False


def _is_direct_video_url(url: str) -> bool:
    if not isinstance(url, str):
        return False
    u = url.lower()
    if u.startswith('blob:'):
        return False
    if any(x in u for x in ('douyin_pc_client', 'download/douyin_pc', 'static.bytednsdoc.com/obj/eden-cn', '.exe')):
        return False
    if any(x in u for x in ('.mp4', '.m3u8', 'douyinvod.com', '/video/tos/')):
        return True
    # 小红书视频流：sns-video*.xhscdn.com/stream/.../xxx（有时 .mp4 后缀在 query 前，有时省略），
    # 统一按 xhscdn + /stream/ 认定为可直传的真实流。
    if 'xhscdn.com' in u and '/stream/' in u:
        return True
    return False


def _normalize_video_candidate(s: str) -> str:
    if not s:
        return ''
    s = str(s).strip().strip('"\'')
    # 页面 JSON 里常见 https:\/\/...、\\u002F、百分号编码混用。
    s = s.replace('\\u002F', '/').replace('\\/', '/').replace('&amp;', '&')
    for _ in range(3):
        try:
            ns = urllib.parse.unquote(s)
        except Exception:
            break
        if ns == s:
            break
        s = ns
    return s


def _extract_video_candidates(text: str) -> list:
    if not text:
        return []
    raw = text
    # 先把常见转义展开一次，避免正则截不到。
    expanded = raw.replace('\\u002F', '/').replace('\\/', '/').replace('&amp;', '&')
    candidates = []
    patterns = [
        r'https?://[^"\'<>\s\\]+(?:douyinvod\.com|/video/tos/|\.mp4|\.m3u8)[^"\'<>\s\\]*',
        r'https?:\\?/\\?/[^"\'<>\s]+(?:douyinvod\.com|/video/tos/|\.mp4|\.m3u8)[^"\'<>\s]*',
        # 小红书视频流：sns-video*.xhscdn.com/stream/...（.mp4 后缀可能省略，靠 xhscdn+/stream/ 命中）
        r'https?://[^"\'<>\s\\]*xhscdn\.com/stream/[^"\'<>\s\\]*',
        r'https?:\\?/\\?/[^"\'<>\s]*xhscdn\.com/stream/[^"\'<>\s]*',
    ]
    for body in (raw, expanded):
        for pat in patterns:
            for m in re.finditer(pat, body, flags=re.I):
                u = _normalize_video_candidate(m.group(0))
                if _is_direct_video_url(u):
                    candidates.append(u)

    seen = set()
    uniq = []
    for u in candidates:
        if u in seen:
            continue
        seen.add(u)
        uniq.append(u)

    def score(u):
        lu = u.lower()
        s = 0
        if 'douyinvod.com' in lu:
            s += 100
        if 'xhscdn.com' in lu and '/stream/' in lu:
            s += 100
        if 'static.bytednsdoc.com' in lu or 'douyin_pc_client' in lu:
            s -= 200
        if '.mp4' in lu or 'mime_type=video_mp4' in lu:
            s += 50
        if '.m3u8' in lu:
            s += 30
        if 'playwm' in lu:
            s -= 10
        if any(x in lu for x in ('.webp', '.jpeg', '.jpg', '.png')):
            s -= 100
        return s

    return sorted(uniq, key=score, reverse=True)


def resolve_video_page_url(video_url: str) -> str:
    """把抖音等页面 URL 解析成真实 video/* URL。
    成功返回真实流地址；无法解析时返回原 URL，让调用方决定是否报错/降级。

    ⚠️ 实测结论（2026-07 抖音/小红书均验证）：抖音/小红书的播放页是 SPA，
    真实视频流由 JS 运行时通过 MSE 动态请求，<video>.src 是 blob:，
    静态 HTML 里根本没有 douyinvod.com / xhscdn.com 的真实流地址——
    所以本函数对这两站基本解析不到（只能兜底返回原 URL）。
    正确拿流方式是"人工/浏览器抠流"：用浏览器打开播放页 → 实际播放 →
    抓 network requests，捞里面的真实流：
      · 抖音：DASH 拆分双轨，需分别抓 media-video-hvc1（视频/HEVC）
              + media-audio-und-mp4a（音频），下载后用 moviepy 合并成完整 mp4；
              视频轨要页面实际在播放、buffer 起来后才会出现。
      · 小红书：单文件完整 mp4，形如 sns-video-v2.xhscdn.com/stream/.../xxx.mp4，
              直接下载即可（注意 Referer 要用 xiaohongshu.com，见 _referer_for）。
    抠到真实流后可直接喂 download_video_to_temp / analyze_image。
    """
    if not isinstance(video_url, str) or not video_url.startswith(('http://', 'https://')):
        return video_url
    if _is_direct_video_url(video_url):
        return video_url
    if not _is_video_page_url(video_url):
        return video_url

    try:
        log.info('尝试从视频页面解析真实视频流: %s', video_url)
        html = urllib.request.urlopen(_video_request(video_url), timeout=30).read().decode('utf-8', errors='replace')
        cands = _extract_video_candidates(html)
        if cands:
            log.info('从视频页面解析到真实视频流: %s', cands[0][:120])
            return cands[0]
        log.warning('视频页面未解析到真实视频流: %s', video_url)
    except Exception as e:
        log.warning('解析视频页面失败: %s -> %s', video_url, e)
    return video_url


def download_video_to_temp(video_url: str, suffix: str = '.mp4', max_bytes: int = 512 * 1024 * 1024) -> str:
    """把真实视频 URL 下载到本地临时文件，调用方负责删除。"""
    fd, path = tempfile.mkstemp(prefix='media_', suffix=suffix, dir=OUTPUT_DIR)
    os.close(fd)
    total = 0
    try:
        with urllib.request.urlopen(_video_request(video_url), timeout=120) as resp, open(path, 'wb') as f:
            ct = (resp.headers.get('Content-Type') or '').lower()
            if 'text/html' in ct:
                raise RuntimeError(f'下载到 HTML 而不是视频: content-type={ct}')
            while True:
                chunk = resp.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise RuntimeError(f'视频超过下载上限 {max_bytes} bytes')
                f.write(chunk)
        if total <= 0:
            raise RuntimeError('下载视频为空')
        log.info('已下载视频到临时文件: %s (%.1fMB)', path, total / 1024 / 1024)
        return path
    except Exception:
        try:
            os.remove(path)
        except Exception:
            pass
        raise


def _mb(n) -> float:
    return n / (1024 * 1024)


def prepare_video_for_vlm(video: str, target_bytes: int = None,
                          files_max_bytes: int = None) -> dict:
    """本地视频超过 Files API 上限时，用 moviepy 压成"审查代理"文件再上传。

    - 只做传输/审查体积压缩（降分辨率+码率），不动创作原片；
    - moviepy 底层用 imageio-ffmpeg 自带的静态 ffmpeg 二进制，无需系统装 ffmpeg；
    - 多档降级 1080p→720p→540p，直到压到 target_bytes 以内；
    - 返回 {'path', 'prepared', 'reason', ...}，代理路径由调用方负责用完删除。
    远程 URL / 不存在 / 未超限：原样返回 prepared=False。
    """
    if target_bytes is None:
        target_bytes = _VLM_PROXY_TARGET_BYTES
    if files_max_bytes is None:
        files_max_bytes = _ARK_FILES_MAX_BYTES
    if not isinstance(video, str) or video.startswith(('http://', 'https://')):
        return {'path': video, 'prepared': False, 'reason': 'remote_url'}
    src = os.path.expanduser(video)
    if not os.path.exists(src):
        return {'path': video, 'prepared': False, 'reason': 'missing_local_file'}
    size = os.path.getsize(src)
    if size <= files_max_bytes:
        return {'path': src, 'prepared': False, 'reason': 'within_files_api_limit',
                'source_size_mb': round(_mb(size), 1)}

    try:
        # moviepy 2.x 从顶层导入且方法名为 resized()；1.x 从 moviepy.editor 导入且为 resize()。
        try:
            from moviepy import VideoFileClip  # 2.x
        except Exception:
            from moviepy.editor import VideoFileClip  # 1.x
    except Exception as e:
        raise RuntimeError(
            f'视频 {_mb(size):.0f}MB 超过 Files API 上限 {_mb(files_max_bytes):.0f}MB，'
            f'需要 moviepy 压缩代理，但导入 moviepy 失败: {e}。请 pip install moviepy。')

    def _resize_clip(clip, height):
        if clip.h <= height:
            return clip
        fn = getattr(clip, 'resized', None) or getattr(clip, 'resize', None)  # 2.x / 1.x
        return fn(height=height)

    stem, _ = os.path.splitext(os.path.basename(src))
    out = os.path.join(OUTPUT_DIR, f'{stem}_vlm_review_{uuid_mod.uuid4().hex[:8]}.mp4')
    last_err = None
    for height, bitrate in ((1080, '2000k'), (720, '1200k'), (540, '800k')):
        clip = None
        try:
            log.info('moviepy 压缩审查代理: %s -> 高%s 码率%s', src, height, bitrate)
            clip = VideoFileClip(src)
            target_clip = _resize_clip(clip, height)
            target_clip.write_videofile(
                out, codec='libx264', audio_codec='aac', bitrate=bitrate,
                preset='ultrafast', threads=os.cpu_count() or 2,
                ffmpeg_params=['-movflags', '+faststart'],
                logger=None,
            )
            proxy_size = os.path.getsize(out)
            if proxy_size <= target_bytes:
                log.info('审查代理已生成: %s (%.1fMB, 高%s)', out, _mb(proxy_size), height)
                return {'path': out, 'prepared': True, 'reason': 'compressed',
                        'source_path': src, 'source_size_mb': round(_mb(size), 1),
                        'proxy_size_mb': round(_mb(proxy_size), 1), 'height': height}
            last_err = RuntimeError(
                f'代理仍过大 {_mb(proxy_size):.1f}MB > {_mb(target_bytes):.1f}MB，降档重试')
            log.warning('%s', last_err)
        except Exception as e:
            last_err = e
            log.warning('moviepy 压缩(高%s)失败: %s', height, e)
        finally:
            if clip is not None:
                try:
                    clip.close()
                except Exception:
                    pass
    try:
        if os.path.exists(out):
            os.remove(out)
    except Exception:
        pass
    raise RuntimeError(f'prepare_video_for_vlm 压缩失败 source={src} '
                       f'size={_mb(size):.1f}MB last_error={last_err}')


def _strip_md_fence(s: str) -> str:
    s = s.strip()
    if s.startswith('```'):
        s = s.strip('`')
        if s.lower().startswith('json'):
            s = s[4:]
        s = s.strip()
    return s


def _extract_output_text(data: dict) -> str:
    """从火山引擎 Responses API 的返回中稳健地提取模型文本。
    Responses API 的 output 是一个 list，里面可能混有 reasoning（思维链，无 text 字段）
    和 message 项；真正的回答在 message 项的 content[*].text（type=='output_text'）里。
    同时兼容旧版 Chat API 的 choices 结构，以及 output 为 dict 的情况。"""
    if not isinstance(data, dict):
        return ''
    out = data.get('output')
    # 新版 Responses API：output 是 list
    if isinstance(out, list):
        texts = []
        for item in out:
            if not isinstance(item, dict):
                continue
            # 跳过 reasoning 思维链项
            if item.get('type') == 'reasoning':
                continue
            content = item.get('content')
            if isinstance(content, list):
                for c in content:
                    if isinstance(c, dict) and c.get('type') in ('output_text', 'text') and c.get('text'):
                        texts.append(c['text'])
            elif item.get('text'):  # 兜底：item 直接带 text
                texts.append(item['text'])
        if texts:
            return '\n'.join(texts).strip()
    # output 是 dict（部分网关）
    if isinstance(out, dict):
        if out.get('text'):
            return out['text'].strip()
        content = out.get('content')
        if isinstance(content, list):
            texts = [c.get('text', '') for c in content if isinstance(c, dict) and c.get('text')]
            if texts:
                return '\n'.join(texts).strip()
    # 兼容 Chat API 的 choices 结构
    choices = data.get('choices')
    if isinstance(choices, list) and choices:
        msg = choices[0].get('message', {})
        if msg.get('content'):
            return msg['content'].strip()
    return ''


# ─────────────────────────────────────────────────────────────────
# ① 审图（支持单图 / 多图对比）
# ─────────────────────────────────────────────────────────────────
def review_image(image: str, hint: str = '', reference_images=None) -> dict:
    """严格档审图。
    - 单图模式：只传 image，按 review_solo 提示词评分。
    - 对比模式：附带 reference_images（同品类爆款封面），按 review_with_refs 提示词评分，
      额外输出 viral_patterns / gaps_vs_viral / roast 字段。
    （提示词均加载自 skills/soft_skills/xhs_review_prompt/，可编辑）
    """
    image_url = image_to_url(image)

    if reference_images:
        sys_prompt = _load_prompt('review_with_refs')
        user_text = (
            f'第 1 张是【待审原图】，后面 {len(reference_images)} 张是【该品类目前的爆款封面】。\n'
            '请严格按系统提示做对比分析，并直接输出 JSON。'
        )
        if hint:
            user_text += f'\n补充信息：{hint}'
        
        ref_urls = [image_to_url(ref) for ref in reference_images]
        inputs = _build_responses_input(sys_prompt, user_text, images=[image_url] + ref_urls)
    else:
        sys_prompt = _load_prompt('review_solo')
        user_text = '请按系统提示中的严格档标准审核这张菜品图，并直接输出 JSON。'
        if hint:
            user_text += f'\n补充信息：{hint}'
        inputs = _build_responses_input(sys_prompt, user_text, images=[image_url])

    payload = {
        'model': VLM_MODEL,
        'input': inputs,
        'temperature': 0.3,
        'max_output_tokens': 2048,
    }
    data = _http_post_json(RESPONSES_URL, payload)
    
    try:
        raw = _extract_output_text(data)
        return json.loads(_strip_md_fence(raw))
    except Exception as e:
        log.error('review_image parse failed: %s', e)
        return {
            "verdict": "ERROR",
            "roast": "",
            "issues": [f"模型返回无法解析，不能判定为通过: {e}"],
            "raw_output": _extract_output_text(data)[:1000],
        }


# ─────────────────────────────────────────────────────────────────
# ② 改图
# ─────────────────────────────────────────────────────────────────
def edit_image(image: str, edit_prompts, save_path: str = None) -> str:
    """把多条 edit_prompts 合并后调用图像编辑模型；返回保存的本地路径。"""
    if isinstance(edit_prompts, str):
        prompts = [edit_prompts]
    else:
        prompts = list(edit_prompts)
    merged = '；'.join(prompts)
    image_url = image_to_url(image)

    if save_path is None:
        save_path = os.path.join(OUTPUT_DIR, f'edited_{int(time.time())}.png')

    # 带 image 参数触发图生图。
    # 注意：4.5 不支持 SeedEdit 专属的 seed / guidance_scale / size='adaptive'，
    #      size 必须用 '1K' / '2K' / '4K' 或具体宽高（如 '2048x2048'）。
    payload = {
        'model': EDIT_MODEL,
        'prompt': merged,
        'image': image_url,
        'response_format': 'url',
        'size': '2K',
        'watermark': False,
    }
    data = _http_post_json(IMAGES_URL, payload, timeout=300)
    img_url = data['data'][0]['url']
    _download(img_url, save_path)
    return save_path


# ─────────────────────────────────────────────────────────────────
# ③ 生图
# ─────────────────────────────────────────────────────────────────
def generate_image(prompt: str, size: str = '2K', save_path: str = None) -> str:
    """文生图。返回保存的本地路径。"""
    if save_path is None:
        save_path = os.path.join(OUTPUT_DIR, f'generated_{int(time.time())}.png')
    payload = {
        'model': GEN_MODEL,
        'prompt': prompt,
        'size': size,
        'response_format': 'url',
        'watermark': False,
    }
    data = _http_post_json(IMAGES_URL, payload, timeout=300)
    img_url = data['data'][0]['url']
    _download(img_url, save_path)
    return save_path


# ─────────────────────────────────────────────────────────────────
# ④ 识菜（豆包 VLM，输出 dish_name + search_keywords）
# ─────────────────────────────────────────────────────────────────
def identify_dish(image: str) -> dict:
    """识菜：返回 {dish_name, category, search_keywords, confidence}。"""
    image_url = image_to_url(image)
    inputs = _build_responses_input(
        sys_prompt="你是美食类目识别专家。",
        user_text=_load_prompt('identify_dish'),
        images=[image_url]
    )
    payload = {
        'model': VLM_MODEL,
        'input': inputs,
        # Responses API 没有外层的 parameters 字段，这些参数直接和 model 同级
        'temperature': 0.1,
        'max_output_tokens': 512,
    }
    data = _http_post_json(RESPONSES_URL, payload)
    
    try:
        raw = _extract_output_text(data)
        return json.loads(_strip_md_fence(raw))
    except Exception as e:
        log.error('identify_dish parse failed: %s', e)
        return {"dish_name": "未知", "category": "未知", "search_keywords": [], "confidence": 0.0}


# ─────────────────────────────────────────────────────────────────
# ⑤ 图片 OCR / 读图：把小红书笔记图里的攻略文字提出来
# ─────────────────────────────────────────────────────────────────
def extract_text_from_image(image: str) -> str:
    """对单张图做 OCR/读图。返回提取出的纯文本（无文字时返回空串）。"""
    image_url = image_to_url(image)
    inputs = _build_responses_input(
        sys_prompt="你是图像文字提取助手。",
        user_text=_load_prompt('ocr'),
        images=[image_url]
    )
    payload = {
        'model': VLM_MODEL,
        'input': inputs,
        'temperature': 0.1,
        'max_output_tokens': 2048,
    }
    data = _http_post_json(RESPONSES_URL, payload)
    try:
        return _extract_output_text(data)
    except:
        return ""


# ─────────────────────────────────────────────────────────────────
# ⑥ 通用 VLM 图像分析 (vlm_analyze_image)
# ─────────────────────────────────────────────────────────────────
import typing


def _http_post_multipart(url: str, fields: dict, file_field: str = None,
                         file_path: str = None, timeout: int = 600) -> dict:
    """以 multipart/form-data 上传（Files API 用）。
    fields: 普通表单字段（含 'preprocess_configs[video][fps]' 这类带方括号的 key）。
    file_field/file_path: 若提供则附带一个二进制文件。
    使用 requests 流式读取文件，避免把最高 512MB 的视频整体复制进内存。
    """
    _require_api_key()
    log.info('POST(multipart) %s fields=%s file=%s', url, list(fields.keys()), file_path or '-')
    _t0 = time.time()
    file_handle = None
    try:
        multipart_fields = {k: str(v) for k, v in fields.items()}
        if file_field and file_path:
            fname = os.path.basename(file_path)
            mime = mimetypes.guess_type(fname)[0] or 'application/octet-stream'
            file_handle = open(file_path, 'rb')
            multipart_fields[file_field] = (fname, file_handle, mime)
        encoder = MultipartEncoder(fields=multipart_fields)
        resp = requests.post(
            url,
            headers={
                'Authorization': f'Bearer {API_KEY}',
                'Content-Type': encoder.content_type,
            },
            data=encoder,
            timeout=timeout,
        )
        if not resp.ok:
            log.error('POST(multipart) %s 失败 HTTP %s (%.1fs): %s',
                      url, resp.status_code, time.time() - _t0, resp.text[:500])
            raise RuntimeError(f'HTTP {resp.status_code} @ {url}: {resp.text}')
        data = resp.json()
    except requests.RequestException as e:
        log.error('POST(multipart) %s 异常 (%.1fs): %s', url, time.time() - _t0, e)
        raise RuntimeError(f'Files API request failed @ {url}: {e}') from e
    finally:
        if file_handle is not None:
            file_handle.close()
    log.info('POST(multipart) %s 成功 (%.1fs)', url, time.time() - _t0)
    return data


def _http_get_json(url: str, timeout: int = 60) -> dict:
    _require_api_key()
    req = urllib.request.Request(
        url, headers={'Authorization': f'Bearer {API_KEY}'}, method='GET')
    resp = urllib.request.urlopen(req, timeout=timeout).read().decode('utf-8')
    return json.loads(resp)


def delete_uploaded_file(file_id: str) -> bool:
    """删除 Files API 上传的文件，释放托管空间（总容量有上限，用完即删）。
    失败只告警不抛错（文件默认 7 天也会自动过期）。"""
    if not file_id or not str(file_id).startswith('file-'):
        return False
    _require_api_key()
    try:
        req = urllib.request.Request(
            f'{FILES_URL}/{file_id}',
            headers={'Authorization': f'Bearer {API_KEY}'}, method='DELETE')
        urllib.request.urlopen(req, timeout=30).read()
        log.info('已删除 Files API 文件，释放托管空间: %s', file_id)
        return True
    except Exception as e:
        log.warning('删除 Files API 文件失败(忽略，将随 7 天到期自动清理): %s -> %s', file_id, e)
        return False


def upload_video_for_understanding(video: str, fps: float = None,
                                   wait_active: bool = True, poll_timeout: int = 1200) -> str:
    """把整段视频交给 Files API（最大 512MB），返回可在 Responses API 引用的 File ID。
    - video: 本地路径 或 公网 http(s) URL。
    - fps: 抽帧采样率（越低越省 token），默认取 VIDEO_SAMPLE_FPS(1.0)，长视频建议 0.3。
    - 本地文件超过 512MB 时，先用 moviepy 压成审查代理再上传（代理上传后即删）。
    - 上传后文件需异步预处理；wait_active=True 时轮询直到 status=active 再返回。
    返回 file_id 字符串；失败抛 RuntimeError。
    """
    if fps is None:
        fps = VIDEO_SAMPLE_FPS
    fields = {
        'purpose': 'user_data',
        'preprocess_configs[video][fps]': fps,
    }
    is_url = isinstance(video, str) and video.startswith(('http://', 'https://'))
    proxy_to_cleanup = None
    if is_url:
        fields['url'] = video
        log.info('Files API 上传视频(URL方式) fps=%s: %s', fps, video[:80])
        data = _http_post_multipart(FILES_URL, fields, timeout=VIDEO_UNDERSTAND_TIMEOUT)
    else:
        path = os.path.expanduser(video)
        if not os.path.exists(path):
            raise FileNotFoundError(f'视频不存在: {path}')
        # 超过 Files API 512MB 上限 → 先压缩成审查代理再上传
        prep = prepare_video_for_vlm(path)
        if prep.get('prepared'):
            proxy_to_cleanup = prep['path']
            path = prep['path']
            log.info('本地视频超上限，改用压缩代理上传: %s (%.1fMB)', path, prep.get('proxy_size_mb', -1))
        try:
            log.info('Files API 上传视频(本地文件) fps=%s: %s', fps, path)
            data = _http_post_multipart(FILES_URL, fields, file_field='file',
                                        file_path=path, timeout=VIDEO_UNDERSTAND_TIMEOUT)
        finally:
            # 代理文件已进入上传流，无论成败都清理，避免 temp 膨胀
            if proxy_to_cleanup:
                try:
                    os.remove(proxy_to_cleanup)
                    log.info('已删除审查代理文件: %s', proxy_to_cleanup)
                except Exception as e:
                    log.warning('删除审查代理失败(忽略): %s -> %s', proxy_to_cleanup, e)

    file_id = data.get('id')
    if not file_id:
        raise RuntimeError(f'Files API 上传未返回 file id: {json.dumps(data, ensure_ascii=False)[:300]}')
    status = data.get('status', '')
    log.info('Files API 上传完成 file_id=%s status=%s', file_id, status)

    try:
        if wait_active and status not in ('active', ''):
            _t0 = time.time()
            while time.time() - _t0 < poll_timeout:
                time.sleep(3)
                try:
                    info = _http_get_json(f'{FILES_URL}/{file_id}')
                except Exception as e:
                    log.warning('轮询文件状态失败(忽略继续): %s', e)
                    continue
                status = info.get('status', '')
                log.info('Files API 文件 %s 状态=%s (%.0fs)', file_id, status, time.time() - _t0)
                if status == 'active':
                    break
                if status in ('failed', 'error', 'expired'):
                    raise RuntimeError(f'Files API 文件预处理失败 status={status}: '
                                       f'{json.dumps(info, ensure_ascii=False)[:300]}')
            else:
                raise RuntimeError(f'Files API 文件 {file_id} 在 {poll_timeout}s 内未变 active(当前 {status})')
    except Exception:
        delete_uploaded_file(file_id)
        raise
    return file_id


def _prepare_one_video(v: str):
    """把单条视频准备成可供模型引用的 file_id（或降级 video_url）。
    线程安全：只用局部变量 + 各自的网络请求，供并发上传调用。
    返回 dict：{file_id, video_url, uploaded_fid, temp_file, error}
    （uploaded_fid 表示本函数上传、调用方需负责删除；temp_file 同理需删）。
    """
    out = {'file_id': None, 'video_url': None, 'uploaded_fid': None,
           'temp_file': None, 'error': None}
    # 外部传入的 file_id 不归本函数管，不删。
    if isinstance(v, str) and v.startswith('file-'):
        out['file_id'] = v
        return out
    original_v = v
    v = resolve_video_page_url(v) if isinstance(v, str) else v
    if _is_video_page_url(original_v) and v == original_v:
        out['error'] = f'未能从页面 URL 解析真实视频流: {original_v}'
        log.warning('拒绝把视频页面 HTML URL 当视频传给模型: %s', original_v)
        return out
    try:
        file_id = upload_video_for_understanding(v)
        out['file_id'] = file_id
        out['uploaded_fid'] = file_id
        log.info('视频已上传 Files API，整段理解 file_id=%s', file_id)
        return out
    except Exception as e:
        log.warning('Files API 上传视频失败，尝试本地下载后重新上传: %s', e)
        try:
            if isinstance(v, str) and v.startswith(('http://', 'https://')) and _is_direct_video_url(v):
                suffix = '.m3u8' if '.m3u8' in v.lower() else '.mp4'
                local_video = download_video_to_temp(v, suffix=suffix)
                out['temp_file'] = local_video
                file_id = upload_video_for_understanding(local_video)
                out['file_id'] = file_id
                out['uploaded_fid'] = file_id
                log.info('视频下载后已上传 Files API，整段理解 file_id=%s', file_id)
                return out
        except Exception as e2:
            log.warning('本地下载/上传视频失败: %s', e2)
        # 只有真实视频 URL 才允许最后降级为 video_url 直传；页面 URL 不再降级，避免 text/html 再次报错。
        if (isinstance(v, str) and v.startswith(('https://', 'http://'))
                and urllib.parse.urlsplit(v).netloc and _is_direct_video_url(v)):
            log.warning('降级为 video_url 直传真实视频流: %s', v[:120])
            out['video_url'] = v
        else:
            out['error'] = f'视频预处理失败: {original_v} -> {e}'
        return out


# 视频上传并发度：多条视频"上传+预处理等待"彼此独立，可并行跑掉等待期。
# 想调只改 mykey.video_upload_concurrency。
_VIDEO_UPLOAD_CONCURRENCY = int(getattr(mykey, 'video_upload_concurrency', 4))


def _is_video_media(media: str) -> bool:
    if not isinstance(media, str):
        return False
    if media.startswith('file-') or _is_video_page_url(media) or _is_direct_video_url(media):
        return True
    path = urllib.parse.urlparse(media).path if media.startswith(('http://', 'https://')) else media
    ext = os.path.splitext(path.lower())[1]
    if ext in ('.mp4', '.m3u8', '.mov', '.m4v', '.webm', '.avi', '.mkv'):
        return True
    mime = mimetypes.guess_type(path)[0] or ''
    return mime.startswith('video/')


def analyze_image(images: typing.Union[str, list], prompt: str) -> str:
    """通用图片/视频分析：将一张或多张图片、视频和自定义 Prompt 喂给 VLM，返回分析结果。
    视频走 Files API 上传整段（最大 512MB，按 VIDEO_SAMPLE_FPS 抽帧理解全片），
    而非只看几张关键帧——这样模型能真正"看完整段视频"。
    """
    if isinstance(images, str):
        images = [images]

    imgs_list = []
    prep_errors = []
    raw_vids = []
    for media in images:
        if not media or media.startswith('<skip'):
            continue
        try:
            if _is_video_media(media):
                raw_vids.append(media)
            else:
                imgs_list.append(image_to_url(media))
        except Exception as e:
            prep_errors.append(f'媒体准备失败: {e}')
            log.warning('analyze_image skipped a media due to: %s', e)

    # 视频：优先用 Files API 上传整段，拿到 file_id 让模型看完整片；
    # 上传失败则降级为直接传 video_url（受 50MB 限制，仅兜底）。
    # ⚡ 并发准备：每条视频的"上传+预处理等待(status=active)"彼此独立，
    # 串行会白等前一条预处理完才开始下一条上传；这里用线程池并行跑掉等待期，
    # 结果按原始顺序回收，保证喂给模型的视频顺序稳定。
    vids_list = []
    uploaded_here = []  # 本次调用上传的 file_id，用完即删，释放托管空间（总容量有上限）
    temp_video_files = []  # 本次调用下载的临时视频，用完即删
    if raw_vids:
        max_workers = max(1, min(_VIDEO_UPLOAD_CONCURRENCY, len(raw_vids)))
        results = [None] * len(raw_vids)
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            future_to_idx = {ex.submit(_prepare_one_video, v): i for i, v in enumerate(raw_vids)}
            for fut in as_completed(future_to_idx):
                idx = future_to_idx[fut]
                try:
                    results[idx] = fut.result()
                except Exception as e:
                    results[idx] = {'file_id': None, 'video_url': None, 'uploaded_fid': None,
                                    'temp_file': None, 'error': f'视频准备异常: {e}'}
        # 按原始顺序回收结果
        for r in results:
            if not r:
                continue
            if r.get('temp_file'):
                temp_video_files.append(r['temp_file'])
            if r.get('uploaded_fid'):
                uploaded_here.append(r['uploaded_fid'])
            if r.get('file_id'):
                vids_list.append(r['file_id'])
            elif r.get('video_url'):
                vids_list.append(r['video_url'])
            elif r.get('error'):
                prep_errors.append(r['error'])

    if not imgs_list and not vids_list:
        for p in temp_video_files:
            try:
                os.remove(p)
                log.info('已删除本地临时视频: %s', p)
            except Exception as e:
                log.warning('删除本地临时视频失败(忽略): %s -> %s', p, e)
        media_kind = '视频' if raw_vids else '图片'
        return f'[Error] {media_kind}准备失败，未拿到可供模型理解的有效素材。' + (
            '\n' + '\n'.join(prep_errors) if prep_errors else ''
        )

    inputs = _build_responses_input(
        sys_prompt="你是一个全模态新媒体运营助手。请仔细阅读用户提供的图片或视频，并按要求回答。",
        user_text=prompt,
        images=imgs_list,
        videos=vids_list
    )

    payload = {
        'model': VLM_MODEL,
        'input': inputs,
        'max_output_tokens': VLM_MAX_OUTPUT_TOKENS,
    }

    has_video = bool(vids_list)
    # 视频整片理解单次推理很慢，给足超时；纯图片走默认超时即可。
    req_timeout = VIDEO_UNDERSTAND_TIMEOUT if has_video else 180
    try:
        data = _http_post_json(RESPONSES_URL, payload, timeout=req_timeout)
    except RuntimeError as e:
        return f"[Error] API 请求失败: {e}"
    finally:
        # 无论成败，清理本次上传的托管文件，避免占满总容量
        for fid in uploaded_here:
            delete_uploaded_file(fid)
        # 清理本次下载的本地临时视频，避免 temp 膨胀
        for p in temp_video_files:
            try:
                os.remove(p)
                log.info('已删除本地临时视频: %s', p)
            except Exception as e:
                log.warning('删除本地临时视频失败(忽略): %s -> %s', p, e)

    try:
        # 使用稳健的提取器（兼容 Responses API 的 reasoning+message 混合 output 结构）
        res_text = _extract_output_text(data)
        if not res_text:
            res_text = f"[Error] 模型返回空内容，原始返回: {json.dumps(data, ensure_ascii=False)[:500]}"
    except Exception as e:
        res_text = f"[Error] failed to parse response: {e}, raw: {data}"

    # 输出被长度截断防御：模型未答完就 return，上层可能误当"通过/完整结论"。
    # 保留已生成的半截，并在最前面显著标注"未完成"，逼上层拆分重问而非采信。
    if isinstance(data, dict) and data.get('status') == 'incomplete':
        reason = (data.get('incomplete_details') or {}).get('reason', '')
        if reason == 'length' and not res_text.startswith('[Error]'):
            res_text = ('【⚠️未完成·不可作为最终/通过结论】VLM 输出达到长度上限被截断，'
                        '以下仅为半截结果；请缩小提问范围或分镜/分段拆开重问后再下结论。\n\n' + res_text)

    if prep_errors and not res_text.startswith('[Error]'):
        res_text = (
            '【⚠️未完成·不可作为全量结论】部分媒体未能完成预处理，以下分析仅覆盖成功载入的媒体。\n'
            + '\n'.join(f'- {error}' for error in prep_errors)
            + '\n\n'
            + res_text
        )

    return res_text
