"""Layer 2 / 2a 理解子阶段（MVP 契约见 doc/intelligence-layer2-mvp.md §5）

## 流程
1. **tokens**：`build_layer2_input_document({"source", "annotations"})["tokens"]`，
   每项仅 `index` + `text`，不含 t_start/t_end/gap_after 等执行层字段。
2. **R1（LLM，仅内存）**：`purpose_rough`、`outline_blocks_rough`、
   `candidate_misrecognitions`。
3. **R2（LLM，仅内存）**：`purpose`、`outline_blocks`、`corrections`
   （唯一替换列表，nth 1-based）。
4. **程序**：按 `corrections` 生成稀疏 `cleaned_annotations[]`，
   **不**改写 `annotations[].content`（Append-only）。

LLM 调用封装：`autosmartcut.intelligence_llm.call_llm_structured`。

## 输入（manifest 片段）
- `annotations[]`：句级语音（index、t_start、t_end、content、gap_after 等）。
- `goal`：可选。
- `source`：可选，传入 `build_layer2_input_document`。

## 输出（写入 manifest）
manifest_dict["comprehension"] = {
    "purpose": str,
    "outline_blocks": [{"start_index", "end_index", "summary"}, ...],
    "cleaned_annotations": [{"annotation_index", "cleaned_content"}, ...],  # 稀疏；程序生成
}

不持久化 R1/R2 中间结构（candidate_misrecognitions、corrections、outline_blocks_rough）；
不写入 symbol_table。

## 注意
- 2b 对未出现在 cleaned_annotations 的 index 回退使用 `annotations[].content`。
"""

from collections import defaultdict
from dataclasses import dataclass
from types import MappingProxyType

from autosmartcut.intelligence_llm import call_llm_structured
from autosmartcut.perception import build_layer2_input_document


# ============================================================================
# 模型参数（便于调试）
# ============================================================================

# 2a 两轮调用均使用普通 chat 模型（文本理解任务，不需要 reasoner）
ENABLE_REASONING = False

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

def run_2a_comprehension(manifest_dict: dict) -> dict:
    """2a 理解子阶段：两轮 LLM + 一次程序替换

    Args:
        manifest_dict: 包含 annotations、goal、source 的工作数据

    Returns:
        追加了 comprehension 字段的 manifest_dict
    """
    print("[2a] 理解子阶段开始")

    annotations = manifest_dict["annotations"]
    goal = manifest_dict.get("goal", "")

    # 构造 tokens（仅 index + text，不含时间戳等执行层字段）
    layer2_doc = build_layer2_input_document({
        "source": manifest_dict.get("source", ""),
        "annotations": annotations,
    })
    tokens = layer2_doc["tokens"]

    # Round 1: 粗理解 + ASR 误识候选
    purpose_rough, outline_blocks_rough, candidate_misrecognitions = _run_round1(
        tokens, goal
    )

    # Round 2: 精化主旨 + 分块 + 纠错列表（中间态，不持久化）
    purpose, outline_blocks, raw_corrections = _run_round2(
        tokens, goal, purpose_rough, outline_blocks_rough, candidate_misrecognitions
    )

    # 程序步骤使用只读视图，防止误改上游原文。
    # key=index, value=原始 content（immutable string）
    annotation_content_view = MappingProxyType(
        {ann["index"]: ann.get("content", "") for ann in annotations}
    )

    # 程序步骤：按 corrections 在原文上锚定并一次性替换，生成稀疏 cleaned_annotations
    cleaned_annotations = _build_cleaned_annotations(
        annotation_content_view, raw_corrections
    )

    manifest_dict["comprehension"] = {
        "purpose": purpose,
        "outline_blocks": outline_blocks,
        "cleaned_annotations": cleaned_annotations,
    }

    print(f"[2a] 完成 - 主旨: {purpose[:60]}...")
    print(f"[2a] 消歧标注: {len(cleaned_annotations)} 条")
    print(f"[2a] 分块: {len(outline_blocks)} 块")

    return manifest_dict


# ============================================================================
# Round 1: 粗理解 + ASR 误识候选
# ============================================================================

