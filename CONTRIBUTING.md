# Contributing to AutoMedia

Keep changes small, evidence-based, and consistent with the existing execution boundaries.

Useful contributions include a minimal reproduction of a platform page change,
a media-analysis example, or a reusable Skill with observable evidence.
See the [capability matrix](docs/CAPABILITY_MATRIX.md) before claiming new support.

## Rules

1. `skills/registry.json` is the source of truth for skill paths, dependencies, and tool gates.
2. A Skill describes decisions and constraints; a Tool performs one bounded action.
3. Do not duplicate selectors, request payloads, or retry timings in Skills. Those belong in code.
4. New dynamic tools require both `tools/<name>/tool.json` and
   `tools/<name>/execute.py`; the schema name must match the directory and
   `execute` must be synchronous.
   Prefixes `web_` and `xhs_` are reserved for built-in guarded tools.
5. Never commit credentials, runtime tasks, logs, browser profiles, or `skills/local/`.
6. Account side effects need explicit intent and observable verification.

## Before a Pull Request

```bash
python -m pytest -q
python scripts/check_skill_graph.py
python -m build
```

Include the behavior change, evidence used, and remaining risk in the PR description.
UI changes should include desktop and narrow-screen screenshots with sample data.
Offline fixtures do not establish successful real-account publishing.
