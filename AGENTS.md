# overseas-stock-tax — Agent Guide

> 本文件是 OpenAI Codex CLI / 其它通用 AI Agent 的入口。
> Claude Code 用户请直接读 [SKILL.md](./SKILL.md)（同一份指南，仅文件名不同）。

## What this project is

一个面向中国境内自然人的 **港美股个税计算工具**。输入富途/长桥的 PDF 月结单或 Excel 交易明细，输出 4 份可直接对照税局《B 表》填写的 Excel：

1. `01_逐笔交易底稿_{年份}.xlsx`
2. `02_B表填报字段_{年份}.xlsx`
3. `03_四算法对比表_{年份}.xlsx`
4. `04_税务综合报告与合规建议_{年份}.xlsx`

## How to run as an Agent

1. **先读 [SKILL.md](./SKILL.md)** ——里面有完整的 8 步工作流、5 节点对账、4 算法说明、硬约束与禁止行为。`SKILL.md` 是唯一权威指南，本文件只是入口。
2. **再读** `references/01-政策依据.md` 与 `references/02-计算口径与四算法对比.md`。
3. 调用脚本：`scripts/parse_futu.py` / `parse_longbridge.py` 解析流水 → `cost_basis.py` 算成本 → `fx_rate.py` 换汇 → `compute_tax.py` 算税 → `reconcile.py` 对账 → `render_report.py` 出 Excel。
4. 单元测试：`python3 scripts/test_cost_basis.py`（12 个用例必须全过）。

## Hard constraints (read SKILL.md §Hard constraints for full list)

- ❌ **禁止用 LLM 视觉能力直接读 PDF 数字**——必须走 `parse_*.py` 脚本（基于 `pdfplumber` 文本层提取）
- ❌ **禁止 LLM 心算金额**——所有计算走 Python `Decimal`，禁用 float
- ❌ **禁止跳过 5 节点对账**——任一节点失败必须阻塞输出
- ❌ **禁止给灰色避税建议**（隐匿账户、虚构亏损、推迟申报等）
- ✅ 4 算法都算，让用户决策——不替用户选

## File layout

| 路径 | 作用 |
|---|---|
| `SKILL.md` | ⭐ 主指南（Claude Code 与本文件等价） |
| `references/*.md` | 8 篇参考资料（政策、算法、流水字段、汇率、城市差异、抵免） |
| `scripts/*.py` | 计算引擎（schema / parsers / cost_basis / fx_rate / compute_tax / penalty / reconcile / render_report / tests） |
| `assets/cny_mid_rate.json` | 央行人民币中间价缓存（公开数据） |
| `index.html` + `app.js` + `style.css` | 浏览器纯前端（Pyodide）|

## Disclaimer

本工具输出**仅供参考**，最终申报金额以主管税务机关认定为准。强烈建议委托执业税务师 / 注册会计师签字复核。详见 SKILL.md 末尾的合规声明。
