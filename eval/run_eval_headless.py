#!/usr/bin/env python3
"""Retrieval evaluation through Claude Code headless (`claude -p`) + the
canada50 MCP server — no Anthropic SDK, no API key (standing decision).

    python3 eval/run_eval_headless.py                      # all questions
    python3 eval/run_eval_headless.py --ids q13,q14 --model sonnet
    python3 eval/run_eval_headless.py --dry                # list questions

Each question is one `claude -p` run with the canada50 server as the only
MCP server (--strict-mcp-config) and only its tools allowed; the answer text
is scored exactly as eval/run_eval.py does (`facts` = fraction of expect
groups matched, `cited` = cite regex hit). Tool traces come from
--output-format stream-json when available; the summary JSON is kept either
way. Results append to eval/results/headless_<model>.jsonl.

Prerequisites: `python3 build/gen_wiki.py && python3 -m canada50_mcp.index`
and the `mcp` package (`pip install -e ".[mcp]"`).
"""
import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
QUESTIONS = ROOT / "eval/questions.jsonl"
MCP_CONFIG = ROOT / "eval/mcp/canada50.json"

SYSTEM = """You are a research assistant answering questions about the Canadian
Department of Indian Affairs annual reports (1880–1930) using ONLY the canada50
MCP tools (lookup_entity, open_page, search, neighbors, series, get_document,
get_segment). Do not use any other tool, file, or prior knowledge.

Method: lookup_entity to find entities (prefer the best-attested candidate when
several share a name); neighbors for relations with the years they are attested
(pass year= for a snapshot); series for annual statistics (population, school
roll, crops, trust funds); open_page for hub pages with cited passages; search
(FTS5 BM25) for text — and for a question about one report year, get_document(year)
to see that report's regional and thematic sections; get_segment to read the text at an address before asserting
anything a passage must support. Answer concisely, then cite every factual claim
with its source: an address `doc_id / segment_id` and printed page ([p. N] in
segment text), or for a statistic the paper_id and page the series tool returns.
If the sources do not settle the question, say so rather than guessing.
Historical bands are distinct from the modern First Nations that succeeded them."""


def score(answer, q):
    a = answer.lower()
    groups = q.get("expect", [])
    hit = [any(alt.lower() in a for alt in grp) for grp in groups]
    facts = sum(hit) / len(groups) if groups else 1.0
    cited = bool(re.search(q["cite"], answer, re.I)) if q.get("cite") else True
    return {"facts": facts, "missed": [g for g, h in zip(groups, hit) if not h],
            "cited": cited, "pass": facts == 1.0 and cited}


def run_question(q, model, max_turns, timeout):
    cmd = ["claude", "-p", q["question"], "--mcp-config", str(MCP_CONFIG), "--strict-mcp-config",
           "--allowedTools", "mcp__canada50__*", "--disallowedTools", "Bash,Read,Write,Edit,Glob,Grep,WebFetch,WebSearch,Agent",
           "--system-prompt", SYSTEM, "--output-format", "stream-json", "--verbose",
           "--max-turns", str(max_turns)]
    if model:
        cmd += ["--model", model]
    t0 = time.time()
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL,
                           cwd=str(ROOT))
    except subprocess.TimeoutExpired:
        return {"answer": "", "error": f"timeout after {timeout}s", "tool_calls": [], "seconds": timeout}
    trace, answer, summary = [], "", {}
    for line in p.stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if ev.get("type") == "assistant":
            for block in ev.get("message", {}).get("content", []):
                if block.get("type") == "tool_use":
                    trace.append({"tool": block.get("name", "").replace("mcp__canada50__", ""),
                                  "args": block.get("input")})
        elif ev.get("type") == "result":
            summary = ev
            answer = ev.get("result") or ""
    if not answer and p.returncode != 0:
        return {"answer": "", "error": (p.stderr or p.stdout)[-800:], "tool_calls": trace,
                "seconds": round(time.time() - t0, 1)}
    return {"answer": answer, "stop_reason": summary.get("stop_reason"),
            "tool_calls": trace, "num_turns": summary.get("num_turns"),
            "cost_usd": summary.get("total_cost_usd"), "seconds": round(time.time() - t0, 1)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ids")
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--model", default="sonnet")
    ap.add_argument("--max-turns", type=int, default=30)
    ap.add_argument("--timeout", type=int, default=600)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    qs = [json.loads(l) for l in QUESTIONS.read_text(encoding="utf-8").splitlines()
          if l.strip() and not l.startswith("#")]
    if args.ids:
        want = set(args.ids.split(","))
        qs = [q for q in qs if q["id"] in want]
    if args.dry:
        for q in qs:
            print(f"{q['id']}: {q['question']}\n    expect {q['expect']}  cite {q.get('cite')}")
        return 0
    out = Path(args.out or ROOT / f"eval/results/headless_{args.model}.jsonl")
    out.parent.mkdir(parents=True, exist_ok=True)
    results = []
    with out.open("a", encoding="utf-8") as fh:
        for q in qs:
            print(f"\n=== {q['id']}: {q['question']}", flush=True)
            r = run_question(q, args.model, args.max_turns, args.timeout)
            s = score(r.get("answer", ""), q)
            rec = {"id": q["id"], "question": q["question"], "model": args.model, **r,
                   "score": s, "ts": time.strftime("%Y-%m-%dT%H:%M:%S")}
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            fh.flush()
            results.append(rec)
            tools_used = " → ".join(t["tool"] for t in r.get("tool_calls", []))
            print(f"[{'PASS' if s['pass'] else 'FAIL'}] facts {s['facts']:.2f} cited {s['cited']} "
                  f"| {len(r.get('tool_calls', []))} tool calls: {tools_used}"
                  + (f" | ${r['cost_usd']:.2f}" if r.get("cost_usd") else "")
                  + (f"\n  ERROR {r['error']}" if r.get("error") else "")
                  + f"\n{r.get('answer', '')[:900]}", flush=True)
            if s["missed"]:
                print("  missed:", s["missed"])
    n = len(results)
    print(f"\n{sum(r['score']['pass'] for r in results)}/{n} pass; mean facts "
          f"{sum(r['score']['facts'] for r in results) / max(n, 1):.2f}; "
          f"cited {sum(r['score']['cited'] for r in results)}/{n}; "
          f"cost ${sum(r.get('cost_usd') or 0 for r in results):.2f}; results → {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
