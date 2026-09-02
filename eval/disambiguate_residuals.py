#!/usr/bin/env python3
"""Tier-2 mention disambiguation with the Qwen vLLM server.

Two phases so the cluster side needs only the Python stdlib (the vLLM
container has no pandas):

  # 1. locally (needs pandas + the DIA markdown): resolve contexts and candidate
  #    descriptions for every residual into one JSONL
  python3 eval/disambiguate_residuals.py --prepare eval/residuals_items.jsonl

  # 2. on the cluster (stdlib only): ask the model, 20 items per prompt
  python3 eval/disambiguate_residuals.py --items eval/residuals_items.jsonl \
      --base-url http://127.0.0.1:8000/v1 --out eval/results/residuals_qwen38 \
      [--batch 20] [--parallel 3] [--limit N]

Output: <out>/decisions.jsonl — one line per residual: residual_idx, choice
(candidate id or "none"), confidence, reason. Resumable (decided indices are
skipped). Decisions are proposals: a later linker pass applies them with the
same confidence gate as Tier 0/1 (high+medium primary, low unverified).
"""
import argparse
import json
import os
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from extract_1902 import chat, parse_json_array  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DIA_MD = Path(os.environ.get("CANADA50_DIA_MD", Path.home() / "DeptIndianAffairs/markdown"))

SYSTEM = """You are an expert on the Department of Indian Affairs annual reports (Canada,
1880–1930) resolving which registry entity a mention refers to. Answer with JSON only."""

PROMPT = """Each item below is a mention from a DIA annual report ({tag}), with surrounding
text (the mention is marked [[like this]]) and the candidate registry entities it could
refer to. The candidate list is complete for this surface; if the text clearly refers to
something else (a place that is not the reserve/agency/band named, a person, a ship, a
school, or a generic use of the word), answer "none". Agencies are administrative units;
reserves are tracts of land; bands are peoples. A letter written from an agency usually
refers to reserves and bands within it; regional cues in the context (province, river,
lake, treaty) matter.

Items:
{items}

Return a JSON array with one object per item, in order:
  {{"i": <item number>, "choice": "<candidate id or none>", "confidence": "high|medium|low",
    "reason": "<one short clause>"}}
Output only the JSON array."""


# ----------------------------------------------------------------- prepare (local)

def prepare(out_path, context=350):
    import pandas as pd  # local only
    meta = {}
    reg = pd.read_parquet(ROOT / "registries/entities/reserves.parquet")
    for r in reg.itertuples():
        no = "" if pd.isna(r.reserve_no) else f" No. {r.reserve_no}"
        meta[f"reserve:{r.reserve_id}"] = (
            f"reserve: {r.name or (r.band_norm or '') + ' reserve'}{no} "
            f"({(r.division_norm or '').title()}, {(r.province or '').title()}"
            f"{'; band ' + r.band_norm if isinstance(r.band_norm, str) else ''})")
    for c in pd.read_parquet(ROOT / "registries/entities/agency_chains.parquet").itertuples():
        meta[f"agency:{c.chain_id}"] = f"agency: {c.canonical.title()} ({c.unit_type.lower()}, {c.first_year}–{c.last_year})"
    for x in pd.read_parquet(ROOT / "registries/entities/bands.parquet").itertuples():
        provs = ", ".join(list(x.provinces) if x.provinces is not None else [])
        meta[f"band:{x.band_id}"] = f"band: {x.name} ({provs.title()})"
    res = pd.read_parquet(ROOT / "registries/annotations/mention_residuals_dia.parquet")
    segs = pd.read_parquet(ROOT / "registries/documents/dia_segments.parquet").set_index(["tag", "segment_id"])
    bodies = {}
    n = 0
    with open(out_path, "w", encoding="utf-8") as fh:
        for idx, r in enumerate(res.itertuples()):
            if r.tag not in bodies:
                raw = (DIA_MD / f"{r.tag}.md").read_text(encoding="utf-8", errors="replace")
                m = re.match(r"^---\n.*?\n---\n", raw, re.S)
                bodies[r.tag] = raw[m.end():] if m else raw
            body = bodies[r.tag]
            try:
                s = segs.loc[(r.tag, r.segment_id)]
                a = int(s.char_start) + int(r.char_start); b = int(s.char_start) + int(r.char_end)
                pre = re.sub(r"\s+", " ", body[max(int(s.char_start), a - context):a])
                post = re.sub(r"\s+", " ", body[b:min(int(s.char_end), b + context)])
                ctx = f"{pre}[[{body[a:b]}]]{post}"
            except KeyError:
                ctx = f"[[{r.surface}]]"
            cands = json.loads(r.candidates) if isinstance(r.candidates, str) else list(r.candidates)
            fh.write(json.dumps(dict(
                residual_idx=idx, tag=r.tag, segment_id=r.segment_id, char_start=int(r.char_start),
                char_end=int(r.char_end), surface=r.surface,
                context_agency=r.context_agency if isinstance(r.context_agency, str) else None,
                context=ctx, candidates=[{"id": c, "desc": meta.get(c, c)} for c in cands]),
                ensure_ascii=False) + "\n")
            n += 1
    print(f"prepared {n:,} items → {out_path}")


