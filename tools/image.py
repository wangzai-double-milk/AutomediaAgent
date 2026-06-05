"""
小红书图像四件套（库函数；不直接做工作流编排，编排逻辑见 memory/xhs_sop.md）：
  - review_image(image, hint='', reference_images=None)   严格档审图（支持多图对比）
  - edit_image(image, edit_prompts, save_path=None)       Seedream 4.5 i2i
  - generate_image(prompt, size='2K', save_path=None)     Seedream 4.5 t2i
  - extract_text_from_image(image)                        VLM OCR / 读图（提攻略文字）

所有函数返回普通 dict（review）或本地路径 str（edit/generate）。
没有 print，便于 GA 工具调用时只取返回值。
"""

import os
import sys
import json
import time
import base64
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
# 配置
# ─────────────────────────────────────────────────────────────────
_CFG = mykey.native_oai_config
API_BASE = _CFG['apibase'].rstrip('/')
API_KEY = _CFG['apikey']

VLM_MODEL = 'doubao-seed-2-0-pro-260215'                              # 审图：全模态 VLM (Seed 2.0 Pro)
# 修图 / 生图 / 多图融合，统一用 Seedream 4.5。
# Seedream 4.5 原生支持 文生图 / 图生图(i2i) / 多图融合 —— 带 image 参数就是图生图，不带就是文生图。
# 如果未来需要单独的 SeedEdit 3.0，需要先在方舟控制台手动开通该模型。
EDIT_MODEL = 'doubao-seedream-4-5-251128'              # 改图（i2i）
GEN_MODEL = 'doubao-seedream-4-5-251128'               # 生图（t2i）

CHAT_URL = API_BASE + '/chat/completions'
RESPONSES_URL = API_BASE + '/responses'
IMAGES_URL = API_BASE + '/images/generations'

OUTPUT_DIR = os.path.join(_PROJECT_ROOT, 'temp', 'image_review')
os.makedirs(OUTPUT_DIR, exist_ok=True)


