"""Layer 2 智能层 - 流程编排主入口

## 职责
- 提供统一入口 run_intelligence_layer()
- 按顺序调用 2a → 2b → 2c → 2d
- 文件交接模式（读取 layer1.json，输出 layer2.json）
- 不包含具体业务逻辑，只负责流程编排

## 输入 Schema (layer1.json)
{
    "source": str,              # 源视频路径
    "language": str,            # 语言代码（如 "zh"）
    "raw_text": str,            # 完整 ASR 原文
    "annotations": [            # 标注列表
        {
            "index": int,       # 全局唯一序号（0-based）
            "t_start": float,   # 开始时间（秒）
            "t_end": float,     # 结束时间（秒）
            "content": str,     # 转写文字
            "gap_after": float, # 与下一条 speech 的间隔秒数
            "confidence": float,# ASR 置信度
            "metadata": dict    # 扩展字段（如 char_timestamps）
        },
        ...
    ]
}

## 输出 Schema (layer2.json)
{
    "keep_mask": [              # 决策掩码列表
        {
            "index": int,       # 对应 annotations[].index
            "keep": bool        # True=保留, False=删除
        },
        ...
    ]
}

## 数据流
layer1.json
  ↓ 加载
manifest_dict (内存对象)
  ↓ 2a 理解子阶段
manifest_dict["comprehension"]
  ↓ 2b 决策子阶段
manifest_dict["keep_mask"]
  ↓ 2c 审核子阶段
manifest_dict["review_report"]
  ↓ 2d 人工子阶段
manifest_dict["keep_mask"] (最终版)
  ↓ 保存
layer2.json

## 核心不变量
- index 序列是主坐标系，所有处理围绕它展开
- keep_mask 长度必须等于 annotations 长度
- keep_mask 与 annotations 通过 index 一一对应
"""

import json
from pathlib import Path
from typing import Any

from autosmartcut.intelligence_2a import run_2a_comprehension
from autosmartcut.intelligence_2b import run_2b_decision
from autosmartcut.intelligence_2c import run_2c_review
from autosmartcut.intelligence_2d import run_2d_human_review


# ============================================================================
# 文件 I/O
# ============================================================================

