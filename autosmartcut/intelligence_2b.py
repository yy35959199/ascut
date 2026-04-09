"""Layer 2 / 2b 决策子阶段

## 职责
基于 2a 的理解结果，对每个 speech 标注做出保留/删除决策，生成 keep_mask。
这是智能层的核心输出，直接决定最终视频的内容。

## 决策逻辑（待 LLM 实现）
- 输入：消歧后的文本 + 主旨 + 分块信息 + 用户目标
- 任务：判断每个 speech 片段是否与目标相关，是否包含有价值内容
- 输出：对每个 speech 标注输出 keep=true/false
- 静音：keep=null（不由 LLM 决策，由执行层规则推导）

## 输入 Schema
manifest_dict = {
    "annotations": [            # 来自 Layer 1
        {
            "index": int,
            "type": str,        # "speech" | "silence"
            "content": str,
            ...
        }
    ],
    "comprehension": {          # 来自 2a
        "purpose": str,             # 精化主旨
        "cleaned_annotations": [    # 消歧文本
            {
                "annotation_index": int,
                "cleaned_content": str
            }
        ],
        "outline_blocks": [         # 内容分块
            {
                "start_index": int,
                "end_index": int,
                "summary": str
            }
        ]
    },
    "goal": str                 # 用户目标
}

## 输出 Schema
manifest_dict["keep_mask"] = [
    {
        "index": int,           # 对应 annotations[].index
        "keep": bool | None     # True=保留, False=删除, None=静音
    },
    ...
]

## 注意
- keep_mask 长度必须等于 annotations 长度
- keep_mask 与 annotations 通过 index 一一对应
- speech 条目：keep 为 bool（LLM 决策）
- silence 条目：keep 为 null（执行层规则推导）
"""

from autosmartcut.intelligence_llm import call_llm_structured


# ============================================================================
# 模型参数（便于调试）
# ============================================================================

# 2b 使用普通 chat 模型（文本分类任务，不需要 reasoner）
ENABLE_REASONING = False

# 温度：偏低，决策需要确定性和稳定性
TEMPERATURE = 0.2


# ============================================================================
# 主入口
# ============================================================================

def run_2b_decision(manifest_dict: dict) -> dict:
    """2b 决策子阶段：固定一次 LLM 调用，输出 keep_mask

    Args:
        manifest_dict: 包含 annotations、comprehension、goal 的工作数据

    Returns:
        追加了 keep_mask 字段的 manifest_dict
    """
    print("[2b] 决策子阶段开始")

    annotations = manifest_dict["annotations"]
    comprehension = manifest_dict.get("comprehension", {})
    goal = manifest_dict.get("goal", "")

    # 调用 LLM 生成 keep_mask
    keep_mask = _generate_keep_mask(annotations, comprehension, goal)

    # 验证 keep_mask 格式
    if len(keep_mask) != len(annotations):
        raise ValueError(
            f"keep_mask 长度不匹配: {len(keep_mask)} != {len(annotations)}"
        )

    # 验证 index 对齐
    for i, entry in enumerate(keep_mask):
        if entry["index"] != annotations[i]["index"]:
            raise ValueError(
                f"keep_mask[{i}] 的 index 不对齐: "
                f"期望 {annotations[i]['index']}, 实际 {entry['index']}"
            )

    manifest_dict["keep_mask"] = keep_mask

    keep_count = sum(1 for e in keep_mask if e["keep"] is True)
    cut_count = sum(1 for e in keep_mask if e["keep"] is False)

    print(f"[2b] 决策完成")
    print(f"[2b] 保留: {keep_count} | 删除: {cut_count}")

    return manifest_dict


# ============================================================================
# LLM 决策生成
# ============================================================================

