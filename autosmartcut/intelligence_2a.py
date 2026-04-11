"""Layer 2 / 2a 理解子阶段（MVP 契约见 doc/intelligence-layer2-mvp.md §5）

## 流程
1. **tokens**：manifest 中 ``tokens[]``，每项仅 ``index`` + ``text``（JSON2 句面）。
2. **R1（LLM，仅内存）**：`purpose_rough`、`outline_blocks_rough`、
   `candidate_misrecognitions`。
3. **R2（LLM，仅内存）**：`purpose`、`outline_blocks`、`corrections`
   （唯一替换列表，nth 1-based）。
4. **程序**：按 `corrections` 在 **只读句面**（``tokens[].text``）上替换，生成稠密
   `cleaned_annotations[]`；**不**改写 ``tokens``（Append-only）。

LLM 调用封装：R1 ``call_once_structured_with_raw_content``；R2 ``call_turn_structured``（真多轮，前缀与 R1 共享以利缓存）。

## 输入（manifest 片段）
- `tokens[]`：句级句面（index、text）。
- `goal`：可选。

## 输出（写入 manifest）
manifest_dict["comprehension"] = {
    "purpose": str,
    "outline_blocks": [{"start_index", "end_index", "summary"}, ...],
    "cleaned_annotations": [{"annotation_index", "cleaned_content"}, ...],  # 稠密；程序生成
}

默认不在本模块内写盘；若调用方传入 ``on_phase_save``，由编排层将 R1/R2 检查点写入
``timeline_manifest.json`` 的 ``current.l2_checkpoints``（见 ``manifest_io.write_l2_checkpoint``）。
不写入 symbol_table。

## 注意
- cleaned_annotations 为稠密序列，长度与 tokens 一致（字段名沿用 MVP ``annotation_index``）。
"""

import copy
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from types import MappingProxyType

from autosmartcut.intelligence_llm import (
    StructuredLLMResult,
    call_once_structured_with_raw_content,
    call_turn_structured,
    prepare_next_turn_messages,
)
from autosmartcut.annotation_tokens import validate_tokens


# ============================================================================
# 模型参数（便于调试）
# ============================================================================

# R1：全局压缩 + 分段，默认非思考模式（见 doc/专家角色提示与模型性能关系.md）
ENABLE_REASONING_R1 = False

# R2：纠错 / 一致性，可按需单独开 reasoner
ENABLE_REASONING_R2 = False

# R1 温度：略高，允许一定创造性（主旨概括）
R1_TEMPERATURE = 0.5

# R2 温度：偏低，消歧和分块需要确定性
R2_TEMPERATURE = 0.2


# ============================================================================
# 纠错数据结构
# ============================================================================

@dataclass
class Correction:
    """单条纠错规则（对应 R2 输出的单条 correction）"""
    nth: int    # 该子串在原句中从左到右第几次出现（1-based）
    old: str    # 原始错误子串
    new: str    # 替换为


# ============================================================================
# 主入口
# ============================================================================

