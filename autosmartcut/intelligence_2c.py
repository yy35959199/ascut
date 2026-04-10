"""Layer 2 / 2c 审核子阶段

## 职责
验证 2b 决策的合理性，确保核心内容被覆盖、决策连贯无矛盾。
MVP 阶段为占位实现，自动生成 pass 报告；未来扩展为真实 LLM 审核。

## 审核逻辑（未来实现）
- 输入：主旨 + checklist + 当前 keep_mask
- 任务：
  1. 检查 checklist 各项是否被保留的片段覆盖
  2. 验证决策连贯性（无明显断层）
  3. 识别可能的噪声片段
- 输出：pass | fix_decision | fix_checklist

## 输入 Schema
manifest_dict = {
    "comprehension": {          # 来自 2a
        "purpose": str,
        "checklist": [          # 未来实现
            {
                "item": str,
                "priority": str,    # "must" | "optional"
                "covered": bool
            }
        ]
    },
    "keep_mask": [              # 来自 2b
        {
            "index": int,
            "keep": bool
        }
    ],
    "tokens": [ {"index": int, "text": str}, ... ]  # JSON2 句面
}

## 输出 Schema
manifest_dict["review_report"] = {
    "round": int,               # 审核轮次（MVP 固定为 0）
    "verdict": str,             # "pass" | "fix_decision" | "fix_checklist"
    "coverage_issues": [str],   # 覆盖问题列表（MVP 为空）
    "completeness_issues": [str], # 完整性问题列表（MVP 为空）
    "token_spent": int          # Token 消耗（MVP 为 0）
}

## MVP 实现
- 不调用 LLM
- 自动生成 verdict="pass" 的占位报告
- 保持 schema 与完整版一致，便于未来升级
"""


# ============================================================================
# 模型参数（未来实现时使用）
# ============================================================================

# 2c 审核层建议使用 reasoner 模型（需要逻辑推理和透明解释）
ENABLE_REASONING = True

# 温度：偏低，审核需要确定性
TEMPERATURE = 0.2


# ============================================================================
# 主入口（MVP 占位实现）
# ============================================================================

def run_2c_review(manifest_dict: dict) -> dict:
    """2c 审核子阶段：MVP 阶段自动生成 pass 占位报告

    Args:
        manifest_dict: 包含 comprehension、keep_mask、tokens 的工作数据

    Returns:
        追加了 review_report 字段的 manifest_dict
    """
    print("[2c] 审核子阶段（占位模式）")

    # MVP 占位实现：自动通过
    review_report = {
        "round": 0,
        "verdict": "pass",
        "coverage_issues": [],
        "completeness_issues": [],
        "token_spent": 0
    }

    manifest_dict["review_report"] = review_report

    print("[2c] 自动通过审核")
    return manifest_dict


# ============================================================================
# 未来实现：真实审核逻辑
# ============================================================================

def run_2c_review_full(manifest_dict: dict) -> dict:
    """完整版审核逻辑（未来实现）

    审核流程：
    1. 提取当前保留的片段内容
    2. 对照 comprehension.checklist 逐项检查覆盖率
    3. 验证决策连贯性（无明显断层）
    4. 输出结构化审核报告

    裁决类型：
    - pass: 核心要素全覆盖，无明显噪声
    - fix_decision: 某些 must 项未被覆盖，但 checklist 本身完整
    - fix_checklist: checklist 遗漏了重要内容维度

    Args:
        manifest_dict: 包含 comprehension、keep_mask、tokens 的工作数据

    Returns:
        追加了 review_report 字段的 manifest_dict

    Raises:
        NotImplementedError: 未来实现
    """
    raise NotImplementedError("完整版审核逻辑待实现")

    # 未来实现的伪代码：
    # 1. 提取保留片段
    # kept_annotations = _extract_kept_annotations(manifest_dict)
    #
    # 2. 构造审核 Prompt
    # prompt = _build_review_prompt(manifest_dict, kept_annotations)
    #
    # 3. 调用 LLM（启用 reasoner）
    # from autosmartcut.intelligence_llm import call_llm_structured
    # response = call_llm_structured(
    #     prompt=prompt,
    #     schema=_get_review_schema(),
    #     temperature=TEMPERATURE,
    #     enable_reasoning=ENABLE_REASONING
    # )
    #
    # 4. 解析审核结果
    # review_report = _parse_review_response(response)
    # manifest_dict["review_report"] = review_report
    #
    # return manifest_dict


def _extract_kept_annotations(manifest_dict: dict) -> list[dict]:
    """提取当前保留的 speech 片段（未来实现）

    Args:
        manifest_dict: 包含 tokens 和 keep_mask

    Returns:
        保留的 speech 标注列表
    """
    raise NotImplementedError()


def _build_review_prompt(manifest_dict: dict, kept_annotations: list[dict]) -> str:
    """构造审核 Prompt（未来实现）

    输入元素：
    - 主旨
    - checklist
    - 保留的片段内容

    任务：
    1. 逐项检查 checklist 是否被覆盖
    2. 验证决策连贯性
    3. 输出结构化审核报告
    """
    raise NotImplementedError()


def _get_review_schema() -> dict:
    """审核输出的 JSON Schema（未来实现）"""
    return {
        "type": "object",
        "properties": {
            "verdict": {
                "type": "string",
                "enum": ["pass", "fix_decision", "fix_checklist"],
                "description": "审核裁决"
            },
            "coverage_issues": {
                "type": "array",
                "description": "覆盖问题列表（fix_decision 时填写）",
                "items": {"type": "string"}
            },
            "completeness_issues": {
                "type": "array",
                "description": "完整性问题列表（fix_checklist 时填写）",
                "items": {"type": "string"}
            },
            "reasoning": {
                "type": "string",
                "description": "审核推理过程（reasoner 模式输出）"
            }
        },
        "required": ["verdict"]
    }


def _parse_review_response(response: dict) -> dict:
    """解析审核响应为 review_report（未来实现）"""
    raise NotImplementedError()
