---
name: xhs_review
description: "小红书审图到私密发布工作流：识别、找参考、审图、修图或重拍、成稿、发布。"
tags: [workflow, xiaohongshu, image_review]
---
# Xiaohongshu Review Workflow

用于“审图、帮我 P、这张图能不能发、准备发”等完整任务。单独审图只调用
`xhs_edit` capability；已有素材和成稿直接发布则路由到 `xhs_publish`。

## 闭环

1. **初始化**：`xhs_task_init(user_request, image)`，后续产物全部写入 task_dir。
2. **识别**：`xhs_identify_dish(original)` 获取品类和关键词。仅当
   `confidence < 0.5` 时询问用户。
3. **找参考**：用首个关键词执行 `xhs_search_viral(top_n=10,
   fetch_content=3)`；完整对比取 3–5 张本地参考图。用户只要单图诊断时可跳过。
4. **审图**：`xhs_image_review(original, hint, reference_images?)`，按
   `PASS / REWORK / REPHOTO` 分支。
5. **处理**：
   - `PASS`：直接进入成稿。
   - `REWORK`：最多编辑两轮；每轮都以 original 为输入，再审新图。
   - `REPHOTO` 或两轮仍未通过：停止美化，在 original 上添加箭头和中文批注，
     输出重拍角度。
6. **成稿**：结合本轮 `viral_meta.json` 的正文、OCR、评论和真实标签，输出
   1 个最终封面、3 个标题、200–400 字正文、最多 10 个真实标签及本次修改说明。
7. **发布**：调用 `xhs_publish_note` 显式传 `visibility=self`；用户已指定其他可见性时采用该参数，不重复询问。

## 修图规则

- 每轮 `xhs_image_edit` 都读取
  [`xhs_edit`](../../soft_skills/xhs_edit/SKILL.md) 并从 original 开始，
  禁止在上一轮生成图上叠加。
- 第一轮结果变成 `REPHOTO` 时，可依据新问题重写一次编辑指令；第二轮仍未通过
  就进入重拍方案。
- 编辑必须保留主体结构、纹理、品牌文字和未指定区域。

## 成稿规则

- 标题、正文和标签只能基于本轮真实样本，不凭空编造平台热词。
- 样本文案不足时继续读取 OCR 和评论；证据仍不足就明确说明，不补写伪事实。
- 点评格式与输出字段读取
  [`xhs_review_prompt`](../../soft_skills/xhs_review_prompt/SKILL.md)。

## 发布规则

- 发布前读取 [`xhs_publish`](../xhs_publish/SKILL.md)。
- 工具核对请求可见性与内容后自动提交，这是实际发布；预填使用 `xhs_prepare_note`。
- 返回 `unverified` 或失败时停止，进入人工核对，禁止自动重发。

## 失败处理

- 无 task_dir、素材不存在、模型/API 失败：停止并返回具体错误。
- 完整对比缺参考图：重新搜索；单图诊断不强制参考图。
- 页面、可见性或发布结果无法验证：不得宣称完成。