def run_2a_comprehension(
    manifest_dict: dict,
    *,
    on_phase_save: Callable[[str, dict], None] | None = None,
) -> dict:
    """2a 理解子阶段：两轮 LLM + 稀疏纠错 + 稠密回填

    Args:
        manifest_dict: 包含 ``tokens``（JSON2 句面）、``goal``、可选 ``source`` 的工作数据
        on_phase_save: 可选；``("2a_r1", payload)`` 在 R1 完成后、``("2a_r2", payload)`` 在
            稠密 ``comprehension`` 写入 ``manifest_dict`` 之后调用，用于将 TimelineManifest 落盘。

    Returns:
        追加了 comprehension 字段的 manifest_dict
    """
    print("[2a] 理解子阶段开始")

    # 句面列表（内存稠密），与清单 annotations 等长；供 2a 提示词与 LLM 使用
    tokens = manifest_dict["tokens"]
    # 校验 index 与下标一致，避免后续 prompt / 程序步骤错位
    validate_tokens(tokens)
    # 用户剪辑目标，原样进入 R1/R2 提示词首行「用户目标：…」
    goal = manifest_dict.get("goal", "")

    # Round 1：内部会 `_build_r1_prompt` + `call_once_structured_with_raw_content`（见 intelligence_llm）
    purpose_rough, outline_blocks_rough, candidate_misrecognitions, r1_completion = (
        _run_round1(tokens, goal)
    )
    if on_phase_save is not None:
        on_phase_save(
            "2a_r1",
            {
                "purpose_rough": purpose_rough,
                "outline_blocks_rough": copy.deepcopy(outline_blocks_rough),
                "candidate_misrecognitions": copy.deepcopy(
                    candidate_misrecognitions
                ),
            },
        )

    # Round 2：`prepare_next_turn_messages` 接上 R1 的 assistant JSON，再 `call_turn_structured` 追加 schema
    purpose, outline_blocks, raw_corrections = _run_round2(
        tokens,
        goal,
        candidate_misrecognitions,
        r1_completion,
    )

    # 程序步骤：只读句面为 tokens[].text，不修改 tokens
    token_text_view = MappingProxyType(
        {int(t["index"]): str(t.get("text", "")) for t in tokens}
    )

    sparse_cleaned_annotations = _build_sparse_cleaned_annotations(
        token_text_view, raw_corrections
    )
    cleaned_annotations = _densify_cleaned_annotations_from_tokens(
        tokens, sparse_cleaned_annotations
    )

    manifest_dict["comprehension"] = {
        "purpose": purpose,
        "outline_blocks": outline_blocks,
        "cleaned_annotations": cleaned_annotations,
    }
    if on_phase_save is not None:
        on_phase_save(
            "2a_r2",
            {"comprehension": copy.deepcopy(manifest_dict["comprehension"])},
        )

    print(f"[2a] 完成 - 主旨: {purpose[:60]}...")
    print(f"[2a] 消歧标注(稠密): {len(cleaned_annotations)} 条")
    print(f"[2a] 分块: {len(outline_blocks)} 块")

    return manifest_dict


# ============================================================================
# Round 1: 粗理解 + ASR 误识候选
# ============================================================================

def _run_round1(
    tokens: list[dict],
    goal: str,
) -> tuple[str, list[dict], list[dict], StructuredLLMResult]:
    """Round 1: 粗理解 + ASR 误识候选

    Returns:
        (purpose_rough, outline_blocks_rough, candidate_misrecognitions, r1_completion)
    """
    # 控制台阶段标记，便于对照日志与 LLM 调用
    print("[2a-R1] 粗理解与误识候选构建")

    # 纯文本 user 主体（不含 JSON Schema 示例；示例由 intelligence_llm._build_messages 追加）
    prompt = _build_r1_prompt(tokens, goal)
    # R1 输出结构的 jsonschema，用于生成「示例 JSON」尾缀 + 校验模型返回
    schema = _get_r1_schema()

    # intelligence_llm.call_once_structured_with_raw_content：
    # - 内部 _build_messages → system=SYSTEM_PROMPT，user=prompt+JSON 示例+纪律说明
    # - _call_api 带 response_format=json_object；失败按 max_retries 重试
    # - 返回 StructuredLLMResult：含解析后 dict、assistant 原文 JSON、请求快照（供 R2 多轮前缀）
    r1_completion = call_once_structured_with_raw_content(
        prompt=prompt,  # 本模块构造的 R1 中文任务描述
        schema=schema,  # 与 prompt 对齐的字段约束
        temperature=R1_TEMPERATURE,  # 2a 文件头常量，略高以利粗分块/候选
        enable_reasoning=ENABLE_REASONING_R1,  # False 时走 chat 采样路径
    )
    # 已通过 jsonschema 校验的结构化对象（可直接按键取值）
    response = r1_completion.data

    # 必填主旨（schema required）
    purpose_rough = response["purpose_rough"]
    # 粗分块可选；缺省按空列表处理，避免下游 KeyError
    outline_blocks_rough = response.get("outline_blocks_rough", [])
    # 误识候选必填列表（可为空数组）
    candidate_misrecognitions = response.get("candidate_misrecognitions", [])

    # 日志截断显示，避免终端被长文本刷屏
    print(f"[2a-R1] 粗糙主旨: {purpose_rough[:60]}...")
    print(f"[2a-R1] 误识候选: {len(candidate_misrecognitions)} 条")

    # r1_completion 整块交给 R2 以复用 OpenAI 式 messages 前缀
    return purpose_rough, outline_blocks_rough, candidate_misrecognitions, r1_completion


