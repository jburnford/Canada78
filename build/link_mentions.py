#!/usr/bin/env python3
"""Tier 0/1 mention linking over the DIA segments (EB-plan NER design:
deterministic gazetteer matching, then rule-based disambiguation; LLM only
for residuals, which this script queues rather than resolves).

Gazetteer facets: agencies (chain variants), reserves, bands, agents (LINCS
postings). Annotations are STAND-OFF: (doc_id, segment_id, text_version,
segment-relative char offsets) -> entity, tier, confidence.

Tier 0: word-boundary match of a gazetteer surface form.
Tier 1 rules:
  - type cue within a small window ("agency", "reserve", "band", "Indians",
    "No.") fixes the entity type; single-token surfaces REQUIRE a cue.
  - segment agency context (heading -> agency chain) prefers reserves/bands
    administered by that agency when a surface is ambiguous across agencies.
  - agent signatures ("J. SMITH, Indian Agent") match LINCS agents by
    surname whose postings overlap the report year (+/-2).
Residuals (ambiguous surfaces, unmatched signatures) go to a queue for
Tier 2.

Usage: python3 build/link_mentions.py [--tags dia_ar_1885,dia_ar_1900]
"""
import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DIA_MD = Path.home() / "DeptIndianAffairs/markdown"
OUT = ROOT / "registries/annotations"

CUE_RE = re.compile(
    r"\b(agency|agencies|reserve|reserves|band|bands|indians|tribes?|I\.? ?R\.?|"
    r"No\.|superintendency|inspectorate)\b", re.I)
SIG_RE = re.compile(
    r"^\s*([A-Z][A-Z.'’\- ]{3,40}),?\s*\n\s*(?:Indian\s+)?(Agent|Acting Agent|"
    r"Indian Superintendent|Superintendent|Inspector|Farmer in charge|"
    r"Agent and Clerk)\.?\s*$", re.M)
STOP = {"HOPE", "YORK", "KENT", "VICTORIA", "DOUGLAS", "GRAVE-YARD",
        "GRAVEYARD", "FISHERY", "TIMBER", "ISLAND", "CREEK", "LAKE", "RIVER",
        "BAY", "POINT", "TOWN", "MISSION", "SCHOOL", "CHURCH", "MOUNTAIN"}


def norm_surface(s):
    return re.sub(r"\s+", " ", s.strip()).upper()


REGION_MAP = [
    (r"BRITISH COLUMBIA", "BC"), (r"YUKON", "YT"), (r"ONTARIO", "ON"),
    (r"QUEBEC", "QC"), (r"NOVA SCOTIA", "NS"), (r"NEW BRUNSWICK", "NB"),
    (r"PRINCE EDWARD", "PE"),
    (r"MANITOBA|NORTH-?WEST|SASKATCHEWAN|ALBERTA|ASSINIBOIA|KEEWATIN", "PR"),
]


def region(prov):
    """Collapse province strings (segment hints and 1902 Schedule headers)
    to a coarse region key; the Schedule lumps the prairies together."""
    if not isinstance(prov, str):
        return None
    for pat, key in REGION_MAP:
        if re.search(pat, prov.upper()):
            return key
    return None


DATELINE_RE = re.compile(
    r"\b(ONT|ONTARIO|QUE|QUEBEC|P\.? ?Q|N\.? ?S|NOVA SCOTIA|N\.? ?B|NEW BRUNSWICK|"
    r"P\.? ?E\.? ?I|MAN|MANITOBA|N\.? ?-? ?W\.? ?T|NORTH-?WEST TERRITOR(?:Y|IES)|"
    r"B\.? ?C|BRITISH COLUMBIA|ALTA|ALBERTA|SASK|SASKATCHEWAN|ASSA|ASSINIBOIA|"
    r"YUKON|Y\.? ?T)\b\.?", re.I)
DATELINE_MAP = [
    (r"^ONT", "ON"), (r"^QUE|^P ?Q", "QC"), (r"^N ?S|^NOVA", "NS"),
    (r"^N ?B|^NEW", "NB"), (r"^P ?E", "PE"), (r"^MAN", "PR"), (r"^N ?W ?T|^NORTH", "PR"),
    (r"^B ?C|^BRITISH", "BC"), (r"^ALTA|^ALBERTA|^SASK|^ASSA", "PR"), (r"^Y", "YT"),
]


def dateline_region(text):
    """Region from the letter's own address block (first ~500 chars):
    'PARRY SOUND, ONT., 14th September' -> ON. Datelines beat any hint."""
    head = text[:500]
    best = None
    for m in DATELINE_RE.finditer(head):
        tok = re.sub(r"[^A-Z ]", "", m.group(1).upper()).strip()
        for pat, key in DATELINE_MAP:
            if re.search(pat, tok):
                best = key
                break
        if best:
            break
    return best


