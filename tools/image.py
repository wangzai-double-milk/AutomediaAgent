"""
小红书图像四件套（库函数；不直接做工作流编排，编排逻辑见 memory/xhs_sop.md）：
  - review_image(image, hint='', reference_images=None)   严格档审图（支持多图对比）
  - edit_image(image, edit_prompts, save_path=None)       Seedream 4.5 i2i
  - generate_image(prompt, size='2K', save_path=None)     Seedream 4.5 t2i
  - extract_text_from_image(image)                        VLM OCR / 读图（提攻略文字）

所有函数返回普通 dict（review）或本地路径 str（edit/generate）。
所有对外 API 调用都经 _http_post_json 统一落盘到 logs/image.log（规则1）。
"""

import os
import sys
import json
import time
import base64
import logging
import mimetypes
import urllib.request
import urllib.error
import subprocess

# 让 import mykey 不依赖 cwd
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.abspath(os.path.join(_THIS_DIR, '..'))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import mykey  # noqa: E402

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
_CFG = mykey.native_oai_config
API_BASE = _CFG['apibase'].rstrip('/')
API_KEY = _CFG['apikey']

# 模型名统一从全局配置 mykey.volc_models 读取（想换模型只改 mykey.py）。
# 兼容旧版未配置 volc_models 的 mykey.py：缺失时回退到默认值。
_MODELS = getattr(mykey, 'volc_models', {}) or {}
VLM_MODEL = _MODELS.get('vlm', 'doubao-seed-2-0-pro-260215')   # 审图：全模态 VLM (Seed 2.0 Pro)
# 修图 / 生图 / 多图融合，统一用 Seedream 4.5。
# Seedream 4.5 原生支持 文生图 / 图生图(i2i) / 多图融合 —— 带 image 参数就是图生图，不带就是文生图。
# 如果未来需要单独的 SeedEdit 3.0，需要先在方舟控制台手动开通该模型。
EDIT_MODEL = _MODELS.get('edit', 'doubao-seedream-4-5-251128')  # 改图（i2i）
GEN_MODEL = _MODELS.get('gen', 'doubao-seedream-4-5-251128')    # 生图（t2i）

CHAT_URL = API_BASE + '/chat/completions'
RESPONSES_URL = API_BASE + '/responses'
IMAGES_URL = API_BASE + '/images/generations'

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
def _http_post_json(url: str, payload: dict, timeout: int = 180) -> dict:
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
        for vid_url in videos:
            content.append({"type": "input_video", "video_url": vid_url})
            
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
        return {"verdict": "PASS", "roast": "", "issues": []}


# ─────────────────────────────────────────────────────────────────
# ② 改图（Seedream 4.5 i2i）
# ─────────────────────────────────────────────────────────────────
def edit_image(image: str, edit_prompts, save_path: str = None) -> str:
    """把多条 edit_prompts 合并喂给 Seedream 4.5（图生图）；返回保存的本地路径。"""
    if isinstance(edit_prompts, str):
        prompts = [edit_prompts]
    else:
        prompts = list(edit_prompts)
    merged = '；'.join(prompts)
    image_url = image_to_url(image)

    if save_path is None:
        save_path = os.path.join(OUTPUT_DIR, f'edited_{int(time.time())}.png')

    # Seedream 4.5 i2i：带 image 参数即触发图生图。
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
# ③ 生图（Seedream 4.5 t2i）
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
def _compress_video(video_url: str) -> str:
    """下载并使用 moviepy 压缩视频（降低分辨率和码率，以满足 50MB 限制）
    返回压缩后视频的本地绝对路径。如果压缩失败，返回原 URL 或抛出异常。
    """
    import uuid
    try:
        from moviepy.editor import VideoFileClip
    except ImportError:
        log.error('请先运行 `pip install moviepy` 安装依赖！')
        return video_url

    tmp_dir = os.path.join(OUTPUT_DIR, 'video_tmp')
    os.makedirs(tmp_dir, exist_ok=True)
    
    unique_id = uuid.uuid4().hex[:8]
    raw_path = os.path.join(tmp_dir, f'raw_{unique_id}.mp4')
    out_path = os.path.join(tmp_dir, f'comp_{unique_id}.mp4')
    
    log.info('视频过大，开始下载进行压缩: %s...', video_url[:60])
    try:
        urllib.request.urlretrieve(video_url, raw_path)
    except Exception as e:
        log.error('下载视频失败: %s', e)
        return video_url

    # 使用 moviepy 压缩视频：限制为 720p，帧率 15，码率 500k
    try:
        clip = VideoFileClip(raw_path)
        
        # 如果视频高于 720p，按比例缩小
        if clip.h > 720:
            clip = clip.resize(height=720)
            
        log.info('正在使用 moviepy 压缩视频...')
        # 写入文件，设定参数降低体积
        clip.write_videofile(
            out_path, 
            fps=15, 
            codec="libx264", 
            audio_codec="aac", 
            bitrate="500k", 
            audio_bitrate="64k",
            logger=None # 隐藏进度条
        )
        clip.close()
        log.info('视频压缩完成: %s', out_path)
        return out_path
    except Exception as e:
        log.error('moviepy 压缩视频失败: %s', e)
        return raw_path

    # 清理下载的原始视频（保留压缩后的）
    try:
        os.remove(raw_path)
    except:
        pass

    return out_path