# ----------------------------------------------------------------- run (cluster)

def run_items(args):
    out = Path(args.out); out.mkdir(parents=True, exist_ok=True)
    dec_path = out / "decisions.jsonl"
    done = set()
    if dec_path.exists():
        for l in dec_path.read_text(encoding="utf-8").splitlines():
            if l.strip():
                done.add(json.loads(l)["residual_idx"])
    items = [json.loads(l) for l in open(args.items, encoding="utf-8") if l.strip()]
    todo = [it for it in items if it["residual_idx"] not in done]
    if args.limit:
        todo = todo[:args.limit]
    print(f"items {len(items):,} | done {len(done):,} | todo {len(todo):,}")
    by_tag = {}
    for it in todo:
        by_tag.setdefault(it["tag"], []).append(it)
    batches = []
    for tag, rows in by_tag.items():
        for k in range(0, len(rows), args.batch):
            batches.append((tag, rows[k:k + args.batch]))
    print(f"{len(batches)} prompts of ≤{args.batch} items")

    def render(tag, rows):
        parts = []
        for n, r in enumerate(rows, 1):
            cl = "\n".join(f"      - {c['id']}  →  {c['desc']}" for c in r["candidates"])
            ag = f" (letter from: {r['context_agency']})" if r.get("context_agency") else ""
            parts.append(f"{n}. surface \"{r['surface']}\"{ag}\n   context: …{r['context']}…\n   candidates:\n{cl}")
        return PROMPT.format(tag=tag, items="\n\n".join(parts))

    if args.dry:
        p = render(*batches[0]); print(p[:3000]); print(f"... ≈ {len(p) // 4:,} tokens"); return
    lock = threading.Lock()
    stats = {"decided": 0, "failed": 0, "t0": time.time()}

    def run(batch):
        tag, rows = batch
        prompt = render(tag, rows)
        try:
            resp = chat(args.base_url, args.model,
                        [{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}],
                        max_tokens=args.max_tokens, temperature=0.1, effort=args.reasoning_effort,
                        timeout=args.timeout, api_key=args.api_key)
            msg = resp["choices"][0]["message"]
            reply = msg.get("content") or ""
            if not reply.strip() and (msg.get("reasoning") or "").find("[") >= 0:
                reply = msg["reasoning"][msg["reasoning"].find("["):]
            decisions, _ = parse_json_array(reply)
        except Exception as e:  # noqa: BLE001
            with lock:
                stats["failed"] += len(rows)
            print(f"  batch {tag} x{len(rows)} FAILED: {e}", flush=True)
            return
        by_i = {d.get("i"): d for d in decisions if isinstance(d, dict)}
        with lock, dec_path.open("a", encoding="utf-8") as fh:
            for n, r in enumerate(rows, 1):
                d = by_i.get(n)
                if not d:
                    continue
                fh.write(json.dumps(dict(residual_idx=r["residual_idx"], tag=r["tag"], segment_id=r["segment_id"],
                                         char_start=r["char_start"], char_end=r["char_end"], surface=r["surface"],
                                         choice=d.get("choice"), confidence=d.get("confidence"),
                                         reason=d.get("reason")), ensure_ascii=False) + "\n")
                stats["decided"] += 1
            if stats["decided"] % 200 < args.batch:
                el = time.time() - stats["t0"]
                print(f"  decided {stats['decided']:,} failed {stats['failed']:,} — "
                      f"{stats['decided'] / max(el, 1) * 3600:,.0f}/h", flush=True)

    with ThreadPoolExecutor(max_workers=args.parallel) as ex:
        list(ex.map(run, batches))
    (out / "run.json").write_text(json.dumps(dict(stats, seconds=round(time.time() - stats["t0"]))))
    print(f"done: decided {stats['decided']:,} failed {stats['failed']:,} → {dec_path}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--prepare", metavar="OUT_JSONL", help="build the items file locally (needs pandas)")
    ap.add_argument("--items", default=str(ROOT / "eval/residuals_items.jsonl"))
    ap.add_argument("--base-url", default="http://127.0.0.1:8000/v1")
    ap.add_argument("--model", default="Qwen/Qwen3.8-27B-FP8")
    ap.add_argument("--api-key", default="none")
    ap.add_argument("--out", default=str(ROOT / "eval/results/residuals_qwen38"))
    ap.add_argument("--batch", type=int, default=20)
    ap.add_argument("--parallel", type=int, default=3)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--context", type=int, default=350)
    ap.add_argument("--reasoning-effort", default="none", choices=["none", "low", "medium", "xhigh"],
                    help="'none' = thinking off: a 20-way pick needs no deliberation and thinking "
                         "tokens were exhausting the reply budget")
    ap.add_argument("--max-tokens", type=int, default=8000)
    ap.add_argument("--timeout", type=int, default=900)
    ap.add_argument("--dry", action="store_true")
    args = ap.parse_args()
    if args.prepare:
        prepare(args.prepare, args.context)
    else:
        run_items(args)


if __name__ == "__main__":
    main()
