"""Layer 2 / 2a 理解子阶段

## 职责
将 Layer 1 的原始 ASR 标注序列转化为语义理解结果，为 2b 决策提供基础。
采用"理解先行、纠错后行"策略，分两轮 LLM 调用完成。

## 两轮调用逻辑
- Round 1（粗理解）：快速扫描全文，提炼粗糙主旨，识别 ASR 可能误识的专有名词/术语
- Round 2（精化消歧）：基于 R1 的符号表候选，对原始标注进行消歧，精化主旨，生成分块

## 输入 Schema
manifest_dict = {
    "annotations": [            # 来自 Layer 1
        {
            "index": int,       # 全局唯一序号（0-based）
            "type": str,        # "speech" | "silence"
            "content": str,     # 转写文字（silence 时为空）
            ...
        }
    ],
    "goal": str,                # 用户指定的分析目标
    "raw_text": str,            # 完整 ASR 原文
}

## 输出 Schema
manifest_dict["comprehension"] = {
    "purpose": str,             # 精化后的主旨描述
    "symbol_table": [           # 符号表（ASR 误识形式 → 正确形式）
        {
            "term": str,            # 正确形式（如"张伟"）
            "raw_form": str,        # ASR 可能的误识形式（如"章维"）
            "category": str,        # "person" | "term" | "entity" | "other"
            "first_occurrence": int # 首次出现的 annotation index
        }
    ],
    "cleaned_annotations": [    # 消歧后的标注（不修改原始 content）
        {
            "annotation_index": int,    # 对应 annotations[].index
            "cleaned_content": str      # 消歧后的文本
        }
    ],
    "outline_blocks": [         # 内容分块（按主题/段落划分）
        {
            "start_index": int,     # 块起始 annotation index
            "end_index": int,       # 块结束 annotation index（含）
            "summary": str          # 块内容摘要
        }
    ]
}

## 注意
- cleaned_annotations 只覆盖有消歧需要的条目，不需要消歧的条目不出现在列表中
- 2b 决策层消费 cleaned_annotations 而非原始 annotations[].content
- symbol_table 仅供审计，不向 2b/2c 传递
"""

from autosmartcut.intelligence_llm import call_llm_structured


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
# 主入口
# ============================================================================

def run_2a_comprehension(manifest_dict: dict) -> dict:
    """2a 理解子阶段：固定两轮 LLM 调用

    Args:
        manifest_dict: 包含 annotations、goal、raw_text 的工作数据

    Returns:
        追加了 comprehension 字段的 manifest_dict
    """
    print("[2a] 理解子阶段开始")

    annotations = manifest_dict["annotations"]
    goal = manifest_dict.get("goal", "")

    # Round 1: 粗理解 + 符号表候选
    purpose_rough, symbol_table_candidates = _run_round1(annotations, goal)

    # Round 2: 精化主旨 + 消歧标注 + 理解分块
    comprehension = _run_round2(annotations, goal, purpose_rough, symbol_table_candidates)

    manifest_dict["comprehension"] = comprehension

    print(f"[2a] 完成 - 主旨: {comprehension['purpose'][:60]}...")
    print(f"[2a] 符号表: {len(comprehension['symbol_table'])} 条")
    print(f"[2a] 消歧标注: {len(comprehension['cleaned_annotations'])} 条")
    print(f"[2a] 分块: {len(comprehension['outline_blocks'])} 块")

    return manifest_dict


# ============================================================================
# Round 1: 粗理解 + 符号表候选
# ============================================================================

def _run_round1(annotations: list[dict], goal: str) -> tuple[str, list[dict]]:
    """Round 1: 粗理解 + 符号表候选

    任务：
    1. 快速扫描全文，提炼粗糙主旨
    2. 识别 ASR 可能误识的专有名词/人名/术语

    Args:
        annotations: Layer 1 标注列表
        goal: 用户分析目标

    Returns:
        (purpose_rough, symbol_table_candidates)
    """
    print("[2a-R1] 粗理解与符号表构建")

    prompt = _build_r1_prompt(annotations, goal)
    schema = _get_r1_schema()

    response = call_llm_structured(
        prompt=prompt,
        schema=schema,
        temperature=R1_TEMPERATURE,
        enable_reasoning=ENABLE_REASONING
    )

    purpose_rough = response["purpose_rough"]
    symbol_table_candidates = response.get("symbol_table_candidates", [])

    print(f"[2a-R1] 粗糙主旨: {purpose_rough[:60]}...")
    print(f"[2a-R1] 符号表候选: {len(symbol_table_candidates)} 条")

    return purpose_rough, symbol_table_candidates


def _build_r1_prompt(annotations: list[dict], goal: str) -> str:
    """构造 Round 1 的 Prompt

    输入元素：
    - 用户目标
    - 所有 speech 标注的原始文本（按 index 顺序）

    任务：
    1. 粗糙主旨概括
    2. 识别 ASR 可能误识的专有名词
    """
    # 取所有条目，按 index 顺序排列（当前 Layer1 为 speech-only）
    speech_lines = [
        f"[{ann['index']}] {ann['content']}"
        for ann in annotations
        if ann.get("content", "").strip()
    ]
    speech_text = "\n".join(speech_lines)

    goal_line = f"用户目标：{goal}" if goal else "用户目标：无特定目标，提取核心内容"

    return f"""{goal_line}

以下是视频 ASR 转写文本（格式：[index] 文字内容）：

{speech_text}

请完成以下任务：
1. 用 1-2 句话概括内容的核心主旨（purpose_rough）
2. 识别可能被 ASR 误识的专有名词、人名、术语，输出符号表候选列表

请以 JSON 格式输出。"""


