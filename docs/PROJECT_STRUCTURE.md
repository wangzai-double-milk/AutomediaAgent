# 配置与维护

代码目录和启动步骤见[README](../README.md)。

`settings/character.json`包含`profile.persona`、`voice`、`appearance`及`base_revision`。
程序导出当前角色，不复制其他用户身份；版本由程序维护。
`settings/rules.json`包含发布间隔、Thinking/Talk指导、改写上限及三域模型路由。
凭据保留在`config.json`或环境变量。

```bash
python -m automedia.configuration init
python -m automedia.configuration check
python -m automedia.configuration apply
```

`check`校验格式和连接，不调用模型。`apply`通过原版本检查更新角色，保留历史。
工作台保存角色同步文件；文件有待应用修改时拒绝覆盖。冲突时保留双方内容，合并后再应用。
规则在新任务中冻结，变更使旧任务失效，不重新执行已完成动作。

模型路由默认引用`native_oai_config`，`model`留空表示继承；填写模型名可独立选择。
这适用于接入三域的工作流，普通Agent会话仍保留原模型选择。

隔离数据库使用独立配置目录；可用`AUTOMEDIA_SETTINGS`明确指定目录。
运行数据位于`memory/`、`tasks/`、`logs/`、`temp/`，不随公开源码分发。
现有`operations.*`、平台`tools.*`和根级Python入口是兼容入口，与新模块共享状态。

Thinking统筹意图并审稿，Talk提供表达候选，Behavior执行并回读。
平台适配、网页/API变更和缺陷仍需要工程维护，配置入口不替代这些工作。