# ─────────────────────────────────────────────────────────────────
# 严格档审核 prompt
# ─────────────────────────────────────────────────────────────────
SYSTEM_PROMPT_SOLO = """你是一位毒舌但专业的小红书美食内容评判官。说话直接、不留情面，但每句吐槽都准、能让博主立刻知道哪里烂。
你的任务：看用户这张图，做出三档判断 —— **能用 / 能 P 就 P / 必须重拍**。不输出任何评分数字。

判定原则：
- **PASS**：图本身没明显硬伤，可以直接发。
- **REWORK**：问题靠修图能救（曝光/色温/轻度构图裁剪/背景杂物消除/氛围增强如油光蒸汽饱和度）。
- **REPHOTO**：有"修图救不回"的硬伤（主菜失焦/严重模糊/构图根本错/主菜被严重遮挡/角度错/锅碗太小没气势）—— 必须重拍。

毒舌风格要求（PASS 不需要 roast，REWORK 和 REPHOTO 都必须带，且必须**真的损人**）：
- 输出 **3-5 句**，每句 20-40 字，要密集连击，不是一句敷衍的"光线偏暗哦~"。
- 必须**指名道姓地损**：损构图、损色调、损光线、损主菜，每条都要带攻击性的比喻。
- 可以用：嘲讽、反问、夸张比喻、地狱玩笑、网络梗。**绝对不要**"建议..."、"可以..."、"哦~"、"呢~"这种温柔语气。
- 句末不许带表情符号（emoji）和"哦/啦/呢/呀"。
- 风格示例（学会这个调性）：
  "这是菜还是车祸现场？薄荷把牛肉盖得比殡仪馆还彻底。"
  "锅小得像过家家玩具，气势？不存在的。"
  "你这光线，配上字节 logo 当背景板，活像食堂监控偷拍。"
  "色调灰得像我去年的工资条，谁看了想点开？"
  "这构图我闭着眼睛用脚拍都比你强。"
  "拍出来像隔夜剩饭，发出去要被取关的。"
- **核心要求**：损完之后用户必须记住"我哪里烂"。不许写出"建议调整"这种甲方话术。

edit_prompts（REWORK 必填）：
- 给图生图模型的可执行中文指令。包含两类：
  A. **画质类**：色调/光线/裁剪/去杂物。例："提亮 15%、色温调暖 200K、消除右上角阴影"。
  B. **封面文字类**（小红书爆款几乎都有，缺了就吃亏）：在合适位置叠加大字标题、价格角标、贴纸。
     例："在画面左上角加白底黑字的大标题'3 块钱拍出米其林'，手写体，带轻微阴影"、
         "在右下角加黄色圆形贴纸，写'人均 25'，红色描边"、
         "在主菜旁加箭头 + 手写字'灵魂在这'"。
- A、B 两类都要考虑：如果原图缺标题文字、缺角标、缺氛围贴纸，就主动补上。
- 不写"重新拍"、"换角度"这种修图做不到的事。
- 最多 4 条，按重要性排序（画质类优先，文字类紧跟）。

annotation_prompts（REPHOTO 必填）：
- 用 i2i 在原图上**画红色箭头 + 中文标注**做"示意图"教用户怎么拍。
- 例："在主菜上方画红色箭头，标注'加蒸汽'"、"在锅边画箭头标注'改用大铜锅'"。
- 最多 4 条。

只输出一个 JSON，不要 markdown、不要解释。

JSON 协议：
{
  "verdict": "PASS" | "REWORK" | "REPHOTO",
  "issues": [<最多 3 条最致命问题，每条 15 字内>],
  "edit_prompts": [<REWORK 必填，REPHOTO/PASS 留空数组>],
  "additional_shots_needed": [<REPHOTO 必填，重拍/补拍清单>],
  "annotation_prompts": [<REPHOTO 必填，画批注的 i2i 指令>],
  "roast": "<毒舌点评，REWORK/REPHOTO 必填，PASS 留空>",
  "title_hints": [<2-3 个小红书标题方向>],
  "summary": "<一句话总结 30 字内>"
}
"""

