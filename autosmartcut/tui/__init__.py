"""autosmartcut.tui — TUI 子包（基于 Textual 框架）。

子模块：
- stream_vm.py   LLM 流式输出 ViewModel（纯 Python，无 Textual 依赖）
- addons.py      LLMStreamView 插件 Widget（DecisionsAddon、ResultAddon）
- formatters.py  兼容重导出（实现见 ``autosmartcut.formatters``）
- widgets.py     Textual Widget 组件（PipelineSidebar、MainArea、LogArea 等）
- screens.py     Textual Screen（ResumeScreen、QuitDialog、PauseDialog、LogScreen）
- app.py         PipelineApp 主体
"""
from __future__ import annotations

try:
    from autosmartcut.tui.app import PipelineApp
    __all__ = ["PipelineApp"]
except ImportError:
    # Textual 未安装时不报错，运行时再抛
    __all__ = []
