"""Layer 2 / 2d 人工子阶段 — 薄包装层

## 职责
保留 `run_2d_human_review(manifest_dict) → dict` 签名向后兼容。
内部调用 Core API 和 TUI Shell 完成交互。

## 交互流程
1. 尝试导入 TUI Shell 的 run_2d_interactive
2. 若可用：启动 TUI 交互，返回最终 manifest
3. 若不可用：回退到简单 input() 循环（通过 Core API）

## 输入 Schema
manifest_dict = {
    "keep_mask": [...],
    "tokens": [{"index": int, "text": str}, ...],
    "comprehension": {...},
}

## 输出 Schema
manifest_dict["keep_mask"] = [{"index": int, "keep": bool}, ...]
manifest_dict["human_feedback_history"] = [...]
"""

import logging

from autosmartcut.nodes.l2.intelligence_2d_core import (
    AcceptAction,
    QuitAction,
    ShowAction,
    Signal,
    run_2d,
)

logger = logging.getLogger(__name__)


def run_2d_human_review(manifest_dict: dict) -> dict:
    """2d 人工子阶段：交互审阅 + 确认输出。

    保持向后兼容的签名。内部调用 TUI Shell 或回退到简单 input() 循环。

    Args:
        manifest_dict: 包含 keep_mask、tokens、comprehension 的工作数据

    Returns:
        追加了最终 keep_mask 和 human_feedback_history 的 manifest_dict

    Raises:
        KeyboardInterrupt: 用户取消（quit）
        ValueError: keep_mask 为空
    """
    logger.info("[2d] 人工审阅开始")

    tokens = manifest_dict.get("tokens", [])
    keep_mask = manifest_dict.get("keep_mask", [])

    if not keep_mask:
        raise ValueError("keep_mask 为空，无法进行人工审阅")

    try:
        from autosmartcut.nodes.l2.intelligence_2d_shell import run_2d_interactive

        manifest_dict, signal = run_2d_interactive(manifest_dict)

        if signal == Signal.QUIT:
            raise KeyboardInterrupt("用户取消")

        # REFLOW_2A / REFLOW_2B 信号在此薄包装层中不处理回流
        # （回流由编排器 _run_2d_with_reflow 处理）
        # 如果直接调用 run_2d_human_review（旧路径），
        # 遇到 reflow 信号时当作 DONE 处理（向后兼容）
        if signal in (Signal.REFLOW_2A, Signal.REFLOW_2B):
            logger.warning(
                "[2d] 薄包装层收到 %s 信号但无回流能力，自动确认",
                signal.value,
            )
            result = run_2d(manifest_dict, AcceptAction())
            manifest_dict = result.manifest_dict

        logger.info("[2d] 人工审阅完成")
        return manifest_dict

    except ImportError:
        logger.info("[2d] TUI Shell 不可用，使用简单交互模式")
        return _run_2d_simple(manifest_dict)


def _run_2d_simple(manifest_dict: dict) -> dict:
    """简单 input() 循环回退，使用 Core API。"""
    from autosmartcut.nodes.l2.intelligence_2d_core import (
        ToggleAction,
    )

    # 初始显示
    result = run_2d(manifest_dict, ShowAction())
    manifest_dict = result.manifest_dict

    if result.display_data:
        _print_simple_display(result.display_data)

    while True:
        try:
            cmd = input("\n命令 [t <index>] [a] [q]: ").strip()
        except EOFError:
            logger.info("[2d] 非交互模式，自动确认")
            result = run_2d(manifest_dict, AcceptAction())
            return result.manifest_dict

        if cmd.startswith("t "):
            try:
                index = int(cmd.split()[1])
                result = run_2d(manifest_dict, ToggleAction(index=index))
                manifest_dict = result.manifest_dict
                if result.display_data:
                    _print_simple_display(result.display_data)
                if result.message:
                    print(f"  → {result.message}")
            except (ValueError, IndexError):
                logger.warning("无效命令，格式: t <index>")

        elif cmd == "a":
            result = run_2d(manifest_dict, AcceptAction())
            manifest_dict = result.manifest_dict
            logger.info("[2d] 人工审阅完成")
            return manifest_dict

        elif cmd == "q":
            logger.info("[2d] 退出不保存")
            raise KeyboardInterrupt("用户取消")

        else:
            logger.warning("无效命令；可用: t <index> | a | q")


def _print_simple_display(dd) -> None:
    """简单模式下打印决策状态。"""
    tokens = dd.tokens
    mask = dd.effective_mask

    print("\n" + "=" * 100)
    print("当前决策状态（index + 句面；时间轴见 Layer1 JSON1）")
    print("=" * 100)
    print(f"{'Index':<6} {'状态':<8} {'内容预览':<70}")
    print("-" * 100)

    for i, tok in enumerate(tokens):
        keep_status = mask[i]["keep"] if i < len(mask) else True
        status = "[保留]" if keep_status else "[删除]"
        text = str(tok.get("text", ""))
        preview = (text[:67] + "…") if len(text) > 70 else text
        if not preview:
            preview = "(空)"
        print(f"{i:<6} {status:<8} {preview:<70}")

    stats = dd.stats
    print("-" * 100)
    print(
        f"保留: {stats['keep_count']} | "
        f"删除: {stats['cut_count']} | "
        f"句数: {stats['total']}"
    )
    print(f"人工修改: {stats['override_count']} 条")
