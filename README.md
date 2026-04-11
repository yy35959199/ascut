# AutoSmartCut（ascut）

## 环境

使用 Miniconda 环境 **`ascut`**（已安装 `smartcut`、`autosmartcut` 等依赖）。

在 **PowerShell 7** 中：

```powershell
cd D:\Workspace\Code\Workspace\AutoSmartCut\ascut
conda activate ascut
```

若在新会话中 `conda activate` 不可用，先执行一次（路径按本机 Miniconda 安装位置调整）：

```powershell
(& "D:\Workspace\Environment\miniconda3\Scripts\conda.exe" "shell.powershell" "hook") | Out-String | Invoke-Expression
conda activate ascut
```

## 演示命令

主产物为输出目录下的 **`timeline_manifest.json`**（见 [doc/AutoSmartCut-MVP-Mini.md](doc/AutoSmartCut-MVP-Mini.md)）。

```powershell
# Demo1：ASR（需模型与输入视频）→ 写入 outputs/timeline_manifest.json 等
python demos/demo1_asr.py

# 由 Demo1 完整 JSON 生成带 mock keep_mask 的清单（辅助工具）
python demos/tools/gen_demo_jsons.py

# Demo2：智能层更新清单（需配置 LLM；默认跳过 2d，加 --interactive-2d 可人工审阅）
python demos/demo2_llm.py --manifest outputs/timeline_manifest.json --goal "提取核心观点"
# 等价：python -m autosmartcut.intelligence --manifest outputs/timeline_manifest.json --goal "..." --auto

# 三层编排（默认 --stage 123 全流程）
ascut run --input samples\alxe_01.mp4 --goal "提取核心观点"
ascut run --stage 23 --manifest outputs\timeline_manifest.json --goal "精华剪辑"
ascut run --stage 3 --manifest outputs\timeline_manifest.json --output-name only_l3.mp4
ascut run --stage 3 ... --no-vad-snap   # 关闭 L3 VAD 切点吸附

# Demo3：json 模式（清单 → smartcut）
python demos/demo3_smartcut.py json `
  --manifest outputs/timeline_manifest.json `
  --output outputs/demo3_from_mask.mp4

# Demo3：dense 压测（不依赖清单）
python demos/demo3_smartcut.py dense --input samples\alxe_01.mp4
```

清单中 `source_media.path`（或顶层 `source`）须能解析到真实视频文件（相对清单所在目录或当前工作目录）。文档与示例中 **`output/`**、**`outputs/`** 均可能出现，以你本机实际产物路径为准。

### Layer 3：切点吸附（VAD Snap）

执行层在 `gap_after_cap` + `pre_pad` / `post_pad` 得到粗切点后，可选地用 **Silero VAD** 在源视频上再解码一条 **16 kHz 单声道**音轨（内存中处理，**不写回**源文件），求静音区间，并在配置半径内将每条保留段的 **入点 / 出点** 吸附到静音边界，使接缝更自然。

- **配置**：`config.toml` 的 `[execution]` — `vad_snap_enabled`、`vad_snap_radius`、`vad_threshold`、`vad_min_silence_ms`、`vad_speech_pad_ms`（详见 [AutoSmartCut-MVP.md](doc/AutoSmartCut-MVP.md)）。
- **关闭**：编排命令加 **`--no-vad-snap`**（优先级高于配置文件中的 VAD 项）。`demo3_smartcut.py json` 同样支持 **`--no-vad-snap`**；可选 **`--config`** 指定 `config.toml`。
- **`gap_after`**：仍参与句末右边界粗定位与 L2 节奏语义；VAD 主要在 L3 做切点微调，失败时自动退回无吸附行为。

## 测试（按环节）

在仓库根目录执行；**无 torch** 时可先跑 L2 子集与 L3 纯逻辑：

| 前缀 | 环节 | 说明 |
|------|------|------|
| `tests/test_l1_*.py` | Layer 1 | 依赖 `perception`、**PyAV（`import av`）**、torch 等 |
| `tests/test_l2_*.py`、`tests/test_intelligence_llm_multiturn.py` | Layer 2 | `test_l2_2b` / `test_l2_llm_schema` / `test_intelligence_llm_multiturn` 不经过 torch |
| `tests/test_l3_*.py` | Layer 3 | 执行层区间逻辑 |
| `tests/test_vad_silence.py` | Layer 3 | VAD 静音补集与切点吸附（snap）纯逻辑 |
| `tests/test_manifest_*.py`、`tests/test_annotation_tokens.py` | 清单 / 句面 | 无 torch |

```powershell
pytest tests/test_l2_2b.py tests/test_l2_llm_schema.py tests/test_intelligence_llm_multiturn.py tests/test_l3_execution.py tests/test_vad_silence.py -q
pytest tests/ -q
```

句级 **`annotations[]`**：每条含 `index`、`t_start`、`t_end`、`content`、`gap_after`（无独立静音行）。智能层运行时由 **`tokens[]`（仅 `index` + `text`）** 在内存中从 `annotations` 派生。
