# bbduck-server AGENTS

- 本仓库的压缩逻辑以“保真优先”为最高优先级。
- 修改压缩逻辑时必须运行后端测试。
- 不允许为了体积收益牺牲透明通道、ICC/gamma 色彩信息、GIF 帧数/时长/loop 语义。
- 不能只看文件大小；必须记录候选算法、参数、体积、指标、淘汰原因。
- 外部工具不可用时必须 fallback，不能让服务不可用。
- 修改 compress.py / metrics.py / config.py 时必须补测试。
- 不要引入大型依赖，除非收益明显且有 fallback。
