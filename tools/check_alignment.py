import json
import re
import sys

path = sys.argv[1] if len(sys.argv) > 1 else "outputs/demo1_annotations_punc.json"

data = json.load(open(path, encoding="utf-8"))
raw = re.sub(r'[^\u4e00-\u9fa5a-zA-Z0-9]', '', data.get("raw_text", ""))
chars = [it.get("text", "") for ann in data.get("annotations", []) if ann.get("type") == "speech" for it in ann.get("metadata", {}).get("char_timestamps", [])]
joined = ''.join(chars)
multi = [(i, text) for i, text in enumerate(chars) if len(text) != 1]
print(f"chars_count={len(chars)}")
print(f"joined_len={len(joined)}")
print(f"raw_len={len(raw)}")
print(f"equal={joined == raw}")
print(f"multi_count={len(multi)}")
print(f"multi_head={multi[:10]}")
