"""快速查看 layer1 JSON 前后若干条与时长统计。

辅助工具，非 L1/L2/L3 环节演示脚本。用法（仓库根目录）::

    python demos/tools/inspect_demo1.py
"""
import json
from pathlib import Path


def main() -> None:
    path = Path("outputs/layer1_annotations.json")
    data = json.loads(path.read_text(encoding="utf-8"))
    anns = data["annotations"]

    print("source:", data.get("source", ""))
    print("total annotations:", len(anns))
    print()

    print("=== 前5条 ===")
    for a in anns[:5]:
        t = "[{:.2f}-{:.2f}]".format(a["t_start"], a["t_end"])
        c = (a.get("content") or "")[:40]
        print("speech  {}  {}  gap_after={:.2f}".format(t, c, float(a.get("gap_after", 0.0))))

    print()
    print("=== 后5条 ===")
    for a in anns[-5:]:
        t = "[{:.2f}-{:.2f}]".format(a["t_start"], a["t_end"])
        c = (a.get("content") or "")[:40]
        print("speech  {}  {}  gap_after={:.2f}".format(t, c, float(a.get("gap_after", 0.0))))

    total_gap = sum(float(a.get("gap_after", 0.0)) for a in anns)
    total_speech = sum(float(a["t_end"] - a["t_start"]) for a in anns)
    print()
    print("speech duration: {:.1f}s".format(total_speech))
    print("gap duration: {:.1f}s".format(total_gap))
    if anns:
        print("covered duration: {:.1f}s".format(float(anns[-1]["t_end"]) + float(anns[-1].get("gap_after", 0.0))))


if __name__ == "__main__":
    main()
