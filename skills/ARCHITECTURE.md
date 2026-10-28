# Skill Architecture

Skills use a one-way dependency graph:

```text
Router -> Workflow -> Capability -> Resource
```

## Layers

- `router`: selects user intent, target platforms, and one primary workflow.
- `workflow`: owns a complete user outcome and orchestrates capabilities.
- `capability`: performs one bounded operation; it never invokes a workflow.
- `resource`: prompts, style guides, and domain playbooks; it is never routed directly.

`registry.json` is the executable source of truth for dependencies and
`tool_skill_gates`. Markdown links are documentation, not runtime dependencies.

## Runtime Rules

1. Read `agent_core` once for a new social-media task.
2. Select one primary workflow. Do not load multiple workflows unless the user
   explicitly asks for multiple independent outcomes.
3. A workflow may load only the capabilities/resources listed in its registry entry.
4. A capability must not send the agent back to a router or workflow.
5. Cross-cutting safety rules are enforced in code gates, not copied into every skill.
6. Content playbooks generated from research are resources. They belong in
   `soft_skills/` and must not contain tool-routing instructions.
7. Dynamic tools cannot use the reserved `web_` or `xhs_` prefixes. Platform
   tools using those prefixes are built in and protected by `tool_skill_gates`.

## Ownership

| Concern | Owner |
|---|---|
| Intent and platform selection | `agent_core` |
| Research outcome | `research_mode` |
| Content planning and account operation | `operate_mode` |
| One-shot review-to-publish flow | `xhs_review` |
| Browser perception and action | `perception_action` |
| Unknown-platform exploration | `explore_mode` |
| Xiaohongshu search/read | `xhs_search` |
| Xiaohongshu interactions | `xhs_interact` |
| Image review/generation/editing | `xhs_edit` |
| Self-visible publishing | `xhs_publish` |
| Prompt/style/domain knowledge | resource skills |