def _build_r1_prompt(tokens: list[dict], goal: str) -> str:
    # 仅保留非空 text 的句行，格式 "[idx] 文本"，与 LLM 约定一致
    lines = [f"[{t['index']}] {t['text']}" for t in tokens if t.get("text", "").strip()]
    # 拼成单一大段 ASR 正文，插入 prompt 中部
    text_block = "\n".join(lines)
    # 首行：有 goal 则照抄；无则给默认剪辑意图说明
    goal_line = f"用户目标：{goal}" if goal else "用户目标：无特定目标，提取核心内容"

    # 以下为 R1 user 正文；末尾「请以 JSON…」之后 intelligence_llm 会再拼「示例 JSON + Schema 尾缀」
    return f"""{goal_line}

以下是视频 ASR 转写文本（格式：[index] 文字内容）：

{text_block}

请完成以下任务：
1. 用 1-2 句话描述内容的核心论点与叙事意图（purpose_rough）：
   - 重点是“说话者要论证/传达什么”，而非仅描述“讲了什么话题”
   - 若内容有明显的先后论述顺序，需适度体现（如“先…再…最后…”）
2. 将内容按主题/段落初步划分为若干块（outline_blocks_rough）：**每个元素**必须是 JSON 对象，且**仅**包含三个键——整数 `start_index`、整数 `end_index`、字符串 `topic`（表示该块覆盖的句级 index 闭区间与块主题）。**禁止**使用 `index_range` 或其它键名表示区间。
3. 识别可能被 ASR 误识的专有名词、人名、术语，给出候选正确形式

candidate_misrecognitions 中每条包含：
- annotation_index：发生误识的句子 index
- wrong：ASR 识别出的错误子串（必须是该句子的真实子串）
- suggestions：候选正确形式列表（通常 1 个；仅在语义上难以分辨时给 2-3 个）

请以 JSON 格式输出。"""


def _get_r1_schema() -> dict:
    """R1 的 JSON Schema：与 `_build_r1_prompt` 任务字段一一对应；供 intelligence_llm 生成示例尾缀 + 校验。"""
    return {
        "type": "object",  # 根必须为 JSON 对象（json_object 模式）
        "properties": {  # 允许的顶层键集合
            "purpose_rough": {  # 任务 1：粗糙主旨
                "type": "string",
                "description": "内容核心主旨（粗糙版，1-2句话）",
            },
            "outline_blocks_rough": {  # 任务 2：粗分块（数组元素形状见 items）
                "type": "array",
                "description": "初步内容分块",
                "items": {  # 每一块：闭区间 [start_index,end_index] + 主题词 topic
                    "type": "object",
                    "properties": {
                        "start_index": {"type": "integer"},  # 与 tokens 的 index 同坐标系
                        "end_index": {"type": "integer"},
                        "topic": {"type": "string", "description": "块主题词"},
                    },
                    "required": ["start_index", "end_index", "topic"],  # 缺任一即校验失败
                },
            },
            "candidate_misrecognitions": {  # 任务 3：误识候选（可为空数组）
                "type": "array",
                "description": "ASR 可能误识的词条候选列表",
                "items": {  # 每条候选绑定到句级 index
                    "type": "object",
                    "properties": {
                        "annotation_index": {
                            "type": "integer",
                            "description": "发生误识的句子 index",
                        },
                        "wrong": {
                            "type": "string",
                            "description": "ASR 识别出的错误子串",
                        },
                        "suggestions": {  # R2 纠错时 new 必须来自此列表
                            "type": "array",
                            "description": "候选正确形式（通常 1 个）",
                            "items": {"type": "string"},  # 字符串建议列表
                        },
                    },
                    "required": ["annotation_index", "wrong", "suggestions"],
                },
            },
        },
        "required": ["purpose_rough", "candidate_misrecognitions"],  # 粗分块非 required，允许模型省略
    }


# ============================================================================
# Round 2: 精化主旨 + 分块 + 纠错列表
# ============================================================================