SYSTEM_PROMPT_WITH_REFS = """你是一位毒舌但专业的小红书美食内容评判官。**第 1 张是用户原图，后面 N 张是该品类的爆款封面**。
你的任务：拿原图和爆款比，做出三档判断 —— **能用 / 能 P 就 P / 必须重拍**。不输出任何评分数字。

判定原则：
- **PASS**：原图视觉品质对得起爆款门槛，可以直接发。
- **REWORK**：差距靠修图能补（曝光/色温/裁剪/背景杂物/氛围增强）。
- **REPHOTO**：差距属"重拍才能补"（主菜失焦/构图根本错/锅具太小没气势/角度错/主菜被严重遮挡）—— 必须重拍，**给参考图 + 画批注示意图**。

对比要求（必填）：
A. 总结爆款的**共性**（3-5 条）：色调？角度？主体占比？背景？氛围元素（油光/蒸汽/留白）？**封面文字**（带不带大字标题/价格角标/贴纸？什么调性？）？
B. 指出原图与爆款的**关键差距**（最多 3 条，按致命程度排序），每条标注 [修图可补] 或 [必须重拍]。
C. 只要任一差距是 [必须重拍]，verdict 立即降级 REPHOTO，不许判 REWORK。

毒舌风格（PASS 不需要 roast，REWORK 和 REPHOTO 都必须带，且必须**真的损人**）：
- 输出 **3-5 句**，每句 20-40 字，密集连击。
- 必须**对着爆款打脸**：句子里要带"看看人家"、"爆款人家"、"对比之下"等比较口吻，让用户感受到差距。
- 可以用：嘲讽、反问、夸张比喻、网络梗。**绝对不要**"建议..."、"可以..."、"哦~"、"呢~"。
- 句末不许带 emoji 和"哦/啦/呢/呀"。
- 风格示例：
  "看看人家爆款大铜锅气势全开，你这小锅活像过家家。"
  "爆款人家牛肉切片摆得整整齐齐，你这锅薄荷把主菜埋了，发悬疑剧呢？"
  "色调灰得像我工资条，对比爆款那个暖光油润，差出三个食欲值。"
  "构图被字节 logo 抢戏，活像食堂监控偷拍流出。"
  "你这要是发出去，评论区只会问'这是啥'。"
- **核心要求**：用爆款做参照系狠损原图，让用户立刻知道"我跟爆款差在哪"。不许甲方话术。

edit_prompts（REWORK 必填）：
- 给 i2i 的中文修图指令。包含两类：
  A. **画质类**：色调/光线/去杂物/构图微调。例："提亮 15%、色温调暖、消除背景 logo"。
  B. **封面文字类**（参考爆款是否带大字标题/角标/贴纸；缺就补）。
     例："在左上角加白底黑字大标题'灵魂铜锅 三人吃撑'，手写体"、
         "在右下角加红色圆角贴纸，写'人均 80'"、
         "在画面顶部加爆款常用的黄字'真的会回购'"。
- 标题文字内容要呼应 viral_patterns（爆款都怎么写就照那个调性写），不要假大空。
- 最多 4 条，按重要性排序（画质类优先，文字类紧跟）。

annotation_prompts（REPHOTO 必填）：
- 用 i2i 在**原图**上画红色箭头 + 中文标注做"示意图"教用户怎么拍。
- 例："在锅边画红色箭头，标注'换大铜锅'"、"在背景画箭头标注'去掉 logo'"、
  "在主菜上画箭头标注'露出牛肉别全盖薄荷'"。
- 最多 4 条。

只输出一个 JSON，不要 markdown、不要解释。

JSON 协议：
{
  "verdict": "PASS" | "REWORK" | "REPHOTO",
  "viral_patterns": [<爆款共性 3-5 条>],
  "gaps_vs_viral": [<原图 vs 爆款关键差距，每条标注 [修图可补]/[必须重拍]>],
  "issues": [<原图本身硬伤，每条 15 字内>],
  "edit_prompts": [<REWORK 必填>],
  "additional_shots_needed": [<REPHOTO 必填>],
  "annotation_prompts": [<REPHOTO 必填，画批注的 i2i 指令>],
  "roast": "<毒舌点评，REWORK/REPHOTO 必填，PASS 留空>",
  "title_hints": [<2-3 个标题方向>],
  "summary": "<一句话总结 30 字内>"
}
"""


