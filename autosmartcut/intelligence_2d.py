"""Layer 2 / 2d 人工子阶段

## 职责
提供 CLI 交互界面，允许人工审阅和修改 2b/2c 的决策结果。
MVP 阶段提供基础交互：查看决策、手动切换 keep/cut、确认输出。

## 交互流程
1. 显示当前决策状态（表格形式）
2. 等待用户命令：
   - [t <index>] 切换指定 index 的 keep/cut 状态
   - [a] 确认并输出
   - [q] 退出不保存
3. 用户修改通过 overrides 记录（delta 模式）
4. 最终有效决策 = keep_mask + overrides 合并

## 输入 Schema
manifest_dict = {
    "keep_mask": [              # 来自 2b/2c
        {
            "index": int,
            "keep": bool | None
        }
    ],
    "annotations": [            # 来自 Layer 1
        {
            "index": int,
            "t_start": float,
            "t_end": float,
            "type": str,
            "content": str,
            ...
        }
    ]
}

## 输出 Schema
manifest_dict["keep_mask"] = [  # 最终有效决策（keep_mask + overrides 合并后）
    {
        "index": int,
        "keep": bool | None
    }
]

manifest_dict["human_feedback_history"] = [  # 人工操作历史（未来扩展）
    {
        "round": int,
        "verdict": str,         # "confirm"
        "overrides": [          # 人工修改记录
            {"index": int, "keep": bool}
        ],
        "timestamp": str
    }
]

## 注意
- overrides 只记录人工修改的条目，不修改原始 keep_mask
- 最终输出时将 overrides 合并到 keep_mask
- MVP 阶段不支持自然语言反馈（路径③）
"""

from datetime import datetime


# ============================================================================
# 主入口
# ============================================================================

def run_2d_human_review(manifest_dict: dict) -> dict:
    """2d 人工子阶段：CLI 交互，手动覆盖决策 + 确认输出

    Args:
        manifest_dict: 包含 keep_mask、annotations 的工作数据

    Returns:
        追加了最终 keep_mask 和 human_feedback_history 的 manifest_dict
    """
    print("[2d] 人工审阅开始")

    annotations = manifest_dict["annotations"]
    keep_mask = manifest_dict.get("keep_mask", [])

    if not keep_mask:
        raise ValueError("keep_mask 为空，无法进行人工审阅")

    # 人工覆盖记录（delta 模式）
    overrides = []

    # 交互循环
    while True:
        # 显示当前状态
        _display_review_ui(annotations, keep_mask, overrides)

        # 等待用户输入
        try:
            cmd = input("\n命令 [t <index>] [a] [q]: ").strip()
        except EOFError:
            # 非交互模式（如测试），自动确认
            print("[2d] 非交互模式，自动确认")
            cmd = "a"

        if cmd.startswith("t "):
            # 切换指定 index 的 keep/cut
            try:
                index = int(cmd.split()[1])
                overrides = _toggle_keep(annotations, keep_mask, overrides, index)
            except (ValueError, IndexError):
                print("❌ 无效命令，格式: t <index>")

        elif cmd == "a":
            # 确认并输出
            final_keep_mask = _merge_keep_mask(keep_mask, overrides)
            manifest_dict["keep_mask"] = final_keep_mask

            # 记录人工反馈历史
            feedback_round = {
                "round": 0,
                "verdict": "confirm",
                "overrides": overrides,
                "feedback": "",
                "timestamp": datetime.now().isoformat()
            }
            manifest_dict.setdefault("human_feedback_history", []).append(feedback_round)

            print("[2d] 人工审阅完成")
            break

        elif cmd == "q":
            print("[2d] 退出不保存")
            raise KeyboardInterrupt("用户取消")

        else:
            print("❌ 无效命令")
            print("可用命令:")
            print("  t <index>  - 切换指定 index 的保留/删除状态")
            print("  a          - 确认并输出")
            print("  q          - 退出不保存")

    return manifest_dict


# ============================================================================
# UI 显示
# ============================================================================

def _display_review_ui(
    annotations: list[dict],
    keep_mask: list[dict],
    overrides: list[dict]
) -> None:
    """显示当前决策状态（CLI 表格形式）

    Args:
        annotations: 标注列表
        keep_mask: 原始决策掩码
        overrides: 人工覆盖记录
    """
    # 合并 keep_mask + overrides
    effective_mask = _merge_keep_mask(keep_mask, overrides)

    print("\n" + "="*100)
    print("当前决策状态")
    print("="*100)
    print(f"{'Index':<6} {'时间范围':<20} {'状态':<8} {'内容预览':<50}")
    print("-"*100)

    for i, ann in enumerate(annotations):
        keep_status = effective_mask[i]["keep"]

        # 状态显示
        if keep_status:
            status = "[保留]"
        else:
            status = "[删除]"

        # 时间范围
        time_range = f"{ann['t_start']:.1f}-{ann['t_end']:.1f}s"

        # 内容预览
        content = ann.get("content", "")
        content_preview = content[:50] if content else "(空)"

        print(f"{i:<6} {time_range:<20} {status:<8} {content_preview:<50}")

    # 统计信息
    keep_count = sum(1 for e in effective_mask if e["keep"] is True)
    cut_count = sum(1 for e in effective_mask if e["keep"] is False)
    total_duration = sum(
        annotations[i]["t_end"] - annotations[i]["t_start"]
        for i, e in enumerate(effective_mask) if e["keep"] is True
    )

    print("-"*100)
    print(f"保留: {keep_count} | 删除: {cut_count} | 预计时长: {total_duration:.1f}s")
    print(f"人工修改: {len(overrides)} 条")


# ============================================================================
# 交互逻辑
# ============================================================================

def _toggle_keep(
    annotations: list[dict],
    keep_mask: list[dict],
    overrides: list[dict],
    index: int
) -> list[dict]:
    """切换指定 index 的 keep/cut 状态

    Args:
        annotations: 标注列表
        keep_mask: 原始决策掩码
        overrides: 当前覆盖记录
        index: 要切换的 annotation index

    Returns:
        更新后的 overrides 列表
    """
    # 验证 index 范围
    if index < 0 or index >= len(annotations):
        print(f"❌ index {index} 超出范围 [0, {len(annotations)-1}]")
        return overrides

    # 计算当前有效状态
    effective_mask = _merge_keep_mask(keep_mask, overrides)
    current_keep = effective_mask[index]["keep"]

    # 追加覆盖记录
    new_keep = not current_keep
    overrides.append({"index": index, "keep": new_keep})

    print(f"✓ index {index} 已切换为 {'[保留]' if new_keep else '[删除]'}")
    return overrides


def _merge_keep_mask(
    keep_mask: list[dict],
    overrides: list[dict]
) -> list[dict]:
    """合并 keep_mask + overrides，返回有效决策

    规则：later overrides 覆盖 earlier

    Args:
        keep_mask: 原始决策掩码
        overrides: 人工覆盖记录 [{"index": int, "keep": bool}, ...]

    Returns:
        合并后的有效决策掩码
    """
    # 复制原始 keep_mask
    result = [{"index": e["index"], "keep": e["keep"]} for e in keep_mask]

    # 应用 overrides（按顺序，later 覆盖 earlier）
    for override in overrides:
        idx = override["index"]
        result[idx]["keep"] = override["keep"]

    return result
