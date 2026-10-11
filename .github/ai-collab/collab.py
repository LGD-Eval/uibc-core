#!/usr/bin/env python3
"""uibc-core multi-AI collaboration harness (branch-isolated, free tier only).

Seats
-----
  A = reviewer   : correctness / spec conformance
  B = red-team   : adversarial / failure semantics / silent-failure hunting
  C = arbiter    : mutual rebuttal between A and B + final verdict
  D = fixer      : patch proposal (NEVER auto-applied, never auto-merged)

Model backend
-------------
GitHub Models (https://models.github.ai) via the workflow's own built-in
GITHUB_TOKEN with `permissions: models: read`. No personal token, no paid key,
no secret stored in the repo.

Falsifiability (R1): if the model backend is unreachable, the seat records
mode="heuristic-fallback" and still emits REAL findings from deterministic
static checks. The report always states which mode each seat actually ran in.
A seat that produces nothing is reported as FAILED, never silently skipped.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import urllib.error
import urllib.request

API = "https://api.github.com"

# Backend is pluggable and keyless by default.
#   COLLAB_LLM_BASE_URL + COLLAB_LLM_MODELS (+ optional COLLAB_LLM_KEY) -> real model seats
#   unset -> deterministic heuristic seats, honestly labelled "heuristic-fallback"
# NOTE: the former default (GitHub Models, https://models.github.ai) was RETIRED on
# 2026-07-30 for all customers (docs.github.com/en/github-models). It is kept here only
# so `probe` can demonstrate the retirement instead of pretending a seat exists.
LEGACY_MODELS = "https://models.github.ai"
MODELS = (os.environ.get("COLLAB_LLM_BASE_URL") or LEGACY_MODELS).rstrip("/")

# Preferred model seats, most-wanted first. Intersected with the live catalog.
PREFERRED_A = ["openai/gpt-4o-mini", "openai/gpt-4o", "microsoft/phi-4", "meta/llama-3.3-70b-instruct"]
PREFERRED_B = ["mistral-ai/mistral-small", "cohere/cohere-command-r-plus-08-2024", "meta/llama-3.3-70b-instruct", "microsoft/phi-4"]
PREFERRED_C = ["openai/gpt-4o", "openai/gpt-4o-mini", "meta/llama-3.3-70b-instruct"]
PREFERRED_D = ["openai/gpt-4o-mini", "microsoft/phi-4", "meta/llama-3.3-70b-instruct"]

MAX_FILES = 8
MAX_BYTES = 200_000
TEXT_EXT = (".py", ".yml", ".yaml", ".md", ".json", ".toml", ".cfg", ".txt")


def env(key: str, default: str = "") -> str:
    return os.environ.get(key, default) or default


def token() -> str:
    t = env("GH_TOKEN") or env("GITHUB_TOKEN")
    if t:
        return t
    # local-diagnosis fallback only: in CI the built-in GITHUB_TOKEN is always in env
    try:
        import subprocess
        r = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, timeout=20)
        if r.returncode == 0:
            return r.stdout.strip()
    except Exception:  # noqa: BLE001
        pass
    return ""


def http(url: str, data=None, method: str = "GET", tok: str | None = None, auth_style: str = "Bearer"):
    """Minimal HTTP helper. Returns (status, text). Never raises."""
    hdrs = {"Accept": "application/vnd.github+json", "User-Agent": "uibc-ai-collab"}
    t = tok if tok is not None else token()
    if t:
        hdrs["Authorization"] = (auth_style + " " + t) if auth_style else t
    body = None
    if data is not None:
        body = json.dumps(data).encode("utf-8")
        hdrs["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=int(env("COLLAB_HTTP_TIMEOUT", "120"))) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")
    except Exception as exc:  # noqa: BLE001
        return -1, f"{type(exc).__name__}: {exc}"


def gh(path: str):
    status, raw = http(API + path)
    try:
        return status, json.loads(raw)
    except Exception:  # noqa: BLE001
        return status, {"_raw": raw[:4000]}


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()


def write_json(path: str, obj) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)


def read_json(path: str):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


# --------------------------------------------------------------------------- #
# model backend
# --------------------------------------------------------------------------- #

CATALOG_DIAG: list = []


def catalog_models() -> list[str]:
    """Try every plausible auth style; record a per-attempt diagnosis.

    Self-diagnosing by design: when the backend is unreachable the reason lands
    in the workflow log and in the report instead of vanishing into a fallback.
    """
    global CATALOG_DIAG
    CATALOG_DIAG = []
    configured = [m.strip() for m in env("COLLAB_LLM_MODELS").split(",") if m.strip()]
    if configured:
        CATALOG_DIAG.append({"source": "COLLAB_LLM_MODELS", "models": len(configured),
                             "base_url_configured": bool(env("COLLAB_LLM_BASE_URL"))})
        return configured
    for style in ("Bearer", "token", ""):
        status, raw = http(MODELS + "/catalog", auth_style=style)
        ids: list[str] = []
        if status == 200:
            try:
                data = json.loads(raw)
            except Exception:  # noqa: BLE001
                data = None
            if isinstance(data, dict):
                data = data.get("models") or data.get("data") or []
            for item in data or []:
                if isinstance(item, dict):
                    mid = item.get("id") or item.get("name")
                    if mid:
                        ids.append(str(mid))
        CATALOG_DIAG.append({"auth": style or "none", "status": status,
                             "models": len(ids), "body_head": raw[:200]})
        if ids:
            return ids
    return []


def pick(preferred: list[str], available: list[str]) -> str | None:
    for want in preferred:
        if want in available:
            return want
    return available[0] if available else None


def chat(model: str, system: str, user: str) -> dict:
    payload = {
        "model": model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "temperature": 0.2,
        "max_tokens": 900,
    }
    last = {"ok": False, "error": "no attempt"}
    for style in ("Bearer", "token"):
        status, raw = http(MODELS + "/inference/chat/completions", data=payload, method="POST",
                           tok=(env("COLLAB_LLM_KEY") or token()), auth_style=style)
        if status == 200:
            try:
                data = json.loads(raw)
                return {"ok": True, "text": data["choices"][0]["message"]["content"]}
            except Exception as exc:  # noqa: BLE001
                last = {"ok": False, "error": f"[{style}] parse: {exc}: {raw[:200]}"}
        else:
            last = {"ok": False, "error": f"[{style}] HTTP {status}: {raw[:200]}"}
    return last


# --------------------------------------------------------------------------- #
# deterministic reviewer (fallback + always-run second opinion)
# --------------------------------------------------------------------------- #

CHECKS = [
    (r"\beval\s*\(", "high", "eval() on dynamic input -- code-execution surface"),
    (r"\bexec\s*\(", "high", "exec() on dynamic input -- code-execution surface"),
    (r"subprocess\.[A-Za-z_]+\([^)]*shell\s*=\s*True", "high", "shell=True -- injection surface"),
    (r"except\s*:\s*$|except\s+Exception\s*:\s*pass", "medium", "bare/blanket except swallows failures silently"),
    (r"TODO|FIXME|XXX", "low", "unresolved TODO/FIXME marker"),
    (r"assert\s+True\b", "medium", "assert True -- no-op test, provides no evidence"),
    (r"return\s+None\s*#\s*stub|NotImplementedError", "medium", "stub path -- declared but unimplemented"),
    (r"0\.0\.0\.0", "medium", "binds to all interfaces"),
    (r"(?i)(password|secret|token|api[_-]?key)\s*=\s*[\"'][^\"']{6,}", "high", "hard-coded credential literal"),
]


def heuristic_findings(path: str, text: str) -> list[dict]:
    out = []
    lines = text.splitlines()
    for pattern, sev, msg in CHECKS:
        rx = re.compile(pattern)
        for i, line in enumerate(lines, 1):
            if rx.search(line):
                out.append({"file": path, "line": i, "severity": sev, "issue": msg, "evidence": line.strip()[:160]})
    if path.endswith(".py") and "def test_" not in text and path.startswith("tests/"):
        out.append({"file": path, "line": 0, "severity": "medium", "issue": "test module declares no test_ function", "evidence": ""})
    return out


def seat_prompt(seat: str) -> str:
    if seat == "a":
        return (
            "You are REVIEWER-A in a multi-AI review of the repository zhaoxinghua09-cell/uibc-core "
            "(LGD / UIBC reference implementation). Focus on correctness and spec conformance. "
            "Return STRICT JSON: {\"findings\":[{\"file\":str,\"line\":int,\"severity\":\"high|medium|low\","
            "\"issue\":str,\"evidence\":str}],\"summary\":str}. No prose outside the JSON."
        )
    return (
        "You are RED-TEAM-B. Adversarially hunt for silent failures, unverifiable claims, missing negative "
        "controls and failure-semantics gaps in the given files. Assume the reviewer missed something and say "
        "what. Return STRICT JSON: {\"findings\":[{\"file\":str,\"line\":int,\"severity\":\"high|medium|low\","
        "\"issue\":str,\"evidence\":str}],\"summary\":str}. No prose outside the JSON."
    )


def extract_json(text: str) -> dict | None:
    if not text:
        return None
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:  # noqa: BLE001
        return None


# --------------------------------------------------------------------------- #
# sub-commands
# --------------------------------------------------------------------------- #

def repo() -> str:
    return env("GITHUB_REPOSITORY", "zhaoxinghua09-cell/uibc-core")


def target_ref() -> str:
    return env("COLLAB_REF", "ai-collab")


def cmd_probe(args) -> int:
    """Backend reachability probe with full diagnosis (safe to run locally or in CI)."""
    avail = catalog_models()
    model = pick(PREFERRED_A, avail)
    if model:
        res = chat(model, "Reply with the single word: ok", "ping")
    else:
        res = {"ok": False, "error": "no model id available from catalog"}
    out = {
        "endpoint": MODELS,
        "legacy_endpoint_retired": "GitHub Models retired 2026-07-30 for all customers (docs.github.com/en/github-models)",
        "configured_model_list": env("COLLAB_LLM_MODELS"),
        "catalog_attempts": CATALOG_DIAG,
        "models_found": len(avail),
        "models_head": avail[:15],
        "chat_model": model,
        "chat_ok": res.get("ok"),
        "chat_detail": (res.get("text") or res.get("error") or "")[:300],
    }
    print(json.dumps(out, ensure_ascii=False, indent=2)[:4000])
    return 0


def cmd_divide(args) -> int:
    base = args.base or "main"
    ref = args.ref or target_ref()
    status, cmp = gh(f"/repos/{repo()}/compare/{base}...{ref}")
    files = []
    if status == 200 and isinstance(cmp, dict):
        for f in cmp.get("files", []):
            name = f.get("filename", "")
            if not name.endswith(TEXT_EXT) or (f.get("changes") or 0) > 4000:
                continue
            files.append(name)
    if not files:
        status, tree = gh(f"/repos/{repo()}/contents/.github/ai-collab?ref={ref}")
        if isinstance(tree, list):
            files = [t["path"] for t in tree if t.get("path", "").endswith(TEXT_EXT)]
    files = files[:MAX_FILES]

    tasks = []
    for i, path in enumerate(files, 1):
        st, blob = gh(f"/repos/{repo()}/contents/{path}?ref={ref}")
        content = ""
        if st == 200 and isinstance(blob, dict) and blob.get("content"):
            import base64
            try:
                content = base64.b64decode(blob["content"]).decode("utf-8", "replace")
            except Exception:  # noqa: BLE001
                content = ""
        tasks.append({
            "task_id": f"T{i:02d}",
            "file": path,
            "sha256": sha(content),
            "bytes": len(content),
            "seats": ["A:reviewer", "B:red-team"],
            "deliverable": "findings JSON (severity + evidence)",
        })

    out = {
        "seat": "orchestrator",
        "repo": repo(),
        "base": base,
        "ref": ref,
        "file_count": len(tasks),
        "tasks": tasks,
        "note": "deterministic assignment: every target file goes to BOTH A and B in parallel",
    }
    write_json(args.out, out)
    print(json.dumps({"divided": len(tasks), "out": args.out}, ensure_ascii=False))
    return 0


def cmd_review(args) -> int:
    tasks = read_json(args.tasks)
    seat = args.seat
    avail = catalog_models()
    cfg = [m.strip() for m in env("COLLAB_LLM_MODELS").split(",") if m.strip()]
    if cfg:
        # heterogeneous seats by design: two DIFFERENT local models => independent perspectives
        model = cfg[0] if seat == "a" else (cfg[1] if len(cfg) > 1 else cfg[0])
    else:
        model = pick(PREFERRED_A if seat == "a" else PREFERRED_B, avail)
    results, mode = [], "heuristic-fallback"
    model_errors = []
    import base64
    for t in tasks["tasks"]:
        st, blob = gh(f"/repos/{repo()}/contents/{t['file']}?ref={tasks['ref']}")
        content = ""
        if st == 200 and isinstance(blob, dict) and blob.get("content"):
            try:
                content = base64.b64decode(blob["content"]).decode("utf-8", "replace")
            except Exception:  # noqa: BLE001
                content = ""
        findings, summary = heuristic_findings(t["file"], content), "deterministic static checks"
        if model and content:
            res = chat(model, seat_prompt(seat), f"FILE: {t['file']}\n---\n{content[:12000]}")
            if res.get("ok"):
                parsed = extract_json(res["text"]) or {}
                model_findings = parsed.get("findings") or []
                if model_findings:
                    findings = model_findings
                    mode = "model"
                summary = parsed.get("summary") or summary
            else:
                model_errors.append(res.get("error", "")[:200])
        results.append({
            "input_sha256": t["sha256"],
            "file": t["file"],
            "finding_count": len(findings),
            "findings": findings,
            "summary": summary,
        })
    out = {
        "seat": seat.upper(),
        "model": model if mode == "model" else "(none)",
        "mode": mode,
        "catalog_size": len(avail),
        "catalog_diag": CATALOG_DIAG,
        "model_errors": model_errors[:3],
        "handoff": {
            "received_tasks_sha256": sha(json.dumps(tasks, ensure_ascii=False, sort_keys=True)),
            "item_count_in": len(tasks["tasks"]),
            "item_count_out": len(results),
        },
        "results": results,
        "total_findings": sum(r["finding_count"] for r in results),
    }
    out["output_sha256"] = sha(json.dumps(out.get("results"), ensure_ascii=False, sort_keys=True))
    write_json(args.out, out)
    print(json.dumps({"seat": seat, "mode": mode, "findings": out["total_findings"],
                      "catalog": CATALOG_DIAG, "errors": model_errors[:2]}, ensure_ascii=False))
    return 0


def cmd_rebut(args) -> int:
    a, b = read_json(args.review_a), read_json(args.review_b)
    avail = catalog_models()
    cfg = [m.strip() for m in env("COLLAB_LLM_MODELS").split(",") if m.strip()]
    model = cfg[0] if cfg else pick(PREFERRED_C, avail)

    def key(f):
        return (f.get("file"), f.get("issue"))

    a_only = [f for r in a["results"] for f in r["findings"] if key(f) not in {key(x) for rr in b["results"] for x in rr["findings"]}]
    b_only = [f for r in b["results"] for f in r["findings"] if key(f) not in {key(x) for rr in a["results"] for x in rr["findings"]}]
    agreed = [f for r in a["results"] for f in r["findings"] if key(f) in {key(x) for rr in b["results"] for x in rr["findings"]}]

    rebuttals, verdict_mode = [], "structural-diff"
    contested = []
    if model:
        res = chat(model, (
            "You are ARBITER-C. Two independent AI seats (A reviewer, B red-team) reviewed the same files. "
            "You must (1) list where they CONFLICT, (2) rebut the weaker side with a concrete reason, "
            "(3) issue a verdict per contested item: ACCEPT_A | ACCEPT_B | NEEDS_HUMAN. "
            "Be adversarial: a claim repeated by both is not automatically true. "
            "Return STRICT JSON: {\"contested\":[{\"topic\":str,\"side_a\":str,\"side_b\":str,\"rebuttal\":str,"
            "\"verdict\":str}],\"summary\":str}"
        ), json.dumps({"A_only": a_only[:20], "B_only": b_only[:20], "agreed": agreed[:20]}, ensure_ascii=False)[:14000])
        if res.get("ok"):
            parsed = extract_json(res["text"]) or {}
            if parsed.get("contested"):
                contested = parsed["contested"]
                verdict_mode = "model"
                rebuttals.append(parsed.get("summary", ""))

    for f in a_only[:20]:
        contested.append({"topic": f"{f.get('file')}: {f.get('issue')}", "side_a": "RAISED", "side_b": "SILENT",
                          "rebuttal": "Only seat A flagged this; seat B must either confirm or state why it is a non-issue.",
                          "verdict": "NEEDS_HUMAN"})
    for f in b_only[:20]:
        contested.append({"topic": f"{f.get('file')}: {f.get('issue')}", "side_a": "SILENT", "side_b": "RAISED",
                          "rebuttal": "Only seat B flagged this; adversarial claim requires human confirmation before action.",
                          "verdict": "NEEDS_HUMAN"})

    out = {
        "seat": "C",
        "model": model if verdict_mode == "model" else "(none)",
        "mode": verdict_mode,
        "handoff": {
            "review_a_sha256": a.get("output_sha256"),
            "review_b_sha256": b.get("output_sha256"),
            "items_in": {"a": a.get("total_findings"), "b": b.get("total_findings")},
            "items_out": len(contested),
        },
        "consensus_findings": len(agreed),
        "a_only": len(a_only),
        "b_only": len(b_only),
        "contested": contested[:40],
        "arbiter_summary": " ".join(rebuttals)[:1200],
    }
    write_json(args.out, out)
    print(json.dumps({"contested": len(contested), "mode": verdict_mode, "consensus": len(agreed)}, ensure_ascii=False))
    return 0


def cmd_fix(args) -> int:
    reb = read_json(args.rebuttal)
    avail = catalog_models()
    cfg = [m.strip() for m in env("COLLAB_LLM_MODELS").split(",") if m.strip()]
    model = cfg[0] if cfg else pick(PREFERRED_D, avail)
    lines = ["# Proposed fixes (seat D) -- NOT APPLIED, awaiting human acceptance", ""]
    mode = "heuristic-fallback"
    if model:
        res = chat(model, (
            "You are FIXER-D. Given contested review findings, produce the SMALLEST safe patch proposals. "
            "Never propose deleting functionality. Never propose touching main. Output a unified-diff style "
            "sketch plus a risk note per item. Markdown only."
        ), json.dumps(reb.get("contested", [])[:20], ensure_ascii=False)[:12000])
        if res.get("ok") and res.get("text"):
            lines += [res["text"], ""]
            mode = "model"
    lines.append("## Mechanical checklist derived from the rebuttal verdicts")
    for item in reb.get("contested", [])[:20]:
        lines.append(f"- [ ] `{item.get('topic')}` -> verdict `{item.get('verdict')}` :: {item.get('rebuttal','')[:160]}")
    lines += ["", "> Applied to `main`: **NO**. Applied to the collab branch: **NO** (proposal only)."]
    os.makedirs(os.path.dirname(os.path.abspath(args.out)) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    write_json(args.out + ".meta.json", {"seat": "D", "mode": mode, "model": model or "(none)", "items": len(reb.get("contested", []))})
    print(json.dumps({"fix_proposal": args.out, "mode": mode}, ensure_ascii=False))
    return 0


def cmd_collect_tests(args) -> int:
    try:
        log = open(args.log, encoding="utf-8", errors="replace").read()
    except Exception:  # noqa: BLE001
        log = ""
    m = re.search(r"(\d+) passed", log)
    passed = int(m.group(1)) if m else 0
    m = re.search(r"(\d+) failed", log)
    failed = int(m.group(1)) if m else 0
    m = re.search(r"(\d+) error", log)
    errors = int(m.group(1)) if m else 0
    m = re.search(r"collected (\d+) item", log)
    collected = int(m.group(1)) if m else (passed + failed)
    out = {
        "seat": "test-harness",
        "collected": collected,
        "passed": passed,
        "failed": failed,
        "errors": errors,
        "exit_code": args.exit_code,
        "log_sha256": sha(log),
        "log_tail": log[-1500:],
    }
    write_json(args.out, out)
    print(json.dumps({k: out[k] for k in ("collected", "passed", "failed", "errors", "exit_code")}, ensure_ascii=False))
    return 0


def cmd_aggregate(args) -> int:
    def locate(basename):
        """Class-level fix: artifact uploads may nest paths (LCA of the uploaded
        file set decides the internal layout), so never assume a flat dir --
        search recursively for the basename."""
        for root, _dirs, files in os.walk(args.dir):
            for fn in files:
                if fn == basename:
                    return os.path.join(root, fn)
        return None

    def load(name):
        p = locate(name)
        if not p:
            return None
        try:
            return read_json(p)
        except Exception:  # noqa: BLE001
            return None

    tasks = load("tasks.json")
    ra, rb = load("review-a.json"), load("review-b.json")
    reb = load("rebuttal.json")
    fixmeta = load("proposed-fix.md.meta.json")
    tests = load("tests.json")
    fixmd = ""
    fp = locate("proposed-fix.md")
    if fp:
        with open(fp, encoding="utf-8") as fh:
            fixmd = fh.read()

    missing = [n for n, v in [("tasks", tasks), ("review-a", ra), ("review-b", rb), ("rebuttal", reb), ("tests", tests)] if v is None]

    L = []
    L.append("# uibc-core multi-AI collaboration report")
    L.append("")
    L.append(f"- repo: `{repo()}`  |  ref: `{env('COLLAB_REF','ai-collab')}`  |  run: `{env('GITHUB_RUN_ID','-')}`")
    L.append("- branch-isolated: this run writes ONLY to a non-main branch; `main` is never touched.")
    L.append(f"- pipeline integrity: missing stages = {missing or 'none'}")
    L.append("")
    L.append("## 1. 自动分工 (task division)")
    if tasks:
        L.append(f"- targets: **{tasks['file_count']}** files, every file assigned to BOTH seats in parallel")
        L.append("")
        L.append("| task | file | sha256 (12) | seats |")
        L.append("|---|---|---|---|")
        for t in tasks["tasks"]:
            L.append(f"| {t['task_id']} | `{t['file']}` | `{t['sha256'][:12]}` | {', '.join(t['seats'])} |")
    else:
        L.append("- **FAILED**: no tasks.json")
    L.append("")
    L.append("## 2. 并行审查 (parallel review, two independent seats)")
    for tag, r in (("A reviewer", ra), ("B red-team", rb)):
        if not r:
            L.append(f"- {tag}: **FAILED (no artifact)**")
            continue
        L.append(f"- {tag}: mode=`{r['mode']}` model=`{r['model']}` findings=**{r['total_findings']}** "
                 f"(in {r['handoff']['item_count_in']} / out {r['handoff']['item_count_out']}) out_sha=`{r['output_sha256'][:12]}`")
        if r["mode"] != "model":
            L.append(f"  - backend diagnosis: catalog_size={r.get('catalog_size')} "
                     f"diag={json.dumps(r.get('catalog_diag'), ensure_ascii=False)[:400]} "
                     f"errors={json.dumps(r.get('model_errors'), ensure_ascii=False)[:300]}")
    L.append("")
    oa, ob = load("review-ollama-a.json"), load("review-ollama-b.json")
    if oa or ob:
        L.append("### 2b. 真·模型席位 (real-model seats, self-hosted runner + local Ollama)")
        for tag, r in (("reviewer-local", oa), ("red-team-local", ob)):
            if r:
                L.append(f"- {tag}: mode=`{r['mode']}` model=`{r['model']}` findings=**{r['total_findings']}** "
                         f"(in {r['handoff']['item_count_in']} / out {r['handoff']['item_count_out']})")
            else:
                L.append(f"- {tag}: artifact missing (runner ran but seat output lost)")
    else:
        L.append("### 2b. 真·模型席位 (real-model seats): offline / not configured — heuristic seats carried this run")
    L.append("")
    L.append("## 3. 相互反驳 (mutual rebuttal + arbitration)")
    if reb:
        L.append(f"- arbiter mode=`{reb['mode']}` model=`{reb['model']}`")
        L.append(f"- consensus={reb['consensus_findings']} | A-only={reb['a_only']} | B-only={reb['b_only']} | contested=**{len(reb['contested'])}**")
        L.append("")
        L.append("| topic | A | B | verdict | rebuttal |")
        L.append("|---|---|---|---|---|")
        for c in reb["contested"][:20]:
            L.append(f"| {c.get('topic','')[:60]} | {c.get('side_a','')} | {c.get('side_b','')} | **{c.get('verdict','')}** | {c.get('rebuttal','')[:90]} |")
    else:
        L.append("- **FAILED**: no rebuttal.json")
    L.append("")
    L.append("## 4. 修复建议 (fix proposals -- NOT applied)")
    L.append(f"- seat D mode=`{(fixmeta or {}).get('mode','?')}` items={(fixmeta or {}).get('items','?')}")
    if fixmd:
        L.append("")
        L.append("<details><summary>proposed-fix.md</summary>")
        L.append("")
        L.append(fixmd[:6000])
        L.append("")
        L.append("</details>")
    L.append("")
    L.append("## 5. 测试 (test collection)")
    if tests:
        L.append(f"- collected=**{tests['collected']}** passed=**{tests['passed']}** failed=**{tests['failed']}** errors=**{tests['errors']}** exit={tests['exit_code']}")
        L.append(f"- log sha256=`{tests['log_sha256'][:16]}`")
    else:
        L.append("- **FAILED**: no tests.json")
    L.append("")
    L.append("## 6. 人工验收 (human final acceptance — REQUIRED)")
    L.append("- [ ] Steven: read §2/§3, decide which contested items block")
    L.append("- [ ] Steven: accept or reject the §4 fix proposals (nothing is applied automatically)")
    L.append("- [ ] Steven: merge (or not) `ai-collab` into `main` **manually**")
    L.append("- [ ] auto-merge: **disabled by design**; no publishing, no release, no PR to main from this pipeline")
    L.append("")
    L.append("---")
    L.append(f"generated by .github/ai-collab/collab.py (run `{env('GITHUB_RUN_ID','-')}`)")
    os.makedirs(os.path.dirname(os.path.abspath(args.out)) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write("\n".join(L) + "\n")
    print(json.dumps({"report": args.out, "missing_stages": missing}, ensure_ascii=False))
    return 1 if missing else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    pr = sub.add_parser("probe"); pr.set_defaults(fn=cmd_probe)
    d = sub.add_parser("divide"); d.add_argument("--base", default="main"); d.add_argument("--ref"); d.add_argument("--out", required=True); d.set_defaults(fn=cmd_divide)
    r = sub.add_parser("review"); r.add_argument("--seat", required=True, choices=["a", "b"]); r.add_argument("--tasks", required=True); r.add_argument("--out", required=True); r.set_defaults(fn=cmd_review)
    b = sub.add_parser("rebut"); b.add_argument("--review-a", required=True); b.add_argument("--review-b", required=True); b.add_argument("--out", required=True); b.set_defaults(fn=cmd_rebut)
    f = sub.add_parser("fix"); f.add_argument("--rebuttal", required=True); f.add_argument("--out", required=True); f.set_defaults(fn=cmd_fix)
    t = sub.add_parser("collect-tests"); t.add_argument("--log", required=True); t.add_argument("--out", required=True); t.add_argument("--exit-code", default="0"); t.set_defaults(fn=cmd_collect_tests)
    a = sub.add_parser("aggregate"); a.add_argument("--dir", required=True); a.add_argument("--out", required=True); a.set_defaults(fn=cmd_aggregate)

    args = ap.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