def _generate_keep_mask(
    annotations: list[dict],
    comprehension: dict,
    goal: str
) -> list[dict]:
    """调用 LLM 生成 keep_mask

    Args:
        annotations: Layer 1 标注列表
        comprehension: 2a 理解结果
        goal: 用户目标

    Returns:
        keep_mask 列表 [{"index": int, "keep": bool|None}, ...]
    """
    print("[2b] 调用 LLM 生成决策")

    prompt = _build_prompt(annotations, comprehension, goal)
    schema = _get_schema()

    response = call_llm_structured(
        prompt=prompt,
        schema=schema,
        temperature=TEMPERATURE,
        enable_reasoning=ENABLE_REASONING
    )

    # 解析 LLM 输出
    llm_decisions = response.get("decisions", [])

    # 构造完整 keep_mask（speech-only）
    keep_mask = _build_keep_mask_from_llm_decisions(annotations, llm_decisions)

    return keep_mask


def _build_prompt(
    annotations: list[dict],
    comprehension: dict,
    goal: str
) -> str:
    """构造 2b 决策的 Prompt

    输入元素：
    - 用户目标
    - 2a 的主旨
    - 2a 的消歧标注（优先使用）
    - 2a 的分块信息
    - 所有 speech 标注

    任务：
    对每个 speech 标注判断是否保留
    """
    purpose = comprehension.get("purpose", "")
    cleaned_map = {
        c["annotation_index"]: c["cleaned_content"]
        for c in comprehension.get("cleaned_annotations", [])
    }
    outline_blocks = comprehension.get("outline_blocks", [])

    # 构造分块摘要
    if outline_blocks:
        block_lines = [
            f"  块 {i+1} [index {b['start_index']}-{b['end_index']}]: {b['summary']}"
            for i, b in enumerate(outline_blocks)
        ]
        blocks_section = "内容分块：\n" + "\n".join(block_lines)
    else:
        blocks_section = "内容分块：无"

    # 构造标注列表（使用消歧文本）
    speech_lines = []
    for ann in annotations:
        idx = ann["index"]
        # 优先使用消歧文本
        text = cleaned_map.get(idx, ann.get("content", ""))
        speech_lines.append(f"[{idx}] {text}")

    speech_text = "\n".join(speech_lines)

    goal_line = f"用户目标：{goal}" if goal else "用户目标：无特定目标，提取核心内容"

    return f"""{goal_line}

内容主旨：{purpose}

{blocks_section}

以下是所有 speech 标注（格式：[index] 文字内容）：

{speech_text}

请对每个 speech 标注做出保留/删除决策：
- 保留（keep=true）：与用户目标相关，包含有价值内容
- 删除（keep=false）：无关内容、口头语、重复、语气词等

注意：
1. 你需要对列表中的每条内容都做决策
2. 输出格式为 decisions 数组，每条包含 index 和 keep 字段
3. 必须覆盖所有条目

请以 JSON 格式输出。"""


def _get_schema() -> dict:
    """2b 决策的输出 JSON Schema"""
    return {
        "type": "object",
        "properties": {
            "decisions": {
                "type": "array",
                "description": "对每个 speech 标注的决策列表",
                "items": {
                    "type": "object",
                    "properties": {
                        "index": {"type": "integer", "description": "对应 annotations[].index"},
                        "keep": {"type": "boolean", "description": "true=保留, false=删除"}
                    },
                    "required": ["index", "keep"]
                }
            }
        },
        "required": ["decisions"]
    }


def _build_keep_mask_from_llm_decisions(
    annotations: list[dict],
    llm_decisions: list[dict]
) -> list[dict]:
    """将 LLM 决策扩展为完整 keep_mask（speech-only）

    Args:
        annotations: 完整标注列表（speech-only）
        llm_decisions: LLM 输出决策 [{"index": int, "keep": bool}, ...]

    Returns:
        完整 keep_mask [{"index": int, "keep": bool}, ...]
    """
    # 构造 LLM 决策的 index → keep 映射
    decision_map = {d["index"]: d["keep"] for d in llm_decisions}

    # 构造完整 keep_mask
    keep_mask = []
    for ann in annotations:
        idx = ann["index"]
        if idx not in decision_map:
            # LLM 未覆盖此条目，默认保留
            print(f"[2b] 警告: LLM 未覆盖 index {idx}，默认保留")
            keep = True
        else:
            keep = bool(decision_map[idx])
        keep_mask.append({"index": idx, "keep": keep})

    return keep_mask