def initials(name):
    """'J.R. STEVENSON' -> 'JR'; 'Gordon, J.H.' -> 'JH'; 'THOMAS GORDON' -> 'T'."""
    if "," in name:
        given = name.split(",", 1)[1]
    else:
        parts = name.strip().split()
        given = " ".join(parts[:-1]) if len(parts) > 1 else ""
    return "".join(p[0] for p in re.findall(r"[A-Za-z]+", given)).upper()


def build_gazetteer(chain_region=None):
    chain_region = chain_region or {}
    entries = []  # (surface_upper, entity_type, entity_id, agency_ctx, region)
    chains = pd.read_parquet(ROOT / "registries/entities/agency_chain_members.parquet")
    for r in chains.drop_duplicates(["name", "chain_id"]).itertuples():
        reg = chain_region.get(r.chain_id)
        for surf in {r.name, r.canonical}:
            s = norm_surface(surf)
            entries.append((s, "agency", r.chain_id, None, reg))
        # bare toponym ("DUCK LAKE") so agency places compete with same-named
        # reserves elsewhere instead of the reserve winning by default
        topo = re.sub(r"\b(AGENCY|SUPERINTENDENCY|INSPECTORATE|COMMISSIONER|"
                      r"SUPERINTENDENT)\b", "", r.canonical)
        topo = re.sub(r"\s*-\s*\d\w\w DIVISION", "", topo)
        topo = re.sub(r"\s+", " ", topo).strip(" -")
        if len(topo) >= 5 and topo not in STOP:
            entries.append((topo, "agency", r.chain_id, None, reg))
    reserves = pd.read_parquet(ROOT / "registries/entities/reserves.parquet")
    for r in reserves.itertuples():
        if isinstance(r.name, str) and len(r.name) >= 4:
            entries.append((norm_surface(r.name), "reserve", r.reserve_id,
                            r.division_norm, region(r.province)))
    bands = pd.read_parquet(ROOT / "registries/entities/bands.parquet")
    modern = {}
    mp = ROOT / "registries/external/band_modern_province.csv"
    if mp.exists():
        modern = dict(pd.read_csv(mp).values)  # qid -> "Ontario|Quebec"
    for r in bands.itertuples():
        if isinstance(r.name, str) and 4 <= len(r.name) <= 60:
            divs = list(r.divisions) if r.divisions is not None else []
            provs = list(r.provinces) if r.provinces is not None else []
            reg = region(provs[0]) if provs else None
            if reg is None and isinstance(r.succeeded_by_qid, str):
                # modern First Nation's province (Wikidata P131) as fallback
                reg = region(str(modern.get(r.succeeded_by_qid, "")).split("|")[0])
            entries.append((norm_surface(r.name), "band", r.band_id,
                            divs[0] if divs else None, reg))
    gaz = defaultdict(list)
    for s, t, i, ctx, reg in entries:
        if s in STOP or len(s) < 4 or not re.search(r"[A-Z]{3}", s):
            continue
        gaz[s].append((t, i, ctx, reg))
    return gaz


def build_agents():
    acts = pd.read_parquet(ROOT / "registries/external/lincs_ia_activities_dedup.parquet")
    acts = acts.dropna(subset=["agent_label"])
    acts["year"] = pd.to_numeric(acts.begin.str[:4], errors="coerce")
    acts["surname"] = acts.agent_label.str.split(",").str[0].str.upper().str.strip()
    by_surname = defaultdict(list)
    for r in acts.itertuples():
        by_surname[r.surname].append((r.agent, r.agent_label, r.group_label, r.year))
    return by_surname


def heading_to_chain(chains_members):
    m = {}
    for r in chains_members.itertuples():
        m[norm_surface(r.name)] = r.chain_id
    return m