def _run_round1(
    tokens: list[dict],
    goal: str,
) -> tuple[str, list[dict], list[dict]]:
    """Round 1: 粗理解 + ASR 误识候选

    Returns:
        (purpose_rough, outline_blocks_rough, candidate_misrecognitions)
    """
    print("[2a-R1] 粗理解与误识候选构建")

    prompt = _build_r1_prompt(tokens, goal)
    schema = _get_r1_schema()

    response = call_llm_structured(
        prompt=prompt,
        schema=schema,
        temperature=R1_TEMPERATURE,
        enable_reasoning=ENABLE_REASONING,
    )

    purpose_rough = response["purpose_rough"]
    outline_blocks_rough = response.get("outline_blocks_rough", [])
    candidate_misrecognitions = response.get("candidate_misrecognitions", [])

    print(f"[2a-R1] 粗糙主旨: {purpose_rough[:60]}...")
    print(f"[2a-R1] 误识候选: {len(candidate_misrecognitions)} 条")

    return purpose_rough, outline_blocks_rough, candidate_misrecognitions


def _build_r1_prompt(tokens: list[dict], goal: str) -> str:
    lines = [f"[{t['index']}] {t['text']}" for t in tokens if t.get("text", "").strip()]
    text_block = "\n".join(lines)
    goal_line = f"用户目标：{goal}" if goal else "用户目标：无特定目标，提取核心内容"

    return f"""{goal_line}

以下是视频 ASR 转写文本（格式：[index] 文字内容）：

{text_block}

请完成以下任务：
1. 用 1-2 句话概括内容的核心主旨（purpose_rough）
2. 将内容按主题/段落初步划分为若干块（outline_blocks_rough），每块给出 index 范围和主题词
3. 识别可能被 ASR 误识的专有名词、人名、术语，给出候选正确形式

candidate_misrecognitions 中每条包含：
- annotation_index：发生误识的句子 index
- wrong：ASR 识别出的错误子串（必须是该句子的真实子串）
- suggestions：候选正确形式列表（通常 1 个；仅在语义上难以分辨时给 2-3 个）

请以 JSON 格式输出。"""


def _get_r1_schema() -> dict:
    return {
        "type": "object",
        "properties": {
            "purpose_rough": {
                "type": "string",
                "description": "内容核心主旨（粗糙版，1-2句话）"
            },
            "outline_blocks_rough": {
                "type": "array",
                "description": "初步内容分块",
                "items": {
                    "type": "object",
                    "properties": {
                        "start_index": {"type": "integer"},
                        "end_index": {"type": "integer"},
                        "topic": {"type": "string", "description": "块主题词"}
                    },
                    "required": ["start_index", "end_index", "topic"]
                }
            },
            "candidate_misrecognitions": {
                "type": "array",
                "description": "ASR 可能误识的词条候选列表",
                "items": {
                    "type": "object",
                    "properties": {
                        "annotation_index": {
                            "type": "integer",
                            "description": "发生误识的句子 index"
                        },
                        "wrong": {
                            "type": "string",
                            "description": "ASR 识别出的错误子串"
                        },
                        "suggestions": {
                            "type": "array",
                            "description": "候选正确形式（通常 1 个）",
                            "items": {"type": "string"}
                        }
                    },
                    "required": ["annotation_index", "wrong", "suggestions"]
                }
            }
        },
        "required": ["purpose_rough", "candidate_misrecognitions"]
    }


# ============================================================================
# Round 2: 精化主旨 + 分块 + 纠错列表
# ============================================================================

def _run_round2(
    tokens: list[dict],
    goal: str,
    purpose_rough: str,
    outline_blocks_rough: list[dict],
    candidate_misrecognitions: list[dict],
) -> tuple[str, list[dict], list[dict]]:
    """Round 2: 精化主旨 + 分块 + 纠错列表

    Returns:
        (purpose, outline_blocks, raw_corrections)
        raw_corrections: [{"index": int, "old": str, "nth": int, "new": str}, ...]
    """
    print("[2a-R2] 精化理解与纠错确定")

    prompt = _build_r2_prompt(
        tokens, goal, purpose_rough, outline_blocks_rough, candidate_misrecognitions
    )
    schema = _get_r2_schema()

    response = call_llm_structured(
        prompt=prompt,
        schema=schema,
        temperature=R2_TEMPERATURE,
        enable_reasoning=ENABLE_REASONING,
    )

    purpose = response["purpose"]
    outline_blocks = response.get("outline_blocks", [])
    raw_corrections = response.get("corrections", [])

    print(f"[2a-R2] 精化主旨: {purpose[:60]}...")
    print(f"[2a-R2] 分块: {len(outline_blocks)} 块，纠错: {len(raw_corrections)} 条")

    return purpose, outline_blocks, raw_corrections


