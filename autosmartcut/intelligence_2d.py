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
    "keep_mask": [...],
    "tokens": [                 # JSON2 句面（index + text）；无时间轴
        {"index": int, "text": str},
    ]
}

## 输出 Schema
manifest_dict["keep_mask"] = [  # 最终有效决策（keep_mask + overrides 合并后）
    {"index": int, "keep": bool}
]

manifest_dict["human_feedback_history"] = [...]

## 注意
- overrides 只记录人工修改的条目，不修改原始 keep_mask
- 不展示真实时间轴（时间由 Layer1 JSON1 供执行层）；仅 index + 文本预览
"""

from datetime import datetime


def run_2d_human_review(manifest_dict: dict) -> dict:
    """2d 人工子阶段：CLI 交互，手动覆盖决策 + 确认输出

    Args:
        manifest_dict: 包含 keep_mask、tokens 的工作数据

    Returns:
        追加了最终 keep_mask 和 human_feedback_history 的 manifest_dict
    """
    print("[2d] 人工审阅开始")

    tokens = manifest_dict["tokens"]
    keep_mask = manifest_dict.get("keep_mask", [])

    if not keep_mask:
        raise ValueError("keep_mask 为空，无法进行人工审阅")

    overrides: list[dict] = []

    while True:
        _display_review_ui(tokens, keep_mask, overrides)

        try:
            cmd = input("\n命令 [t <index>] [a] [q]: ").strip()
        except EOFError:
            print("[2d] 非交互模式，自动确认")
            cmd = "a"

        if cmd.startswith("t "):
            try:
                index = int(cmd.split()[1])
                overrides = _toggle_keep(tokens, keep_mask, overrides, index)
            except (ValueError, IndexError):
                print("❌ 无效命令，格式: t <index>")

        elif cmd == "a":
            final_keep_mask = _merge_keep_mask(keep_mask, overrides)
            manifest_dict["keep_mask"] = final_keep_mask

            feedback_round = {
                "round": 0,
                "verdict": "confirm",
                "overrides": overrides,
                "feedback": "",
                "timestamp": datetime.now().isoformat(),
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


def _display_review_ui(
    tokens: list[dict],
    keep_mask: list[dict],
    overrides: list[dict],
) -> None:
    effective_mask = _merge_keep_mask(keep_mask, overrides)

    print("\n" + "=" * 100)
    print("当前决策状态（index + 句面；时间轴见 Layer1 JSON1）")
    print("=" * 100)
    print(f"{'Index':<6} {'状态':<8} {'内容预览':<70}")
    print("-" * 100)

    for i, tok in enumerate(tokens):
        keep_status = effective_mask[i]["keep"]
        status = "[保留]" if keep_status else "[删除]"
        text = str(tok.get("text", ""))
        preview = (text[:67] + "…") if len(text) > 70 else text
        if not preview:
            preview = "(空)"
        print(f"{i:<6} {status:<8} {preview:<70}")

    keep_count = sum(1 for e in effective_mask if e["keep"] is True)
    cut_count = sum(1 for e in effective_mask if e["keep"] is False)

    print("-" * 100)
    print(f"保留: {keep_count} | 删除: {cut_count} | 句数: {len(tokens)}")
    print(f"人工修改: {len(overrides)} 条")


def _toggle_keep(
    tokens: list[dict],
    keep_mask: list[dict],
    overrides: list[dict],
    index: int,
) -> list[dict]:
    if index < 0 or index >= len(tokens):
        print(f"❌ index {index} 超出范围 [0, {len(tokens)-1}]")
        return overrides

    effective_mask = _merge_keep_mask(keep_mask, overrides)
    current_keep = effective_mask[index]["keep"]

    new_keep = not current_keep
    overrides.append({"index": index, "keep": new_keep})

    print(f"✓ index {index} 已切换为 {'[保留]' if new_keep else '[删除]'}")
    return overrides


def _merge_keep_mask(
    keep_mask: list[dict],
    overrides: list[dict],
) -> list[dict]:
    result = [{"index": e["index"], "keep": e["keep"]} for e in keep_mask]

    for override in overrides:
        idx = override["index"]
        result[idx]["keep"] = override["keep"]

    return result
