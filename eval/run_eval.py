#!/usr/bin/env python3
"""Phase 2 evaluation harness: can an agent restricted to the canada50 tools
answer real research questions with correct, cited answers?

    python3 eval/run_eval.py                 # all questions in eval/questions.jsonl
    python3 eval/run_eval.py --ids q01,q05   # subset
    python3 eval/run_eval.py --dry           # print questions + expected, no API calls
    python3 eval/run_eval.py --model claude-opus-5 --effort high --out eval/results/run.jsonl

Each question record: {"id", "question", "expect": [[alt1, alt2], ...]   # every
group must be matched by at least one alternative (case-insensitive substring),
"cite": "regex the answer's cited address/heading should match" (optional),
"notes": "where the answer lives"}.  Scoring: `facts` = fraction of expect
groups matched; `cited` = whether the cite regex matched (or no cite required);
`pass` = all facts + cited.  Results are appended as JSONL with the full tool
trace so failures can be diagnosed (wrong tool path vs. wrong data).

Requires the anthropic SDK and credentials (ANTHROPIC_API_KEY or `ant auth login`).
The tools are the same functions the MCP server exposes (canada50_mcp.store),
called in-process — no MCP transport needed for evaluation.
"""
import argparse
import json
import re
import sys
import time
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from canada50_mcp.store import Store  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
QUESTIONS = ROOT / "eval/questions.jsonl"

SYSTEM = """You are a research assistant answering questions about the Canadian
Department of Indian Affairs annual reports (1880–1930) using ONLY the provided
tools. The tools expose a registry of agencies, reserves (Schedule of 1902),
historical bands, and officers, plus the full report text by segment.

Method: lookup_entity to find entities; neighbors / open_page for relations and
cited passages; search (FTS5 BM25) for text; get_segment to read the text at an
address before asserting anything the passage must support. Answer concisely, then
cite every factual claim with its address in the form `doc_id / segment_id` and the
printed page ([p. N] in segment text). If the sources do not settle the question,
say so rather than guessing. Historical bands are distinct from the modern First
Nations that succeeded them."""


def make_tools(store: Store, trace: list):
    from anthropic import beta_tool

    def rec(name, args, out):
        trace.append({"tool": name, "args": args, "chars": len(out)})
        return out

    def dump(x):
        return json.dumps(x, ensure_ascii=False, default=str)

    @beta_tool
    def lookup_entity(name: str, type: Optional[str] = None, limit: int = 10) -> str:
        """Find registry entities by name or alias as printed in the reports.

        Args:
            name: name or alias (e.g. "Blackfoot", "Kamloops Agency", "Begg").
            type: optional filter: agency | reserve | band | person.
            limit: max candidates.
        """
        return rec("lookup_entity", {"name": name, "type": type}, dump(store.lookup_entity(name, type, limit)))

    @beta_tool
    def open_page(ref: str) -> str:
        """Read a wiki page as text: an entity hub page (infobox, relations, cited
        passages with addresses), a report contents page, or a segment page.

        Args:
            ref: canonical URI, page url (/agencies/x/), or typed id (agency:AG-x,
                reserve:x, band:BAND-x, person:PERSON-x).
        """
        return rec("open_page", {"ref": ref}, dump(store.open_page(ref)))

    @beta_tool
    def search(query: str, year_from: Optional[int] = None, year_to: Optional[int] = None,
               kind: Optional[str] = None, agency: Optional[str] = None, limit: int = 10) -> str:
        """BM25 full-text search over report segments. FTS5 syntax: "exact phrase",
        AND / OR / NOT, prefix*.

        Args:
            query: FTS5 query.
            year_from: earliest report year.
            year_to: latest report year.
            kind: agency_letter | tabular_statement | thematic_section | return | appendix | presentation_letter.
            agency: agency id or name to restrict to that agency's own letters.
            limit: max hits.
        """
        return rec("search", {"query": query, "year_from": year_from, "year_to": year_to, "kind": kind, "agency": agency},
                   dump(store.search(query, year_from, year_to, kind, agency, limit)))

    @beta_tool
    def neighbors(ref: str, edge_type: Optional[str] = None, limit: int = 50) -> str:
        """Graph edges of an entity: OCCUPIES (band→reserve), ADMINISTERED_BY
        (reserve→agency), MERGED_INTO / SPLIT_INTO (agency chains), SIGNED_FOR
        (person→agency), SUCCEEDED_BY (band→modern First Nation QID), plus
        MENTIONED_IN counts per report year and top segments.

        Args:
            ref: entity reference (see open_page).
            edge_type: optional single edge type.
            limit: max edges.
        """
        return rec("neighbors", {"ref": ref, "edge_type": edge_type}, dump(store.neighbors(ref, edge_type, limit)))

    @beta_tool
    def get_document(doc_id: str) -> str:
        """A report issue's metadata and table of contents (segments with headings,
        pages, agency, mention counts).

        Args:
            doc_id: sessional paper id (1886_4), provisional id (prov:dia_ar_1925), or report year (1885).
        """
        return rec("get_document", {"doc_id": doc_id}, dump(store.get_document(doc_id)))

    @beta_tool
    def get_segment(doc_id: str, segment_id: str, with_mentions: bool = True) -> str:
        """Full text of one segment (page markers as [p. N]) with linked entities.

        Args:
            doc_id: document id or report year.
            segment_id: segment id (e.g. p0170-blackfoot-agency-n-w-t-treaty).
            with_mentions: include the linked-entity list.
        """
        return rec("get_segment", {"doc_id": doc_id, "segment_id": segment_id},
                   dump(store.get_segment(doc_id, segment_id, with_mentions)))

    return [lookup_entity, open_page, search, neighbors, get_document, get_segment]


