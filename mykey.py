# ══════════════════════════════════════════════════════════════════════════════
#  mykey.py — 配置适配器（请勿在此填写密钥！）
# ──────────────────────────────────────────────────────────────────────────────
#  普通用户无需修改本文件。你只要做一件事：
#      1. 复制 config.example.json 为 config.json
#      2. 在 config.json 里填入你的火山引擎 API Key（一把 Key 全套通用）
#
#  本文件只负责把 config.json 里的纯数据，翻译成 GenericAgent 框架认识的
#  session 配置变量（native_oai_config / mixin_config / volc_models）。
#  框架靠"变量名是否含 native+oai / config / api"来识别，所以这些变量名不能改。
# ══════════════════════════════════════════════════════════════════════════════

import os as _os
import json as _json

_THIS_DIR = _os.path.dirname(_os.path.abspath(__file__))

# 内置默认值（config.json 缺字段时回退，保证不崩）
_DEFAULTS = {
    'volc_apibase': 'https://ark.cn-beijing.volces.com/api/v3',
    'models': {
        'llm': 'doubao-seed-2-0-pro-260215',
        'vlm': 'doubao-seed-2-0-pro-260215',
        'edit': 'doubao-seedream-4-5-251128',
        'gen': 'doubao-seedream-4-5-251128',
    },
}


def _load_user_config():
    """读取同目录下的 config.json；不存在则给出清晰的指引。"""
    path = _os.path.join(_THIS_DIR, 'config.json')
    if not _os.path.exists(path):
        raise FileNotFoundError(
            '[配置缺失] 未找到 config.json。请复制 config.example.json 为 config.json，'
            '并在其中填入你的火山引擎 API Key。'
        )
    with open(path, encoding='utf-8') as f:
        return _json.load(f)


_cfg = _load_user_config()

_apikey = _cfg.get('volc_apikey', '').strip()
_apibase = _cfg.get('volc_apibase', '').strip() or _DEFAULTS['volc_apibase']
_models = {**_DEFAULTS['models'], **(_cfg.get('models') or {})}

if not _apikey or _apikey.startswith('ark-在这里'):
    raise ValueError('[配置缺失] 请在 config.json 的 "volc_apikey" 字段填入真实的火山引擎 API Key。')


# ── 框架识别的 LLM session 配置（变量名含 native+oai → NativeOAISession）──
# 只保留必需项：name / apikey / apibase / model。
# 其余调优参数（api_mode / max_retries / 超时 / context_win 等）一律不写死，
# 交给框架默认值与 agent 运行时自行决定。
native_oai_config = {
    'name': 'doubao',
    'apikey': _apikey,
    'apibase': _apibase,
    'model': _models['llm'],
}

# mixin_config.llm_nos 不是调优参数，而是结构性配置：
# 告诉框架"用哪个 session"（必须匹配上面的 name='doubao'），不可省略。
mixin_config = {
    'llm_nos': ['doubao'],
}

# ── 火山引擎多模型名（审图 / 改图 / 生图，供 tools/image.py 读取）──
volc_models = {
    'vlm': _models['vlm'],
    'edit': _models['edit'],
    'gen': _models['gen'],
}