def _run_round2(
    tokens: list[dict],
    goal: str,
    candidate_misrecognitions: list[dict],
    r1_completion: StructuredLLMResult,
) -> tuple[str, list[dict], list[dict]]:
    """Round 2: 精化主旨 + 分块 + 纠错列表（多轮第二跳）

    Returns:
        (purpose, outline_blocks, raw_corrections)
        raw_corrections: [{"index": int, "old": str, "nth": int, "new": str}, ...]
    """
    print("[2a-R2] 精化理解与纠错确定")

    # R2 的「新增 user」纯文本：承接 R1 JSON，不在此重复粘贴 R1 全文字段
    r2_user = _build_r2_user_followup(tokens, goal, candidate_misrecognitions)
    # R2 与 R1 不同 schema（精化 purpose、outline_blocks、corrections）
    schema = _get_r2_schema()

    # intelligence_llm.prepare_next_turn_messages：
    # - 拷贝 R1 请求快照 + 追加 assistant=R1 原始 JSON 字符串 + 追加 user=r2_user
    # - 供 API 形成「同一对话前缀」以利缓存与多轮契约
    r2_messages = prepare_next_turn_messages(
        r1_completion.request_messages,  # R1 的 system+user（含 R1 任务与 JSON 尾缀）
        assistant_content=r1_completion.assistant_content,  # R1 模型返回的 JSON 原文
        next_user_content=r2_user,  # 本函数构造的第二轮 user 任务说明
    )

    # intelligence_llm.call_turn_structured：
    # - 在**最后一条** user 末尾再拼 R2 的 JSON 示例 + Schema 说明（augment_last_user=True）
    # - 其余与单轮类似：json_object、校验、重试
    response = call_turn_structured(
        r2_messages,  # 已含 R1 全前缀 + assistant + 第二轮 user
        schema,  # R2 schema；位置参数与 intelligence_llm 签名一致
        temperature=R2_TEMPERATURE,  # 低于 R1，偏确定性的纠错/分块
        enable_reasoning=ENABLE_REASONING_R2,
    )

    # R2 精化主旨（schema required）
    purpose = response["purpose"]
    # 精化分块列表（required；get 防御性一致化）
    outline_blocks = response.get("outline_blocks", [])
    # 稀疏纠错规则；可为空数组表示模型未发现可确认误识
    raw_corrections = response.get("corrections", [])

    print(f"[2a-R2] 精化主旨: {purpose[:60]}...")
    print(f"[2a-R2] 分块: {len(outline_blocks)} 块，纠错: {len(raw_corrections)} 条")

    return purpose, outline_blocks, raw_corrections


def _build_r2_user_followup(
    tokens: list[dict],
    goal: str,
    candidate_misrecognitions: list[dict],
) -> str:
    """R2 仅追加 user：主旨/粗分块/候选列表以 R1 assistant JSON 为准，此处只给任务与句面锚点。"""
    # 与 R1 相同的首行目标语，便于模型在第二轮仍对齐用户意图
    goal_line = f"用户目标：{goal}" if goal else "用户目标：无特定目标，提取核心内容"

    # R2 仍展示**原始** tokens 行（非 cleaned），供核对 old/nth 锚点；含空 text 句以防索引错位
    lines = [f"[{t['index']}] {t['text']}" for t in tokens]
    text_block = "\n".join(lines)

    # index → 原句文本，用于展开 candidate_misrecognitions 对照段
    token_map = {t["index"]: t["text"] for t in tokens}

    if candidate_misrecognitions:
        # 逐条展开「错词 + 候选」与当前句文本，降低模型抄错句的风险
        cand_lines = []
        for c in candidate_misrecognitions:
            ann_idx = c["annotation_index"]  # 句级 index，与 tokens 对齐
            original = token_map.get(ann_idx, "（未找到原句）")  # 防御：R1 若给错 index
            cand_lines.append(
                f"  - [{ann_idx}] 原句：\"{original}\"\n"
                f"    错词：\"{c['wrong']}\" → 候选：{c['suggestions']}"
            )
        # 多行拼成「核对」小节，插入 prompt 下半部
        cand_section = (
            "上一轮 JSON 中 candidate_misrecognitions 与下列原句核对（若不一致以原句为准）：\n"
            + "\n".join(cand_lines)
        )
    else:
        # 无候选时明确告知，避免模型虚构上一轮内容
        cand_section = "上一轮未输出 candidate_misrecognitions 或为空。"

    # R2 user 正文：提醒「上一轮 JSON 在 assistant」+ 全量句面 + 核对段 + 三项任务；JSON 示例仍由 intelligence_llm 追加
    return f"""{goal_line}

你上一轮已在 assistant 消息中输出 JSON（含 purpose_rough、outline_blocks_rough、candidate_misrecognitions）。请在保持与上一轮一致的前提下，完成本回合输出（字段名与语义见下方 JSON 格式说明）。

以下是完整标注序列（格式：[index] 文字内容），用于核对纠错锚点：

{text_block}

{cand_section}

请完成以下任务：

1. 精化核心论点与叙事意图（purpose），比粗糙版更准确、完整
   - 聚焦“说话者要论证/传达什么”，以及贯穿全文的核心结论
   - 叙事结构细节（先讲什么/再讲什么）由 outline_blocks 承载，purpose 无需重复

2. 将内容按主题/段落划分为若干块（outline_blocks）：**每个元素**必须包含整数 `start_index`、整数 `end_index`、字符串 `summary`（与下方 JSON 示例键名一致）。**禁止**用其它字段名表示 index 区间。

3. 对上一轮 candidate_misrecognitions 中确认存在误识的条目，给出唯一纠错规则（corrections）
   每条纠错包含：
   - index：发生误识的句子 index
   - old：原文中的错误子串（必须是该句子的真实子串）
   - nth：该子串在原句中从左到右第几次出现（1-based，第一次出现填 1）
   - new：替换为的正确内容（必须来自上一轮候选，不得自造新词）

   规则：
   - 若无法确定唯一正确词，宁可不输出该条
   - old 和 nth 必须能在该句子中精确定位
   - 同一句子内多条纠错，在原文中不得存在字符重叠

请以 JSON 格式输出。"""


