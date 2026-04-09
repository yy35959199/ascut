# After MVP TODO

> 本文档仅记录“已确认不在 MVP 内”的后续能力清单。  
> MVP 当前实现以 `doc/intelligence-layer2-mvp.md` 为准。

---

## Layer 2 / 智能层

- [ ] 多周目能力：支持第 N 周目、跨周目状态管理与恢复。
- [ ] 多周目数据形态：定义新周目 Manifest 与检查点文件（如 `manifest.layer2.rN`）的落盘关系。
- [ ] 跨周目上下文策略：是否将上一周目 2a 产物注入下一周目 2a，注入范围与优先级规则。
- [ ] 2d 闭环反馈：恢复结构化反馈框 1/2/3（主旨偏差、关键词纠错、内容选择）并打通回流链路。
- [ ] 2d 框 4（剪辑时间节点/气口）：确认最终归属层（执行层或新增阶段）及对应数据契约。
- [ ] checklist 机制重启：重新定义 checklist 在 2a/2b/2c 的生成、消费与验证关系。
- [ ] `checklist_coverage` 展示：在 checklist 回归后补齐 2d 审阅界面显示。
- [ ] 真实 2c 审核：启用 LLM 审核与 `pass/fix_decision/fix_checklist` 三类裁决。
- [ ] 2c 回路控制：引入内外循环边（2c -> 2b、2c -> 2a）与对应状态机实现。
- [ ] Token 与成本可观测性：记录 usage、日志与 CLI 展示。
- [ ] Token 预算守卫：实现硬预算截断，并与循环终止条件联动。
- [ ] Layer 2 异常回退策略：定义 2c/循环启用后的失败回退与人工接管规则。

## 文档对齐任务

- [x] `doc/AutoSmartCut-MVP.md`、`doc/intelligence-layer2-mvp.md`、`doc/AutoSmartCut.md` 等与当前 Layer1（speech + `gap_after`）及 JSON2（`index`+`text`）契约对齐。
- [ ] Demo 2 脚本落地后，将 `demos/demo2_llm.py` 与文档中的示例路径、Prompt 字段再核对一遍。
- [ ] 统一仓库文档命名：`outline_blocks`（理解分块）与 **`keep_mask`**（MVP 多为 manifest/JSON3 顶层；历史文档或曾用 `segments.keep_mask` 嵌套）的术语使用。

