# ai-collab — multi-AI 自动协作流水线（uibc-core）

> 分支隔离 · 只用免费额度 · 不自动合并 · 人工终验收
> 本目录由 `ai-collab` 流水线自己维护，`main` 永不被本流水线写入。

## 它做什么

一条流水线串起七个阶段，全程留证（每阶段产出带 sha256 的 JSON 工件）：

| # | 阶段 | 谁来做 | 产出 |
|---|------|--------|------|
| 0 | guard | — | 拒绝在 `main` 上运行（硬失败） |
| 1 | 自动分工 | 编排席（确定性脚本） | `tasks.json`：每个目标文件同时派给 A/B 两席 |
| 2 | 并行审查 | 席 A 审查员 + 席 B 红队（**并行两 job**） | `review-a.json` / `review-b.json` |
| 3 | 相互反驳 | 席 C 仲裁 + 结构化差异比对 | `rebuttal.json`：共识 / 单向发现 / 争议项 + 裁定 |
| 4 | 修复 | 席 D 修复员 | `proposed-fix.md`（**只提案，绝不落盘到 main、绝不自动应用**） |
| 5 | 测试 | pytest | `tests.json`（collected / passed / failed / exit code + 日志哈希） |
| 6 | 汇总 | 聚合器 | `COLLAB-REPORT.md` → 只提交到协作分支 + 上传工件 |
| 7 | **人工验收** | **Steven** | 报告里勾选清单；合并/不合并由人决定 |

## 模型后端（默认零密钥 / 可插拔）

**关键事实（2026-10-11 核实）**：`models.github.ai`（GitHub Models）**已于 2026-07-30 对所有客户全面退役**——
playground、模型目录、推理 API、BYOK 全部停止（据官方页 `docs.github.com/en/github-models`）。
实测证据：CI 内 `GET /catalog` 返回 `HTTP 200` + 正文 `OK`、`models=0`（退役网关的健康应答）。

因此本流水线的后端是**可插拔**的：

| 配置 | 席位行为 | 报告如何标注 |
|---|---|---|
| 默认（什么都不配） | 确定性启发式席位（静态检查：`eval/exec`、`shell=True`、吞异常、硬编码凭据、`assert True`、`0.0.0.0`…） | `mode="heuristic-fallback"` |
| 配了 `COLLAB_LLM_BASE_URL` + `COLLAB_LLM_MODELS`（OpenAI 兼容） | 真正的多模型席位（A 审查员 / B 红队 / C 仲裁 / D 修复） | `mode="model"` + 模型名 |
| 另配 `COLLAB_LLM_KEY`（可选） | 同上，带鉴权 | 同上 |

- 前两项用**仓库 Variables**（非机密）；密钥只走**仓库 Secrets**，绝不写进仓库文件、也绝不进对话。
- **零密钥默认**：不配置也能跑完整七阶段，且报告如实标注席位模式——**不会假装某模型已接入**。
- 想升级成真·多模型又不花钱，两条现成路线（待 Steven 拍板，见会话汇报）：
  ①**自托管 runner** 挂在本机 Ollama（qwen3:8b 等，零成本、零外发）；
  ②填一个免费/自有 OpenAI 兼容端点到仓库 Variables/Secrets。
- 安全基线对齐：本流水线所有 `actions/*` 均已**固定到 commit SHA**（配合另一窗口的 OpenSSF Scorecard 整改线）。

## 手动触发

```bash
# 分支推送到 ai-collab 时自动跑；也可手动 dispatch：
gh workflow run ai-collab.yml --ref ai-collab -f base=main -f ref=ai-collab
```

## 设计红线（不可被配置绕过）

- `guard` job 在 `main` 上直接失败 ⇒ 永不触碰 main。
- 无 `pull_request_target`、无 `push` 到 main、无 release/publish 步骤。
- 报告提交信息带 `[skip ci]`，且 `paths-ignore` 覆盖 `runs/`，**不会自触发循环**。
- 席 D 只写 `proposed-fix.md`，**没有任何步骤把它应用到工作区**。

## 目录

```
.github/workflows/ai-collab.yml   # 流水线定义
.github/ai-collab/collab.py       # 各席实现（divide/review/rebut/fix/collect-tests/aggregate）
.github/ai-collab/runs/           # 每次运行的协作报告归档（机器人提交）
```