def _get_r1_schema() -> dict:
    """Round 1 的输出 JSON Schema"""
    return {
        "type": "object",
        "properties": {
            "purpose_rough": {
                "type": "string",
                "description": "内容核心主旨（粗糙版，1-2句话）"
            },
            "symbol_table_candidates": {
                "type": "array",
                "description": "ASR 可能误识的专有名词候选列表",
                "items": {
                    "type": "object",
                    "properties": {
                        "term": {"type": "string", "description": "正确形式"},
                        "raw_form": {"type": "string", "description": "ASR 可能的误识形式"},
                        "category": {"type": "string", "description": "person|term|entity|other"},
                        "first_occurrence": {"type": "integer", "description": "首次出现的 annotation index"}
                    },
                    "required": ["term", "raw_form", "category", "first_occurrence"]
                }
            }
        },
        "required": ["purpose_rough", "symbol_table_candidates"]
    }


# ============================================================================
# Round 2: 精化主旨 + 消歧标注 + 理解分块
# ============================================================================

def _run_round2(
    annotations: list[dict],
    goal: str,
    purpose_rough: str,
    symbol_table_candidates: list[dict]
) -> dict:
    """Round 2: 精化主旨 + 消歧标注 + 理解分块

    任务：
    1. 基于符号表候选，对原始标注进行消歧
    2. 精化主旨描述
    3. 将内容按主题/段落分块

    Args:
        annotations: Layer 1 标注列表
        goal: 用户分析目标
        purpose_rough: R1 产出的粗糙主旨
        symbol_table_candidates: R1 产出的符号表候选

    Returns:
        comprehension dict（包含 purpose, symbol_table, cleaned_annotations, outline_blocks）
    """
    print("[2a-R2] 精化理解与消歧")

    prompt = _build_r2_prompt(annotations, goal, purpose_rough, symbol_table_candidates)
    schema = _get_r2_schema()

    response = call_llm_structured(
        prompt=prompt,
        schema=schema,
        temperature=R2_TEMPERATURE,
        enable_reasoning=ENABLE_REASONING
    )

    # 组装 comprehension（symbol_table 来自 R1 候选，经 R2 确认）
    comprehension = {
        "purpose": response["purpose"],
        "symbol_table": symbol_table_candidates,   # R1 产出，固化
        "cleaned_annotations": response.get("cleaned_annotations", []),
        "outline_blocks": response.get("outline_blocks", [])
    }

    return comprehension


def _build_r2_prompt(
    annotations: list[dict],
    goal: str,
    purpose_rough: str,
    symbol_table_candidates: list[dict]
) -> str:
    """构造 Round 2 的 Prompt

    输入元素：
    - 用户目标
    - 原始标注文本
    - R1 产出的粗糙主旨
    - R1 产出的符号表候选

    任务：
    1. 精化主旨
    2. 对每条 speech 标注生成消歧文本（仅有误识时才输出）
    3. 将内容按主题/段落分块
    """
    # 所有标注按 index 顺序（speech-only）
    all_lines = []
    for ann in annotations:
        all_lines.append(f"[{ann['index']}] {ann.get('content', '')}")
    all_text = "\n".join(all_lines)

    # 符号表候选
    if symbol_table_candidates:
        symbol_lines = [
            f"  - {c['raw_form']} → {c['term']}（{c['category']}）"
            for c in symbol_table_candidates
        ]
        symbol_section = "已识别的 ASR 误识候选：\n" + "\n".join(symbol_lines)
    else:
        symbol_section = "未识别到 ASR 误识候选。"

    goal_line = f"用户目标：{goal}" if goal else "用户目标：无特定目标，提取核心内容"

    return f"""{goal_line}

粗糙主旨（供参考）：{purpose_rough}

{symbol_section}

以下是完整标注序列（格式：[index] 文字内容）：

{all_text}

请完成以下任务：
1. 精化主旨描述（purpose），比粗糙版更准确、完整
2. 对有 ASR 误识的 speech 条目生成消歧文本（cleaned_annotations），无误识的条目不需要出现
3. 将 speech 内容按主题/段落划分为若干块（outline_blocks），每块给出 index 范围和摘要

请以 JSON 格式输出。"""


def _get_r2_schema() -> dict:
    """Round 2 的输出 JSON Schema"""
    return {
        "type": "object",
        "properties": {
            "purpose": {
                "type": "string",
                "description": "精化后的主旨描述"
            },
            "cleaned_annotations": {
                "type": "array",
                "description": "消歧后的标注列表（只包含有误识的条目）",
                "items": {
                    "type": "object",
                    "properties": {
                        "annotation_index": {"type": "integer", "description": "对应 annotations[].index"},
                        "cleaned_content": {"type": "string", "description": "消歧后的文本"}
                    },
                    "required": ["annotation_index", "cleaned_content"]
                }
            },
            "outline_blocks": {
                "type": "array",
                "description": "内容分块列表",
                "items": {
                    "type": "object",
                    "properties": {
                        "start_index": {"type": "integer", "description": "块起始 annotation index"},
                        "end_index": {"type": "integer", "description": "块结束 annotation index（含）"},
                        "summary": {"type": "string", "description": "块内容摘要"}
                    },
                    "required": ["start_index", "end_index", "summary"]
                }
            }
        },
        "required": ["purpose", "cleaned_annotations", "outline_blocks"]
    }
