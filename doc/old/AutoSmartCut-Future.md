# AutoSmartCut — 已确认待扩展设计

> **文档定位（何时读、何时不读）**
>
> - **Demo 阶段与 MVP 阶段：** 本文件**不作为**项目的工作输入。**无需读入、无需分析、无需参考**；请勿在需求推导、方案设计、评审或实现中依赖本文档，以免干扰最小可用交付。此阶段请以 `AutoSmartCut.md`（架构）与 `AutoSmartCut-MVP.md`（落地范围）为准。
> - **MVP 交付完成之后，且第一代产品已达到可维护、可迭代的成熟基线之后：** 再将本文档视为已定型的扩展 backlog，**按条目、分阶段、按优先级逐步纳入**设计与实现，而非一次性全盘建设。
>
> 本文档收录**已完成概念验证、设计已定型，但不进入 MVP 的功能与架构扩展**。下文按**管道阶段**（识别层 / 智能层 / 执行层 / 跨层 / 横切）分组；组内条目按**落地优先级从高到低**排列（同档时：**近期** 优先于 **中期**，**中期** 优先于 **远期**；仅标注 **目标版本：完整版** 的条目排在「近期」之后、「中期」相关条目之前，与附录「完整版与 MVP 配置差异汇总」表一致）。
>
> **标签约定（每项条目均可具备下列属性，无则省略）：**
> - **时机：** 近期 | 中期 | 远期（成熟度时间档，不作章节分组）
> - **目标版本：** 完整版（相对 Demo / MVP 的能力边界，与附录配置对比表对应时标注）
>
> 对应文档：当前架构见 `AutoSmartCut.md`；MVP 落地规划见 `AutoSmartCut-MVP.md`；**智能层 MVP 现行字段与 2a 流程**以 `doc/intelligence-layer2-mvp.md` 为准。本文中涉及 2a 轮次、符号表、checklist 等描述均为**扩展设想**，若与 intelligence-layer2-mvp 冲突，以实现依据为准。

---

## 目录

