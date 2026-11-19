"""python -m automedia.configuration init|check|apply [--database ...]."""
import argparse

from .rules import DEFAULT_RULES, load_rules, model_config
from .character import atomic_json


def main():
    parser = argparse.ArgumentParser(description="初始化、校验或应用本机角色与规则")
    parser.add_argument("action", choices=("init", "check", "apply"))
    parser.add_argument("--database")
    args = parser.parse_args()
    from automedia.application.runtime import RuntimeStore
    from automedia.infrastructure.store import DomainError
    try:
        # check never constructs a RuntimeStore: no import/export side effects.
        if args.action == "check":
            from .rules import settings_path
            from .character import CharacterFile
            directory = settings_path(args.database)
            rules = load_rules(directory)
            for role in rules["models"]:
                model_config(role, rules)
            character = CharacterFile(directory)
            if character.path.exists():
                value = character.document()
                RuntimeStore.validate_character_profile(value["profile"])
            print("角色与规则格式有效；check不更改运行资料。")
            return
        store = RuntimeStore(args.database)
        path = store.settings_dir / "rules.json"
        if not path.exists():
            atomic_json(path, DEFAULT_RULES)
        rules = load_rules(store.settings_dir)
        for role in rules["models"]:
            model_config(role, rules)
        character = store.character_file.apply(store)
        print(f"配置已应用；角色版本 {character['revision']}；发帖间隔 {rules['publishing']['interval_seconds']} 秒。")
    except DomainError as exc:
        parser.exit(2, str(exc) + "\n")


if __name__ == "__main__":
    main()
