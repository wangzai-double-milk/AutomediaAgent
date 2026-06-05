import json
import os
import sys

print("=== 1. Checking Tools (assets/tools_schema.json) ===")
with open('assets/tools_schema.json', 'r', encoding='utf-8') as f:
    schemas = json.load(f)
tool_names = [s['function']['name'] for s in schemas]
print(f"Total tools loaded: {len(tool_names)}")
expected_tools = ['xhs_image_review', 'create_skill', 'update_user_preference', 'code_run']
for t in expected_tools:
    if t in tool_names:
        print(f"  [OK] Found {t}")
    else:
        print(f"  [ERROR] Missing {t}")

print("\n=== 2. Checking Skills (skills/ directory) ===")
skills_dir = 'skills'
if os.path.exists(skills_dir):
    skill_types = ['hard_skills', 'soft_skills']
    for stype in skill_types:
        stype_dir = os.path.join(skills_dir, stype)
        if os.path.exists(stype_dir):
            skills = [d for d in os.listdir(stype_dir) if os.path.isdir(os.path.join(stype_dir, d))]
            print(f"Found {len(skills)} skills in {stype}.")
            for skill in skills:
                md_path = os.path.join(stype_dir, skill, 'SKILL.md')
                if os.path.exists(md_path):
                    with open(md_path, 'r', encoding='utf-8') as f:
                        content = f.read(100)
                        if content.startswith('---'):
                            print(f"  [OK] {skill} has SKILL.md with Frontmatter")
                        else:
                            print(f"  [WARNING] {skill} has SKILL.md but NO Frontmatter")
                else:
                    print(f"  [ERROR] {skill} missing SKILL.md")
        else:
            print(f"  [WARNING] Directory {stype_dir} does not exist.")

print("\n=== 3. Checking Autonomous Trigger (reflect/autonomous.py) ===")
with open('reflect/autonomous.py', 'r', encoding='utf-8') as f:
    content = f.read()
    if '后台自主模式（沉浸式刷小红书状态）触发' in content:
        print("  [OK] Autonomous trigger has the correct new prompt.")
    else:
        print("  [ERROR] Autonomous trigger might be wrong.")

print("\n=== 4. Checking DynamicToolManager ===")
if os.path.exists('plugins/dynamic_tool_manager.py'):
    print("  [OK] plugins/dynamic_tool_manager.py exists.")
else:
    print("  [ERROR] Missing dynamic_tool_manager.py")
    