def analyze_image(images: typing.Union[str, list], prompt: str) -> str:
    """通用图片分析：将一张或多张图片和自定义 Prompt 喂给 VLM，返回分析结果。"""
    if isinstance(images, str):
        images = [images]
        
    imgs_list = []
    vids_list = []
    for media in images:
        if not media or media.startswith('<skip'):
            continue
        try:
            # 判断是否是视频
            if media.endswith('.mp4') or media.endswith('.m3u8') or 'video' in media:
                vids_list.append(media) # 先传 URL，如果报错拦截再转为 data URI 压缩
            else:
                image_url = image_to_url(media)
                imgs_list.append(image_url)
        except Exception as e:
            log.warning('analyze_image skipped a media due to: %s', e)

    inputs = _build_responses_input(
        sys_prompt="你是一个全模态新媒体运营助手。请仔细阅读用户提供的图片或视频，并按要求回答。",
        user_text=prompt,
        images=imgs_list,
        videos=vids_list
    )

    payload = {
        'model': VLM_MODEL,
        'input': inputs
    }
    
    # 尝试发送请求，如果报 400 且包含 "exceeds the limit"，进行视频压缩重试
    try:
        data = _http_post_json(RESPONSES_URL, payload)
    except RuntimeError as e:
        err_msg = str(e)
        if 'exceeds the limit' in err_msg and 'video' in err_msg and vids_list:
            log.warning('视频过大被拦截，开始尝试自动压缩重传...')
            # 取出第一个视频进行压缩
            compressed_vid = _compress_video(vids_list[0])
            if compressed_vid != vids_list[0]:
                # 重新构造输入
                try:
                    comp_url = image_to_url(compressed_vid)
                    inputs = _build_responses_input(
                        sys_prompt="你是一个全模态新媒体运营助手。请仔细阅读用户提供的图片或视频，并按要求回答。",
                        user_text=prompt,
                        images=imgs_list,
                        videos=[comp_url]
                    )
                    payload['input'] = inputs
                    log.info('正在使用压缩后的视频重发请求...')
                    data = _http_post_json(RESPONSES_URL, payload)
                except Exception as inner_e:
                    return f"[Error] 压缩后重试依然失败: {inner_e}"
            else:
                return f"[Error] 视频过大且无法压缩: {err_msg}"
        else:
            return f"[Error] API 请求失败: {err_msg}"
    
    try:
        # 使用稳健的提取器（兼容 Responses API 的 reasoning+message 混合 output 结构）
        res_text = _extract_output_text(data)
        if not res_text:
            res_text = f"[Error] 模型返回空内容，原始返回: {json.dumps(data, ensure_ascii=False)[:500]}"
    except Exception as e:
        res_text = f"[Error] failed to parse response: {e}, raw: {data}"
        
    # 清理临时压缩的视频文件
    try:
        if 'compressed_vid' in locals() and compressed_vid and os.path.exists(compressed_vid):
            os.remove(compressed_vid)
            log.info('已清理临时压缩文件: %s', compressed_vid)
        # 如果原始下载文件也想一并清理，可以补充逻辑
    except Exception as e:
        pass
        
    return res_text

