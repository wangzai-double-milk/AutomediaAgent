---
name: xhs_manage
description: "小红书通知、自己主页、作品管理、数据与创作者入口发现能力。"
tags: [capability, xiaohongshu, operations]
---
# 小红书管理

工具：`xhs_notifications`、`xhs_own_profile`、`xhs_creator_page`、`xhs_manage_note`、
`xhs_messages`、`xhs_drafts`、`xhs_collections`、`xhs_rss`。
测试控制入口：`xhs_test_account`，支持status/bind/pause；不提供降低额度或恢复后台参数。

1. 通知按 comments / likes / follows 读取，最多十轮滚动，返回结构化列表与可见页面文字。没有结构化数据时不能把数量零解读为没有通知。
2. 自己主页先读取真实登录身份；第三方主页和关注动作属于搜索/互动能力。
3. 创作者入口从当前账号页面发现：作品、草稿、账号/笔记/粉丝数据、合集、长文、商品、直播、广告、消息、设置。`unavailable` 是网页未发现入口；`restricted` 是页面显示权限限制，两者都不能宣称完成。
4. 指标仅记录页面实际显示的标签与数值，不将缺失字段补成零，不混用不同统计周期。
5. CDP修改删除提供真实登录`owner_id`。desktop提供`expected_account`、
   `target_title`、`note_id`，可加`published_at`；先唯一卡片，再打开编辑从真实URL核对ID。
   `prepare_edit`只打开回读。桌面页眉昵称仅作为绑定标签，不伪装user_id。
6. `edit`优先“保存修改/确认修改/更新笔记”。真实desktop更新页按钮名为“发布”，
   只有`/publish/update`且查询id完全匹配目标、账号/内容即时回读一致，才能单次保存；
   新建发布页绝不能作为修改页。保存后管理页与编辑页独立核对。
7. 写操作先落盘 `management_attempts.json`；出现 `unverified` 时停止，读取管理页核对，禁止重复删除/保存。
   desktop `visibility=self/public/friends`在同一真实ID编辑页修改，保存后独立卡片核对
   可见性，并重新编辑核对原内容与ID。self→friends→self已实际通过。
8. 私信`xhs_messages`支持list/read/prepare/send/clear。read/prepare真实通过；
   必须真实owner_id、recipient_id与recipient_name，对照当前我链接和/chat/ID/会话头。
   prepare不发送；clear只清除完全匹配的指定测试内容；send需用户已有明确授权，
   输入前保留他人未发草稿，提交前message_attempts.json防重，单次Enter，
   重开会话核对历史，不把输入框或侧栏预览当发送证据。
   unverified停止，不能保证对方收到或已读；未提取稳定消息ID返回null。
9. 合集、长文、电商等仅找到入口不算全流程，需要实际页面与权限验收。
10. `xhs_drafts`列出四种本地草稿，打开/删除必须绑定`expected_account`与
    真实`kind/title/saved_at`。列表共享一个AX容器，按相邻标题/日期/编辑/删除序列分段；
    不生成草稿ID。删除弹层“草稿删除后不可找回”须与刚选定行绑定，记录后只确认一次；
    通过管理页重新进入草稿，核对仅目标消失与其余条目保留。
    草稿/合集/RSS工具当前只支持macOS桌面；失败不能传CDP当作另一条实现。
    action=publish用同一目标草稿恢复到最终编辑器，保持媒体与正文，不重新上传；
    默认self，可明确visibility/schedule_at。提交前library_attempts.json落盘，
    单次发布后复用作品管理和编辑页独立核对，未到最终编辑器的草稿明确拒绝。
11. `xhs_collections`区分普通/播客。普通合集需当前笔记编辑器`target_title`；
    播客名称20、简介100、封面5MB；普通名称20、简介50。inspect/prepare/cancel
    已真实通过。播客支持list/open/edit/delete：按实际名称+简介唯一定位，
    创建/修改后独立重开列表和编辑表单核对；实际创建→修改→空合集删除已通过。
    封面上传需确认平台裁剪弹层，预览封面存在才创建；collection_id未知仍null。
    delete仅验收空合集；确认前核对编辑表单目标与删除原文，独立重开列表确认消失。
    普通合集create/verify/select会保存并重新打开当前图文草稿，再核对绑定和独立下拉列表；
    已真实创建与保存重开通过。普通简介未独立核对，改名/删除/取消加入入口未发现。
    多播客合集按封面/标题/简介/数量/控件相邻序列分段；离线防串卡检查通过，
    多目标真实验收因账号限制中断，不能扩大实际验证范围。
12. `xhs_rss`inspect/prepare/fetch/request_code/confirm/cancel。RSS邮箱由平台解析feed
    自动填写，缺真实地址/合集/验证码时返回needs_input。仅显式accept_agreement才选择协议。
    邮箱请求和最终确认先落盘，不以表单关闭推断导入成功，不记录验证码正文。
13. 自己主页出现“该账号疑似存在风险”时返回restricted，停止账号写操作；
    只有同一稳定ID主页恢复笔记入口且无风险提示才恢复。登录或creator列表可读不算恢复。
    不换transport/通用键鼠绕过暂停，恢复时先核对原journal和队列账号标签。
14. 新账号测试遵循[换号与频率方法](../../../docs/NEW_ACCOUNT_TEST_CONTROL.md)。
    主站健康观察只保存证据，不能清掉旧账号限制；bind同时核对稳定ID与昵称。
    新账号没有登录时继续本地工作，不索要密码/验证码。当前不设每日次数上限，两次发帖间隔10分钟；
    草稿及其他写操作不顺延发帖间隔。未知提交仍保留记录防重。deferred在原提交journal之前产生，
    当轮停止，返回下次时间；不长sleep或另开任务。旧草稿、旧队列不能转给新账号执行。
15. RSS的collection来自“加入合集”独立字段，必须与请求名称完全一致；
    下拉选项出现不等于已选中。当前选中值回读只经离线检查，feed解析、
    邮箱发送与最终导入的真实结果仍待用户真实资料和受预算约束的验收。

已真实发布self图文、队列、自有评论、修改/可见性/删除，以及未来定时提交与删除清理；
私信读取/预填/清除通过，实际发送未验收。状态见`docs/CAPABILITY_MATRIX.md`。
