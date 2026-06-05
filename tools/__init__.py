"""tools - 小红书美食博主 agent 的工具集。

每个子模块对应一类原子能力（被 ga.py 中 do_xxx_* 方法直接 import 调用）：
    - image     图像审核 / 编辑 / 生成（豆包 VLM + Seedream 4.5）
    - notes     抓自家笔记数据（待添加）
    - publish   发布草稿（待添加）

约定：
    - 每个公开函数返回普通 dict / str，便于 ga.py 中的 do_xxx 直接 json.dumps 给模型。
    - 不写 print 到 stdout（除非是 CLI 入口），日志一律走 logger 或返回字段。
    - 业务编排（多步流程、找爆款、调侃用户等）放在 skills/hard_skills/xhs_main/SKILL.md，不写在这里。
"""