# ─────────────────────────────────────────────────────────────────
# 工具函数
# ─────────────────────────────────────────────────────────────────
def _http_post_json(url: str, payload: dict, timeout: int = 180) -> dict:
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
        # 如果是视频过大等特殊情况，这里可以自定义异常或者把 body 带出
        raise RuntimeError(f'HTTP {e.code} @ {url}: {body}')
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
    - 单图模式：只传 image，按 SYSTEM_PROMPT_SOLO 评分。
    - 对比模式：附带 reference_images（同品类爆款封面），按 SYSTEM_PROMPT_WITH_REFS 评分，
      额外输出 viral_patterns / gaps_vs_viral / roast 字段。
    """
    image_url = image_to_url(image)

    if reference_images:
        sys_prompt = SYSTEM_PROMPT_WITH_REFS
        user_text = (
            f'第 1 张是【待审原图】，后面 {len(reference_images)} 张是【该品类目前的爆款封面】。\n'
            '请严格按系统提示做对比分析，并直接输出 JSON。'
        )
        if hint:
            user_text += f'\n补充信息：{hint}'
        
        ref_urls = [image_to_url(ref) for ref in reference_images]
        inputs = _build_responses_input(sys_prompt, user_text, images=[image_url] + ref_urls)
    else:
        sys_prompt = SYSTEM_PROMPT_SOLO
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
        print(f"[Error] review_image parse failed: {e}")
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
_IDENTIFY_PROMPT = """你是美食类目识别专家。看这张图，输出一个 JSON：
{
  "dish_name": "<最具体的菜品名，例：麻婆豆腐 / 提拉米苏 / 日式豚骨拉面>",
  "category": "<品类，例：川菜/西式甜品/日式拉面>",
  "search_keywords": [<2-3 个用于在小红书/抖音搜索同类爆款封面的关键词，按搜索热度优先>],
  "confidence": <0-1 浮点>
}
只输出 JSON，不要任何其他文本。"""


def identify_dish(image: str) -> dict:
    """识菜：返回 {dish_name, category, search_keywords, confidence}。"""
    image_url = image_to_url(image)
    inputs = _build_responses_input(
        sys_prompt="你是美食类目识别专家。",
        user_text=_IDENTIFY_PROMPT,
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
        print(f"[Error] identify_dish parse failed: {e}")
        return {"dish_name": "未知", "category": "未知", "search_keywords": [], "confidence": 0.0}


# ─────────────────────────────────────────────────────────────────
# ⑤ 图片 OCR / 读图：把小红书笔记图里的攻略文字提出来
# ─────────────────────────────────────────────────────────────────
_OCR_PROMPT = """你是图像文字提取助手。这张图来自小红书笔记，可能包含攻略文字、教程步骤、菜单、价格、配方、prompt 文本等。
任务：把图里所有可读的中文/英文/数字文字**完整原样**抄出来，按图中阅读顺序，**不要总结、不要改写、不要翻译**。
- 如果图里有标题/小标题/编号，保留结构（用换行分段）。
- 如果是无文字的纯食物/风景照，输出空字符串。
- 如果文字遮挡严重看不清，用 [模糊] 占位。
只输出文字内容本身，不要任何前缀（如"图片内容："）、不要 markdown 代码块。"""


def extract_text_from_image(image: str) -> str:
    """对单张图做 OCR/读图。返回提取出的纯文本（无文字时返回空串）。"""
    image_url = image_to_url(image)
    inputs = _build_responses_input(
        sys_prompt="你是图像文字提取助手。",
        user_text=_OCR_PROMPT,
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
        print("[Error] 请先运行 `pip install moviepy` 安装依赖！")
        return video_url

    tmp_dir = os.path.join(OUTPUT_DIR, 'video_tmp')
    os.makedirs(tmp_dir, exist_ok=True)
    
    unique_id = uuid.uuid4().hex[:8]
    raw_path = os.path.join(tmp_dir, f'raw_{unique_id}.mp4')
    out_path = os.path.join(tmp_dir, f'comp_{unique_id}.mp4')
    
    print(f"[Info] 视频过大，开始下载进行压缩: {video_url[:60]}...")
    try:
        urllib.request.urlretrieve(video_url, raw_path)
    except Exception as e:
        print(f"[Error] 下载视频失败: {e}")
        return video_url

    # 使用 moviepy 压缩视频：限制为 720p，帧率 15，码率 500k
    try:
        clip = VideoFileClip(raw_path)
        
        # 如果视频高于 720p，按比例缩小
        if clip.h > 720:
            clip = clip.resize(height=720)
            
        print(f"[Info] 正在使用 moviepy 压缩视频...")
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
        print(f"[Info] 视频压缩完成: {out_path}")
        return out_path
    except Exception as e:
        print(f"[Error] moviepy 压缩视频失败: {e}")
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
            print(f"[Warn] analyze_image skipped an media due to: {e}")

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
            print("[Warn] 视频过大被拦截，开始尝试自动压缩重传...")
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
                    print("[Info] 正在使用压缩后的视频重发请求...")
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
            print(f"[Info] 已清理临时压缩文件: {compressed_vid}")
        # 如果原始下载文件也想一并清理，可以补充逻辑
    except Exception as e:
        pass
        
    return res_text

