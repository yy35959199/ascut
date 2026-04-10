"""Layer 2 智能层 - 流程编排主入口

## 职责
- 提供统一入口 run_intelligence_layer()
- 按顺序调用 2a → 2b → 2c → 2d
- 文件交接：读取 **JSON2**（句面 ``tokens[]``），输出 JSON3（``keep_mask``）

## 输入 Schema（JSON2，如 layer2_input.json）
{
    "source": str,              # 源视频路径（供流水线解析；智能层不剪片）
    "tokens": [                 # 句级句面，稠密 index 0..n-1
        {"index": int, "text": str},
        ...
    ]
}

可选顶层字段（若存在则带入 manifest）：``language``、``raw_text``。

``goal`` 由 CLI/API 参数传入，不来自 JSON2。

## 输出 Schema (layer2.json / JSON3)
{
    "keep_mask": [
        {"index": int, "keep": bool},
        ...
    ]
}

## 数据流
JSON2 → manifest(tokens) → 2a → 2b → 2c → 2d → JSON3

执行层（L3）仍使用 **JSON1 + JSON3** 合成时间区间；时间轴不经过 L2 入口。

## 核心不变量
- ``tokens[i].index == i``；``keep_mask`` 与 ``tokens`` 等长且 index 对齐
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from autosmartcut.intelligence_2a import run_2a_comprehension
from autosmartcut.intelligence_2b import run_2b_decision
from autosmartcut.intelligence_2c import run_2c_review
from autosmartcut.intelligence_2d import run_2d_human_review
from autosmartcut.layer2_tokens import load_layer2_tokens_document

logger = logging.getLogger(__name__)


def save_layer2_json(keep_mask: list[dict], output_path: Path) -> None:
    """保存 Layer 2 输出的 keep_mask JSON 文件（JSON3）。"""
    output = {"keep_mask": keep_mask}
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)


def run_intelligence_layer(
    layer2_input_path: Path,
    output_path: Path,
    goal: str = "",
    *,
    auto: bool = False,
    verbose_log: bool = False,
    two_b_mode: str = "single",
) -> None:
    """Layer 2 主入口（JSON2 → JSON3）

    Args:
        layer2_input_path: JSON2 路径（``tokens[]`` + 可选 ``source``）
        output_path: JSON3 输出路径
        goal: 用户指定的分析/剪辑目标
        auto: True 时跳过 2d
        verbose_log: 是否启用 DEBUG 级 stderr 日志
        two_b_mode: ``single`` | ``chunked``
    """
    from autosmartcut.log import ensure_autosmartcut_logging

    ensure_autosmartcut_logging(verbose=verbose_log)

    logger.info("[L2] 智能层开始 输入(JSON2)=%s 输出(JSON3)=%s", layer2_input_path, output_path)
    if goal:
        logger.info("[L2] 目标: %s", goal)
    if two_b_mode not in ("single", "chunked"):
        raise ValueError(f"two_b_mode 须为 'single' 或 'chunked'，实际: {two_b_mode!r}")
    logger.info("[L2] 2b 模式: %s", two_b_mode)

    doc = load_layer2_tokens_document(layer2_input_path)
    tokens = doc["tokens"]

    logger.info("[L2] 加载 %d 条 tokens", len(tokens))

    manifest_dict: dict[str, Any] = {
        "tokens": tokens,
        "goal": goal,
        "source": doc.get("source", ""),
        "language": doc.get("language", ""),
        "raw_text": doc.get("raw_text", ""),
    }

    try:
        manifest_dict = run_2a_comprehension(manifest_dict)
        manifest_dict = run_2b_decision(manifest_dict, mode=two_b_mode)
        manifest_dict = run_2c_review(manifest_dict)

        if auto:
            logger.info("[L2] auto 模式，跳过 2d 人工审阅")
            manifest_dict.setdefault("human_feedback_history", []).append(
                {
                    "round": 0,
                    "verdict": "confirm",
                    "overrides": [],
                    "feedback": "",
                    "timestamp": datetime.now().isoformat(),
                }
            )
        else:
            manifest_dict = run_2d_human_review(manifest_dict)

    except KeyboardInterrupt:
        logger.warning("[L2] 用户中断")
        raise
    except Exception as e:
        logger.error("[L2] 执行失败: %s", e)
        raise

    keep_mask = manifest_dict.get("keep_mask", [])

    if not keep_mask:
        raise ValueError("智能层未生成 keep_mask")

    if len(keep_mask) != len(tokens):
        raise ValueError(
            f"keep_mask 长度不匹配: {len(keep_mask)} != {len(tokens)}"
        )

    for i, entry in enumerate(keep_mask):
        if "index" not in entry:
            raise ValueError(f"keep_mask[{i}] 缺少 index 字段")
        if "keep" not in entry:
            raise ValueError(f"keep_mask[{i}] 缺少 keep 字段")
        if entry["index"] != i:
            raise ValueError(f"keep_mask[{i}] 的 index 不匹配: 期望 {i}, 实际 {entry['index']}")

    save_layer2_json(keep_mask, output_path)

    keep_count = sum(1 for e in keep_mask if e["keep"] is True)
    logger.info(
        "[L2] 完成 保留 %d/%d 句 → %s",
        keep_count,
        len(keep_mask),
        output_path,
    )


def main() -> None:
    """命令行入口：``python -m autosmartcut.intelligence <JSON2> <JSON3> ...``"""
    import sys

    if len(sys.argv) < 3:
        print(
            "用法: python -m autosmartcut.intelligence <layer2_input.json> <output.json> "
            "[--goal 目标] [--auto] [--verbose] [--two-b-mode single|chunked]"
        )
        print("\n示例:")
        print("  python -m autosmartcut.intelligence output/layer2_input.json output/layer2_output.json")
        print("  python -m autosmartcut.intelligence output/layer2_input.json out.json --goal '提取核心观点' --auto")
        sys.exit(1)

    layer2_path = Path(sys.argv[1])
    output_path = Path(sys.argv[2])

    goal = ""
    if "--goal" in sys.argv:
        goal_idx = sys.argv.index("--goal")
        if goal_idx + 1 < len(sys.argv):
            goal = sys.argv[goal_idx + 1]

    auto = "--auto" in sys.argv
    verbose_log = "--verbose" in sys.argv
    two_b_mode = "single"
    if "--two-b-mode" in sys.argv:
        i = sys.argv.index("--two-b-mode")
        if i + 1 < len(sys.argv):
            two_b_mode = sys.argv[i + 1]

    try:
        run_intelligence_layer(
            layer2_path,
            output_path,
            goal,
            auto=auto,
            verbose_log=verbose_log,
            two_b_mode=two_b_mode,
        )
    except Exception as e:
        print(f"\n错误: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