def load_layer1_json(path: Path) -> dict[str, Any]:
    """加载 Layer 1 输出的 JSON 文件

    Args:
        path: layer1.json 文件路径

    Returns:
        包含 annotations、source、language、raw_text 的字典

    Raises:
        FileNotFoundError: 文件不存在
        json.JSONDecodeError: JSON 格式错误
    """
    if not path.exists():
        raise FileNotFoundError(f"Layer 1 输出文件不存在: {path}")

    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_layer2_json(keep_mask: list[dict], output_path: Path) -> None:
    """保存 Layer 2 输出的 keep_mask JSON 文件

    Args:
        keep_mask: 决策掩码列表 [{"index": int, "keep": bool}, ...]
        output_path: 输出文件路径

    输出格式:
        {
            "keep_mask": [
                {"index": 0, "keep": true},
                {"index": 1, "keep": false},
                ...
            ]
        }
    """
    output = {"keep_mask": keep_mask}

    # 确保输出目录存在
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with output_path.open("w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)


# ============================================================================
# 主入口
# ============================================================================

def run_intelligence_layer(
    layer1_path: Path,
    output_path: Path,
    goal: str = ""
) -> None:
    """Layer 2 主入口（文件交接模式）

    执行流程:
        1. 加载 Layer 1 输出（annotations）
        2. 验证输入格式
        3. 依次执行 2a → 2b → 2c → 2d
        4. 验证输出格式
        5. 保存 keep_mask 到文件

    Args:
        layer1_path: Layer 1 输出的 JSON 文件路径
        output_path: Layer 2 输出的 JSON 文件路径
        goal: 用户指定的分析目标（如"提取核心观点"）

    Raises:
        ValueError: 输入格式错误或输出验证失败
        FileNotFoundError: 输入文件不存在
    """
    print("[Layer 2] 智能层开始")
    print(f"[Layer 2] 输入: {layer1_path}")
    print(f"[Layer 2] 输出: {output_path}")
    if goal:
        print(f"[Layer 2] 目标: {goal}")

    # 1. 加载 Layer 1 输出
    layer1_data = load_layer1_json(layer1_path)
    annotations = layer1_data.get("annotations", [])

    # 验证输入
    if not annotations:
        raise ValueError("Layer 1 输出中没有 annotations")

    # 验证 index 连续性
    for i, ann in enumerate(annotations):
        if ann.get("index") != i:
            raise ValueError(f"annotations[{i}] 的 index 不连续: 期望 {i}, 实际 {ann.get('index')}")

    print(f"[Layer 2] 加载 {len(annotations)} 条标注")

    # 2. 初始化工作数据（MVP 阶段直接用 dict，避免 dataclass 转换复杂度）
    manifest_dict = {
        "annotations": annotations,
        "goal": goal,
        "source": layer1_data.get("source", ""),
        "language": layer1_data.get("language", ""),
        "raw_text": layer1_data.get("raw_text", ""),
    }

    # 3. 执行各子阶段
    try:
        # 2a 理解子阶段（固定两轮 LLM 调用）
        manifest_dict = run_2a_comprehension(manifest_dict)

        # 2b 决策子阶段（固定一次 LLM 调用）
        manifest_dict = run_2b_decision(manifest_dict)

        # 2c 审核子阶段（MVP 占位，自动 pass）
        manifest_dict = run_2c_review(manifest_dict)

        # 2d 人工子阶段（CLI 交互）
        manifest_dict = run_2d_human_review(manifest_dict)

    except KeyboardInterrupt:
        print("\n[Layer 2] 用户中断")
        raise
    except Exception as e:
        print(f"\n[Layer 2] 执行失败: {e}")
        raise

    # 4. 提取 keep_mask 并验证
    keep_mask = manifest_dict.get("keep_mask", [])

    if not keep_mask:
        raise ValueError("智能层未生成 keep_mask")

    if len(keep_mask) != len(annotations):
        raise ValueError(
            f"keep_mask 长度不匹配: {len(keep_mask)} != {len(annotations)}"
        )

    # 验证 keep_mask 格式
    for i, entry in enumerate(keep_mask):
        if "index" not in entry:
            raise ValueError(f"keep_mask[{i}] 缺少 index 字段")
        if "keep" not in entry:
            raise ValueError(f"keep_mask[{i}] 缺少 keep 字段")
        if entry["index"] != i:
            raise ValueError(f"keep_mask[{i}] 的 index 不匹配: 期望 {i}, 实际 {entry['index']}")

    # 5. 保存输出
    save_layer2_json(keep_mask, output_path)

    keep_count = sum(1 for e in keep_mask if e["keep"] is True)
    print(f"[Layer 2] 智能层完成")
    print(f"[Layer 2] 保留 {keep_count}/{len(keep_mask)} 个片段")
    print(f"[Layer 2] 输出已保存至: {output_path}")


# ============================================================================
# 测试入口
# ============================================================================

def main():
    """命令行测试入口

    用法:
        python -m autosmartcut.intelligence <layer1.json> <output.json> [--goal 目标]

    示例:
        python -m autosmartcut.intelligence layer1.json layer2.json --goal "提取核心观点"
    """
    import sys

    if len(sys.argv) < 3:
        print("用法: python -m autosmartcut.intelligence <layer1.json> <output.json> [--goal 目标]")
        print("\n示例:")
        print("  python -m autosmartcut.intelligence layer1.json layer2.json")
        print("  python -m autosmartcut.intelligence layer1.json layer2.json --goal '提取核心观点'")
        sys.exit(1)

    layer1_path = Path(sys.argv[1])
    output_path = Path(sys.argv[2])

    goal = ""
    if "--goal" in sys.argv:
        goal_idx = sys.argv.index("--goal")
        if goal_idx + 1 < len(sys.argv):
            goal = sys.argv[goal_idx + 1]

    try:
        run_intelligence_layer(layer1_path, output_path, goal)
    except Exception as e:
        print(f"\n错误: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
