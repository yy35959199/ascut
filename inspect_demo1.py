import json

data = json.load(open("outputs/demo1_annotations.json", encoding="utf-8"))
anns = data["annotations"]
speech = [a for a in anns if a["type"] == "speech"]
silence = [a for a in anns if a["type"] == "silence"]

print("language:", data["language"])
print("total annotations:", len(anns))
print("speech segments:", len(speech))
print("silence segments:", len(silence))
print()

print("=== 前5条 ===")
for a in anns[:5]:
    t = "[{:.2f}-{:.2f}]".format(a["t_start"], a["t_end"])
    c = (a["content"] or "")[:40]
    print("{:7s} {}  {}".format(a["type"], t, c))

print()
print("=== 后5条 ===")
for a in anns[-5:]:
    t = "[{:.2f}-{:.2f}]".format(a["t_start"], a["t_end"])
    c = (a["content"] or "")[:40]
    print("{:7s} {}  {}".format(a["type"], t, c))

print()
has_chars = sum(1 for a in speech if a.get("metadata", {}).get("char_timestamps"))
pct = (100 * has_chars // len(speech)) if speech else 0
print("char_timestamps coverage: {}/{} ({}%)".format(has_chars, len(speech), pct))

# duration check
total_dur = anns[-1]["t_end"] if anns else 0
print("covered duration: {:.1f}s".format(total_dur))