1. [识别层（Layer 1）](#1-识别层layer-1)
2. [智能层（Layer 2）](#2-智能层layer-2)
3. [执行层（Layer 3）](#3-执行层layer-3)
4. [跨层（多阶段协同）](#4-跨层多阶段协同)
5. [横切：偏好、记忆与全局配置](#5-横切偏好记忆与全局配置)
6. [附录：完整版与 MVP 配置差异汇总](#6-附录完整版与-mvp-配置差异汇总)

---

## 1. 识别层（Layer 1）

### 1.1 情绪识别

**时机：** 近期

分析说话人的语气、视线方向与身份特征，为识别层标注情绪状态标签，辅助决策层优先保留高能量或情感关键段落。

---

## 2. 智能层（Layer 2）

### 2.1 人在回路（2d）

**时机：** 近期

智能层 2d 子阶段在每次决策草案生成后暂停等待人工逐条确认或修改，确保最终剪辑决策由人控制。**已在 MVP 架构中实现。**

---

### 2.2 checklist 三级优先级（完整版）

**目标版本：** 完整版

**现状（MVP）：** checklist 每条使用两级优先级 `must | optional`。

**完整版设计：**

| 优先级 | 含义 | 缺失时的影响 |
|--------|------|------------|
| `must` | 核心信息，缺失则成片无法理解 | 2c 审核强制触发 fix_decision |
| `should` | 重要补充，缺失会降低内容质量 | 2c 审核记录 warning，不触发循环 |
| `nice` | 锦上添花，有则更好 | 2c 审核忽略，仅在报告中列出 |

**升级原因：** `must/optional` 两档粒度不足——「应该有但不强制」的内容（如背景说明段）被混入 `optional`，导致审核信号噪声偏高。三档设计后，`should` 可作为内容质量评分的输入而不引发不必要的循环。

**升级步骤：** 在当前识别层基线与智能层提示词稳定运行后，扩展优先级枚举；覆盖报告新增 `should_coverage_rate` 字段。

---

### 2.3 2a 条件触发第 3 轮（完整版）

**目标版本：** 完整版

**现状（MVP 工程）：** 2a 为 **两轮 LLM + 程序替换**（R1 粗分块 + 错词候选；R2 精化 + `corrections`；程序生成 `cleaned_annotations`），见 intelligence-layer2-mvp §5。下述「两轮 + 符号表 + 检查清单」为**完整版叙事**，供本扩展条对照。

**完整版设计：** 当第 2 轮产生的消歧改动明显偏大时，条件触发第 3 轮，用干净文本重新验证主旨是否成立：

- **输入：** `cleaned_annotations[]` + 第 2 轮精化主旨
- **LLM 专注：** 用消歧后的文本重新核验整体主旨，避免第 1 轮基于错误实体建立的理解偏差被沿用到后续阶段
- **产出：** 最终主旨（覆盖第 2 轮主旨），其余字段保持不变或仅做轻微校正

**触发条件（代码层判断，LLM 不感知）：**

- `修正字数 / 总字数 > 阈值`
- 或 `symbol_table[]` 条目数 > 阈值

这意味着技术讲座、术语密集访谈、多实体讨论等内容更容易触发第 3 轮；闲聊类内容通常停在两轮，避免无谓增加 token 消耗。

---

### 2.4 目标导向 AI

**时机：** 中期

用户只需以自然语言设定目标（如「保留核心论点，压缩至原时长 60%」），系统自动开启多轮智能层循环迭代，人工只做最终确认。对应完整版 `max_inner/max_outer > 0` 配置开放。

---

### 2.5 content_map — 叙事图谱

**时机：** 中期

**现状：** `TimelineManifest.comprehension.content_map[]` 字段已在数据模型中预留，MVP 阶段置空，不做任何写入或读取。

**设计意图：** 在逐条标注的基础上，建立更高层次的叙事结构索引——把内容理解从"这段是什么"提升到"这段在整体叙事中扮演什么角色"。

**完整数据结构：**

```
ContentBlock（叙事块）：
  id            uuid              // 块唯一标识
  t_start       float             // 开始时间（秒）
  t_end         float             // 结束时间（秒）
  narrative_role string           // 叙事角色：intro | argument | evidence | transition | climax | outro | digression
  topic_cluster string            // 所属主题簇（可为叙事章节名）
  entity_refs   list[str]         // 涉及的实体引用（人名、概念、事件等）
  sentiment     float             // 情感极性：-1.0 ~ 1.0
  importance    float             // 叙事重要性：0.0 ~ 1.0
  linked_blocks list[uuid]        // 与哪些块存在因果/引用关系
  metadata      dict              // 开放扩展字段
```

**落地前置条件：**
- 需要多轮 LLM 调用（整体叙事分析 + 各块角色推断），Token 消耗显著高于 MVP
- 叙事角色 taxonomy 需在真实素材上验证后才能固定
- 依赖 2c 审核子阶段能稳定产出 pass 的基础（即 MVP 审核循环稳定后再扩展）

**预计落地时机：** MVP 在 3 种以上真实素材上验证稳定后。

---

### 2.6 叙事 AI

**时机：** 中期

识别内容的幕式结构（开端/发展/高潮/结尾）与情感弧线，为决策层提供段落级叙事权重，支持按叙事节奏而非纯时长剪辑。对应智能层 2a 理解扩展（见上文「content_map — 叙事图谱」）。

---

### 2.7 因果推理

**时机：** 远期

对口播内容中的事实性主张和前因后果进行逻辑链标注，防止剪辑破坏论证完整性，杜绝断章取义风险。

---

## 3. 执行层（Layer 3）

### 3.1 字幕过滤词系统

**时机：** 近期

**所属层：** 执行层（Layer 3）字幕后处理节点  
**前置依赖：** 执行层已产出字幕文件（SRT/VTT/ASS）

#### 3.1.1 设计原则

- **只用代码检测，不用 LLM**：敏感词识别是确定性规则匹配，LLM 会带来不一致性与延迟，不适用于此场景。
- **外接词库，不内置**：内置词库涉及版权与合规风险；系统提供加载接口，词库由用户/运营方自行维护。
- **只改字幕，不改视频**：字幕替换是低开销的文本操作；音频打码见「语音自动打码系统」。

#### 3.1.2 处理流程

```
输入：字幕文件（SRT/VTT/ASS）+ FilterRuleSet

Step 1：加载词库
  从 ~/.autosmartcut/subtitle_filter/ 加载 default.json + custom.json + wordlists/*.txt
  构建 Aho-Corasick 自动机（多模式串匹配，O(n) 时间复杂度）

Step 2：扫描字幕文本
  逐行匹配；记录命中的规则与字幕行号

Step 3：按规则应用（优先级：delete > replace）
  action=delete  → 整词替换为空字符串，前后空格合并
  action=replace → 替换为 replacement 字段指定的词

Step 4：输出
  sanitized_subtitle.*    已处理的字幕文件（原文件备份为 *.original）
  filter_report.json      过滤报告（命中词、行号、规则统计）
```

#### 3.1.3 数据结构

```python
@dataclass
class FilterRule:
    pattern: str                          # 匹配模式（字面量或正则）
    action: Literal["replace", "delete"]
    replacement: str | None               # action=replace 时有效
    match_mode: Literal["exact", "regex"] # exact 使用 AC 自动机；regex 使用 re
    priority: int = 0                     # 规则优先级，数值越大越先执行

@dataclass
class FilterReport:
    total_matches: int
    rules_applied: list[dict]             # {rule_pattern, count, sample_contexts[]}
    modified_line_indices: list[int]      # 被修改的字幕行号（0-indexed）
    source_files: list[str]              # 使用的词库文件路径
```

#### 3.1.4 配置目录

```
~/.autosmartcut/subtitle_filter/
├── default.json        # 内置示例规则
├── custom.json         # 用户自定义规则（优先级高于 default）
└── wordlists/          # 外接明文词库（每行一词，UTF-8）
    ├── profanity_zh.txt
    └── sensitive_topics.txt
```

```json
// default.json 示例结构
{
  "rules": [
    {"pattern": "他妈的", "action": "delete", "replacement": null, "match_mode": "exact"},
    {"pattern": "尸体",   "action": "replace", "replacement": "高达", "match_mode": "exact"},
    {"pattern": "fuck",  "action": "replace", "replacement": "***", "match_mode": "exact"}
  ]
}
```

#### 3.1.5 与 TimelineManifest 的集成

`filter_report` 作为新字段追加到 `TimelineManifest.execution_metadata`，可供后续审计：

```
execution_metadata.subtitle_filter_report: FilterReport | null
```

---

### 3.2 B-roll 匹配

**时机：** 近期

根据片段语义自动从本地素材库或免费图库检索配套 B-roll，生成 `action=insert_broll` 指令供执行层按时间轴叠加。

---

### 3.3 平台切版

**时机：** 近期

根据目标平台（YouTube/抖音/Instagram/Bilibili）的分辨率、时长与字幕规范，从同一 EDL 自动渲染多格式版本，避免人工逐平台返工。

---

### 3.4 全文意图识别与视频内容生成

**时机：** 低档近期 / 中档中期 / 高档远期（分档见下表）

**所属层：** 执行层（Layer 3）可选视觉生成节点  
**适用范围：** 口播内容限定（画面单一、内容由语音驱动的视频）  
**前置依赖：**
- 智能层已启用（非 Demo 直通模式）
- `manifest.comprehension.purpose` 已产出（2a 理解子阶段）
- `manifest.comprehension.checklist[]` 已产出（中、高档需要）
- `manifest.segments[]` 已产出（中、高档需要）

> **「全文意图识别」不是新步骤**：这部分已由智能层 2a 理解子阶段完成（产出 `purpose` 与 `checklist[]`）。本章节的核心是**基于已有意图理解，驱动视觉资产生成**。

#### 3.4.1 三档方案对比

| | 低档（Low）| 中档（Medium）| 高档（High）|
|---|---|---|---|
| **输入** | `purpose`（1-2句主旨）| `checklist[]` + `segments[]` | 完整 `comprehension` + `segments[]` + `annotations[]` |
| **生成内容** | 1 张主旨背景图 | 每主题段落 1 张背景/示例图 | 每片段 1 段 AI 生成视频 |
| **图像来源** | AI 图片生成 API | AI 生成 或 免费图库搜索 | AI 视频生成 API |
| **叠加方式** | 全程静态背景 overlay | 按时间轴切换背景 | 替换或合并原始视频轨 |
| **LLM 调用** | 1 次（prompt 生成）| N 次（N = 主题段数）| N 次 + 视频 prompt 精化 |
| **估算成本** | $0.02–0.10 / 视频 | $0.10–1.00 / 视频 | $1–20+ / 视频 |
| **成熟时机** | 近期 | 中期 | 远期 |

#### 3.4.2 低档方案详细设计

```
Step 1：从 manifest.comprehension.purpose 提取主旨文本
Step 2：LLM 调用 → 生成图像生成 prompt（中英文双语，含风格描述符）
Step 3：调用图片生成 API（DALL-E 3 / Stable Diffusion / Flux）
Step 4：下载并本地缓存图片 → visual_assets[0]
Step 5：执行层 overlay：将图片作为视频全程背景（原摄像头画面可透明度叠加或替换）
```

Prompt 生成模板示例：
```
主旨：{purpose}
要求：为一段口播视频生成背景图，风格简洁专业，无文字，无人物面孔。
输出格式：英文图像生成 prompt，150 词以内。
```

#### 3.4.3 中档方案详细设计

```
Step 1：将 segments[] 按 checklist item 分组 → 得到 K 个主题段落
Step 2：对每个主题段落：
  Option A（AI 生成）：
    LLM 调用 → 生成该主题的图像 prompt
    → 图片生成 API 调用 → 本地缓存
  Option B（图库搜索，免费）：
    LLM 调用 → 生成搜索关键词（英文）
    → Pexels / Unsplash API 搜索 → 取第一张版权合适的图片
Step 3：构建时间轴对齐的 VisualAsset[]
Step 4：执行层按时间轴切换背景图（FFmpeg concat / xfade 过渡）
```

配置项（`config.toml`）：
```toml
[visual_generation.medium]
image_source = "pexels"        # ai_image | pexels | unsplash
pexels_api_key_env = "PEXELS_API_KEY"
crossfade_duration = 0.5       # 背景切换过渡时长（秒）
```

#### 3.4.4 高档方案详细设计

```
Step 1：对每个 segment，LLM 生成结构化视频 prompt
  输入：segment 的 label + 时间范围内的 annotations[type=asr].content
  输出：{shot_type, subject, action, setting, mood, duration_s}
Step 2：调用 AI 视频生成 API（Runway Gen-3 / Kling / Sora）
  注意：每段视频生成独立任务，异步轮询状态
  预算控制：每段成本估算 → 累计超过 max_budget_usd 则降档或中止
Step 3：下载生成视频 → 本地缓存
Step 4：执行层将生成视频与原始音轨合并
  保留原始音频（包含打码后的音轨，若已执行「语音自动打码系统」）
  替换视频轨道（原摄像头画面完全替换 或 画中画叠加）
```

**版权注意：** AI 生成内容的版权归属因平台而异，需在 VisualAsset.legal_notes 字段记录所用 API 的版权条款。

#### 3.4.5 数据结构

```python
@dataclass
class VisualAsset:
    id: str
    t_start: float                         # 在最终成片中的起始时间（秒）
    t_end: float                           # 在最终成片中的结束时间（秒）
    asset_type: Literal["background", "overlay", "full_replace"]
    tier: Literal["low", "medium", "high"]
    prompt: str                            # 本次生成使用的 prompt（可审计）
    source: Literal["ai_image", "ai_video", "stock_search", "local"]
    local_path: str | None                 # 本地缓存路径
    external_url: str | None               # 原始来源 URL（图库版权追踪）
    generation_cost_usd: float | None      # 本资产的估算花费（USD）
    legal_notes: str | None                # 版权说明（AI 平台条款摘要）
```

`VisualAsset[]` 作为新字段追加到 `TimelineManifest.execution_metadata.visual_assets`。

#### 3.4.6 全局配置（config.toml）

```toml
[visual_generation]
enabled = false
tier = "low"                       # low | medium | high
max_budget_usd = 1.0               # 单次运行最大开销限制（超出则降档或中止）
overlap_mode = "replace"           # replace（替换原始视频轨）| overlay（叠加）

[visual_generation.image]
provider = "dall-e-3"              # dall-e-3 | stable-diffusion | flux | pexels | unsplash
api_key_env = "OPENAI_API_KEY"     # 图片生成 API Key 环境变量名
resolution = "1920x1080"           # 生成分辨率

[visual_generation.video]
provider = ""                      # runway | kling | sora（高档方案，留空则不启用）
api_key_env = ""
max_segment_duration = 10.0        # 单段视频最长生成时长（秒）
```

---

### 3.5 AI 翻译与配音

**时机：** 中期

基于字幕对话生成多语种译文，并通过口型同步技术将合成配音与原始画面对齐，输出多语言成片。

---

### 3.6 自适应输出

**时机：** 中期

根据目标受众画像（儿童/专业/大众）自动调整内容深度、语速与时长，为同一素材生成差异化版本。

---

### 3.7 语音合成与克隆

**时机：** 远期

克隆原说话人音色，对剪辑造成的语言断点处补录合成语音，实现语义连贯的无缝拼接。

---

## 4. 跨层（多阶段协同）

### 4.1 语音自动打码系统

**时机：** 近期

**所属层：** 识别层（Layer 1）可选节点 + 执行层（Layer 3）音频后处理节点  
**前置依赖：** 识别层基线已产出字级时间戳（如 `Annotation.metadata.char_timestamps`）；「字幕过滤词系统」产出的命中词时间戳

#### 4.1.1 与 MVP ASR 的区别

| | 当前基线（Qwen3-ASR + ForcedAligner）| 其他可选强制对齐实现 |
|---|---|---|
| 时间戳粒度 | **字级**（char-level forced alignment）| 取决于实现，至少需字级或词级 |
| 打码精度 | 精确到字/词边界（约 ±0.1s）| 取决于实现 |
| 依赖 | Qwen3-ASR + Qwen3-ForcedAligner | 取决于实现 |
| 识别层产出 | 句级标注（`content`、`t_start`/`t_end`、`gap_after`），字级时间戳存于 `metadata.char_timestamps` | 与基线兼容的时间戳视图 |

> 语音自动打码的核心前提不是某个特定模型，而是识别层能稳定提供字级时间戳。Qwen3-ForcedAligner 现已满足这一要求，因此该功能不再绑定 WhisperX。

#### 4.1.2 处理流程

```
输入：
  annotations[].metadata.char_timestamps   ← 来自识别层基线强制对齐结果（句级条目的字级明细）
  FilterReport.modified_line_indices ← 来自字幕过滤词系统，含命中词

Step 1：时间戳关联
  匹配 FilterReport 中 action=delete 的命中词 → 查找对应的 char_timestamps
  提取精确 {t_start, t_end}，构造 BeepMask[]
  降级处理：若无字级时间戳，回退到句级时间戳（可能过打）

Step 2：生成 beep 音轨
  按 AudioBeepConfig 在对应时间范围生成正弦波（默认 1000 Hz）
  加 20ms 淡入淡出，避免爆破声

Step 3：音频混音
  原始音轨在 BeepMask 覆盖范围内降至 duck_db（默认 -40 dB）
  overlay beep 音轨（PyAV 帧级操作 或 FFmpeg filter_complex）

输出：
  audio_beeped.*     打码后的音频轨道
  beep_report.json   打码报告（哪些词、哪些时间段被打码）
```

#### 4.1.3 数据结构

```python
@dataclass
class BeepMask:
    t_start: float          # 精确开始时间（秒）
    t_end: float            # 精确结束时间（秒）
    original_word: str      # 被打码的原始词（供报告使用，不进入最终产物）
    source: Literal["word_align", "segment_fallback"]  # 时间戳来源

@dataclass
class AudioBeepConfig:
    tone_hz: int = 1000             # 打码音频频率（Hz），1000 为广播标准
    duck_db: float = -40.0          # 原始音频降幅（dB，负值）
    fade_ms: int = 20               # 边缘淡入淡出时长（毫秒）
    beep_source: Literal["sine", "file"] = "sine"
    beep_file: str | None = None    # 自定义打码音效文件路径（beep_source=file 时有效）
```

#### 4.1.4 实现备注

- **推荐实现路径**：用 `scipy.signal.chirp` 或纯 numpy 生成 sine wave PCM → 写入临时 WAV → 用 PyAV 与原始音轨在帧级合并。PyAV 已为 smartcut 依赖，无需新增。
- **备选路径**：全程 FFmpeg，用 `sine` 音源 filter + `volume` + `amix` filter_complex；命令行可调试但可移植性稍差。
- **字幕与音频同步**：打码词在字幕中已被「字幕过滤词系统」替换（delete/replace），音频打码对应相同的词，两套系统共享 `FilterReport` 作为驱动数据，确保一致性。

---

### 4.2 实时剪辑

**时机：** 远期

识别层、智能层、执行层全部流式运行，支持多机位直播导播场景的实时建单与实时决策。

---

### 4.3 发布智能体

**时机：** 远期

从剪辑决策、格式转换、平台上传到封面生成全流程自治，人类仅需设定发布目标，系统完成端到端闭环，无需逐步介入。

---

## 5. 横切：偏好、记忆与全局配置

### 5.1 用户偏好学习系统

**时机：** 近期 **目标版本：** 完整版（见附录配置对比表）

从历史人工确认的 TimelineManifest 中自动提取编辑偏好模式，更新 UserStyle 风格 Prompt 与 UserPreferences 量化参数（实现细节见下列子节）。

#### 5.1.1 UserPreferences — 量化参数层

基于历史剪辑数据自动学习的数值型参数。每次用户在 2d 人工子阶段修改决策后，系统记录原始决策与人工修改的 diff，用于后续参数调整。

```toml
# ~/.autosmartcut/preferences.json（自动管理，也可手动编辑）
{
  "silence_threshold": 0.5,       # 静音判定阈值（秒），默认 0.5
  "min_segment_duration": 2.0,    # 保留片段最小时长（秒）
  "relevance_cutoff": 0.6,        # 相关性分数低于此值的片段自动标记为 cut
  "max_output_ratio": 0.7,        # 输出时长 / 原始时长，超过则触发提醒
  "prefer_complete_sentences": true, # 是否偏好在句子边界切割
  "derived_from_manifests": 12,   # 用于推断此参数的历史清单数量
  "last_updated": "2026-03-01"
}
```

**学习触发条件：** 积累 N 份(默认 10 份)已人工确认的 TimelineManifest 后，触发一次离线参数重新估计（见「离线学习流程」）。

---

#### 5.1.2 UserStyle — 风格提示层

基于历史清单提取的语义风格描述，作为 2a 理解子阶段和 2b 决策子阶段的 system prompt 注入层。

```json
// ~/.autosmartcut/styles/podcast_cleanup.json
{
  "content_type": "podcast_cleanup",
  "style_prompt": "该用户偏好保留完整论点展开，不喜欢在论据中途切断。对重复说明和口头禅（「就是说」「嗯」）保持零容忍，但会保留说话人表达观点的自然停顿。",
  "derived_from": ["manifest_2026-01-15.json", "manifest_2026-02-03.json"],
  "confidence": 0.82,
  "version": 3,
  "last_updated": "2026-03-01"
}
```

**内容类型分类（初始支持）：**
- `podcast_cleanup` — 播客/访谈静音删除
- `lecture_condensing` — 课程/演讲内容压缩
- `meeting_minutes` — 会议纪要提取
- `highlight_reel` — 高光集锦

---

#### 5.1.3 离线学习流程

```
积累流程：
  每次 2d 人工确认 → TimelineManifest 序列化到 ~/.autosmartcut/history/
  history/ 新增一份 → 检查是否达到学习触发阈值（默认 N=10）
  
学习触发（离线，异步，不阻塞正常使用）：
  Step 1：加载最近 N 份同类型 manifest，提取 human_corrections（人工修改 diff）
  Step 2：发送给 LLM，prompt："分析这 N 次人工修改的共同模式，提取编辑偏好"
  Step 3：LLM 返回结构化偏好描述 → 更新 UserStyle.style_prompt
  Step 4：统计数值型修改模式（如平均 silence_threshold 调整方向）→ 更新 UserPreferences
  
应用：
  下次运行时，2a/2b 子阶段自动注入 UserStyle.style_prompt
  识别层静音检测使用 UserPreferences.silence_threshold
```

**隐私说明：** 所有历史数据保存在本地 `~/.autosmartcut/history/`，不上传云端。LLM 调用时只发送匿名化的编辑 diff，不发送视频内容本身。

---

#### 5.1.4 本地配置目录结构

```
~/.autosmartcut/
├── config.toml              # 全局配置（API Key、模型选择、循环上限等）
├── preferences.json         # UserPreferences 量化参数（自动学习 + 可手动覆盖）
├── styles/                  # UserStyle 语义风格文件（按内容类型）
│   ├── podcast_cleanup.json
│   ├── lecture_condensing.json
│   └── ...
└── history/                 # 历史 TimelineManifest（供偏好学习使用）
    ├── 2026-01-15_podcast.json
    ├── 2026-02-03_podcast.json
    └── ...
```

**config.toml 结构示例：**

```toml
[llm]
provider = "deepseek"           # openai | deepseek | local
api_key_env = "DEEPSEEK_API_KEY"
model = "deepseek-chat"
max_retries = 3

[loop]
max_inner = 2                   # fix_decision 内循环上限（完整版）
max_outer = 1                   # fix_checklist 外循环上限（完整版）
early_stop_threshold = 0.9      # coverage 达到此值提前退出
token_budget_per_minute = 2000  # token 预算（按视频时长分配）

[learning]
trigger_threshold = 10          # 触发偏好学习的历史清单数量
auto_learn = true               # 是否自动触发离线学习
```

---

### 5.2 语义档案

**时机：** 中期

将历史时间轴清单建立向量语义索引，支持跨视频的自然语言检索——例如「找出所有讨论 X 主题且时长超过 2 分钟的片段」。

---

### 5.3 知识图谱

**时机：** 远期

跨多期视频提取实体、事件与观点关系，构建发布者专属知识图谱，支持前后一致性检验与长期内容策略分析。

---

## 6. 附录：完整版与 MVP 配置差异汇总

| 能力 | Demo | MVP | 完整版 |
|------|------|-----|--------|
| 智能层循环 | 禁用（max=0）| 禁用（max=0）| 动态循环（按时长分配 Token 预算）|
| checklist 优先级 | must/optional 两档 | must/optional 两档 | must/should/nice 三档 |
| content_map | 置空 | 置空 | ContentBlock 叙事图谱完整填充 |
| 覆盖报告 | 决策层自产，审核层跳过 | 决策层自产，审核层跳过 | 决策层自产 + 审核层验证（双保险）|
| 用户偏好学习 | 无 | 无 | UserPreferences + UserStyle 自动学习 |
| 本地配置目录 | 无 | 无 | `~/.autosmartcut/` 完整结构 |
| Token 预算 | 不适用（无循环）| 不适用（无循环）| `token_budget_per_minute × duration` |
| 2c 审核子阶段 | 跳过 | 跳过 | 完整执行，产出 ReviewResult |
| 识别层节点 | ASR + 句级聚合 + gap_after | ASR + 句级聚合 + gap_after | + 情绪识别、声纹识别（近期扩展）|
| 执行层节点 | smartcut GOP 剪切 | smartcut GOP 剪切 | + B-roll 插入、多平台输出 |
| 字幕过滤词系统 | 无 | 无 | 外接词库 + AC 自动机检测 + 字幕替换 |
| 语音自动打码 | 无 | 无 | 字级时间戳 + sine beep 音频混音 |
| 视频内容生成 | 无 | 无 | 低/中/高三档（主旨图/分段配图/AI 视频）|
