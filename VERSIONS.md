# uibc-core — Version / Record Mapping Table（修法 A · 待 push 草案）

> **一句话结论**：uibc-core 唯一可引用、溯源完整的版本是 **0.2.1**（DOI `10.5281/zenodo.22821835`）；0.2.2 仅存在于 CHANGELOG 声明；0.3.0 是无 tag、无 commit 对应的 Zenodo 孤立记录，引用概念 DOI 当前会解析到它——引用时须避开。

> 目的：解决「GitHub tag、CHANGELOG、Zenodo 记录三处版本口径打架」的静默失败。
> 性质：纯事实对照表，不改任何代码与判据。数据全部为 2026-11-05 匿名实测（Zenodo API + GitHub API），来源逐条可复查。
> 提交方式：新增 `VERSIONS.md` + `README.md` 加一行指针（共 2 文件，最小 diff）。

## 已核实来源（2026-11-05 匿名实测）

| 事实 | 来源 | 结果 |
|---|---|---|
| GitHub tags = `v0.2.1`, `v0.2.1-roadmap` | `api.github.com/repos/LGD-Eval/uibc-core/tags` | HTTP 200 |
| Zenodo 22821835 = v0.2.1 版本记录，2026-09-18 | `zenodo.org/api/records/22821835` | HTTP 200 |
| Zenodo 22821834（概念）latest = 0.3.0 / id 23019217 | `zenodo.org/api/records/22821834` | HTTP 200 |
| CHANGELOG `[Unreleased]` "released as 0.2.2" | `api.github.com/repos/LGD-Eval/uibc-core/contents/CHANGELOG.md` | HTTP 200 |
| CITATION.cff version 0.2.1 / doi 22821835 | `api.github.com/repos/LGD-Eval/uibc-core/contents/CITATION.cff` | HTTP 200 |

## 待核实（诚实边界）

- 0.3.0 Zenodo 记录（23019217）对应的源码 commit **无法确定**：存档 zip 不含 git 元数据，仓内无对应 tag。修法二选一（补 tag 或撤记录），见文末 Follow-ups。
- Zenodo 侧元数据（记录标题/概念记录指向）需 Zenodo 账号登录方可修改，本表不覆盖。

## 术语与缩写

| 词 | 含义 |
|---|---|
| concept DOI | Zenodo 概念 DOI，指向某记录的**最新版本**（随发版漂移） |
| version DOI | 钉死某一个版本快照的 DOI |
| provenance | 溯源：一个版本号能否对应到确切的源码 commit/tag |
| PROPOSAL | 项目状态声明：非已批准标准（见 CHANGELOG status reminder） |

---

## Version — Record Mapping (verified 2026-11-05)

| Version | GitHub tag | GitHub Release | Zenodo version DOI | Zenodo pubdate | Provenance status |
|---|---|---|---|---|---|
| **0.2.1** | `v0.2.1` | published 2026-10-03 | [`10.5281/zenodo.22821835`](https://doi.org/10.5281/zenodo.22821835) | 2026-09-18 | ✅ **fully mapped** — tag, release and Zenodo record align |
| **0.2.2** | — | — | — | — | ⚠️ **declared only** — CHANGELOG `[Unreleased]` says "released as 0.2.2", but no tag, no release, no Zenodo record exists yet |
| **0.3.0** | — | — | [`10.5281/zenodo.23019217`](https://doi.org/10.5281/zenodo.23019217) | 2026-09-28 | 🔴 **Zenodo-only, provenance unmapped** — no matching tag or release; the deposited zip carries no git metadata, so it cannot be tied to a specific commit |

**Concept DOI**: [`10.5281/zenodo.22821834`](https://doi.org/10.5281/zenodo.22821834) — resolves to the *latest Zenodo version*, which is currently **0.3.0** (see 🔴 above). If you need a citable, provenance-verified snapshot, **cite 0.2.1 via [`10.5281/zenodo.22821835`](https://doi.org/10.5281/zenodo.22821835)**.

## How to read this table

1. `CITATION.cff` pins `version: 0.2.1` + `doi: 10.5281/zenodo.22821835` — that pairing is **correct and safe**.
2. The `identifiers:` entry in `CITATION.cff` marks 22821834 as "resolves to the latest release". Read *latest Zenodo version*, which today is 0.3.0 — a record whose build provenance is **not established** (no tag, no commit hash).
3. The Zenodo 0.3.0 title already says "uibc-core v0.3.0", but nothing in this repository produced a v0.3.0 tag. Until a tag↔record mapping exists, treat 0.3.0 as an **unverifiable deposit**, not a release.
4. Everything up to and including 0.2.1 remains a **PROPOSAL** (see CHANGELOG status reminder).

## Follow-ups (tracked, not fixed by this table)

- [ ] 0.2.2: tag + release + Zenodo deposit when the `[Unreleased]` section ships.
- [ ] 0.3.0: either (a) publish the matching tag/commit and record its hash here, or (b) mark the Zenodo record superseded/withdrawn.
- [ ] Zenodo record title for the concept record should not advertise a version that the repository has not tagged (requires Zenodo-side edit, owner action).

---

## 附：修法 diff 概要（2 文件）

1. **新增 `VERSIONS.md`** = 上文正文。
2. **`README.md`** 在引用/Citation 节加一行：
   `> Version–record mapping (including known provenance gaps): see [VERSIONS.md](VERSIONS.md).`