def link_segment(text, seg, gaz, gaz_re, agents, seg_chain, chain_agency_name,
                 minted):
    anns, residual = [], []
    for m in gaz_re.finditer(text):
        surf = norm_surface(m.group(0))
        cands = gaz.get(surf)
        if not cands:
            continue
        # never match inside a hyphenated compound ("Pete-qua-quay")
        if (m.start() > 0 and text[m.start() - 1] == "-") or \
           (m.end() < len(text) and text[m.end()] == "-"):
            continue
        single_token = " " not in surf and "-" not in surf
        # single tokens need a cue close by and mostly to the right
        # ("Alexander reserve"), so a cue 40 chars away can't rescue a given
        # name like "Alexander Logan"
        if single_token:
            window = text[max(0, m.start() - 12): m.end() + 25]
        else:
            window = text[max(0, m.start() - 40): m.end() + 40]
        cue = CUE_RE.search(window)
        cue_word = cue.group(1).lower() if cue else None
        if single_token and not cue:
            continue  # Tier 1 rule: bare single tokens need a type cue
        # region guard: a reserve/band from another region is not this mention
        seg_region = seg.get("region")
        region_mismatch = False
        if seg_region:
            guarded = [c for c in cands
                       if c[0] == "agency" or c[3] is None or c[3] == seg_region]
            if guarded:
                cands = guarded
            elif seg.get("kind") == "agency_letter":
                # letter regions come from datelines (reliable): a mismatch
                # means the only gazetteer entity is the wrong one -> residual
                residual.append(dict(char_start=m.start(), char_end=m.end(),
                                     surface=m.group(0),
                                     candidates=sorted(f"{t}:{i}" for t, i, _, _ in cands),
                                     note=f"region mismatch (letter {seg_region})"))
                continue
            else:
                region_mismatch = True  # tables: region less certain, downgrade
        # filter by cue type
        typed = cands
        if cue_word:
            if cue_word.startswith("agenc") or cue_word in ("superintendency", "inspectorate"):
                typed = [c for c in cands if c[0] == "agency"] or cands
            elif cue_word.startswith("reserve") or cue_word in ("no.", "i.r.", "ir"):
                typed = [c for c in cands if c[0] == "reserve"] or cands
            elif cue_word.startswith("band") or cue_word in ("indians", "tribe"):
                typed = [c for c in cands if c[0] == "band"] or cands
        # prefer entities under the segment's agency context
        if len(typed) > 1 and chain_agency_name:
            ctx = [c for c in typed if c[2] and c[2] == chain_agency_name]
            if ctx:
                typed = ctx
        # collapse duplicates of the same entity
        uniq = {(t, i) for t, i, _, _ in typed}
        if len(uniq) == 1:
            t, i = next(iter(uniq))
            conf = "high" if (cue and not single_token) else "medium"
            if not cue and seg.get("kind") in ("tabular_statement", "return", "appendix"):
                conf = "low"  # bare name in a table: entity type is a guess
            if not seg_region and t != "agency":
                conf = "low" if conf == "medium" else "medium"  # unverified region
            note = None
            if region_mismatch and t != "agency":
                conf = "low"
                note = f"region mismatch: segment {seg_region}"
            tier = 1 if cue else 0
            anns.append(dict(char_start=m.start(), char_end=m.end(),
                             surface=m.group(0), entity_type=t, entity_id=i,
                             tier=tier, confidence=conf, note=note))
        else:
            residual.append(dict(char_start=m.start(), char_end=m.end(),
                                 surface=m.group(0),
                                 candidates=sorted(f"{t}:{i}" for t, i in uniq)))
    # agent signatures
    for sm in SIG_RE.finditer(text):
        name = sm.group(1).strip(" ,.")
        surname = name.split()[-1].upper().strip(".,'")
        cands = [c for c in agents.get(surname, [])
                 if c[3] is None or pd.isna(c[3]) or abs(c[3] - seg["report_year"]) <= 2]
        # initials must agree where both sides have them
        sig_ini = initials(name)
        cands = [c for c in cands
                 if not sig_ini or not initials(c[1]) or
                 sig_ini[0] == initials(c[1])[0]]
        uniq = {c[0]: c for c in cands}
        if len(uniq) == 1:
            uri, label, grp, yr = next(iter(uniq.values()))
            anns.append(dict(char_start=sm.start(1), char_end=sm.end(1),
                             surface=name, entity_type="agent", entity_id=uri,
                             tier=1, confidence="high" if grp else "medium",
                             note=f"LINCS {label} ({grp}, {yr})"))
        elif len(uniq) > 1:
            residual.append(dict(char_start=sm.start(1), char_end=sm.end(1),
                                 surface=name, candidates=sorted(uniq)))
        else:
            # mint a person entity from the signature (user decision
            # 2026-08-24); dedupe corpus-wide on normalized name
            key = re.sub(r"[^A-Z]", "", name.upper().replace(".", ""))
            if key not in minted:
                slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
                minted[key] = dict(person_id=f"PERSON-{slug}", name=name,
                                   first_year=seg["report_year"],
                                   last_year=seg["report_year"],
                                   n_signatures=0, chains=set())
            p = minted[key]
            p["first_year"] = min(p["first_year"], seg["report_year"])
            p["last_year"] = max(p["last_year"], seg["report_year"])
            p["n_signatures"] += 1
            if seg_chain:
                p["chains"].add(seg_chain)
            anns.append(dict(char_start=sm.start(1), char_end=sm.end(1),
                             surface=name, entity_type="agent",
                             entity_id=p["person_id"], tier=1,
                             confidence="medium", note="minted from signature"))
    return anns, residual


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tags", default=None)
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)

    agents = build_agents()
    members = pd.read_parquet(ROOT / "registries/entities/agency_chain_members.parquet")
    h2c = heading_to_chain(members)
    chains = pd.read_parquet(ROOT / "registries/entities/agency_chains.parquet")
    chain_name = dict(zip(chains.chain_id, chains.canonical))

    segs = pd.read_parquet(ROOT / "registries/documents/dia_segments.parquet")
    segs["report_year"] = segs.tag.str.extract(r"(\d{4})").astype(int)
    if args.tags:
        segs = segs[segs.tag.isin(args.tags.split(","))]

    # pass 1: dateline regions per segment; majority vote per agency chain
    bodies, seg_dateline, chain_votes = {}, {}, defaultdict(lambda: defaultdict(int))
    for tag, grp in segs.groupby("tag"):
        raw = (DIA_MD / f"{tag}.md").read_text(encoding="utf-8", errors="replace")
        m = re.match(r"^---\n.*?\n---\n", raw, re.S)
        bodies[tag] = raw[m.end():] if m else raw
        for seg in grp.itertuples():
            dl = dateline_region(bodies[tag][seg.char_start: seg.char_start + 600])
            seg_dateline[(tag, seg.segment_id)] = dl
            ch = h2c.get(norm_surface(seg.heading or ""))
            if dl and ch:
                chain_votes[ch][dl] += 1
    chain_region = {ch: max(v, key=v.get) for ch, v in chain_votes.items()}
    # supplement chain regions from the reserves registry (schedule provinces)
    reserves = pd.read_parquet(ROOT / "registries/entities/reserves.parquet")
    for div, prov in reserves.dropna(subset=["division_norm"]).groupby(
            "division_norm").province.agg(lambda s: s.mode().iat[0]).items():
        ch = "AG-" + re.sub(r"[^A-Za-z0-9]+", "-", div.lower()).strip("-")
        chain_region.setdefault(ch, region(prov))
    print(f"chain regions resolved: {len(chain_region)} "
          f"(dateline votes: {len(chain_votes)})")
    gaz = build_gazetteer(chain_region)
    surfaces = sorted(gaz, key=len, reverse=True)
    gaz_re = re.compile(r"\b(" + "|".join(re.escape(s) for s in surfaces) + r")\b",
                        re.I)

    all_anns, all_res, minted = [], [], {}
    for tag, grp in segs.groupby("tag"):
        body = bodies[tag]
        for seg in grp.itertuples():
            text = body[seg.char_start:seg.char_end]
            seg_chain = h2c.get(norm_surface(seg.heading or ""))
            agency_name = chain_name.get(seg_chain) if seg_chain else None
            seg_region = seg_dateline.get((tag, seg.segment_id)) or \
                chain_region.get(seg_chain)  # never the stale province hint
            anns, res = link_segment(
                text, {"report_year": seg.report_year, "kind": seg.kind,
                       "region": seg_region},
                gaz, gaz_re, agents, seg_chain, agency_name, minted)
            base = dict(doc_id=seg.doc_id, tag=tag, segment_id=seg.segment_id,
                        text_version=seg.text_version, context_agency=seg_chain)
            all_anns += [dict(base, **a) for a in anns]
            all_res += [dict(base, **r) for r in res]
        print(f"{tag}: {sum(1 for a in all_anns if a['tag']==tag)} mentions, "
              f"{sum(1 for r in all_res if r['tag']==tag)} residuals")

    ann = pd.DataFrame(all_anns)
    res = pd.DataFrame(all_res)
    suffix = "_sample" if args.tags else ""
    if minted:
        pm = pd.DataFrame([dict(p, chains=sorted(p["chains"]),
                                uri=f"https://jimclifford.ca/canada50/persons/{p['person_id'][7:]}",
                                uri_source="minted", source="dia_signature")
                           for p in minted.values()])
        pm.to_parquet(ROOT / f"registries/entities/persons_minted{suffix}.parquet",
                      index=False)
        print(f"persons minted from signatures: {len(pm)}")
    ann.to_parquet(OUT / f"mentions_dia{suffix}.parquet", index=False)
    res["candidates"] = res.candidates.map(json.dumps) if len(res) else res.get("candidates")
    res.to_parquet(OUT / f"mention_residuals_dia{suffix}.parquet", index=False)
    print(f"\nTOTAL mentions: {len(ann)} | residuals: {len(res)}")
    if len(ann):
        print(ann.entity_type.value_counts().to_string())
        print("tier:", ann.tier.value_counts().to_dict(),
              "| confidence:", ann.confidence.value_counts().to_dict())
        print("distinct entities mentioned:", ann.entity_id.nunique())


if __name__ == "__main__":
    sys.exit(main())
