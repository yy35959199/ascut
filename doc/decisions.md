# 技术决策记录（ADR）

> 本文档记录 AutoSmartCut 的关键技术决策。每条决策写下后不再修改，新决策追加到末尾。
> 当前实现细节见 [architecture.md](architecture.md)、[intelligence.md](intelligence.md)、[cli-and-config.md](cli-and-config.md)。

---

## D1：仓库结构

**决策**：新建独立仓库 `AutoSmartCut`，不在 smartcut 仓库内扩展。

**理由**：AutoSmartCut 与 smartcut 职责完全不同。smartcut 是 GOP 级视频剪切库，AutoSmartCut 是语义处理管道。smartcut 作为 pip 依赖引入，保持职责分离。

---

## D2：Demo 脚本存放

**决策**：仓库内 `demos/` 目录，保留 `demo1_asr.py`、`demo2_llm.py`、`demo3_smartcut.py` 三个脚本。

**理由**：便于对照验证结果，提交到仓库作为验证记录。

---

## D3：ASR 引擎选型

**决策**：Qwen3-ASR-1.7B（转写）+ Qwen3-ForcedAligner-0.6B（字级对齐）。推理后端选 vLLM，editable install（`pip install -e "./Qwen3-ASR[vllm]"`）。

**理由**：字级对齐是精确切点的生死线（~0.1s 精度）。`Qwen3ASRModel.transcribe(return_time_stamps=True)` 一次调用同时返回转写文本与 `ForcedAlignResult`。perception 层在组装 `Annotation.metadata.char_timestamps` 时将 `ForcedAlignItem` 的 `start_time`/`end_time` 字段归一化为 `start`/`end`（更短，LLM Prompt 展示更紧凑）。

---

## D4：目标语言

**决策**：中文为主（Demo 阶段）。

**理由**：目标用户场景以中文内容创作为主。

---

## D5：句间间隔表示

**决策**：`gap_after = 下一句 t_start − 当前句 t_end`；末句为 `媒体时长 − 当前句 t_end`。不引入独立静音行。

**理由**：字级对齐提供句级 `t_start`/`t_end`；间隔写入每条标注的 `gap_after`，不单独插入静音行，保持 `annotations[]` 与 `keep_mask[]` 的一一对应关系。

---

## D6：首选 LLM

**决策**：DeepSeek（V3/R1，OpenAI 兼容协议）。

**理由**：中文理解好，成本极低，API 格式与 OpenAI 兼容，一个适配器覆盖大多数提供商。

---

## D7：LLM 分析目标传入方式

**决策**：CLI 参数 `--goal "..."`。

**理由**：LLM 需要明确目标才能做有意义的相关性评分；目标因场景差异大，由用户指定。

---

## D8：CLI 界面语言

**决策**：中文。

**理由**：目标用户为中文使用者。

---

## D9：人工反馈历史策略

**决策**：保留所有轮次，超过 N 轮时压缩为摘要；N 可配置，Demo 阶段 N=1。

**理由**：累积上下文使 LLM 越来越了解用户意图；压缩机制控制 Prompt 长度。

---

## D10：smartcut 依赖方式

**决策**：`pip install smartcut`（PyPI）。

**理由**：稳定版本；PyAV 通过 smartcut 间接引入，统一 FFmpeg 集成方式。smartcut 的 GOP 级 Remux + 切点局部 Recode 方案实现帧精确剪切，速度与视频时长无关，非切点区域零质量损失，且内建 HEVC CRA/RASL 花屏修复。

---

## D11：检查点存储位置

**决策**：与输入视频同目录，子目录名携带视频名（如 `ascut_out_<ULID前8位>/`）。

**理由**：便于关联，不污染其他目录；携带 ULID 避免多次运行冲突。

---

## D12：批量处理

**决策**：MVP 只支持单文件。

**理由**：降低 MVP 复杂度；单文件场景已足够验证核心流程。

---

## D13：智能层 LLM 调用策略

**决策**：2a 两次 LLM（R1 单轮；R2 与 R1 同一 `messages` 前缀上的第二跳）+ 程序一步生成 `cleaned_annotations`；2b 单轮 `call_llm_structured`；2c 单轮 `call_llm_structured`（两阶段输出：checklist → judgments）。

**理由**：2a 中间结构不落盘；持久化 `comprehension` 仅 `purpose`/`outline_blocks`/`corrections`；2b 读 `comprehension` + 内存 `tokens[]`（由 `annotation_tokens.tokens_from_annotations` 自 `annotations[]` 派生，不落盘）。R1→R2 真多轮利于前缀缓存命中，降低 API 成本。

---

## D14：2c 审核子阶段实现

**决策**：已实现真实 LLM 审核。单次调用两阶段输出（先生成 checklist 再逐条判断），verdict 由程序计算；2b↔2c 内循环（`two_c_max_review_rounds` 默认 1）；`= 0` 时退化为占位透传。

**理由**：checklist 将模糊 goal 分解为离散布尔条件，降低 LLM 判断随机性；verdict 程序计算消除 LLM 自相矛盾；内循环修正注入具体 index，2d 人工仍为最终兜底。

---

