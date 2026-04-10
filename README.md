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

```powershell
# Demo1：ASR（需模型与输入视频）
python demos/demo1_asr.py

# 由 Demo1 完整 JSON 生成 layer1 / layer2 / mock mask（辅助工具，非环节演示）
python demos/tools/gen_demo_jsons.py

# Demo2：智能层 JSON2 → JSON3（需配置 LLM；默认跳过 2d，加 --interactive-2d 可人工审阅）
python demos/demo2_llm.py --layer2 output/layer2_input.json --output output/layer2_output.json --goal "提取核心观点"
# 等价：python -m autosmartcut.intelligence output/layer2_input.json output/layer2_output.json --goal "..." --auto

# 三层编排（Windows 示例）
# ascut run --input samples\alxe_01.mp4 --goal "提取核心观点"
# ascut run --from-stage 2 --layer2-json output\layer2_input.json --goal "精华剪辑"
# ascut run --from-stage 3 --layer1-json output\layer1_annotations.json --layer3-json output\layer2_output.json

# Demo3：json 模式（JSON1 + JSON3 → smartcut；路径按本机产物目录填写 output/ 或 outputs/）
python demos/demo3_smartcut.py json `
  --layer1 output/layer1_annotations.json `
  --mask output/layer2_output_mock.json `
  --output output/demo3_from_mask.mp4

# Demo3：dense 压测（不依赖 layer1）
python demos/demo3_smartcut.py dense --input samples\alxe_01.mp4
```

`layer1_annotations.json` 中的 `source` 需能解析到真实视频文件（相对 JSON 所在目录或当前工作目录）。文档与示例中 **`output/`**、**`outputs/`** 均可能出现，以你本机实际产物路径为准。

## 测试（按环节）

在仓库根目录执行；**无 torch** 时可先跑 L2 子集与 L3 纯逻辑：

| 前缀 | 环节 | 说明 |
|------|------|------|
| `tests/test_l1_*.py` | Layer 1 | 依赖 `perception`、**PyAV（`import av`）**、torch 等 |
| `tests/test_l2_*.py` | Layer 2 | `test_l2_2b` / `test_l2_llm_schema` 不经过 torch |
| `tests/test_l3_*.py` | Layer 3 | 执行层区间逻辑 |

```powershell
pytest tests/test_l2_2b.py tests/test_l2_llm_schema.py tests/test_l3_execution.py -q
pytest tests/ -q
```

Layer 1 清单为**句级语音**：每条含 `index`、`t_start`、`t_end`、`content`、`gap_after`（无独立静音行）。`layer2_input.json` 中 `tokens[]` 仅 `index` 与 `text`。
