# AutoSmartCut 未来升级路线

> 本文档记录已讨论但不在当前 MVP 范围内的升级方向。当前 MVP 的设计应为这些方向预留扩展空间，但不提前实现。

---

## 1. 插件化节点注册

**现状（MVP）：** 8 个内置节点硬编码注册到 PipelineSession。

**未来方向：**
- 开放 `session.register(node)` 接口，允许第三方节点注册
- 节点声明 `requires`（必需输入）/ `produces`（产出）/ `consumes`（可选输入），DAG 自动重建
- `consumes` 不参与 DAG 依赖推导，节点运行时有就读、没有也能跑
- 新增节点（如场景检测、说话人分离）只需实现 StageNode 协议并注册，不改主干代码

**MVP 预留：** StageNode 协议已定义 `reads/writes`，未来拆分为 `requires/produces/consumes` 时接口变更最小。

---

## 2. 节点配置 Schema

**现状（MVP）：** 节点配置通过全局 `AppConfig` 传入，各节点从中取自己需要的部分。

**未来方向：**
- 节点自带 `config_schema: dict`（JSON Schema），描述本节点接受的配置项
- `config.toml` 按 `[nodes.xxx]` 组织，内置节点和插件节点用同一套机制
- PipelineSession 在调度节点时自动验证配置并传入

**MVP 预留：** config.toml 的结构暂不改动，但 StageContext 传入的 config 可以在未来替换为节点级配置。

---

## 3. Agent 化调度（AgentScheduler）

**现状（MVP）：** FixedScheduler 按 DAG 拓扑序 + 硬编码循环规则驱动流水线。

**未来方向：**
- AgentScheduler：LLM Agent 持有节点列表作为 tools，DAG 作为约束边界
- Agent 自主决定：下一步调哪个节点、传什么参数、要不要重跑、循环几次
- Agent 可以在任意节点后请求人工确认（不限于 2d）
- PipelineSnapshot 提供结构化状态快照供 Agent 决策

**MVP 预留：** Scheduler 协议已定义，FixedScheduler 是第一个实现。未来 AgentScheduler 只需实现同一协议。

---

## 4. 2c 审核层与 Agent 调度的关系

**结论：** 2c 和 Agent 调度不冲突。2c 是"深度审核工具"（窄焦点、逐句扫描），Agent 是"全局决策者"（宽视野、状态推理）。Agent 消费 2c 的结构化输出做高层决策，不替代 2c 的逐句审核。

**未来方向：**
- Agent 可以选择性调用 2c（短视频可能不需要审核）
- Agent 可以用不同参数重跑 2c（调整 must_pass_rate）
- Agent 可以看 2c 的中间结果决定回 2b 还是回 2a

---

## 5. GUI（Web / Desktop）

**现状（MVP）：** TUI 适配器（Textual）作为 PipelineSession 的第一个交互式消费者。

**未来方向：**
- Web GUI：FastAPI + WebSocket 推送事件，React/Vue 前端渲染
- Desktop GUI：Tauri / Electron，或 Python 原生（如 DearPyGui）
- 鼠标点击视频片段、拖拽调整时间边界（2d 阶段的刚需）
- 视频预览播放

**MVP 预留：** PipelineSession 的事件总线 + send_action 接口与 UI 框架无关，GUI 适配器通过相同接口接入。

---

## 6. 多版本同时输出

**未来方向：**
- 同一份 manifest，在 L2b 阶段套用不同的剪辑策略预设（完整版、精华版、短视频）
- L3 执行层并行生成多个输出版本
- 共享 L1 + L2a 的计算成本

---

## 7. 流式处理（Streaming）

**未来方向：**
- L1 识别层实时运行（流式 ASR）
- L2 智能层实时做出切换决策
- 应用场景：自动直播导播、实时字幕

---

## 8. 数据飞轮

**未来方向：**
- 人工在 2d 的修正作为隐式反馈，积累后训练"编辑风格模型"
- 学习特定剪辑师的决策偏好，自动生成符合其风格的剪辑决策
