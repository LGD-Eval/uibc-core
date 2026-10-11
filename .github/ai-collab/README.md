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

## 模型后端（免费、零密钥）

用 **GitHub Models**（`https://models.github.ai`），鉴权用工作流自带的 `GITHUB_TOKEN`
（`permissions: models: read`）。

- ❌ 不需要个人 PAT，❌ 不需要付费 key，❌ 仓库里不存任何密钥。
- 席 A/B/C/D 从实时目录 `GET /catalog` 里各自挑一个模型（偏好列表见 `collab.py`）。
- **可证伪**：若后端不可达，该席记 `mode="heuristic-fallback"`，改用确定性静态检查
  （`eval/exec`、`shell=True`、吞异常、硬编码凭据、`assert True`、绑定 `0.0.0.0` 等）
  仍产出**真实**发现；报告会如实标注每个席实际跑的是 `model` 还是 `heuristic-fallback`。
  某席什么都没产出 → 报告写 `FAILED`，聚合步骤以非 0 退出。

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