def _build_r2_prompt(
    tokens: list[dict],
    goal: str,
    purpose_rough: str,
    outline_blocks_rough: list[dict],
    candidate_misrecognitions: list[dict],
) -> str:
    lines = [f"[{t['index']}] {t['text']}" for t in tokens]
    text_block = "\n".join(lines)
    goal_line = f"用户目标：{goal}" if goal else "用户目标：无特定目标，提取核心内容"

    if candidate_misrecognitions:
        cand_lines = [
            f"  - [{c['annotation_index']}] 错词：\"{c['wrong']}\" → 候选：{c['suggestions']}"
            for c in candidate_misrecognitions
        ]
        cand_section = "R1 识别出的 ASR 误识候选：\n" + "\n".join(cand_lines)
    else:
        cand_section = "R1 未识别到 ASR 误识候选。"

    return f"""{goal_line}

粗糙主旨（供参考）：{purpose_rough}

{cand_section}

以下是完整标注序列（格式：[index] 文字内容）：

{text_block}

请完成以下任务：

1. 精化主旨描述（purpose），比粗糙版更准确、完整

2. 将内容按主题/段落划分为若干块（outline_blocks），每块给出 index 范围和摘要

3. 对 R1 候选中确认存在误识的条目，给出唯一纠错规则（corrections）
   每条纠错包含：
   - index：发生误识的句子 index
   - old：原文中的错误子串（必须是该句子的真实子串）
   - nth：该子串在原句中从左到右第几次出现（1-based，第一次出现填 1）
   - new：替换为的正确内容（必须来自 R1 候选，不得自造新词）

   规则：
   - 若无法确定唯一正确词，宁可不输出该条
   - old 和 nth 必须能在该句子中精确定位
   - 同一句子内多条纠错，在原文中不得存在字符重叠

请以 JSON 格式输出。"""


def _get_r2_schema() -> dict:
    return {
        "type": "object",
        "properties": {
            "purpose": {
                "type": "string",
                "description": "精化后的主旨描述"
            },
            "outline_blocks": {
                "type": "array",
                "description": "内容分块列表",
                "items": {
                    "type": "object",
                    "properties": {
                        "start_index": {"type": "integer"},
                        "end_index": {"type": "integer"},
                        "summary": {"type": "string", "description": "块内容摘要"}
                    },
                    "required": ["start_index", "end_index", "summary"]
                }
            },
            "corrections": {
                "type": "array",
                "description": "纠错规则列表（仅包含确认的误识，不确定时宁可不输出）",
                "items": {
                    "type": "object",
                    "properties": {
                        "index": {
                            "type": "integer",
                            "description": "发生误识的句子 index"
                        },
                        "old": {
                            "type": "string",
                            "description": "原文中的错误子串"
                        },
                        "nth": {
                            "type": "integer",
                            "description": "该子串在原句中第几次出现（1-based）"
                        },
                        "new": {
                            "type": "string",
                            "description": "替换为的正确内容（来自 R1 候选）"
                        }
                    },
                    "required": ["index", "old", "nth", "new"]
                }
            }
        },
        "required": ["purpose", "outline_blocks", "corrections"]
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


def _build_cleaned_annotations(
    annotation_content_view: dict[int, str],
    raw_corrections: list[dict],
) -> list[dict]:
    """按 index 分组 corrections，逐句应用，生成稀疏 cleaned_annotations。

    Args:
        annotation_content_view: 只读的 index->content 视图
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
        original = annotation_content_view.get(ann_index)
        if original is None:
            print(f"[2a] 警告: corrections 中 index={ann_index} 不存在于 annotations，跳过")
            continue
        try:
            result = _apply_corrections_to_sentence(original, corrs)
        except ValueError as e:
            print(f"[2a] 警告: index={ann_index} 纠错失败，跳过该句 ({e})")
            continue

        if result != original:
            cleaned.append({"annotation_index": ann_index, "cleaned_content": result})

    return cleaned