def _get_r2_schema() -> dict:
    """R2 的 JSON Schema：与 `_build_r2_user_followup` 三项任务对齐；多轮第二跳追加校验。"""
    return {
        "type": "object",  # 根对象；与 R1 相同 json_object 契约
        "properties": {
            "purpose": {  # 任务 1：精化主旨
                "type": "string",
                "description": "精化后的主旨描述",
            },
            "outline_blocks": {  # 任务 2：精化分块（与 R1 粗块独立，可重划边界）
                "type": "array",
                "description": "内容分块列表",
                "items": {
                    "type": "object",
                    "properties": {
                        "start_index": {"type": "integer"},
                        "end_index": {"type": "integer"},
                        "summary": {"type": "string", "description": "块内容摘要"},
                    },
                    "required": ["start_index", "end_index", "summary"],
                },
            },
            "corrections": {  # 任务 3：可执行纠错表（程序 `_apply_corrections` 消费）
                "type": "array",
                "description": "纠错规则列表（仅包含确认的误识，不确定时宁可不输出）",
                "items": {
                    "type": "object",
                    "properties": {
                        "index": {
                            "type": "integer",
                            "description": "发生误识的句子 index",
                        },
                        "old": {
                            "type": "string",
                            "description": "原文中的错误子串",
                        },
                        "nth": {
                            "type": "integer",
                            "description": "该子串在原句中第几次出现（1-based）",
                        },
                        "new": {
                            "type": "string",
                            "description": "替换为的正确内容（来自 R1 候选）",
                        },
                    },
                    "required": ["index", "old", "nth", "new"],
                },
            },
        },
        "required": ["purpose", "outline_blocks", "corrections"],  # 三块全必填；corrections 可为 []
    }


# ============================================================================
# 程序步骤：纠错算法
# ============================================================================