## D15：Qwen3-ASR 安装方式

**决策**：editable install：`pip install -e "./Qwen3-ASR[vllm]"`；不使用预打包版本。

**理由**：Qwen3-ASR 仓库作为子目录存在于工作区，editable install 保证本地修改立即生效；选 vLLM extra 以启用高性能 inference backend。

---

## D16：句级聚合分割规则

**决策**：分割模式：`punctuation`（默认）或 `timing`；punctuation 以句终标点为分割依据；timing 以 `split_pause_threshold` 为分割依据；`max_chars` 兜底；每条句级标注带 `gap_after`。

**理由**：`split_pause_threshold` 仅影响 timing 切分；配置项 `silence_threshold` 保留兼容，当前实现不用于插入静音行。

---

## D17：LLM 决策粒度

**决策**：LLM 通过 `keep_mask` 对每条句级标注输出 `keep: true/false`，不输出带时间戳的 Segment。

**理由**：时间由清单 `annotations[]` 的 `t_start`/`t_end`/`gap_after` 与 Layer 3 合并；`keep_mask` 与句级条数等长，项项为布尔，结构简单，校验容易。

---

## D18：持久化载体

**决策**：以 `timeline_manifest.json` 为单一主文件，顶层含 `annotations[]`、`current{…}` 等；`tokens[]` 不落盘；编排用内存 `manifest_dict`。

**理由**：单文件减少「多 JSON 路径约定」带来的心智负担与校验分叉；`tokens[]` 是 `annotations[]` 的纯形式投影，O(n) 微秒级派生，无需持久化。

---

## D19：虚拟消歧 vs 原地修改

**决策**：2a 纠错通过 `cleaned_annotations[]` 旁路表达，不原地修改 `annotations[].content`。

**理由**：原地修改的风险是 LLM 可能创意改写，改变措辞和语序，导致文本与音轨语义不一致。虚拟消歧保留原文，通过 `corrections` 映射生成消歧视图，保证溯源性和音轨一致性（Append-only 原则）。

---

## D20：LLM 处理顺序——先理解后纠错

**决策**：2a R1 先做全文语义理解（主旨 + 分块），R2 再做纠错（`corrections`）。

**理由**：LLM 的处理顺序与人类不同。人类先纠错再理解，但 LLM 先理解（全文语义）再纠错（在理解基础上识别错误）效果更好。这改变了对 2a 两轮分工的理解：R1 应该完成「理解」（包括分块），R2 完成「确认和应用」。

---

## D21：结构化反馈分类——四种类型

**决策**：2d 人工反馈分为四种结构化类型（F1 主旨偏差、F2 关键词纠错、F3 内容选择意见、F4 批量切换时间节点），不同类型触发不同处理路径。

**理由**：统一反馈（所有反馈都触发 2a 重跑）导致不必要的计算和复杂度。分类反馈精准修改对应模块：F1/F2 → 回流 2a，F3 → 回流 2b，F4 → 直接修改 keep_mask。结构化输入降低 LLM 解析难度，避免语义歧义。

---

## D22：DAG 调度替代双轨并行编排

**决策**：用 PipelineSession DAG 自动推导并行能力，替代原 `dual_track_orchestrator.py` 的手动并行逻辑。

**背景**：原实现通过 `dual_track_orchestrator.py` 手动编排 L1B 与 L2 的并行执行，需要维护 partial JSON 文件（`l1b.partial.json`、`l2.partial.json`）和 Barrier 合并逻辑。

**理由**：DAG 方案中，节点通过 `reads`/`writes` 字段声明依赖，PipelineSession 自动推导哪些节点可以并行。节点增减时无需修改编排代码，并行能力由数据依赖关系自然决定。`dual_track_merge.py` 保留为辅助工具，但不再是主编排路径。

---

## D23：L2dNode writes 声明 `l2d_completed` 而非 `keep_mask`

**决策**：`l2d_human` 节点的 `writes` 声明为 `{"human_feedback_history", "l2d_completed"}`，而非包含 `keep_mask`。

**背景**：`l2d_human` 在确认时会覆盖写 `keep_mask`（合并 overrides 后的定稿），但 `l2b_decision` 也声明写出 `keep_mask`。DAG 构建时不允许两个节点写同一字段（会抛出 `ValueError`）。

**理由**：引入 `l2d_completed` 作为 `l2d_human` 的专属输出标识，`l3_execute` 通过读取 `l2d_completed` 建立对 `l2d_human` 的 DAG 依赖。`keep_mask` 的覆盖写在节点 `run()` 内部直接操作 `ctx.manifest`，不通过 DAG `writes` 声明。

---

## D24：2c verdict 由程序计算而非 LLM 输出

**决策**：2c 的 `verdict`（`pass` 或 `fix_decision`）由程序根据 must 项通过率计算，不在 LLM 输出 schema 中包含 `verdict` 字段。

**理由**：LLM 生成 verdict 时可能与自己的 judgments 矛盾（3 条 must 未通过但仍输出 pass）。程序计算是确定性的，消除了这一层随机性。阈值（`two_c_must_pass_rate`）可配置，不需要改 prompt 就能调整审核严格度。
