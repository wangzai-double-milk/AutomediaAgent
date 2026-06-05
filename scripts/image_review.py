"""
小红书美食博主 - 单次严格档审图 CLI（薄壳）。

业务编排（pipeline / 找爆款 / 多轮闭环）请用 GA 对话模式 + skills/hard_skills/xhs_main/SKILL.md。
本脚本只做"单次 review"调试。

用法:
    python scripts/image_review.py <图片路径或URL> [菜品提示]
    python scripts/image_review.py <图片路径> --refs <爆款图1> <爆款图2> ...

例:
    python scripts/image_review.py ~/Desktop/foo.jpg
    python scripts/image_review.py ~/Desktop/foo.jpg "日式拉面"
    python scripts/image_review.py ~/Desktop/foo.jpg "日式拉面" --refs ./viral/r1.jpg ./viral/r2.jpg
"""

import os
import sys
import json
import time

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, PROJECT_ROOT)

from tools import image as xhs_image  # noqa: E402


_VERDICT_COLOR = {'PASS': '\033[92m', 'REWORK': '\033[93m', 'REPHOTO': '\033[91m'}
_RESET = '\033[0m'


def _print_review(r: dict):
    v = r.get('verdict', '?')
    color = _VERDICT_COLOR.get(v, '')
    print('\n' + '═' * 64)
    print(f'  综合分 {r.get("score")}   判定 {color}{v}{_RESET}')
    print(f'  摘要: {r.get("summary", "")}')
    print('═' * 64)

    dims = r.get('dimensions') or {}
    if dims:
        print('\n[6 维评分]')
        for k, val in dims.items():
            print(f'  · {k:<18} {val}/10')

    for label, key in [('爆款共性', 'viral_patterns'),
                       ('与爆款差距', 'gaps_vs_viral'),
                       ('原图硬伤', 'issues'),
                       ('修图话术 (REWORK)', 'edit_prompts'),
                       ('重拍清单 (REPHOTO)', 'additional_shots_needed'),
                       ('标题方向', 'title_hints')]:
        items = r.get(key) or []
        if items:
            print(f'\n[{label}]')
            for i, x in enumerate(items, 1):
                print(f'  {i}. {x}')

    roast = r.get('roast') or ''
    if roast:
        print(f'\n💬 {roast}')


def _parse_args(argv):
    if len(argv) < 2:
        print(__doc__)
        sys.exit(1)
    image = argv[1]
    rest = argv[2:]
    refs = []
    hint_parts = []
    if '--refs' in rest:
        i = rest.index('--refs')
        hint_parts = rest[:i]
        refs = rest[i + 1:]
    else:
        hint_parts = rest
    return image, ' '.join(hint_parts), refs


def main():
    image, hint, refs = _parse_args(sys.argv)

    print(f'[*] 输入图片: {image}')
    if hint:
        print(f'[*] 补充提示: {hint}')
    if refs:
        print(f'[*] 爆款参考图: {len(refs)} 张')
    print(f'[*] 审图模型: {xhs_image.VLM_MODEL}')

    result = xhs_image.review_image(
        image, hint=hint, reference_images=refs or None,
    )
    _print_review(result)

    log_path = os.path.join(xhs_image.OUTPUT_DIR, f'review_{int(time.time())}.json')
    with open(log_path, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2, default=str)
    print(f'\n完整日志: {log_path}')


if __name__ == '__main__':
    main()