def _apply_corrections_to_sentence(
    sentence: str,
    corrections: list[Correction],
) -> str:
    """
    在原文上一次性应用所有纠错规则，避免逐条替换导致的位置漂移与幽灵匹配。

    算法分两阶段：
      1. 锚定 — 在不可变的原文上，为每条规则定位字符区间 (start, end)
      2. 拼接 — 按区间从左到右，用「原文片段 + 替换词」交替拼出结果

    Args:
        sentence:    原始 ASR 转录文本
        corrections: 纠错规则列表，每条包含 nth（1-based）/ old / new

    Returns:
        纠正后的完整字符串

    Raises:
        ValueError: 找不到第 nth 个匹配，或多条规则的区间存在重叠
    """
    # ── 阶段 1：在原文上锚定每条规则的字符区间 ──

    anchored: list[tuple[int, int, str]] = []  # [(start, end, replacement), ...]

    for c in corrections:
        start = 0
        idx = -1
        for i in range(c.nth):
            idx = sentence.find(c.old, start)
            if idx == -1:
                raise ValueError(
                    f"原文中找不到第 {c.nth} 个 '{c.old}'（仅找到 {i} 个）"
                )
            if i < c.nth - 1:
                start = idx + 1
        anchored.append((idx, idx + len(c.old), c.new))

    # ── 冲突检测：按 start 排序后检查区间是否重叠 ──

    anchored.sort(key=lambda t: t[0])

    for i in range(1, len(anchored)):
        prev_start, prev_end, prev_new = anchored[i - 1]
        curr_start, curr_end, curr_new = anchored[i]
        if curr_start < prev_end:
            raise ValueError(
                f"纠错区间重叠: "
                f"({prev_start}:{prev_end})→'{prev_new}' 与 "
                f"({curr_start}:{curr_end})→'{curr_new}'"
            )

    # ── 阶段 2：一次性拼接 ──
    #
    #   原文:  ───[保留]───[替换1]───[保留]───[替换2]───[保留]───
    #   cursor 从 0 开始，每次先拼 cursor→start 的原文片段，再拼替换词

    parts: list[str] = []
    cursor = 0

    for start, end, replacement in anchored:
        parts.append(sentence[cursor:start])
        parts.append(replacement)
        cursor = end

    parts.append(sentence[cursor:])

    return "".join(parts)


def _build_sparse_cleaned_annotations(
    token_text_view: dict[int, str],
    raw_corrections: list[dict],
) -> list[dict]:
    """按 index 分组 corrections，逐句应用，生成稀疏 cleaned_annotations。

    Args:
        token_text_view: 只读的 index -> 句面原文（来自 ``tokens[].text``）
        raw_corrections: R2 输出的纠错列表
                         [{"index": int, "old": str, "nth": int, "new": str}, ...]

    Returns:
        稀疏 cleaned_annotations [{"annotation_index": int, "cleaned_content": str}, ...]
        仅包含内容发生变化的条目。
    """
    # 按 annotation index 分组
    grouped: defaultdict[int, list[Correction]] = defaultdict(list)
    for raw in raw_corrections:
        grouped[raw["index"]].append(
            Correction(nth=raw["nth"], old=raw["old"], new=raw["new"])
        )

    cleaned: list[dict] = []
    for ann_index, corrs in grouped.items():
        original = token_text_view.get(ann_index)
        if original is None:
            print(f"[2a] 警告: corrections 中 index={ann_index} 不存在于 tokens，跳过")
            continue
        try:
            result = _apply_corrections_to_sentence(original, corrs)
        except ValueError as e:
            print(f"[2a] 警告: index={ann_index} 纠错失败，跳过该句 ({e})")
            continue

        if result != original:
            cleaned.append({"annotation_index": ann_index, "cleaned_content": result})

    return cleaned


def _densify_cleaned_annotations_from_tokens(
    tokens: list[dict],
    sparse_cleaned_annotations: list[dict],
) -> list[dict]:
    """将稀疏 cleaned_annotations 回填为稠密全量序列（与 ``tokens`` 等长）。

    规则：
    - 若某 index 在 sparse_cleaned_annotations 中，使用对应 cleaned_content。
    - 否则回填 ``tokens[i].text``。
    - ``annotation_index`` 字段名沿用 MVP 契约，与 ``tokens[].index`` 对齐。
    """
    sparse_map = {
        int(item["annotation_index"]): item["cleaned_content"]
        for item in sparse_cleaned_annotations
    }

    dense: list[dict] = []
    for i, tok in enumerate(tokens):
        ann_index = int(tok["index"])
        if ann_index != i:
            raise ValueError(
                f"tokens index 不连续，无法构造稠密 cleaned_annotations: "
                f"位置{i} 实际 index={ann_index}"
            )
        dense.append({
            "annotation_index": ann_index,
            "cleaned_content": sparse_map.get(ann_index, str(tok.get("text", ""))),
        })

    return dense