def score(answer: str, q: dict):
    a = answer.lower()
    groups = q.get("expect", [])
    hit = [any(alt.lower() in a for alt in grp) for grp in groups]
    facts = sum(hit) / len(groups) if groups else 1.0
    cited = bool(re.search(q["cite"], answer, re.I)) if q.get("cite") else True
    return {"facts": facts, "missed": [g for g, h in zip(groups, hit) if not h],
            "cited": cited, "pass": facts == 1.0 and cited}


def run_question(client, q, store, model, effort, max_turns):
    trace = []
    tools = make_tools(store, trace)
    runner = client.beta.messages.tool_runner(
        model=model, max_tokens=16000, system=SYSTEM, tools=tools,
        output_config={"effort": effort},
        messages=[{"role": "user", "content": q["question"]}],
        max_iterations=max_turns,
    )
    final, usage = None, {"input": 0, "output": 0}
    t0 = time.time()
    for message in runner:
        final = message
        usage["input"] += message.usage.input_tokens
        usage["output"] += message.usage.output_tokens
    answer = "".join(b.text for b in final.content if b.type == "text") if final else ""
    return {"answer": answer, "stop_reason": final.stop_reason if final else None,
            "tool_calls": trace, "usage": usage, "seconds": round(time.time() - t0, 1)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ids")
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--model", default="claude-opus-5")
    ap.add_argument("--effort", default="high", choices=["low", "medium", "high", "xhigh", "max"])
    ap.add_argument("--max-turns", type=int, default=25)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    qs = [json.loads(l) for l in QUESTIONS.read_text(encoding="utf-8").splitlines() if l.strip() and not l.startswith("#")]
    if args.ids:
        want = set(args.ids.split(","))
        qs = [q for q in qs if q["id"] in want]
    if args.dry:
        for q in qs:
            print(f"{q['id']}: {q['question']}\n    expect {q['expect']}  cite {q.get('cite')}\n    {q.get('notes', '')}")
        return 0

    import anthropic
    client = anthropic.Anthropic()
    store = Store()
    out = Path(args.out or ROOT / f"eval/results/{args.model}_{args.effort}.jsonl")
    out.parent.mkdir(parents=True, exist_ok=True)
    results = []
    with out.open("a", encoding="utf-8") as fh:
        for q in qs:
            print(f"\n=== {q['id']}: {q['question']}")
            try:
                r = run_question(client, q, store, args.model, args.effort, args.max_turns)
            except anthropic.RateLimitError as e:
                print("rate limited; sleeping 60s"); time.sleep(60)
                r = run_question(client, q, store, args.model, args.effort, args.max_turns)
            except anthropic.APIStatusError as e:
                r = {"answer": "", "error": f"{e.status_code}: {e.message}", "tool_calls": [], "usage": {}}
            s = score(r["answer"], q)
            rec = {"id": q["id"], "question": q["question"], "model": args.model, "effort": args.effort,
                   **r, "score": s, "ts": time.strftime("%Y-%m-%dT%H:%M:%S")}
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n"); fh.flush()
            results.append(rec)
            tools_used = " → ".join(t["tool"] for t in r["tool_calls"])
            print(f"[{'PASS' if s['pass'] else 'FAIL'}] facts {s['facts']:.2f} cited {s['cited']} "
                  f"| {len(r['tool_calls'])} tool calls: {tools_used}\n{r['answer'][:800]}")
            if s["missed"]:
                print("  missed:", s["missed"])
    n = len(results)
    print(f"\n{sum(r['score']['pass'] for r in results)}/{n} pass; mean facts "
          f"{sum(r['score']['facts'] for r in results) / max(n, 1):.2f}; "
          f"cited {sum(r['score']['cited'] for r in results)}/{n}; results → {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
