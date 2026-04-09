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

# 由 Demo1 完整 JSON 生成 layer1 / layer2 / mock mask
python demos/gen_demo_jsons.py

# Demo3：json 模式（layer1 + keep_mask → smartcut）
python demos/demo3_smartcut.py json `
  --layer1 outputs/layer1_annotations.json `
  --mask outputs/layer2_output_mock.json `
  --output outputs/demo3_from_mask.mp4

# Demo3：dense 压测（不依赖 layer1）
python demos/demo3_smartcut.py dense --input samples\alxe_01.mp4
```

`layer1_annotations.json` 中的 `source` 需能解析到真实视频文件（相对 `outputs` 父目录或当前工作目录）。

Layer 1 清单为**句级语音**：每条含 `index`、`t_start`、`t_end`、`content`、`gap_after`（无独立静音行）。`layer2_input.json` 中 `tokens[]` 仅 `index` 与 `text`。
